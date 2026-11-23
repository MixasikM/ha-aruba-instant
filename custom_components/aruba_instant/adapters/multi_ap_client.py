# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Composite Multi-AP & Auto-Transport adapters for Aruba Instant / AOS Wi-Fi."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import aiohttp

from ..engine.models import ClusterSnapshot, merge_snapshots
from ..ports.cluster_client import (
    ArubaAuthError,
    ArubaConnectionError,
    ArubaMultiAuthError,
    ArubaRestApiDisabledError,
    BaseArubaClusterClient,
)
from .rest_client import ArubaRestClient
from .ssh_client import ArubaSshClient

_TRANSIENT_SSH_FALLBACK_POLLS = 20


class AutoTransportClient(BaseArubaClusterClient):
    """Single-node client preferring REST `:4343` with controlled SSH `:22` fallback."""

    def __init__(
        self,
        rest_client: ArubaRestClient,
        ssh_client: ArubaSshClient,
        *,
        fallback_on_rest_disabled: bool = True,
    ) -> None:
        self._rest_client = rest_client
        self._ssh_client = ssh_client
        self._fallback_on_rest_disabled = fallback_on_rest_disabled
        self._active_client: BaseArubaClusterClient | None = None
        self._rest_disabled_permanently = False
        self._ssh_cooldown_remaining = 0

    @property
    def active_transport(self) -> str | None:
        """Return 'rest' or 'ssh' once resolved."""
        if self._active_client is self._rest_client:
            return "rest"
        if self._active_client is self._ssh_client or self._ssh_cooldown_remaining > 0:
            return "ssh"
        return None

    async def async_fetch_snapshot(self) -> ClusterSnapshot:
        """Fetch snapshot via REST first; fall back to SSH appropriately."""
        if self._rest_disabled_permanently:
            return await self._ssh_client.async_fetch_snapshot()

        if self._ssh_cooldown_remaining > 0:
            self._ssh_cooldown_remaining -= 1
            return await self._ssh_client.async_fetch_snapshot()

        try:
            snap = await self._rest_client.async_fetch_snapshot()
        except ArubaRestApiDisabledError:
            await self._rest_client.async_close()
            if not self._fallback_on_rest_disabled:
                raise
            snap = await self._ssh_client.async_fetch_snapshot()
            self._active_client = self._ssh_client
            self._rest_disabled_permanently = True
            return snap
        except ArubaConnectionError:
            await self._rest_client.async_close()
            snap = await self._ssh_client.async_fetch_snapshot()
            # Re-probe fast REST after cooldown in case 503/reboot was transient
            self._ssh_cooldown_remaining = _TRANSIENT_SSH_FALLBACK_POLLS
            return snap

        self._active_client = self._rest_client
        return snap

    async def async_close(self) -> None:
        """Close underlying REST and SSH clients."""
        await self._rest_client.async_close()
        await self._ssh_client.async_close()


def build_node_client(
    session: aiohttp.ClientSession,
    node: dict[str, Any],
    *,
    fallback_on_rest_disabled: bool = True,
) -> BaseArubaClusterClient:
    """Instantiate the configured transport client for one normalized node dict."""
    host = str(node["host"]).strip()
    username = str(node["username"])
    password = str(node["password"])
    transport = str(node.get("transport", "auto")).lower()
    rest_port = int(node.get("rest_port", 4343))
    ssh_port = int(node.get("ssh_port", 22))
    verify_ssl = bool(node.get("verify_ssl", False))
    legacy_ssh = bool(node.get("legacy_ssh", False))

    rest_client = ArubaRestClient(
        session=session,
        host=host,
        username=username,
        password=password,
        port=rest_port,
        verify_ssl=verify_ssl,
    )
    ssh_client = ArubaSshClient(
        host=host,
        username=username,
        password=password,
        port=ssh_port,
        legacy_ssh=legacy_ssh,
    )
    if transport == "rest":
        return rest_client
    if transport == "ssh":
        return ssh_client
    return AutoTransportClient(
        rest_client=rest_client,
        ssh_client=ssh_client,
        fallback_on_rest_disabled=fallback_on_rest_disabled,
    )


class MultiApClusterClient(BaseArubaClusterClient):
    """Composite client polling multiple Standalone / AOS 10 APs in parallel."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        nodes: list[dict[str, Any]],
        site_name: str,
        node_clients: list[BaseArubaClusterClient] | None = None,
    ) -> None:
        self._nodes = [dict(n) for n in nodes]
        self._site_name = site_name
        self._clients: list[BaseArubaClusterClient] = (
            list(node_clients)
            if node_clients is not None
            else [build_node_client(session, node) for node in self._nodes]
        )

    async def async_fetch_snapshot(self) -> ClusterSnapshot:
        """Poll all configured AP nodes concurrently and merge into one snapshot."""
        results = await asyncio.gather(
            *(client.async_fetch_snapshot() for client in self._clients),
            return_exceptions=True,
        )

        auth_failed_hosts: list[str] = []
        conn_errors: list[str] = []
        node_snapshots: list[tuple[dict[str, Any], ClusterSnapshot | None]] = []
        succeeded = 0

        for node, res in zip(self._nodes, results):
            if isinstance(res, asyncio.CancelledError):
                raise res
            host = str(node.get("host", ""))
            if isinstance(res, ArubaAuthError):
                auth_failed_hosts.append(res.host or host)
                node_snapshots.append((node, None))
            elif isinstance(res, BaseException):
                conn_errors.append(f"{host}: {res}")
                node_snapshots.append((node, None))
            else:
                succeeded += 1
                node_snapshots.append((node, res))

        if auth_failed_hosts:
            raise ArubaMultiAuthError(
                f"Authentication failed for AP(s): {', '.join(auth_failed_hosts)}",
                failed_hosts=auth_failed_hosts,
            )

        if succeeded == 0:
            raise ArubaConnectionError(
                f"All Aruba AP nodes failed to respond ({'; '.join(conn_errors)})"
            )

        return merge_snapshots(
            node_results=node_snapshots,
            site_name=self._site_name,
            fetched_at=datetime.now(timezone.utc),
        )

    async def async_close(self) -> None:
        """Close all underlying per-AP clients concurrently."""
        await asyncio.gather(
            *(client.async_close() for client in self._clients),
            return_exceptions=True,
        )
