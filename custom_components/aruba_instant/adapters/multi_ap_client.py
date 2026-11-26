# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Composite Multi-AP & Auto-Transport adapters for Aruba Instant / AOS Wi-Fi."""
from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import aiohttp

from ..engine.models import (
    ApUpgradeEntry,
    ClusterSnapshot,
    FirmwareCheckResult,
    FirmwareImageInfo,
    merge_snapshots,
)
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

    @property
    def active_host(self) -> str | None:
        """Return currently targeted host on the active transport."""
        return (self._active_client or self._rest_client).active_host

    def set_known_peer_ips(self, peer_ips: Iterable[str]) -> None:
        """Seed discovered peer AP IPs on both REST and SSH adapters."""
        ips = list(peer_ips)
        self._rest_client.set_known_peer_ips(ips)
        self._ssh_client.set_known_peer_ips(ips)

    def get_known_peer_ips(self) -> list[str]:
        """Return merged list of discovered peer AP IPs from both transports."""
        merged: list[str] = []
        for ip_addr in (*self._rest_client.get_known_peer_ips(), *self._ssh_client.get_known_peer_ips()):
            if ip_addr not in merged:
                merged.append(ip_addr)
        return merged

    def get_vc_vip_failure_count(self, vc_ip: str) -> int:
        """Return consecutive VC VIP failure count from the active transport."""
        return self._preferred_client().get_vc_vip_failure_count(vc_ip)

    async def async_fetch_snapshot(self) -> ClusterSnapshot:
        """Fetch snapshot via REST first; fall back to SSH appropriately."""
        if self._rest_disabled_permanently:
            snap = await self._ssh_client.async_fetch_snapshot()
            self._rest_client.set_known_peer_ips(self._ssh_client.get_known_peer_ips())
            return snap

        if self._ssh_cooldown_remaining > 0:
            self._ssh_cooldown_remaining -= 1
            snap = await self._ssh_client.async_fetch_snapshot()
            self._rest_client.set_known_peer_ips(self._ssh_client.get_known_peer_ips())
            return snap

        try:
            snap = await self._rest_client.async_fetch_snapshot()
        except ArubaRestApiDisabledError:
            await self._rest_client.async_close()
            if not self._fallback_on_rest_disabled:
                raise
            snap = await self._ssh_client.async_fetch_snapshot()
            self._active_client = self._ssh_client
            self._rest_disabled_permanently = True
            self._rest_client.set_known_peer_ips(self._ssh_client.get_known_peer_ips())
            return snap
        except ArubaConnectionError:
            await self._rest_client.async_close()
            snap = await self._ssh_client.async_fetch_snapshot()
            # Re-probe fast REST after cooldown in case 503/reboot was transient
            self._ssh_cooldown_remaining = _TRANSIENT_SSH_FALLBACK_POLLS
            self._rest_client.set_known_peer_ips(self._ssh_client.get_known_peer_ips())
            return snap

        self._active_client = self._rest_client
        self._ssh_client.set_known_peer_ips(self._rest_client.get_known_peer_ips())
        return snap

    async def async_close(self) -> None:
        """Close underlying REST and SSH clients."""
        await self._rest_client.async_close()
        await self._ssh_client.async_close()

    def _preferred_client(self) -> BaseArubaClusterClient:
        """Return currently active transport client, defaulting to REST unless SSH is active."""
        if self._rest_disabled_permanently:
            return self._ssh_client
        if self._ssh_cooldown_remaining > 0 and self._active_client is not self._rest_client:
            return self._ssh_client
        return self._active_client or self._rest_client

    async def async_fetch_firmware_details(
        self,
    ) -> tuple[FirmwareImageInfo | None, FirmwareCheckResult | None]:
        """Fetch firmware details via preferred transport with REST->SSH fallback."""
        client = self._preferred_client()
        try:
            return await client.async_fetch_firmware_details()
        except (ArubaRestApiDisabledError, ArubaConnectionError):
            if client is self._ssh_client:
                return (None, None)
            try:
                return await self._ssh_client.async_fetch_firmware_details()
            except ArubaAuthError:
                raise
            except Exception:
                return (None, None)

    async def async_reboot_ap(self, ap_ip: str) -> None:
        """Reboot a single AP via active transport with REST-disabled SSH fallback."""
        client = self._preferred_client()
        try:
            await client.async_reboot_ap(ap_ip)
        except ArubaRestApiDisabledError:
            if not self._fallback_on_rest_disabled:
                raise
            await self._ssh_client.async_reboot_ap(ap_ip)

    async def async_set_ssid_enabled(self, profile_name: str, enabled: bool) -> None:
        """Toggle SSID profile via active transport with REST-disabled SSH fallback."""
        client = self._preferred_client()
        try:
            await client.async_set_ssid_enabled(profile_name, enabled)
        except ArubaRestApiDisabledError:
            if not self._fallback_on_rest_disabled:
                raise
            await self._ssh_client.async_set_ssid_enabled(profile_name, enabled)

    async def async_upgrade_firmware(
        self,
        image_url: str,
        *,
        ap_class: str | None = None,
        auto_reboot: bool = True,
    ) -> None:
        """Trigger firmware upgrade via active transport with REST-disabled SSH fallback."""
        client = self._preferred_client()
        try:
            await client.async_upgrade_firmware(
                image_url,
                ap_class=ap_class,
                auto_reboot=auto_reboot,
            )
        except ArubaRestApiDisabledError:
            if not self._fallback_on_rest_disabled:
                raise
            await self._ssh_client.async_upgrade_firmware(
                image_url,
                ap_class=ap_class,
                auto_reboot=auto_reboot,
            )


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
        chosen: BaseArubaClusterClient = rest_client
    elif transport == "ssh":
        chosen = ssh_client
    else:
        chosen = AutoTransportClient(
            rest_client=rest_client,
            ssh_client=ssh_client,
            fallback_on_rest_disabled=fallback_on_rest_disabled,
        )
    if known_peers := node.get("known_peers"):
        if isinstance(known_peers, (list, tuple, set)):
            chosen.set_known_peer_ips(str(p) for p in known_peers)
    return chosen


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
        self._node_ap_ips: dict[int, set[str]] = {}
        self._node_ssids: dict[int, set[str]] = {}

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

        for idx, (node, res) in enumerate(zip(self._nodes, results)):
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
                self._node_ap_ips[idx] = {host, *(ap.ip_address for ap in res.access_points if ap.ip_address)}
                self._node_ssids[idx] = {n.profile_name for n in res.networks if n.profile_name}
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

    async def async_fetch_firmware_details(
        self,
    ) -> tuple[FirmwareImageInfo | None, FirmwareCheckResult | None]:
        """Gather firmware metadata from all Multi-AP nodes and preserve per-node details."""
        results = await asyncio.gather(
            *(client.async_fetch_firmware_details() for client in self._clients),
            return_exceptions=True,
        )
        all_classes: list[str] = []
        all_statuses: list[ApUpgradeEntry] = []
        upgrade_in_progress = False
        first_info: FirmwareImageInfo | None = None
        first_check: FirmwareCheckResult | None = None

        for idx, res in enumerate(results):
            if isinstance(res, asyncio.CancelledError):
                raise res
            if isinstance(res, BaseException) or not isinstance(res, tuple):
                continue
            fw_info, fw_check = res
            if fw_check is not None and first_check is None:
                first_check = fw_check
            if fw_info is None:
                continue
            if first_info is None:
                first_info = fw_info
            upgrade_in_progress = upgrade_in_progress or fw_info.upgrade_in_progress
            for cls in fw_info.image_classes:
                if cls not in all_classes:
                    all_classes.append(cls)
            if fw_info.ap_statuses:
                for entry in fw_info.ap_statuses:
                    all_statuses.append(
                        replace(
                            entry,
                            reboot_cause=entry.reboot_cause or fw_info.last_reboot_cause,
                            primary_version=entry.primary_version or fw_info.primary_version,
                            backup_version=entry.backup_version or fw_info.backup_version,
                        )
                    )
            else:
                node_host = str(self._nodes[idx].get("host", "")).strip()
                all_statuses.append(
                    ApUpgradeEntry(
                        mac="",
                        ip_address=node_host,
                        ap_class=fw_info.image_classes[0] if fw_info.image_classes else "",
                        reboot_cause=fw_info.last_reboot_cause,
                        primary_version=fw_info.primary_version,
                        backup_version=fw_info.backup_version,
                    )
                )

        if first_info is None:
            return (None, first_check)
        return (
            replace(
                first_info,
                image_classes=tuple(all_classes),
                upgrade_in_progress=upgrade_in_progress,
                ap_statuses=tuple(all_statuses),
            ),
            first_check,
        )

    async def async_reboot_ap(self, ap_ip: str) -> None:
        """Route single-AP reboot exclusively to the matching node client."""
        target = ap_ip.strip()
        for idx, node in enumerate(self._nodes):
            host = str(node.get("host", "")).strip()
            known_ips = self._node_ap_ips.get(idx, {host})
            if target == host or target in known_ips:
                await self._clients[idx].async_reboot_ap(target)
                return
        raise ArubaConnectionError(f"No configured AP node matches IP {target}")

    async def async_set_ssid_enabled(self, profile_name: str, enabled: bool) -> None:
        """Toggle an SSID profile across node clients advertising it."""
        matching_indices = [
            idx for idx, profiles in self._node_ssids.items() if profile_name in profiles
        ]
        if not matching_indices:
            matching_indices = list(range(len(self._clients)))
        results = await asyncio.gather(
            *(self._clients[idx].async_set_ssid_enabled(profile_name, enabled) for idx in matching_indices),
            return_exceptions=True,
        )
        for res in results:
            if isinstance(res, asyncio.CancelledError):
                raise res
        errors = [res for res in results if isinstance(res, BaseException)]
        if len(errors) == len(results):
            raise errors[0]

    async def async_upgrade_firmware(
        self,
        image_url: str,
        *,
        ap_class: str | None = None,
        auto_reboot: bool = True,
    ) -> None:
        """Upgrade standalone nodes in a Multi-AP pool."""
        results = await asyncio.gather(
            *(
                client.async_upgrade_firmware(image_url, ap_class=ap_class, auto_reboot=auto_reboot)
                for client in self._clients
            ),
            return_exceptions=True,
        )
        for res in results:
            if isinstance(res, asyncio.CancelledError):
                raise res
        errors = [res for res in results if isinstance(res, BaseException)]
        if len(errors) == len(results):
            raise errors[0]
