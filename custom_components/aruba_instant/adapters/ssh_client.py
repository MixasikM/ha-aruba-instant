# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Async SSH (`asyncssh`) interactive PTY adapter for Aruba Instant & AOS APs."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import re
from typing import Any

import asyncssh

from ..engine.models import ClusterSnapshot
from ..engine.parsers import build_snapshot
from ..ports.cluster_client import (
    ArubaAuthError,
    ArubaConnectionError,
    ArubaReadOnlyCliError,
    BaseArubaClusterClient,
)

# Line-anchored prompt matching typical Aruba CLI prompts (e.g., "MainCluster# ", "AP(config)# ", "AP> ")
_PROMPT_RE = re.compile(r"(?:^|[\r\n])[^\s#>][^\r\n#>]{0,63}(?:\([a-zA-Z0-9_-]+\))?\s*[#>]\s*$")
_CLI_ERROR_RE = re.compile(
    r"(?:invalid input|incomplete command|cannot|not allowed|read-only|error:|permission denied)",
    re.IGNORECASE,
)

_LEGACY_KEX_ALGS = (
    "curve25519-sha256",
    "curve25519-sha256@libssh.org",
    "ecdh-sha2-nistp256",
    "ecdh-sha2-nistp384",
    "diffie-hellman-group-exchange-sha256",
    "diffie-hellman-group14-sha256",
    "diffie-hellman-group14-sha1",
    "diffie-hellman-group1-sha1",
)
_LEGACY_HOST_KEY_ALGS = (
    "ssh-ed25519",
    "ecdsa-sha2-nistp256",
    "rsa-sha2-512",
    "rsa-sha2-256",
    "ssh-rsa",
)


async def _read_until_prompt(
    process: Any,
    timeout: float,
    host: str,
) -> str:
    """Read stdout from interactive SSH PTY until a line-anchored Aruba CLI prompt appears."""
    chunks: list[str] = []
    tail_window = ""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise ArubaConnectionError(
                f"Timed out waiting for Aruba CLI prompt on {host}",
                host=host,
            )
        try:
            chunk = await asyncio.wait_for(process.stdout.read(4096), timeout=remaining)
        except asyncio.TimeoutError as exc:
            raise ArubaConnectionError(
                f"SSH read timeout waiting for prompt on {host}",
                host=host,
            ) from exc
        except UnicodeDecodeError as exc:
            raise ArubaConnectionError(
                f"SSH encoding error reading from {host}: {exc}",
                host=host,
            ) from exc

        if not chunk:
            if _PROMPT_RE.search(tail_window):
                return "".join(chunks)
            raise ArubaConnectionError(
                f"SSH PTY stream closed unexpectedly on {host}",
                host=host,
            )

        chunks.append(chunk)
        # Keep only last 256 chars for O(1) prompt detection on long command outputs
        tail_window = (tail_window + chunk)[-256:]
        if _PROMPT_RE.search(tail_window):
            return "".join(chunks)


async def _safe_close_ssh(conn: Any, process: Any) -> None:
    """Close SSH process and transport safely with bounded wait timeout."""
    if process is not None:
        try:
            process.close()
        except Exception:
            pass
    if conn is not None:
        try:
            conn.close()
            await asyncio.wait_for(conn.wait_closed(), timeout=3.0)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass


