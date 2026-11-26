# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Diagnostics support with comprehensive PII, MAC, IP, and credential redaction."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import (
    CONF_EXCLUDE_MACS,
    CONF_HOSTS,
    CONF_NETWORK_NAME,
    CONF_SITE_NAME,
    CONF_TRACKED_SSIDS,
)
from .coordinator import ArubaInstantConfigEntry

TO_REDACT = {
    "username",
    "password",
    "sid",
    "uid-aruba",
    "host",
    CONF_HOSTS,
    "target_host",
    "known_peers",
    "known_cluster_ips",
    "known_aps",
    "allowed_ap_macs",
    "unique_id",
    "title",
    CONF_SITE_NAME,
    CONF_NETWORK_NAME,
    "vc_ip",
    "conductor_ip",
    "master_ip",
    "ip",
    "ip_address",
    "ipv6",
    "ipv6_address",
    "link_local_ip",
    "mac",
    "mac_address",
    "serial",
    "serial_number",
    "ap_id",
    CONF_EXCLUDE_MACS,
    "essid",
    "ssid",
    "ssids",
    "bssid",
    "bssids",
    "profile_name",
    "role",
    CONF_TRACKED_SSIDS,
    "name",
    "hostname",
    "access_point",
    "ap_name",
    "master_ap_name",
    "new_image_url",
    "new_drt_url",
    "image_url",
    "image_info",
    "firmware_url",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ArubaInstantConfigEntry,
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""
    runtime_data = getattr(entry, "runtime_data", None)
    if runtime_data is None:
        return async_redact_data({"entry": entry.as_dict(), "runtime_loaded": False}, TO_REDACT)

    snapshot = runtime_data.coordinator.data
    snapshot_dict = asdict(snapshot) if snapshot is not None else None
    if isinstance(snapshot_dict, dict) and snapshot_dict.get("fetched_at") is not None:
        snapshot_dict["fetched_at"] = snapshot_dict["fetched_at"].isoformat()

    payload = {
        "entry": entry.as_dict(),
        "topology_mode": runtime_data.topology_mode,
        "snapshot": snapshot_dict,
        "tracked_mac_count": len(runtime_data.coordinator.known_clients),
    }
    return async_redact_data(payload, TO_REDACT)
