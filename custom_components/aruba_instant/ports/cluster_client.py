# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Abstract port interface and domain exceptions for Aruba transport clients."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from ..engine.models import ClusterSnapshot, FirmwareCheckResult, FirmwareImageInfo


class ArubaClientError(Exception):
    """Base error raised by Aruba cluster/AP transport adapters."""

    def __init__(self, message: str, host: str | None = None) -> None:
        super().__init__(message)
        self.host = host


class ArubaConnectionError(ArubaClientError):
    """Raised when one or more Aruba endpoints are unreachable or timed out."""


class ArubaAuthError(ArubaClientError):
    """Raised when Aruba rejects authentication credentials."""


class ArubaMultiAuthError(ArubaAuthError):
    """Raised when one or more nodes in a Multi-AP pool fail authentication."""

    def __init__(self, message: str, failed_hosts: list[str]) -> None:
        first_host = failed_hosts[0] if failed_hosts else None
        super().__init__(message, host=first_host)
        self.failed_hosts = list(failed_hosts)


class ArubaRestApiDisabledError(ArubaClientError):
    """Raised exclusively when HTTP 503 states REST API Service is not enabled."""


class ArubaReadOnlyCliError(ArubaClientError):
    """Raised when CLI configuration changes are rejected (e.g., AOS 10 / non-admin)."""


class BaseArubaClusterClient(ABC):
    """Abstract port for polling an Aruba Instant cluster or Multi-AP pool."""

    @abstractmethod
    async def async_fetch_snapshot(self) -> ClusterSnapshot:
        """Poll summary, networks, APs, and clients and return a unified snapshot."""

    @abstractmethod
    async def async_close(self) -> None:
        """Release open sessions, tokens, or network sockets cleanly."""

    async def async_fetch_firmware_details(
        self,
    ) -> tuple[FirmwareImageInfo | None, FirmwareCheckResult | None]:
        """Fetch optional firmware, reboot cause, and image-check details."""
        return (None, None)

    async def async_reboot_ap(self, ap_ip: str) -> None:
        """Reboot a single Access Point by IP address (opt-in write operation)."""
        raise ArubaReadOnlyCliError("AP reboot is not supported by this transport client")

    async def async_set_ssid_enabled(self, profile_name: str, enabled: bool) -> None:
        """Enable or disable a WLAN SSID profile (opt-in write operation)."""
        raise ArubaReadOnlyCliError("SSID control is not supported by this transport client")

    async def async_upgrade_firmware(
        self,
        image_url: str,
        *,
        ap_class: str | None = None,
        auto_reboot: bool = True,
    ) -> None:
        """Trigger firmware upgrade from an explicit HTTP/HTTPS image URL."""
        raise ArubaReadOnlyCliError("Firmware upgrade is not supported by this transport client")

    @property
    def active_host(self) -> str | None:
        """Return currently targeted AP/controller host IP if tracked."""
        return None

    def set_known_peer_ips(self, peer_ips: Iterable[str]) -> None:
        """Seed known peer AP management IPs for failover without dedicated VIP."""
        return None

    def get_known_peer_ips(self) -> list[str]:
        """Return known peer AP management IPs discovered for this cluster."""
        return []

    def get_vc_vip_failure_count(self, vc_ip: str) -> int:
        """Return consecutive connection failure count for an advertised VC VIP."""
        return 0
