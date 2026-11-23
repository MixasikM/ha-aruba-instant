# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Pure Python port interfaces for Aruba cluster/AP clients."""
from __future__ import annotations

from .cluster_client import (
    ArubaAuthError,
    ArubaClientError,
    ArubaConnectionError,
    ArubaMultiAuthError,
    ArubaReadOnlyCliError,
    ArubaRestApiDisabledError,
    BaseArubaClusterClient,
)

__all__ = [
    "ArubaAuthError",
    "ArubaClientError",
    "ArubaConnectionError",
    "ArubaMultiAuthError",
    "ArubaReadOnlyCliError",
    "ArubaRestApiDisabledError",
    "BaseArubaClusterClient",
]
