# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Async HTTPS REST API (:4343) adapter for Aruba Instant Virtual Controllers & APs."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import ipaddress
import re
import socket
from typing import Any

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

_REST_DISABLED_MARKER = "rest api service is not enabled"
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
        self._resolved_ip: str | None = self.host if _is_ip_literal(self.host) else None
        self._target_iap_ip: str | None = None
        self._lock = asyncio.Lock()

    @property
    def _base_url(self) -> str:
        host_part = f"[{self.host}]" if ":" in self.host and not self.host.startswith("[") else self.host
        return f"https://{host_part}:{self.port}"

    async def _resolve_iap_ip(self) -> str:
        """Resolve IPv4-preferred host IP for `/rest/show-cmd?iap_ip_addr=`."""
        if self._target_iap_ip:
            return self._target_iap_ip
        if self._resolved_ip:
            return self._resolved_ip
        if _is_ip_literal(self.host):
            self._resolved_ip = self.host
            return self.host
        try:
            loop = asyncio.get_running_loop()
            infos = await loop.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
            ipv4 = next((info[4][0] for info in infos if info[0] == socket.AF_INET and info[4]), None)
            addr = ipv4 or next((info[4][0] for info in infos if info[4]), None)
            if addr:
                self._resolved_ip = str(addr)
                return self._resolved_ip
        except OSError:
            pass
        return self.host

    def _clear_session_state(self) -> None:
        """Reset cached `sid` and conductor IP hints on session expiry or connection failure."""
        self._sid = None
        self._target_iap_ip = None

    async def _login(self) -> str:
        """Authenticate against `/rest/login` and cache `sid`."""
        url = f"{self._base_url}/rest/login"
        body_json = {"user": self.username, "passwd": self.password}
        try:
            async with self._session.post(
                url,
                json=body_json,
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
                    raise ArubaConnectionError(
                        f"HTTP 503 on {self.host}: {raw_text[:200]}",
                        host=self.host,
                    )
                if resp.status in (401, 403):
                    raise ArubaAuthError(
                        f"Authentication rejected by {self.host} (HTTP {resp.status})",
                        host=self.host,
                    )
                if resp.status >= 400:
                    raise ArubaConnectionError(
                        f"Unexpected HTTP {resp.status} from {self.host}: {raw_text[:200]}",
                        host=self.host,
                    )
                try:
                    data = await resp.json(content_type=None)
                except Exception as exc:
                    raise ArubaConnectionError(
                        f"Invalid JSON from {self.host} login: {exc}",
                        host=self.host,
                    ) from exc
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            self._resolved_ip = self.host if _is_ip_literal(self.host) else None
            raise ArubaConnectionError(
                f"REST connection error to {self.host}:{self.port}: {_redact_sid(str(exc))}",
                host=self.host,
            ) from exc

        if not isinstance(data, dict):
            raise ArubaConnectionError(
                f"Unexpected login response type from {self.host}",
                host=self.host,
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
                    f"REST login failed on {self.host}: {msg or 'Authentication failed'}",
                    host=self.host,
                )
            raise ArubaConnectionError(
                f"REST login unavailable on {self.host}: {msg or 'Missing session ID'}",
                host=self.host,
            )

        self._sid = sid
        return sid

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
                    raise ArubaConnectionError(
                        f"HTTP 503 on {self.host}: {raw_text[:200]}",
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
            self._resolved_ip = self.host if _is_ip_literal(self.host) else None
            raise ArubaConnectionError(
                f"REST request error on {self.host}: {_redact_sid(str(exc))}",
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
            return build_snapshot(
                summary_raw=summary_raw,
                networks_raw=networks_raw,
                aps_raw=aps_raw,
                clients_raw=clients_raw,
                fetched_at=datetime.now(timezone.utc),
            )

    async def async_close(self) -> None:
        """Logout and invalidate cached `sid` to free AP REST session slot."""
        async with self._lock:
            sid = self._sid
            self._clear_session_state()
            if not sid:
                return
            url = f"{self._base_url}/rest/logout"
            try:
                async with self._session.post(
                    url,
                    json={"sid": sid},
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=5.0),
                ):
                    pass
            except Exception:
                # Best-effort logout during unload/shutdown
                pass

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
                    raise ArubaConnectionError(
                        f"HTTP 503 on {self.host}: {raw_text[:200]}",
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
            raise ArubaConnectionError(
                f"REST request '/rest/{endpoint}' failed on {self.host}: {_redact_sid(str(exc))}",
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
            fw_info = build_firmware_info(img_raw, ver_raw, upg_raw)
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
        target_ip = self._target_iap_ip or self._resolved_ip or self.host
        ssid_payload: dict[str, Any] = {
            "action": "create",
            "ssid-profile": profile,
            "enable" if enabled else "disable": "yes",
        }
        async with self._lock:
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
