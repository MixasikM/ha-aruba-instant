# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Async HTTPS REST API (:4343) adapter for Aruba Instant Virtual Controllers & APs."""
from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import datetime, timezone
import ipaddress
import re
import socket
from typing import Any
from urllib.parse import urlparse

import aiohttp

from ..engine.models import ClusterSnapshot, FirmwareCheckResult, FirmwareImageInfo
from ..engine.parsers import (
    build_firmware_info,
    build_snapshot,
    parse_image_check_xml,
    parse_summary,
)
from ..ports.cluster_client import (
    ArubaAuthError,
    ArubaClientError,
    ArubaConnectionError,
    ArubaReadOnlyCliError,
    ArubaRestApiDisabledError,
    BaseArubaClusterClient,
)

_INVALID_ARG_RE = re.compile(r"[\r\n\"']")
_VC_VIP_RETRY_POLLS = 20

_REST_DISABLED_MARKER = "rest api service is not enabled"
_REST_NOT_MASTER_MARKERS = (
    "only on master",
    "only on conductor",
)
_AUTH_ERROR_MARKERS = (
    "invalid user",
    "invalid passwd",
    "invalid password",
    "authentication failed",
    "login failed",
    "unauthorized",
    "bad user",
    "permission denied",
    "invalid opcode",
    "privilege",
)


_SID_QUERY_RE = re.compile(r"(sid=)[^&\s'\"]+", re.IGNORECASE)


def _redact_sid(text: str) -> str:
    """Redact Aruba session tokens if an HTTP exception includes the request URL."""
    return _SID_QUERY_RE.sub(r"\1***", text)


def _is_ip_literal(value: str) -> bool:
    """Return True if value is a valid IPv4 or IPv6 literal."""
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _is_usable_cluster_ip(value: str) -> bool:
    """Return True if value is a unicast routable/LAN IP literal."""
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not (addr.is_unspecified or addr.is_loopback or addr.is_multicast or addr.is_link_local)


def _is_safe_cluster_redirect_ip(
    candidate: str,
    reference_host: str,
    known_peers: Iterable[str],
) -> bool:
    """Guard member->master redirects against cross-scope SSRF/unexpected targets."""
    if not _is_usable_cluster_ip(candidate):
        return False
    if candidate in set(known_peers):
        return True
    try:
        cand_addr = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    try:
        ref_addr = ipaddress.ip_address(reference_host)
    except ValueError:
        # Hostname reference: require private/reserved LAN space on redirect
        return cand_addr.is_private
    return cand_addr.is_private == ref_addr.is_private


def _is_not_master_503(raw_text: str) -> bool:
    """Return True if a 503 response states REST API is only available on Master AP."""
    lower = raw_text.lower()
    return any(marker in lower for marker in _REST_NOT_MASTER_MARKERS)


class _ArubaNotMasterError(ArubaConnectionError):
    """Internal marker raised when a 503 response came from a non-Master cluster member."""


def _extract_command_text(payload: Any) -> str:
    """Extract CLI text string from Aruba `/rest/show-cmd` JSON or text payload."""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in ("Command output", "command output", "cli output", "output", "result"):
            if key in payload and isinstance(payload[key], str):
                return payload[key]
        for val in payload.values():
            if isinstance(val, str) and ("\n" in val or "cli output:" in val.lower()):
                return val
    return ""


def _is_session_expired_json(payload: Any) -> bool:
    """Return True if Aruba REST JSON indicates an expired or invalid `sid`."""
    if not isinstance(payload, dict):
        return False
    status_code = str(payload.get("Status-code", ""))
    status = str(payload.get("Status", "")).lower()
    message = str(payload.get("message", "")).lower()
    if "invalid session" in message or "expired" in message:
        return True
    return status_code == "1" and status == "failed" and "session" in message


