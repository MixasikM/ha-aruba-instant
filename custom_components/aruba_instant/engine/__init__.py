# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Pure Python domain models, CLI parsers, and filtering logic."""
from __future__ import annotations

from .filters import ClientDecision, FilterConfig, evaluate_client, parse_mac_set
from .models import (
    AccessPoint,
    ApUpgradeEntry,
    ClusterSnapshot,
    ClusterSummary,
    FirmwareCheckResult,
    FirmwareImageInfo,
    WirelessClient,
    WirelessNetwork,
    merge_snapshots,
    normalize_mac,
)
from .parsers import (
    build_firmware_info,
    build_snapshot,
    classify_band,
    enrich_snapshot_with_firmware,
    parse_aps,
    parse_clients,
    parse_image_check_xml,
    parse_image_version,
    parse_metric_int,
    parse_networks,
    parse_summary,
    parse_upgrade_info,
    parse_uptime_seconds,
    parse_version_details,
    strip_cli_envelope,
)

__all__ = [
    "AccessPoint",
    "ApUpgradeEntry",
    "ClientDecision",
    "ClusterSnapshot",
    "ClusterSummary",
    "FilterConfig",
    "FirmwareCheckResult",
    "FirmwareImageInfo",
    "WirelessClient",
    "WirelessNetwork",
    "build_firmware_info",
    "build_snapshot",
    "classify_band",
    "enrich_snapshot_with_firmware",
    "evaluate_client",
    "merge_snapshots",
    "normalize_mac",
    "parse_aps",
    "parse_clients",
    "parse_image_check_xml",
    "parse_image_version",
    "parse_mac_set",
    "parse_metric_int",
    "parse_networks",
    "parse_summary",
    "parse_upgrade_info",
    "parse_uptime_seconds",
    "parse_version_details",
    "strip_cli_envelope",
]