class ArubaSshClient(BaseArubaClusterClient):
    """Async SSH (`asyncssh`) wide-PTY client for Aruba Instant / AOS APs."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 22,
        legacy_ssh: bool = False,
        timeout: float = 20.0,
    ) -> None:
        self.host = host.strip()
        self.username = username
        self.password = password
        self.port = port
        self.legacy_ssh = legacy_ssh
        self.timeout = timeout
        self._lock = asyncio.Lock()

    async def _connect(self) -> Any:
        """Open an `asyncssh` connection with bounded timeout, safe decoding, and legacy KEX fallback."""
        kwargs: dict[str, Any] = {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": self.password,
            "known_hosts": None,
            "config": (),
            "encoding": "utf-8",
            "errors": "replace",
            "login_timeout": self.timeout,
            "connect_timeout": self.timeout,
        }
        if self.legacy_ssh:
            kwargs["kex_algs"] = _LEGACY_KEX_ALGS
            kwargs["server_host_key_algs"] = _LEGACY_HOST_KEY_ALGS

        async def _dial(dial_kwargs: dict[str, Any]) -> Any:
            return await asyncio.wait_for(
                asyncssh.connect(**dial_kwargs),
                timeout=self.timeout + 2.0,
            )

        try:
            return await _dial(kwargs)
        except asyncssh.PermissionDenied as exc:
            raise ArubaAuthError(
                f"SSH authentication failed for {self.host}: {exc}",
                host=self.host,
            ) from exc
        except asyncssh.KeyExchangeFailed as exc:
            if not self.legacy_ssh:
                retry_kwargs = dict(kwargs)
                retry_kwargs["kex_algs"] = _LEGACY_KEX_ALGS
                retry_kwargs["server_host_key_algs"] = _LEGACY_HOST_KEY_ALGS
                try:
                    return await _dial(retry_kwargs)
                except asyncssh.PermissionDenied as auth_exc:
                    raise ArubaAuthError(
                        f"SSH authentication failed for {self.host}: {auth_exc}",
                        host=self.host,
                    ) from auth_exc
                except (asyncssh.Error, OSError, asyncio.TimeoutError) as retry_exc:
                    raise ArubaConnectionError(
                        f"SSH legacy KEX connection failed for {self.host}: {retry_exc}",
                        host=self.host,
                    ) from retry_exc
            raise ArubaConnectionError(
                f"SSH key exchange failed for {self.host}: {exc}",
                host=self.host,
            ) from exc
        except (asyncssh.Error, OSError, asyncio.TimeoutError, UnicodeDecodeError) as exc:
            raise ArubaConnectionError(
                f"SSH connection error to {self.host}:{self.port}: {exc}",
                host=self.host,
            ) from exc

    async def _run_pty_commands(self, commands: list[str]) -> dict[str, str]:
        """Open wide interactive PTY, disable paging, and run a command sequence."""
        conn = await self._connect()
        process: Any = None
        outputs: dict[str, str] = {}
        try:
            process = await asyncio.wait_for(
                conn.create_process(
                    term_type="vt100",
                    term_size=(512, 1000),
                ),
                timeout=self.timeout,
            )
            await _read_until_prompt(process, self.timeout, self.host)

            process.stdin.write("no paging\n")
            await _read_until_prompt(process, self.timeout, self.host)

            for cmd in commands:
                process.stdin.write(f"{cmd}\n")
                outputs[cmd] = await _read_until_prompt(process, self.timeout, self.host)

            process.stdin.write("exit\n")
            return outputs
        finally:
            await _safe_close_ssh(conn, process)

    async def async_fetch_snapshot(self) -> ClusterSnapshot:
        """Fetch read-only show commands over an interactive SSH PTY."""
        async with self._lock:
            outputs = await self._run_pty_commands(
                ["show summary", "show network", "show aps", "show clients"]
            )
            return build_snapshot(
                summary_raw=outputs["show summary"],
                networks_raw=outputs["show network"],
                aps_raw=outputs["show aps"],
                clients_raw=outputs["show clients"],
                fetched_at=datetime.now(timezone.utc),
            )

    async def async_enable_rest_api(self) -> None:
        """Explicitly enable `allow-rest-api` over SSH ONLY after user confirmation."""
        async with self._lock:
            conn = await self._connect()
            process: Any = None
            try:
                process = await asyncio.wait_for(
                    conn.create_process(
                        term_type="vt100",
                        term_size=(512, 1000),
                    ),
                    timeout=self.timeout,
                )
                banner = await _read_until_prompt(process, self.timeout, self.host)
                if banner.rstrip().endswith(">"):
                    raise ArubaReadOnlyCliError(
                        f"Account on {self.host} lacks privileged '#' enable mode",
                        host=self.host,
                    )

                for cmd in (
                    "configure terminal",
                    "allow-rest-api",
                    "end",
                    "commit apply",
                    "write memory",
                ):
                    process.stdin.write(f"{cmd}\n")
                    out = await _read_until_prompt(process, self.timeout, self.host)
                    if _CLI_ERROR_RE.search(out):
                        raise ArubaReadOnlyCliError(
                            f"CLI rejected '{cmd}' on {self.host}: {out.strip()[:200]}",
                            host=self.host,
                        )
                process.stdin.write("exit\n")
            finally:
                await _safe_close_ssh(conn, process)

    async def async_close(self) -> None:
        """No persistent socket held between polls."""
        return None
