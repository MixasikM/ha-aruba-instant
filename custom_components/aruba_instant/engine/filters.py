# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Pure Python filtering rules for Aruba Wi-Fi clients."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .models import WirelessClient, normalize_mac


class ClientDecision(str, Enum):
    """Decision for how Home Assistant should materialize a discovered client."""

    IGNORE = "ignore"
    CREATE_DISABLED = "disable"
    TRACK_ENABLED = "track"


def parse_mac_set(raw: str | list[str] | set[str] | None) -> set[str]:
    """Parse comma/newline/space-separated MAC strings into normalized aa:bb:cc:dd:ee:ff."""
    if not raw:
        return set()
    if isinstance(raw, (list, set, tuple)):
        items = [str(item) for item in raw]
    else:
        items = [part for chunk in str(raw).replace(";", ",").replace("\n", ",").split(",") for part in chunk.split()]
    result: set[str] = set()
    for item in items:
        cleaned = item.strip()
        if cleaned:
            result.add(normalize_mac(cleaned))
    return result


@dataclass(frozen=True)
class FilterConfig:
    """User-configurable client and SSID filtering options."""

    tracked_ssids: tuple[str, ...] = ()
    unselected_ssid_mode: str = "ignore"  # "ignore" | "disable"
    track_new_clients: bool = True
    track_unnamed_clients: bool = True
    include_wired_clients: bool = False
    require_ip: bool = False
    min_signal: int = 0
    exclude_macs: frozenset[str] = field(default_factory=frozenset)


def evaluate_client(client: WirelessClient, cfg: FilterConfig) -> ClientDecision:
    """Determine whether a parsed client should be tracked, disabled, or ignored."""
    mac = normalize_mac(client.mac)
    if mac in cfg.exclude_macs:
        return ClientDecision.IGNORE

    if client.is_wired:
        if not cfg.include_wired_clients:
            return ClientDecision.IGNORE
    else:
        # Wireless client must satisfy SSID inclusion rules when tracked_ssids is configured
        if cfg.tracked_ssids and client.essid not in cfg.tracked_ssids:
            if cfg.unselected_ssid_mode == "disable":
                return ClientDecision.CREATE_DISABLED
            return ClientDecision.IGNORE

        if cfg.min_signal > 0 and (client.signal is None or client.signal < cfg.min_signal):
            return ClientDecision.IGNORE

    if not cfg.track_unnamed_clients and not client.name.strip():
        return ClientDecision.IGNORE

    if cfg.require_ip and client.ip_address in ("", "0.0.0.0", "--"):
        return ClientDecision.IGNORE

    if not cfg.track_new_clients:
        return ClientDecision.CREATE_DISABLED

    return ClientDecision.TRACK_ENABLED