class ArubaRestClient(BaseArubaClusterClient):
    """Async HTTPS REST client targeting Aruba Instant `:4343` endpoints."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        username: str,
        password: str,
        port: int = 4343,
        verify_ssl: bool = False,
        timeout: float = 15.0,
    ) -> None:
        self._session = session
        self.host = host.strip()
        self.username = username
        self.password = password
        self.port = port
        self.verify_ssl = verify_ssl
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._sid: str | None = None
        self._active_host: str = self.host
        self._resolved_ip: str | None = self.host if _is_ip_literal(self.host) else None
        self._target_iap_ip: str | None = None
        self._known_peer_ips: list[str] = [self.host] if _is_usable_cluster_ip(self.host) else []
        self._unusable_vc_ips: set[str] = set()
        self._vc_vip_failures: dict[str, int] = {}
        self._vc_vip_cooldown: dict[str, int] = {}
        self._lock = asyncio.Lock()

    @property
    def active_host(self) -> str:
        """Return currently targeted REST host (VIP or elected Master AP IP)."""
        return self._active_host

    @property
    def _base_url(self) -> str:
        target = self._active_host or self.host
        host_part = f"[{target}]" if ":" in target and not target.startswith("[") else target
        return f"https://{host_part}:{self.port}"

    def set_known_peer_ips(self, peer_ips: Iterable[str]) -> None:
        """Merge known cluster AP IPs without duplicating or discarding configured host."""
        seen = set(self._known_peer_ips)
        for raw_ip in peer_ips:
            ip_str = str(raw_ip or "").strip()
            if ip_str and _is_usable_cluster_ip(ip_str) and ip_str not in seen:
                self._known_peer_ips.append(ip_str)
                seen.add(ip_str)

    def get_known_peer_ips(self) -> list[str]:
        """Return known cluster AP IPs discovered so far."""
        return list(self._known_peer_ips)

    def get_vc_vip_failure_count(self, vc_ip: str) -> int:
        """Return consecutive connection failure count for an advertised VC VIP."""
        return self._vc_vip_failures.get(vc_ip.strip(), 0)

    async def _resolve_iap_ip(self) -> str:
        """Resolve IPv4-preferred host IP for `/rest/show-cmd?iap_ip_addr=`."""
        if self._target_iap_ip:
            return self._target_iap_ip
        if self._resolved_ip:
            return self._resolved_ip
        target = self._active_host or self.host
        if _is_ip_literal(target):
            self._resolved_ip = target
            return target
        try:
            loop = asyncio.get_running_loop()
            infos = await loop.getaddrinfo(target, self.port, type=socket.SOCK_STREAM)
            ipv4 = next((info[4][0] for info in infos if info[0] == socket.AF_INET and info[4]), None)
            addr = ipv4 or next((info[4][0] for info in infos if info[4]), None)
            if addr:
                self._resolved_ip = str(addr)
                return self._resolved_ip
        except OSError:
            pass
        return target

    def _clear_session_state(self) -> None:
        """Reset cached `sid` and conductor IP hints on session expiry or connection failure."""
        self._sid = None
        self._target_iap_ip = None

    def _switch_active_host(self, new_host: str) -> None:
        """Point subsequent REST calls to a newly elected Master AP IP."""
        clean = new_host.strip()
        if not clean:
            return
        self._active_host = clean
        self._resolved_ip = clean if _is_ip_literal(clean) else None
        self._target_iap_ip = clean if _is_ip_literal(clean) else None
        self._sid = None
        self.set_known_peer_ips([clean])

    async def _probe_redirect_master_ip(self, peer_host: str) -> str | None:
        """Check HTTP/HTTPS WebUI redirect on a cluster AP to find the current Master IP."""
        host_part = f"[{peer_host}]" if ":" in peer_host and not peer_host.startswith("[") else peer_host
        probe_timeout = aiohttp.ClientTimeout(total=3.0)
        urls = [f"https://{host_part}/", f"http://{host_part}/"]
        if self.port != 4343:
            urls.insert(0, f"https://{host_part}:{self.port}/")
        ref_host = self._resolved_ip or self.host
        for url in urls:
            try:
                async with self._session.get(
                    url,
                    allow_redirects=False,
                    timeout=probe_timeout,
                ) as resp:
                    if resp.status not in (301, 302, 303, 307, 308):
                        continue
                    location = (resp.headers.get("Location") or "").strip()
                    if not location:
                        continue
                    parsed = urlparse(location)
                    cand = (parsed.hostname or "").strip()
                    if (
                        cand
                        and cand != peer_host
                        and _is_safe_cluster_redirect_ip(cand, ref_host, self._known_peer_ips)
                    ):
                        return cand
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                continue
        return None

    def _candidate_peers(self, *, exclude: str | None = None) -> list[str]:
        """Build de-duplicated candidate endpoints, deprioritizing unusable VC VIPs."""
        primary_candidates: list[str] = []
        deferred_candidates: list[str] = []
        seen: set[str] = set()
        for raw in (self._active_host, self.host, *self._known_peer_ips):
            clean = (raw or "").strip()
            if not clean or clean == exclude or clean in seen:
                continue
            seen.add(clean)
            if clean in self._unusable_vc_ips:
                deferred_candidates.append(clean)
            else:
                primary_candidates.append(clean)
        return [*primary_candidates, *deferred_candidates]

    async def _best_effort_logout(self, target_host: str, sid: str) -> None:
        """Best-effort `/rest/logout` on a specific host to free its REST session slot."""
        host_part = f"[{target_host}]" if ":" in target_host and not target_host.startswith("[") else target_host
        url = f"https://{host_part}:{self.port}/rest/logout"
        try:
            async with self._session.post(
                url,
                json={"sid": sid},
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=3.0),
            ):
                pass
        except Exception:
            pass

    async def _probe_and_promote_vc_vip(
        self,
        vc_ip: str,
        *,
        conductor_ip: str | None = None,
    ) -> None:
        """Non-destructively probe an advertised VC VIP before closing the active session."""
        old_host = self._active_host
        old_resolved = self._resolved_ip
        old_iap_ip = self._target_iap_ip
        old_sid = self._sid
        try:
            new_sid = await self._login_once(vc_ip, timeout=3.0)
        except ArubaAuthError:
            raise
        except ArubaClientError:
            self._active_host = old_host
            self._resolved_ip = old_resolved
            self._target_iap_ip = old_iap_ip
            self._sid = old_sid
            self._unusable_vc_ips.add(vc_ip)
            self._vc_vip_failures[vc_ip] = self._vc_vip_failures.get(vc_ip, 0) + 1
            self._vc_vip_cooldown[vc_ip] = _VC_VIP_RETRY_POLLS
            return

        if conductor_ip and _is_ip_literal(conductor_ip):
            self._target_iap_ip = conductor_ip
        if old_sid and old_host and old_host != vc_ip:
            await self._best_effort_logout(old_host, old_sid)
        self._sid = new_sid

    async def _login_once(
        self,
        target_host: str,
        *,
        timeout: float | None = None,
    ) -> str:
        """Authenticate against `/rest/login` on a specific target host without failover."""
        host_part = f"[{target_host}]" if ":" in target_host and not target_host.startswith("[") else target_host
        url = f"https://{host_part}:{self.port}/rest/login"
        body_json = {"user": self.username, "passwd": self.password}
        req_timeout = aiohttp.ClientTimeout(total=timeout) if timeout is not None else self._timeout
        try:
            async with self._session.post(
                url,
                json=body_json,
                allow_redirects=False,
                timeout=req_timeout,
            ) as resp:
                raw_text = await resp.text()
                if resp.status == 503:
                    if _REST_DISABLED_MARKER in raw_text.lower():
                        raise ArubaRestApiDisabledError(
                            f"REST API Service is not enabled on {target_host}",
                            host=target_host,
                        )
                    if _is_not_master_503(raw_text):
                        raise _ArubaNotMasterError(
                            f"HTTP 503 on {target_host}: REST API service is available only on Master IAP",
                            host=target_host,
                        )
                    raise ArubaConnectionError(
                        f"HTTP 503 on {target_host}: {raw_text[:200]}",
                        host=target_host,
                    )
                if resp.status in (401, 403):
                    raise ArubaAuthError(
                        f"Authentication rejected by {target_host} (HTTP {resp.status})",
                        host=target_host,
                    )
                if resp.status >= 400:
                    raise ArubaConnectionError(
                        f"Unexpected HTTP {resp.status} from {target_host}: {raw_text[:200]}",
                        host=target_host,
                    )
                try:
                    data = await resp.json(content_type=None)
                except Exception as exc:
                    raise ArubaConnectionError(
                        f"Invalid JSON from {target_host} login: {exc}",
                        host=target_host,
                    ) from exc
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            raise ArubaConnectionError(
                f"REST connection error to {target_host}:{self.port}: {_redact_sid(str(exc))}",
                host=target_host,
            ) from exc

        if not isinstance(data, dict):
            raise ArubaConnectionError(
                f"Unexpected login response type from {target_host}",
                host=target_host,
            )

        sid = str(data.get("sid") or data.get("uid-aruba") or "").strip()
        status_str = str(data.get("Status", "")).lower()
        raw_code = data.get("Status-code")
        status_code_ok = raw_code in (None, 0, "0")
        msg = str(data.get("message") or data.get("Reason") or "")
        lower_msg = msg.lower()

        if status_str == "failed" or not status_code_ok or not sid:
            if any(marker in lower_msg for marker in _AUTH_ERROR_MARKERS) or (
                not msg and status_str == "failed"
            ):
                raise ArubaAuthError(
                    f"REST login failed on {target_host}: {msg or 'Authentication failed'}",
                    host=target_host,
                )
            raise ArubaConnectionError(
                f"REST login unavailable on {target_host}: {msg or 'Missing session ID'}",
                host=target_host,
            )

        self._active_host = target_host
        self._resolved_ip = target_host if _is_ip_literal(target_host) else self._resolved_ip
        self._sid = sid
        self._unusable_vc_ips.discard(target_host)
        self._vc_vip_failures.pop(target_host, None)
        self._vc_vip_cooldown.pop(target_host, None)
        return sid

    async def _login(self, *, skip_primary: bool = False) -> str:
        """Authenticate against `/rest/login`, auto-discovering Master AP via peers on failover."""
        primary = self._active_host or self.host
        first_exc: ArubaConnectionError | None = None
        if not skip_primary:
            try:
                return await self._login_once(primary)
            except ArubaConnectionError as exc:
                first_exc = exc

        not_master = first_exc is not None and (
            isinstance(first_exc, _ArubaNotMasterError)
            or _is_not_master_503(str(first_exc))
        )
        if not not_master and _is_usable_cluster_ip(primary):
            self._unusable_vc_ips.add(primary)
            self._vc_vip_failures[primary] = self._vc_vip_failures.get(primary, 0) + 1
            self._vc_vip_cooldown[primary] = _VC_VIP_RETRY_POLLS
        # If member AP refused REST login with "only on Master IAP", check its :443 redirect first
        redirect_order = [primary] if not_master else []
        for peer in self._candidate_peers(exclude=primary):
            redirect_order.append(peer)

        tried_login: set[str] = {primary}
        last_exc: ArubaConnectionError = first_exc or ArubaConnectionError(
            f"Connection failed to {primary}", host=self.host
        )

        for peer in redirect_order:
            master_ip = await self._probe_redirect_master_ip(peer)
            for target in [ip for ip in (master_ip, peer) if ip and ip not in tried_login]:
                tried_login.add(target)
                try:
                    sid = await self._login_once(target)
                    self._switch_active_host(target)
                    self._sid = sid
                    return sid
                except (ArubaAuthError, ArubaRestApiDisabledError):
                    raise
                except ArubaConnectionError as exc:
                    last_exc = exc

        self._resolved_ip = self.host if _is_ip_literal(self.host) else None
        raise last_exc

    async def _show_cmd(self, cmd: str, retry_on_expired: bool = True) -> str:
        """Execute a read-only CLI command via `/rest/show-cmd`."""
        if not self._sid:
            await self._login()

        iap_ip = await self._resolve_iap_ip()
        url = f"{self._base_url}/rest/show-cmd"
        params = {
            "iap_ip_addr": iap_ip,
            "cmd": cmd,
            "sid": self._sid or "",
        }
        try:
            async with self._session.get(
                url,
                params=params,
                allow_redirects=False,
                timeout=self._timeout,
            ) as resp:
                raw_text = await resp.text()
                if resp.status == 503:
                    if _REST_DISABLED_MARKER in raw_text.lower():
                        raise ArubaRestApiDisabledError(
                            f"REST API Service is not enabled on {self.host}",
                            host=self.host,
                        )
                    if _is_not_master_503(raw_text) and retry_on_expired:
                        self._clear_session_state()
                        if self._known_peer_ips or _is_usable_cluster_ip(self._active_host):
                            await self._login()
                            return await self._show_cmd(cmd, retry_on_expired=False)
                    raise ArubaConnectionError(
                        f"HTTP 503 on {self._active_host}: {raw_text[:200]}",
                        host=self.host,
                    )
                if resp.status in (401, 403):
                    self._clear_session_state()
                    if retry_on_expired:
                        await self._login()
                        return await self._show_cmd(cmd, retry_on_expired=False)
                    raise ArubaAuthError(
                        f"REST session rejected on {self.host} (HTTP {resp.status})",
                        host=self.host,
                    )
                if resp.status >= 400:
                    raise ArubaConnectionError(
                        f"HTTP {resp.status} running '{cmd}' on {self.host}",
                        host=self.host,
                    )
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    if "invalid session" in raw_text.lower() and retry_on_expired:
                        self._clear_session_state()
                        await self._login()
                        return await self._show_cmd(cmd, retry_on_expired=False)
                    return raw_text
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            self._clear_session_state()
            self._resolved_ip = self._active_host if _is_ip_literal(self._active_host) else None
            if retry_on_expired and self._known_peer_ips:
                try:
                    await self._login(skip_primary=True)
                    return await self._show_cmd(cmd, retry_on_expired=False)
                except (ArubaAuthError, ArubaRestApiDisabledError):
                    raise
                except ArubaClientError:
                    pass
            raise ArubaConnectionError(
                f"REST request error on {self._active_host}: {_redact_sid(str(exc))}",
                host=self.host,
            ) from exc

        if _is_session_expired_json(data):
            self._clear_session_state()
            if retry_on_expired:
                await self._login()
                return await self._show_cmd(cmd, retry_on_expired=False)
            raise ArubaAuthError(f"REST session expired on {self.host}", host=self.host)

        if isinstance(data, dict) and str(data.get("Status", "")).lower() == "failed":
            msg = str(data.get("message") or data.get("Reason") or "Command failed")
            lower_msg = msg.lower()
            if "invalid opcode" in lower_msg or "privilege" in lower_msg:
                raise ArubaAuthError(
                    f"Insufficient REST privileges on {self.host}: {msg}",
                    host=self.host,
                )
            # If floating VC VIP rejected iap_ip_addr, retry once querying /rest/aps for conductor IP
            if retry_on_expired and self._target_iap_ip is None and "iap" in lower_msg:
                if await self._discover_conductor_ip():
                    return await self._show_cmd(cmd, retry_on_expired=False)
            raise ArubaConnectionError(
                f"REST command '{cmd}' failed on {self.host}: {msg}", host=self.host
            )

        if isinstance(data, dict):
            return _extract_command_text(data)
        return raw_text

    async def _discover_conductor_ip(self) -> bool:
        """Fallback lookup via `/rest/aps` if VC VIP differs from Conductor member IP."""
        if not self._sid:
            return False
        try:
            async with self._session.get(
                f"{self._base_url}/rest/aps",
                params={"sid": self._sid},
                allow_redirects=False,
                timeout=self._timeout,
            ) as resp:
                if resp.status >= 400:
                    return False
                data = await resp.json(content_type=None)
        except Exception:
            return False

        if isinstance(data, dict):
            aps = data.get("aps") or data.get("Access Points") or []
            if isinstance(aps, list):
                for item in aps:
                    if isinstance(item, dict) and item.get("ip_address"):
                        self._target_iap_ip = str(item["ip_address"]).strip()
                        return True
        return False

    async def async_fetch_snapshot(self) -> ClusterSnapshot:
        """Fetch summary, networks, APs, and clients sequentially under session lock."""
        async with self._lock:
            if not self._sid:
                await self._login()
            summary_raw = await self._show_cmd("show summary")
            summary = parse_summary(summary_raw)
            if summary.conductor_ip and _is_ip_literal(summary.conductor_ip):
                self._target_iap_ip = summary.conductor_ip
            networks_raw = await self._show_cmd("show network")
            aps_raw = await self._show_cmd("show aps")
            clients_raw = await self._show_cmd("show clients")
            snapshot = build_snapshot(
                summary_raw=summary_raw,
                networks_raw=networks_raw,
                aps_raw=aps_raw,
                clients_raw=clients_raw,
                fetched_at=datetime.now(timezone.utc),
            )
            discovered_ips: list[str] = [
                ap.ip_address for ap in snapshot.access_points if ap.ip_address
            ]
            if snapshot.summary.conductor_ip:
                discovered_ips.append(snapshot.summary.conductor_ip)
            if snapshot.summary.vc_ip:
                discovered_ips.append(snapshot.summary.vc_ip)
            self.set_known_peer_ips(discovered_ips)
            # Prefer VC VIP if reachable; fall back cleanly while preserving active session
            pinned_hostname = self.verify_ssl and not _is_ip_literal(self._active_host)
            dns_to_vc_vip = not _is_ip_literal(self._active_host) and self._resolved_ip == snapshot.summary.vc_ip
            vc_ip = snapshot.summary.vc_ip
            conductor_ip = snapshot.summary.conductor_ip
            if (
                vc_ip
                and _is_usable_cluster_ip(vc_ip)
                and not pinned_hostname
                and not dns_to_vc_vip
            ):
                if self._active_host == vc_ip:
                    self._vc_vip_failures.pop(vc_ip, None)
                    self._vc_vip_cooldown.pop(vc_ip, None)
                elif vc_ip in self._unusable_vc_ips:
                    remaining = self._vc_vip_cooldown.get(vc_ip, _VC_VIP_RETRY_POLLS) - 1
                    if remaining <= 0:
                        self._unusable_vc_ips.discard(vc_ip)
                        self._vc_vip_cooldown.pop(vc_ip, None)
                        await self._probe_and_promote_vc_vip(vc_ip, conductor_ip=conductor_ip)
                    else:
                        self._vc_vip_cooldown[vc_ip] = remaining
                else:
                    await self._probe_and_promote_vc_vip(vc_ip, conductor_ip=conductor_ip)
            elif (
                not pinned_hostname
                and not dns_to_vc_vip
                and conductor_ip
                and _is_usable_cluster_ip(conductor_ip)
                and self._active_host != conductor_ip
            ):
                self._active_host = conductor_ip
                self._resolved_ip = conductor_ip
            return snapshot

    async def async_close(self) -> None:
        """Logout and invalidate cached `sid` to free AP REST session slot."""
        async with self._lock:
            sid = self._sid
            host = self._active_host or self.host
            self._clear_session_state()
            if sid:
                await self._best_effort_logout(host, sid)

    async def _post_rest_action(
        self,
        endpoint: str,
        payload: dict[str, Any],
        retry_on_expired: bool = True,
    ) -> dict[str, Any]:
        """Execute a mutating `/rest/<endpoint>` POST call with `sid` expiry retry."""
        if not self._sid:
            await self._login()

        url = f"{self._base_url}/rest/{endpoint.lstrip('/')}"
        try:
            async with self._session.post(
                url,
                params={"sid": self._sid or ""},
                json=payload,
                allow_redirects=False,
                timeout=self._timeout,
            ) as resp:
                raw_text = await resp.text()
                if resp.status == 503:
                    if _REST_DISABLED_MARKER in raw_text.lower():
                        raise ArubaRestApiDisabledError(
                            f"REST API Service is not enabled on {self.host}",
                            host=self.host,
                        )
                    if _is_not_master_503(raw_text) and retry_on_expired:
                        self._clear_session_state()
                        if self._known_peer_ips or _is_usable_cluster_ip(self._active_host):
                            await self._login()
                            return await self._post_rest_action(
                                endpoint, payload, retry_on_expired=False
                            )
                    raise ArubaConnectionError(
                        f"HTTP 503 on {self._active_host}: {raw_text[:200]}",
                        host=self.host,
                    )
                if resp.status in (401, 403):
                    self._clear_session_state()
                    if retry_on_expired:
                        await self._login()
                        return await self._post_rest_action(endpoint, payload, retry_on_expired=False)
                    raise ArubaAuthError(
                        f"REST session rejected on {self.host} (HTTP {resp.status})",
                        host=self.host,
                    )
                if resp.status >= 400:
                    raise ArubaConnectionError(
                        f"HTTP {resp.status} calling '/rest/{endpoint}' on {self.host}: {raw_text[:200]}",
                        host=self.host,
                    )
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    if "invalid session" in raw_text.lower() and retry_on_expired:
                        self._clear_session_state()
                        await self._login()
                        return await self._post_rest_action(endpoint, payload, retry_on_expired=False)
                    return {}
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            self._clear_session_state()
            if retry_on_expired and self._known_peer_ips:
                try:
                    await self._login(skip_primary=True)
                    return await self._post_rest_action(endpoint, payload, retry_on_expired=False)
                except (ArubaAuthError, ArubaRestApiDisabledError, ArubaReadOnlyCliError):
                    raise
                except ArubaClientError:
                    pass
            raise ArubaConnectionError(
                f"REST request '/rest/{endpoint}' failed on {self._active_host}: {_redact_sid(str(exc))}",
                host=self.host,
            ) from exc

        if _is_session_expired_json(data):
            self._clear_session_state()
            if retry_on_expired:
                await self._login()
                return await self._post_rest_action(endpoint, payload, retry_on_expired=False)
            raise ArubaAuthError(f"REST session expired on {self.host}", host=self.host)

        if isinstance(data, dict):
            raw_code = data.get("Status-code")
            status_str = str(data.get("Status", "")).lower()
            if status_str == "failed" or raw_code not in (None, 0, "0"):
                msg = str(data.get("message") or data.get("Reason") or "Command failed")
                lower_msg = msg.lower()
                if "invalid opcode" in lower_msg or "privilege" in lower_msg or "read-only" in lower_msg:
                    raise ArubaReadOnlyCliError(
                        f"Insufficient REST privileges on {self.host}: {msg}",
                        host=self.host,
                    )
                raise ArubaConnectionError(
                    f"REST '/rest/{endpoint}' failed on {self.host}: {msg}",
                    host=self.host,
                )
            return data
        return {}

    async def _fetch_image_check_status(
        self,
        *,
        retry_in_progress: bool = True,
        retry_on_expired: bool = True,
    ) -> FirmwareCheckResult | None:
        """Query `swarm.cgi?opcode=image-check-status` with per-request `Cookie` header."""
        if not self._sid:
            await self._login()
        sid = self._sid or ""
        url = f"{self._base_url}/swarm.cgi"
        params = {
            "opcode": "image-check-status",
            "ip": "127.0.0.1",
            "refresh": "false",
            "sid": sid,
        }
        headers = {"Cookie": f"sid={sid}; userType=Admin"}
        try:
            async with self._session.get(
                url,
                params=params,
                headers=headers,
                allow_redirects=False,
                timeout=self._timeout,
            ) as resp:
                if resp.status in (401, 403):
                    self._clear_session_state()
                    if retry_on_expired:
                        await self._login()
                        return await self._fetch_image_check_status(
                            retry_in_progress=retry_in_progress,
                            retry_on_expired=False,
                        )
                    return None
                if resp.status >= 400:
                    return None
                raw_xml = await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            return None

        parsed = parse_image_check_xml(raw_xml)
        if parsed.server_state and parsed.server_state.lower() == "in_progress" and retry_in_progress:
            await asyncio.sleep(1.0)
            return await self._fetch_image_check_status(
                retry_in_progress=False,
                retry_on_expired=retry_on_expired,
            )
        return parsed

    async def _optional_show_cmd(self, cmd: str) -> str:
        """Run an auxiliary read-only show command, degrading cleanly on unsupported CLIs."""
        try:
            return await self._show_cmd(cmd)
        except (ArubaAuthError, ArubaRestApiDisabledError):
            raise
        except ArubaClientError:
            return ""

    async def async_fetch_firmware_details(
        self,
    ) -> tuple[FirmwareImageInfo | None, FirmwareCheckResult | None]:
        """Fetch partition firmware, reboot cause, upgrade status, and image-check XML."""
        async with self._lock:
            if not self._sid:
                await self._login()
            img_raw = await self._optional_show_cmd("show image version")
            ver_raw = await self._optional_show_cmd("show version")
            upg_raw = await self._optional_show_cmd("show upgrade info")
            allowed_raw = await self._optional_show_cmd("show allowed-aps")
            fw_info = build_firmware_info(img_raw, ver_raw, upg_raw, allowed_raw)
            fw_check = await self._fetch_image_check_status()
            return fw_info, fw_check

    async def async_reboot_ap(self, ap_ip: str) -> None:
        """Reboot a single AP via `POST /rest/reboot`."""
        target = ap_ip.strip()
        if not target or _INVALID_ARG_RE.search(target):
            raise ArubaConnectionError(f"Invalid target AP IP for reboot: {ap_ip!r}", host=self.host)
        async with self._lock:
            await self._post_rest_action(
                "reboot",
                {"iap_ip_addr": target, "reboot-info": {"target": "single"}},
            )

    async def async_set_ssid_enabled(self, profile_name: str, enabled: bool) -> None:
        """Enable or disable a WLAN SSID profile via `POST /rest/ssid`."""
        profile = profile_name.strip()
        if not profile or _INVALID_ARG_RE.search(profile):
            raise ArubaConnectionError(f"Invalid SSID profile name: {profile_name!r}", host=self.host)
        ssid_payload: dict[str, Any] = {
            "action": "create",
            "ssid-profile": profile,
            "enable" if enabled else "disable": "yes",
        }
        async with self._lock:
            target_ip = self._target_iap_ip or self._resolved_ip or self._active_host
            await self._post_rest_action(
                "ssid",
                {
                    "iap_ip_addr": target_ip,
                    "ssid-profile": ssid_payload,
                },
            )

    async def async_upgrade_firmware(
        self,
        image_url: str,
        *,
        ap_class: str | None = None,
        auto_reboot: bool = True,
    ) -> None:
        """Trigger cluster firmware upgrade from explicit HTTP/HTTPS image URL via `/rest/os-upgrade`."""
        url_clean = image_url.strip()
        cls_clean = (ap_class or "").strip()
        if not url_clean.startswith(("http://", "https://")) or _INVALID_ARG_RE.search(url_clean):
            raise ArubaConnectionError("Firmware image URL must start with http:// or https://", host=self.host)
        if not cls_clean or _INVALID_ARG_RE.search(cls_clean):
            raise ArubaConnectionError("AP image class is required for REST firmware upgrade", host=self.host)
        async with self._lock:
            await self._post_rest_action(
                "os-upgrade",
                {
                    "upgrade-info": {
                        "auto-reboot": "yes" if auto_reboot else "no",
                        f"{cls_clean}-url": url_clean,
                    }
                },
            )
