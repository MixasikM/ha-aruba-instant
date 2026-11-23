# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Pure Python domain models for Aruba Instant / AOS Wi-Fi clusters."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Any

_MAC_HEX_RE = re.compile(r"[^0-9a-fA-F]")


def normalize_mac(mac: str) -> str:
    """Normalize any MAC representation (colon, dash, Cisco dot) to aa:bb:cc:dd:ee:ff."""
    cleaned = _MAC_HEX_RE.sub("", mac or "").lower()
    if len(cleaned) != 12:
        return (mac or "").strip().lower()
    return ":".join(cleaned[i : i + 2] for i in range(0, 12, 2))


@dataclass(frozen=True)
class ClusterSummary:
    """Cluster or standalone AP metadata parsed from `show summary`."""

    name: str
    vc_ip: str | None = None
    conductor_ip: str | None = None
    ip_address: str | None = None
    version: str | None = None
    domain: str | None = None
    managed_via: str | None = None


@dataclass(frozen=True)
class WirelessNetwork:
    """Wireless network / SSID definition parsed from `show network`."""

    profile_name: str
    essid: str
    clients: int = 0
    network_type: str = "employee"
    band: str = ""
    status: str = "Enabled"
    active: bool = True


@dataclass(frozen=True)
class AccessPoint:
    """Physical Aruba Access Point telemetry parsed from `show aps`."""

    name: str
    ip_address: str
    serial: str
    model: str
    clients: int = 0
    is_conductor: bool = False
    online: bool = True
    uptime_seconds: int | None = None
    radio_5ghz_channel: str | None = None
    radio_5ghz_utilization: int | None = None
    radio_2_4ghz_channel: str | None = None
    radio_2_4ghz_utilization: int | None = None
    radio_6ghz_channel: str | None = None
    radio_6ghz_utilization: int | None = None
    sw_version: str | None = None

    @property
    def stable_id(self) -> str:
        """Return a stable hardware identifier (serial preferred, else IP/name)."""
        if self.serial and self.serial not in ("-", "--"):
            return self.serial.strip()
        return self.ip_address.strip() or self.name.strip()


@dataclass(frozen=True)
class WirelessClient:
    """Connected wireless (or wired) client parsed from `show clients`."""

    mac: str
    ip_address: str
    name: str
    os: str
    essid: str
    access_point: str
    channel: str
    phy_type: str
    role: str
    ipv6_address: str | None
    signal: int | None
    speed_mbps: int | None
    band: str
    is_wired: bool = False


@dataclass(frozen=True)
class ClusterSnapshot:
    """Point-in-time snapshot of an Aruba Instant cluster or Multi-AP site."""

    summary: ClusterSummary
    networks: list[WirelessNetwork] = field(default_factory=list)
    access_points: list[AccessPoint] = field(default_factory=list)
    clients: list[WirelessClient] = field(default_factory=list)
    fetched_at: datetime | None = None
    master_ap_name: str | None = None

    @property
    def all_ssids(self) -> list[str]:
        """Return deduplicated sorted SSIDs from `show network` and `show clients`."""
        ssids: set[str] = set()
        for net in self.networks:
            if net.essid and net.essid != "--":
                ssids.add(net.essid)
        for client in self.clients:
            if client.essid and client.essid != "--":
                ssids.add(client.essid)
        return sorted(ssids)


def _client_rank(client: WirelessClient) -> tuple[int, int, int]:
    """Rank duplicate roaming client rows: valid IP > stronger SNR > higher speed."""
    has_ip = 1 if client.ip_address not in ("", "0.0.0.0", "--") else 0
    return (has_ip, client.signal if client.signal is not None else -1, client.speed_mbps if client.speed_mbps is not None else -1)


def merge_snapshots(
    node_results: list[tuple[dict[str, Any], ClusterSnapshot | None]],
    site_name: str,
    fetched_at: datetime,
) -> ClusterSnapshot:
    """Merge independent standalone AP snapshots into one unified Multi-AP snapshot."""
    first_summary: ClusterSummary | None = None
    merged_aps: dict[str, AccessPoint] = {}
    merged_networks: dict[str, WirelessNetwork] = {}
    merged_clients: dict[str, WirelessClient] = {}

    for node, snap in node_results:
        host = str(node.get("host", "")).strip()
        persisted_ap_id = str(node.get("ap_id") or host).strip()
        persisted_name = str(node.get("name") or host).strip()

        if snap is None:
            merged_aps.setdefault(
                persisted_ap_id,
                AccessPoint(
                    name=persisted_name,
                    ip_address=host,
                    serial=persisted_ap_id,
                    model="",
                    clients=0,
                    is_conductor=False,
                    online=False,
                ),
            )
            continue

        if first_summary is None:
            first_summary = snap.summary

        if snap.access_points:
            for ap in snap.access_points:
                merged_aps[ap.stable_id] = ap
        else:
            merged_aps.setdefault(
                persisted_ap_id,
                AccessPoint(
                    name=snap.summary.name or persisted_name,
                    ip_address=snap.summary.ip_address or host,
                    serial=persisted_ap_id,
                    model="",
                    clients=len(snap.clients),
                    is_conductor=False,
                    online=True,
                    sw_version=snap.summary.version,
                ),
            )

        for net in snap.networks:
            if net.essid and net.essid not in merged_networks:
                merged_networks[net.essid] = net

        for client in snap.clients:
            mac = normalize_mac(client.mac)
            existing = merged_clients.get(mac)
            if existing is None or _client_rank(client) > _client_rank(existing):
                merged_clients[mac] = client

    # Recompute accurate client counts per SSID across deduplicated clients
    ssid_counts: dict[str, int] = {}
    for client in merged_clients.values():
        if client.essid and client.essid != "--":
            ssid_counts[client.essid] = ssid_counts.get(client.essid, 0) + 1

    final_networks: list[WirelessNetwork] = []
    seen_essids: set[str] = set()
    for essid, net in merged_networks.items():
        seen_essids.add(essid)
        final_networks.append(
            WirelessNetwork(
                profile_name=net.profile_name,
                essid=essid,
                clients=ssid_counts.get(essid, 0),
                network_type=net.network_type,
                band=net.band,
                status=net.status,
                active=net.active,
            )
        )
    for essid, count in sorted(ssid_counts.items()):
        if essid not in seen_essids:
            final_networks.append(WirelessNetwork(profile_name=essid, essid=essid, clients=count))

    summary = ClusterSummary(
        name=site_name,
        vc_ip=None,
        conductor_ip=None,
        ip_address=first_summary.ip_address if first_summary else None,
        version=first_summary.version if first_summary else None,
        domain=first_summary.domain if first_summary else None,
        managed_via=first_summary.managed_via if first_summary else None,
    )

    return ClusterSnapshot(
        summary=summary,
        networks=final_networks,
        access_points=list(merged_aps.values()),
        clients=list(merged_clients.values()),
        fetched_at=fetched_at,
        master_ap_name=None,
    )
