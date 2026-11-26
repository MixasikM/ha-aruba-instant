# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for pure Python engine parsers, Multi-AP merge, filters, and Hexagonal purity."""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

from custom_components.aruba_instant.engine.filters import (
    ClientDecision,
    FilterConfig,
    evaluate_client,
    parse_mac_set,
)
from custom_components.aruba_instant.engine.models import (
    ClusterSnapshot,
    merge_snapshots,
    normalize_mac,
)
from custom_components.aruba_instant.engine.parsers import (
    build_firmware_info,
    build_snapshot,
    classify_band,
    enrich_snapshot_with_firmware,
    parse_allowed_aps,
    parse_aps,
    parse_clients,
    parse_image_check_xml,
    parse_image_version,
    parse_networks,
    parse_summary,
    parse_upgrade_info,
    parse_uptime_seconds,
    parse_version_details,
)
from tests.fixtures.aruba_8_10_samples import (
    IMAGE_CHECK_ERROR_XML,
    IMAGE_CHECK_SUCCESS_EMPTY_XML,
    IMAGE_CHECK_UPDATE_XML,
    MULTI_AP_NODE1_CLIENTS,
    MULTI_AP_NODE2_CLIENTS,
    SHOW_APS_OUTPUT,
    SHOW_APS_SINGLE_SSH_RAW,
    SHOW_CLIENTS_OUTPUT,
    SHOW_CLIENTS_SSH_RAW,
    SHOW_IMAGE_VERSION_OUTPUT,
    SHOW_NETWORK_OUTPUT,
    SHOW_SUMMARY_8_6_OUTPUT,
    SHOW_SUMMARY_OUTPUT,
    SHOW_SUMMARY_STANDALONE_AOS10,
    SHOW_UPGRADE_INFO_OUTPUT,
    SHOW_VERSION_OUTPUT,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG_DIR = REPO_ROOT / "custom_components" / "aruba_instant"


def test_hexagonal_zero_ha_imports_in_engine_and_ports() -> None:
    """engine/ and ports/ must remain 100% pure Python with zero homeassistant imports."""
    for sub in ("engine", "ports"):
        for py_file in (PKG_DIR / sub).glob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith("homeassistant"), py_file
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    assert not mod.startswith("homeassistant"), py_file


def test_parse_summary_variants() -> None:
    s_810 = parse_summary(SHOW_SUMMARY_OUTPUT)
    assert s_810.name == "MainCluster"
    assert s_810.vc_ip == "192.0.2.10"
    assert s_810.conductor_ip == "192.0.2.11"
    assert s_810.version == "8.10.0.24_97188"

    s_86 = parse_summary(SHOW_SUMMARY_8_6_OUTPUT)
    assert s_86.name == "BranchCluster"
    assert s_86.vc_ip == "198.51.100.10"
    assert s_86.conductor_ip == "198.51.100.11"

    s_aos10 = parse_summary(SHOW_SUMMARY_STANDALONE_AOS10)
    assert s_aos10.name == "AP-Standalone-1"
    assert s_aos10.vc_ip is None
    assert s_aos10.managed_via == "Central"
    assert s_aos10.ip_address == "198.51.100.31"


def test_parse_networks_with_zero_clients_and_spaces() -> None:
    nets = parse_networks(SHOW_NETWORK_OUTPUT)
    assert len(nets) == 4
    by_essid = {n.essid: n for n in nets}
    assert by_essid["CorpWiFi"].clients == 4
    assert by_essid["Guest WiFi"].clients == 0
    assert by_essid["Guest WiFi"].network_type == "guest"
    assert by_essid["IoTWiFi"].clients == 2


def test_parse_aps_multi_and_single_ssh_with_6ghz() -> None:
    aps = parse_aps(SHOW_APS_OUTPUT, sw_version="8.10.0.24_97188")
    assert len(aps) == 3
    by_name = {ap.name: ap for ap in aps}

    lobby = by_name["AP-Lobby"]
    assert lobby.ip_address == "192.0.2.11"
    assert lobby.is_conductor is True
    assert lobby.serial == "SN00000001"
    assert lobby.radio_5ghz_channel == "36E"
    assert lobby.radio_5ghz_utilization == 3
    assert lobby.radio_2_4ghz_channel == "1"
    assert lobby.radio_2_4ghz_utilization == 27
    assert lobby.radio_6ghz_channel == "37w"
    assert lobby.radio_6ghz_utilization == 8
    assert lobby.uptime_seconds == 14 * 86400 + 3 * 3600 + 22 * 60 + 10

    lab = by_name["AP-Lab Desk"]
    assert lab.is_conductor is False
    assert lab.clients == 3
    assert lab.radio_6ghz_channel is None

    floor2 = by_name["AP-Floor2"]
    assert floor2.uptime_seconds == 45

    single = parse_aps(SHOW_APS_SINGLE_SSH_RAW)
    assert len(single) == 1
    assert single[0].name == "AP-Standalone"
    assert single[0].is_conductor is True
    assert single[0].uptime_seconds == 3600 + 12 * 60 + 3


def test_parse_clients_spaces_overflow_blank_6ghz_wired_and_ssh() -> None:
    clients = parse_clients(SHOW_CLIENTS_OUTPUT)
    assert len(clients) == 7
    by_mac = {c.mac: c for c in clients}

    alice = by_mac["aa:bb:cc:11:22:33"]
    assert alice.name == "Alice-Phone"
    assert alice.access_point == "AP-Lab Desk"
    assert alice.band == "5 GHz"
    assert alice.signal == 42
    assert alice.speed_mbps == 650

    bob = by_mac["aa:bb:cc:11:22:44"]
    assert bob.name == "Bob S26 Tablet"
    assert bob.band == "2.4 GHz"
    assert bob.ipv6_address is None

    carol = by_mac["aa:bb:cc:11:22:55"]
    assert carol.name == "Carol MacBook Pro"
    assert carol.os == "Mac OS X"
    assert carol.band == "6 GHz"
    assert carol.channel == "37w"

    guest = by_mac["aa:bb:cc:11:22:66"]
    assert guest.name == "android-dhcp-very-long-hostname-01"
    assert guest.essid == "Guest WiFi"
    assert guest.role == "Guest Role"
    assert guest.band == "5 GHz"

    unnamed = by_mac["dd:ee:ff:00:11:22"]
    assert unnamed.name == ""
    assert unnamed.essid == "IoTWiFi"
    assert unnamed.band == "2.4 GHz"

    wired = by_mac["dd:ee:ff:00:33:44"]
    assert wired.is_wired is True
    assert wired.band == "Wired"
    assert wired.signal is None
    assert wired.speed_mbps is None

    ssh_clients = parse_clients(SHOW_CLIENTS_SSH_RAW)
    assert len(ssh_clients) == 1
    assert ssh_clients[0].access_point == "AP-Standalone"
    assert ssh_clients[0].band == "5 GHz"
    assert ssh_clients[0].speed_mbps == 866


def test_classify_band_and_uptime_helpers() -> None:
    assert classify_band("11", "GN") == ("2.4 GHz", False)
    assert classify_band("1", "6GHz-HE") == ("6 GHz", False)
    assert classify_band("37w", "AX") == ("6 GHz", False)
    assert classify_band("100S", "a-HE") == ("5 GHz", False)
    assert classify_band("--", "--") == ("Wired", True)
    assert parse_uptime_seconds("-") is None
    assert parse_uptime_seconds("2d:4h:5m") == 2 * 86400 + 4 * 3600 + 5 * 60
    assert normalize_mac("AA-BB-CC.11.22.33") == "aa:bb:cc:11:22:33"


def test_merge_snapshots_deduplicates_roaming_and_synthesizes_offline_ap() -> None:
    snap1 = ClusterSnapshot(
        summary=parse_summary(SHOW_SUMMARY_STANDALONE_AOS10),
        clients=parse_clients(MULTI_AP_NODE1_CLIENTS),
    )
    snap2 = ClusterSnapshot(
        summary=parse_summary(SHOW_SUMMARY_STANDALONE_AOS10),
        clients=parse_clients(MULTI_AP_NODE2_CLIENTS),
    )
    now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    merged = merge_snapshots(
        [
            ({"host": "198.51.100.31", "ap_id": "SN1", "name": "AP-Standalone-1"}, snap1),
            ({"host": "198.51.100.32", "ap_id": "SN2", "name": "AP-Standalone-2"}, snap2),
            ({"host": "198.51.100.33", "ap_id": "SN3", "name": "AP-Offline"}, None),
        ],
        site_name="Campus",
        fetched_at=now,
    )
    assert len(merged.clients) == 1
    assert merged.clients[0].ip_address == "198.51.100.120"
    assert merged.clients[0].access_point == "AP-Standalone-2"
    assert merged.master_ap_name is None

    by_ap = {ap.name: ap for ap in merged.access_points}
    assert by_ap["AP-Offline"].online is False
    assert by_ap["AP-Offline"].serial == "SN3"


def test_filter_evaluation_rules() -> None:
    snap = build_snapshot(
        SHOW_SUMMARY_OUTPUT, SHOW_NETWORK_OUTPUT, SHOW_APS_OUTPUT, SHOW_CLIENTS_OUTPUT
    )
    by_mac = {c.mac: c for c in snap.clients}

    cfg = FilterConfig(
        tracked_ssids=("CorpWiFi", "Guest WiFi"),
        unselected_ssid_mode="disable",
        track_new_clients=True,
        track_unnamed_clients=False,
        include_wired_clients=False,
        require_ip=True,
        min_signal=20,
        exclude_macs=frozenset(parse_mac_set("aa:bb:cc:11:22:44")),
    )

    assert evaluate_client(by_mac["aa:bb:cc:11:22:33"], cfg) == ClientDecision.TRACK_ENABLED
    assert evaluate_client(by_mac["aa:bb:cc:11:22:44"], cfg) == ClientDecision.IGNORE  # excluded MAC
    assert evaluate_client(by_mac["dd:ee:ff:00:11:22"], cfg) == ClientDecision.CREATE_DISABLED  # unselected SSID -> disable
    assert evaluate_client(by_mac["dd:ee:ff:00:33:44"], cfg) == ClientDecision.IGNORE  # wired off
    assert evaluate_client(by_mac["dd:ee:ff:00:99:99"], cfg) == ClientDecision.IGNORE  # 0.0.0.0 & min_signal

    cfg_unnamed = FilterConfig(
        tracked_ssids=("IoTWiFi",),
        track_unnamed_clients=False,
        track_new_clients=True,
    )
    assert evaluate_client(by_mac["dd:ee:ff:00:11:22"], cfg_unnamed) == ClientDecision.IGNORE


def test_parser_tricky_edge_cases() -> None:
    # 1. Long ESSID >= 18 chars in show network shifting Clients column right
    long_net = """
Networks
--------
Profile Name      ESSID             Clients  Type      Band         Status
------------      -----             -------  ----      ----         ------
VeryLongNetwork12345 VeryLongNetwork12345 12   employee  2.4ghz,5ghz  Enabled
"""
    nets = parse_networks(long_net)
    assert len(nets) == 1
    assert nets[0].essid == "VeryLongNetwork12345"
    assert nets[0].clients == 12

    # 2. Client with "--" placeholder name + "VLAN 20 Guest" role (digits in Role)
    tricky_clients = """
Client List
-----------
Name         IP Address    MAC Address        OS       ESSID     Access Point  Channel  Type  Role           IPv6 Address  Signal    Speed (mbps)
----         ----------    -----------        --       -----     ------------  -------  ----  ----           ------------  ------    ------------
--           192.0.2.150   aa:bb:cc:77:88:99  Linux    CorpWiFi  AP-Lobby      36E      AX    VLAN 20 Guest  --            30(good)  400(good)
"""
    clients = parse_clients(tricky_clients)
    assert len(clients) == 1
    assert clients[0].name == ""
    assert clients[0].channel == "36E"
    assert clients[0].phy_type == "AX"
    assert clients[0].role == "VLAN 20 Guest"

    # 3. AP whose name contains an IPv4 string ("AP-192.0.2.99") and blank Zone
    tricky_aps = """
1 Access Point
--------------
Name           IP Address    Mode    Spectrum  Clients  Type         IPv6 Address  Mesh Role  Zone  Serial #    radio0 Channel  radio0 Utilization (%)  Age
----           ----------    ----    --------  -------  ----         ------------  ---------  ----  --------    --------------  ----------------------  ---
AP-192.0.2.99  198.51.100.5* access  disable   4        515(indoor)  --            Portal           SN99999999  36E             11(good)                5m:10s
"""
    aps = parse_aps(tricky_aps)
    assert len(aps) == 1
    assert aps[0].name == "AP-192.0.2.99"
    assert aps[0].ip_address == "198.51.100.5"
    assert aps[0].is_conductor is True
    assert aps[0].serial == "SN99999999"
    assert aps[0].radio_5ghz_utilization == 11


def test_firmware_parsers_xml_and_enrichment() -> None:
    img = parse_image_version(SHOW_IMAGE_VERSION_OUTPUT)
    assert img.primary_version == "8.10.0.24_97188 LSR (Digitally Signed - Production Build)"
    assert img.backup_version == "8.10.0.21_94501 LSR (Digitally Signed - Production Build)"
    assert img.image_classes == ("Hercules",)

    model_line, reboot_cause = parse_version_details(SHOW_VERSION_OUTPUT)
    assert model_line == "ArubaOS (MODEL: 325), Version 8.10.0.24 LSR"
    assert reboot_cause and "Image Upgrade Successful" in reboot_cause

    in_prog, auto_reboot, statuses = parse_upgrade_info(SHOW_UPGRADE_INFO_OUTPUT)
    assert in_prog is False
    assert auto_reboot is True
    assert len(statuses) == 3
    assert statuses[0].mac == "02:00:00:10:00:01"
    assert statuses[0].image_info == "From Seed"
    assert statuses[1].ap_class == "Hercules"

    fw_info = build_firmware_info(SHOW_IMAGE_VERSION_OUTPUT, SHOW_VERSION_OUTPUT, SHOW_UPGRADE_INFO_OUTPUT)
    err_chk = parse_image_check_xml(IMAGE_CHECK_ERROR_XML)
    assert err_chk.server_state == "error"
    assert err_chk.server_message == "Internal error 001 code 2"
    assert err_chk.new_image_version is None

    # DOCTYPE/ENTITY XML hardening + Disabled network without Active column
    assert parse_image_check_xml('<?xml version="1.0"?><!DOCTYPE x><re/>').server_state is None
    disabled_net = """
Networks
--------
Profile Name      ESSID             Clients  Type      Band         Status
------------      -----             -------  ----      ----         ------
DisabledNet       DisabledNet       0        guest     2.4ghz,5ghz  Disabled
"""
    dnets = parse_networks(disabled_net)
    assert len(dnets) == 1
    assert dnets[0].active is False

    from tests.fixtures.aruba_8_10_samples import SHOW_ALLOWED_APS_OUTPUT

    allowed = parse_allowed_aps(SHOW_ALLOWED_APS_OUTPUT)
    assert allowed == ("02:00:00:10:00:01", "02:00:00:10:00:02", "02:00:00:10:00:03")
    assert parse_allowed_aps("COMMAND=show allowed-aps\n% Parse error.\n") == ()
    fw_with_allow = build_firmware_info(
        SHOW_IMAGE_VERSION_OUTPUT, SHOW_VERSION_OUTPUT, SHOW_UPGRADE_INFO_OUTPUT, SHOW_ALLOWED_APS_OUTPUT
    )
    assert fw_with_allow.allowed_ap_macs == allowed
    assert err_chk.new_drt_version == "1.0_97611"

    ok_empty_chk = parse_image_check_xml(IMAGE_CHECK_SUCCESS_EMPTY_XML)
    assert ok_empty_chk.server_state == "success"
    assert ok_empty_chk.new_image_version is None

    upd_chk = parse_image_check_xml(IMAGE_CHECK_UPDATE_XML)
    assert upd_chk.server_state == "success"
    assert upd_chk.new_image_version == "8.10.0.25_98000"
    assert upd_chk.new_image_url == "http://192.0.2.50/ArubaInstant_Hercules_8.10.0.25_98000"

    assert parse_image_check_xml("<broken>").server_state is None

    snap = build_snapshot(SHOW_SUMMARY_OUTPUT, SHOW_NETWORK_OUTPUT, SHOW_APS_OUTPUT, SHOW_CLIENTS_OUTPUT)
    enriched = enrich_snapshot_with_firmware(snap, fw_info, upd_chk)
    by_name = {ap.name: ap for ap in enriched.access_points}
    assert by_name["AP-Lobby"].mac == "02:00:00:10:00:01"
    assert by_name["AP-Lobby"].ap_class == "Hercules"
    assert by_name["AP-Lobby"].upgrade_status == "image-ok"
    assert by_name["AP-Lobby"].upgrade_error is None
    assert by_name["AP-Lobby"].last_reboot_cause == fw_info.last_reboot_cause
    assert by_name["AP-Lab Desk"].mac == "02:00:00:10:00:02"
    assert by_name["AP-Lab Desk"].backup_firmware == img.backup_version
