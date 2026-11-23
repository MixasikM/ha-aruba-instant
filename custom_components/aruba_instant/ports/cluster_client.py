# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Abstract port interface and domain exceptions for Aruba transport clients."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..engine.models import ClusterSnapshot


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
