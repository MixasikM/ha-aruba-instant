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
class ApUpgradeEntry:
    """Per-AP entry parsed from `show upgrade info`."""

    mac: str
    ip_address: str
    ap_class: str = ""
    status: str = ""
    image_info: str = ""
    error_detail: str = ""
    reboot_cause: str | None = None
    primary_version: str | None = None
    backup_version: str | None = None


@dataclass(frozen=True)
class FirmwareImageInfo:
    """Cluster or standalone AP firmware, partition, and upgrade metadata."""

    primary_version: str | None = None
    primary_build_time: str | None = None
    backup_version: str | None = None
    backup_build_time: str | None = None
    image_classes: tuple[str, ...] = ()
    model_version_line: str | None = None
    last_reboot_cause: str | None = None
    upgrade_in_progress: bool = False
    auto_reboot: bool | None = None
    ap_statuses: tuple[ApUpgradeEntry, ...] = ()


@dataclass(frozen=True)
class FirmwareCheckResult:
    """Result parsed from InstantOS `swarm.cgi?opcode=image-check-status` XML."""

    server_state: str | None = None
    server_message: str | None = None
    new_image_version: str | None = None
    new_image_url: str | None = None
    new_drt_version: str | None = None
    new_drt_url: str | None = None


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
    mac: str | None = None
    ap_class: str | None = None
    primary_firmware: str | None = None
    backup_firmware: str | None = None
    upgrade_status: str | None = None
    upgrade_error: str | None = None
    last_reboot_cause: str | None = None

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
    firmware_info: FirmwareImageInfo | None = None
    firmware_check: FirmwareCheckResult | None = None

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
    fw_infos: list[FirmwareImageInfo] = []
    first_fw_check: FirmwareCheckResult | None = None

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
        if snap.firmware_info is not None:
            fw_infos.append(snap.firmware_info)
        if first_fw_check is None and snap.firmware_check is not None:
            first_fw_check = snap.firmware_check

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

    merged_fw_info: FirmwareImageInfo | None = None
    if fw_infos:
        all_classes: list[str] = []
        all_statuses: list[ApUpgradeEntry] = []
        for info in fw_infos:
            for cls in info.image_classes:
                if cls not in all_classes:
                    all_classes.append(cls)
            all_statuses.extend(info.ap_statuses)
        lead = fw_infos[0]
        merged_fw_info = FirmwareImageInfo(
            primary_version=lead.primary_version,
            primary_build_time=lead.primary_build_time,
            backup_version=lead.backup_version,
            backup_build_time=lead.backup_build_time,
            image_classes=tuple(all_classes),
            model_version_line=lead.model_version_line,
            last_reboot_cause=lead.last_reboot_cause,
            upgrade_in_progress=any(info.upgrade_in_progress for info in fw_infos),
            auto_reboot=lead.auto_reboot,
            ap_statuses=tuple(all_statuses),
        )

    return ClusterSnapshot(
        summary=summary,
        networks=final_networks,
        access_points=list(merged_aps.values()),
        clients=list(merged_clients.values()),
        fetched_at=fetched_at,
        master_ap_name=None,
        firmware_info=merged_fw_info,
        firmware_check=first_fw_check,
    )
