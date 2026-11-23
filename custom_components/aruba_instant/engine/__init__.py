# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Pure Python domain models, CLI parsers, and filtering logic."""
from __future__ import annotations

from .filters import ClientDecision, FilterConfig, evaluate_client, parse_mac_set
from .models import (
    AccessPoint,
    ClusterSnapshot,
    ClusterSummary,
    WirelessClient,
    WirelessNetwork,
    merge_snapshots,
    normalize_mac,
)
from .parsers import (
    build_snapshot,
    classify_band,
    parse_aps,
    parse_clients,
    parse_metric_int,
    parse_networks,
    parse_summary,
    parse_uptime_seconds,
    strip_cli_envelope,
)

__all__ = [
    "AccessPoint",
    "ClientDecision",
    "ClusterSnapshot",
    "ClusterSummary",
    "FilterConfig",
    "WirelessClient",
    "WirelessNetwork",
    "build_snapshot",
    "classify_band",
    "evaluate_client",
    "merge_snapshots",
    "normalize_mac",
    "parse_aps",
    "parse_clients",
    "parse_mac_set",
    "parse_metric_int",
    "parse_networks",
    "parse_summary",
    "parse_uptime_seconds",
    "strip_cli_envelope",
]
