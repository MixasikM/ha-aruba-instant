# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Pure Python CLI & REST output parsers for HPE Aruba Instant / AOS Wi-Fi."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import re
import xml.etree.ElementTree as ET

from .models import (
    AccessPoint,
    ApUpgradeEntry,
    ClusterSnapshot,
    ClusterSummary,
    FirmwareCheckResult,
    FirmwareImageInfo,
    WirelessClient,
    WirelessNetwork,
    normalize_mac,
)

_SUMMARY_KV_RE = re.compile(r"^\s*([^:]+?)\s*(?:\*)?:\s*(.*?)\s*$")
_MULTI_SPACE_SPLIT_RE = re.compile(r"\s{2,}")
_IPV4_TOKEN_RE = re.compile(r"(?:^|\s)((?:\d{1,3}\.){3}\d{1,3})\*?(?=\s|$)")
_CLIENT_IP_MAC_RE = re.compile(
    r"\s((?:\d{1,3}\.){3}\d{1,3}|--)\s+((?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2})(?:\s+|$)"
)
_METRIC_INT_RE = re.compile(r"^(-?\d+)")
_UPTIME_PART_RE = re.compile(r"(\d+)\s*([dhms])", re.IGNORECASE)
_CHANNEL_TOKEN_RE = re.compile(r"^(?:--|\d{1,3}(?:[ESw+-]|160)?)$")
_PROMPT_LINE_RE = re.compile(r"^[^\r\n#>]{1,80}[#>]\s*$")
_ECHOED_CMD_RE = re.compile(r"^(?:no\s+paging|show\s+\S+)\s*$", re.IGNORECASE)

_FIVE_GHZ_PHY_TOKENS = ("ac", "a-he", "a-vht", "a-ht", "an", "a", "6ghz", "ax")


def strip_cli_envelope(raw: str) -> str:
    """Strip REST `cli output:` / `COMMAND=` headers and SSH PTY echoes/prompts."""
    normalized = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")

    # Drop leading blank lines, REST headers, and echoed commands
    while lines:
        head = lines[0].strip()
        if (
            not head
            or head.lower() == "cli output:"
            or head.upper().startswith("COMMAND=")
            or _ECHOED_CMD_RE.match(head)
        ):
            lines.pop(0)
        else:
            break

    # Drop trailing blank lines and interactive CLI prompts (e.g., "AP-Standalone# ")
    while lines:
        tail = lines[-1]
        if not tail.strip() or _PROMPT_LINE_RE.match(tail.strip()):
            lines.pop()
        else:
            break

    return "\n".join(lines)


def parse_metric_int(value: str | None) -> int | None:
    """Parse metrics such as '42(good)', '-92(good)', or '--' into an int."""
    if not value:
        return None
    cleaned = value.strip()
    if cleaned in ("-", "--", "N/A", ""):
        return None
    match = _METRIC_INT_RE.match(cleaned)
    return int(match.group(1)) if match else None


def parse_uptime_seconds(age: str | None) -> int | None:
    """Parse Aruba AP Age strings ('14d:3h:22m:10s', '2d:4h:5m', '45s') to seconds."""
    if not age:
        return None
    cleaned = age.strip()
    if cleaned in ("-", "--", ""):
        return None
    multipliers = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    total = 0
    matched = False
    for num_str, unit in _UPTIME_PART_RE.findall(cleaned):
        total += int(num_str) * multipliers[unit.lower()]
        matched = True
    return total if matched else None


def classify_band(channel: str | None, phy_type: str | None) -> tuple[str, bool]:
    """Return (band_label, is_wired) using both PHY type and channel number/suffix."""
    ch = (channel or "").strip()
    phy = (phy_type or "").strip()
    phy_lower = phy.lower()

    if ch in ("", "-", "--") and phy in ("", "-", "--"):
        return ("Wired", True)

    if "6ghz" in phy_lower or "6e" in phy_lower or ch.endswith("w"):
        return ("6 GHz", False)

    if phy_lower.startswith("g-") or phy_lower in ("gn", "bg", "b", "g"):
        return ("2.4 GHz", False)

    if phy_lower.startswith("a-") or phy_lower in ("ac", "an", "a"):
        return ("5 GHz", False)

    ch_digits = _METRIC_INT_RE.match(ch)
    if ch_digits:
        ch_num = int(ch_digits.group(1))
        if ch_num <= 14:
            return ("2.4 GHz", False)
        return ("5 GHz", False)

    if "ax" in phy_lower:
        return ("5 GHz", False)

    return ("Wired", True) if ch in ("", "-", "--") else ("5 GHz", False)


def _extract_column_spans(header_line: str) -> list[tuple[str, int]]:
    """Split a table header row by 2+ spaces, returning (column_name, start_index)."""
    spans: list[tuple[str, int]] = []
    pos = 0
    for match in re.finditer(r"\S(?:.*?\S)?(?=\s{2,}|$)", header_line):
        # Refine by splitting on 2+ spaces cleanly from pos
        break
    parts = [p for p in _MULTI_SPACE_SPLIT_RE.split(header_line.strip()) if p]
    search_from = 0
    for part in parts:
        idx = header_line.find(part, search_from)
        if idx >= 0:
            spans.append((part, idx))
            search_from = idx + len(part)
    return spans


def parse_summary(raw: str) -> ClusterSummary:
    """Parse `show summary` output across InstantOS 8.4–8.13 and AOS 10 Standalone."""
    cleaned = strip_cli_envelope(raw)
    kv: dict[str, str] = {}
    for line in cleaned.splitlines():
        match = _SUMMARY_KV_RE.match(line)
        if not match:
            continue
        key = match.group(1).strip().lower()
        val = match.group(2).strip()
        kv[key] = val

    vc_ip = kv.get("vc ip address")
    if vc_ip in ("", "-", "--", "0.0.0.0"):
        vc_ip = None

    conductor_ip = kv.get("conductor ip address") or kv.get("master ip address")
    if conductor_ip:
        conductor_ip = conductor_ip.lstrip("*").strip() or None

    ip_addr = kv.get("ip address") or conductor_ip or vc_ip
    if ip_addr in ("", "-", "--"):
        ip_addr = None

    return ClusterSummary(
        name=kv.get("name") or ip_addr or "Aruba Cluster",
        vc_ip=vc_ip,
        conductor_ip=conductor_ip,
        ip_address=ip_addr,
        version=kv.get("version") or None,
        domain=kv.get("domain") or None,
        managed_via=kv.get("managed via") or None,
    )


def parse_networks(raw: str) -> list[WirelessNetwork]:
    """Parse `show network` output into WirelessNetwork objects."""
    cleaned = strip_cli_envelope(raw)
    lines = cleaned.splitlines()
    header_idx: int | None = None
    for idx, line in enumerate(lines):
        if "Profile Name" in line and "ESSID" in line and "Clients" in line:
            header_idx = idx
            break
    if header_idx is None:
        return []

    spans = _extract_column_spans(lines[header_idx])
    col_starts = {name.lower(): start for name, start in spans}
    ordered_starts = [start for _, start in spans]

    def slice_col(row: str, col_key: str) -> str:
        start = col_starts.get(col_key)
        if start is None or start >= len(row):
            return ""
        pos_idx = ordered_starts.index(start)
        end = ordered_starts[pos_idx + 1] if pos_idx + 1 < len(ordered_starts) else len(row)
        return row[start:end].strip()

    networks: list[WirelessNetwork] = []
    start_row = header_idx + 1
    if start_row < len(lines) and set(lines[start_row].replace(" ", "")) <= {"-"}:
        start_row += 1

    for row in lines[start_row:]:
        stripped = row.strip()
        if not stripped or set(stripped.replace(" ", "")) <= {"-"}:
            continue
        profile_name = slice_col(row, "profile name")
        essid = slice_col(row, "essid") or profile_name
        clients_str = slice_col(row, "clients")
        net_type_slice = slice_col(row, "type").lower()
        valid_types = ("employee", "guest", "voice", "wired", "mesh")
        if not clients_str.isdigit() or (net_type_slice and net_type_slice not in valid_types):
            # Fallback if long profile/ESSID shifted column boundaries
            m_fallback = re.search(
                r"^(.*?)\s+(\d+)\s+(employee|guest|voice|wired|mesh)"
                r"(?:\s+(?!Enabled\b|Disabled\b|Yes\b|No\b)(\S+))?"
                r"(?:\s+(Enabled|Disabled))?(?:\s+(Yes|No))?\b",
                row,
                re.IGNORECASE,
            )
            if not m_fallback:
                continue
            left_raw = m_fallback.group(1).strip()
            left_parts = _MULTI_SPACE_SPLIT_RE.split(left_raw)
            if len(left_parts) > 1:
                profile_name = left_parts[0]
                essid = left_parts[1]
            elif " " in left_raw:
                mid = len(left_raw) // 2
                if (
                    len(left_raw) % 2 == 1
                    and left_raw[mid] == " "
                    and left_raw[:mid] == left_raw[mid + 1 :]
                ):
                    profile_name = left_raw[:mid]
                    essid = profile_name
                else:
                    essid_col = col_starts.get("essid", 18)
                    split_at = left_raw.find(" ", max(1, essid_col - 1))
                    if split_at != -1:
                        profile_name = left_raw[:split_at].strip()
                        essid = left_raw[split_at + 1 :].strip() or profile_name
                    else:
                        profile_name = left_raw
                        essid = left_raw
            else:
                profile_name = left_raw
                essid = left_raw
            clients_val = int(m_fallback.group(2))
            net_type = m_fallback.group(3).lower()
            status_val = m_fallback.group(5) or "Enabled"
            active_val = (status_val.lower() != "disabled") and (
                (m_fallback.group(6) or "yes").lower() != "no"
            )
            networks.append(
                WirelessNetwork(
                    profile_name=profile_name,
                    essid=essid,
                    clients=clients_val,
                    network_type=net_type,
                    band=m_fallback.group(4) or "",
                    status=status_val,
                    active=active_val,
                )
            )
            continue

        active_str = slice_col(row, "active").lower()
        status_val = slice_col(row, "status") or "Enabled"
        networks.append(
            WirelessNetwork(
                profile_name=profile_name,
                essid=essid,
                clients=int(clients_str),
                network_type=(net_type_slice or "employee"),
                band=slice_col(row, "band"),
                status=status_val,
                active=(status_val.lower() != "disabled") and (active_str != "no"),
            )
        )
    return networks


def _assign_radio_band(
    channel_val: str | None,
    util_val: int | None,
    slot_index: int,
    assigned: dict[str, tuple[str | None, int | None]],
) -> None:
    """Map radio0/radio1/radio2 channel + utilization to 2.4 GHz, 5 GHz, or 6 GHz."""
    if not channel_val or channel_val in ("-", "--"):
        return
    if channel_val.endswith("w") or slot_index == 2:
        assigned.setdefault("6 GHz", (channel_val, util_val))
        return
    digits = _METRIC_INT_RE.match(channel_val)
    if digits:
        num = int(digits.group(1))
        if num <= 14:
            assigned.setdefault("2.4 GHz", (channel_val, util_val))
            return
        assigned.setdefault("5 GHz", (channel_val, util_val))
        return
    fallback_band = "5 GHz" if slot_index == 0 else "2.4 GHz"
    assigned.setdefault(fallback_band, (channel_val, util_val))


def parse_aps(raw: str, sw_version: str | None = None) -> list[AccessPoint]:
    """Parse `show aps` output (singular or plural, with or without radio2)."""
    cleaned = strip_cli_envelope(raw)
    lines = cleaned.splitlines()
    header_idx: int | None = None
    for idx, line in enumerate(lines):
        if line.lstrip().startswith("Name") and "IP Address" in line and "Clients" in line:
            header_idx = idx
            break
    if header_idx is None:
        return []

    spans = _extract_column_spans(lines[header_idx])
    col_names = [name for name, _ in spans]
    ip_col_idx = next((i for i, n in enumerate(col_names) if n.lower() == "ip address"), None)
    if ip_col_idx is None:
        return []
    cols_from_ip = [n.lower() for n in col_names[ip_col_idx:]]
    ip_header_start = spans[ip_col_idx][1]
    col_starts = {name.lower(): start for name, start in spans}
    ordered_col_keys = [n.lower() for n in col_names]

    start_row = header_idx + 1
    if start_row < len(lines) and set(lines[start_row].replace(" ", "")) <= {"-"}:
        start_row += 1

    aps: list[AccessPoint] = []
    for row in lines[start_row:]:
        stripped = row.strip()
        if not stripped or set(stripped.replace(" ", "")) <= {"-"}:
            continue
        # Search near expected IP Address offset first so AP names like "AP-192.0.2.1" don't hijack column 0
        search_pos = max(0, ip_header_start - 2)
        ip_match = _IPV4_TOKEN_RE.search(row, pos=search_pos) or _IPV4_TOKEN_RE.search(row)
        if not ip_match:
            continue

        ap_name = row[: ip_match.start(1)].strip()
        tokens = row[ip_match.start(1) :].strip().split()
        if len(tokens) == len(cols_from_ip):
            row_map = dict(zip(cols_from_ip, tokens))
        else:
            # Header-offset slicing handles blank or multi-word Zone columns
            delta = ip_match.start(1) - ip_header_start
            row_map = {}
            for pos_i, key in enumerate(ordered_col_keys[ip_col_idx:], start=ip_col_idx):
                s_idx = max(0, col_starts[key] + delta)
                if pos_i + 1 < len(ordered_col_keys):
                    e_idx = max(s_idx, col_starts[ordered_col_keys[pos_i + 1]] + delta)
                    row_map[key] = row[s_idx:e_idx].strip()
                else:
                    row_map[key] = row[s_idx:].strip()
            if not row_map.get("ip address"):
                continue
        raw_ip = row_map.get("ip address", "")
        is_conductor = raw_ip.endswith("*") or (
            row_map.get("hierarchy mode", "").lower() in ("conductor", "master")
        )
        ip_clean = raw_ip.rstrip("*").strip()
        clients_val = parse_metric_int(row_map.get("clients")) or 0

        radios: dict[str, tuple[str | None, int | None]] = {}
        for slot in (0, 1, 2):
            ch_key = f"radio{slot} channel"
            ut_key = f"radio{slot} utilization (%)"
            if ch_key in row_map:
                _assign_radio_band(
                    row_map.get(ch_key),
                    parse_metric_int(row_map.get(ut_key)),
                    slot,
                    radios,
                )

        ch_5g, util_5g = radios.get("5 GHz", (None, None))
        ch_2g, util_2g = radios.get("2.4 GHz", (None, None))
        ch_6g, util_6g = radios.get("6 GHz", (None, None))

        aps.append(
            AccessPoint(
                name=ap_name or ip_clean,
                ip_address=ip_clean,
                serial=row_map.get("serial #", "").strip(),
                model=row_map.get("type", "").strip(),
                clients=clients_val,
                is_conductor=is_conductor,
                online=True,
                uptime_seconds=parse_uptime_seconds(row_map.get("age")),
                radio_5ghz_channel=ch_5g,
                radio_5ghz_utilization=util_5g,
                radio_2_4ghz_channel=ch_2g,
                radio_2_4ghz_utilization=util_2g,
                radio_6ghz_channel=ch_6g,
                radio_6ghz_utilization=util_6g,
                sw_version=sw_version,
            )
        )
    return aps


def _slice_mid_columns(
    row: str,
    header_starts: dict[str, int],
    ordered_keys: list[str],
    delta: int,
    tail_end: int,
) -> dict[str, str] | None:
    """Slice middle columns (OS, ESSID, Access Point, Channel, Type, Role) by offset+delta."""
    extracted: dict[str, str] = {}
    for key in ("os", "essid", "access point", "channel", "type", "role"):
        if key not in header_starts:
            return None
        pos = ordered_keys.index(key)
        start = max(0, header_starts[key] + delta)
        if key == "role":
            end = tail_end
        else:
            next_key = ordered_keys[pos + 1]
            end = max(start, header_starts[next_key] + delta)
        extracted[key] = row[start:end].strip() if start < len(row) else ""

    channel_val = extracted["channel"]
    if not channel_val or not _CHANNEL_TOKEN_RE.match(channel_val):
        return None
    if not extracted["type"] or " " in extracted["type"]:
        return None
    return extracted


_KNOWN_PHY_RE = re.compile(
    r"^(?:--|AC|AX|AN|GN|BG|HE|VHT|HT|A|B|G|(?:a|g|6GHz)-(?:HE|VHT|HT)|6E)$",
    re.IGNORECASE,
)


def _fallback_parse_mid(mid_text: str) -> dict[str, str]:
    """Fallback token parser for `OS .. Role` when wide middle fields shift columns."""
    tokens = mid_text.split()
    channel_idx: int | None = None
    # Scan right-to-left skipping Role tokens to find genuine (Channel, PHY Type) pair
    for idx in range(len(tokens) - 2, 0, -1):
        cand_ch = tokens[idx]
        cand_type = tokens[idx + 1]
        if _CHANNEL_TOKEN_RE.match(cand_ch) and _KNOWN_PHY_RE.match(cand_type):
            if cand_ch != "--" or cand_type == "--":
                channel_idx = idx
                break

    if channel_idx is None:
        return {"os": "", "essid": "", "access point": "", "channel": "--", "type": "--", "role": ""}

    role = " ".join(tokens[channel_idx + 2 :]).strip()
    channel = tokens[channel_idx]
    phy_type = tokens[channel_idx + 1]

    # Locate exact substring boundaries for OS / ESSID / Access Point via 2+ space splits
    ch_match = re.search(rf"\s{re.escape(channel)}\s+{re.escape(phy_type)}(?:\s|$)", mid_text)
    left_blob = mid_text[: ch_match.start()].strip() if ch_match else " ".join(tokens[:channel_idx])
    left_parts = [p.strip() for p in _MULTI_SPACE_SPLIT_RE.split(left_blob) if p.strip()]
    if len(left_parts) >= 3:
        os_val = left_parts[0]
        ap_val = left_parts[-1]
        essid_val = " ".join(left_parts[1:-1])
    elif len(left_parts) == 2:
        os_val = left_parts[0]
        essid_val = left_parts[1]
        ap_val = ""
    elif len(left_parts) == 1:
        os_val = left_parts[0]
        essid_val = ""
        ap_val = ""
    else:
        os_val = essid_val = ap_val = ""

    return {
        "os": os_val,
        "essid": essid_val,
        "access point": ap_val,
        "channel": channel,
        "type": phy_type,
        "role": role,
    }


def parse_clients(raw: str) -> list[WirelessClient]:
    """Parse `show clients` output robustly across multi-word fields and row overflow."""
    cleaned = strip_cli_envelope(raw)
    lines = cleaned.splitlines()
    header_idx: int | None = None
    for idx, line in enumerate(lines):
        if "IP Address" in line and "MAC Address" in line and "ESSID" in line:
            header_idx = idx
            break
    if header_idx is None:
        return []

    spans = _extract_column_spans(lines[header_idx])
    header_starts = {name.lower(): start for name, start in spans}
    ordered_keys = [name.lower() for name, _ in spans]
    ip_header_start = header_starts.get("ip address", 0)

    start_row = header_idx + 1
    if start_row < len(lines) and set(lines[start_row].replace(" ", "")) <= {"-"}:
        start_row += 1

    clients: list[WirelessClient] = []
    for row in lines[start_row:]:
        stripped = row.strip()
        if not stripped or set(stripped.replace(" ", "")) <= {"-"}:
            continue
        if stripped.lower().startswith(("number of clients", "total clients", "info:")):
            continue

        m_ip_mac = _CLIENT_IP_MAC_RE.search(row)
        if not m_ip_mac:
            continue

        client_name = row[: m_ip_mac.start(1)].strip()
        if client_name in ("-", "--", "---", "?"):
            client_name = ""
        ip_addr = m_ip_mac.group(1).strip()
        mac_addr = normalize_mac(m_ip_mac.group(2))

        tail_start = m_ip_mac.end()
        tail = row[tail_start:]
        m_right = re.search(r"\s+(\S+)\s+(\S+)\s+(\S+)\s*$", " " + tail)
        if not m_right:
            continue

        ipv6_raw, signal_raw, speed_raw = m_right.group(1), m_right.group(2), m_right.group(3)
        # m_right.start() is relative to (" " + tail), so slice boundary in `row` is tail_start + max(0, m_right.start() - 1)
        tail_end_in_row = tail_start + max(0, m_right.start() - 1)

        delta = m_ip_mac.start(1) - ip_header_start
        cols = _slice_mid_columns(row, header_starts, ordered_keys, delta, tail_end_in_row)
        if cols is None:
            cols = _fallback_parse_mid(row[tail_start:tail_end_in_row])

        essid = cols["essid"]
        if essid in ("-", "--"):
            essid = ""

        ipv6_addr: str | None = ipv6_raw.strip()
        if ipv6_addr in ("-", "--", "::", ""):
            ipv6_addr = None

        channel = cols["channel"]
        phy_type = cols["type"]
        band, is_wired = classify_band(channel, phy_type)
        if not essid and is_wired:
            essid = ""

        clients.append(
            WirelessClient(
                mac=mac_addr,
                ip_address=ip_addr,
                name=client_name,
                os=cols["os"],
                essid=essid,
                access_point=cols["access point"],
                channel=channel,
                phy_type=phy_type,
                role=cols["role"],
                ipv6_address=ipv6_addr,
                signal=parse_metric_int(signal_raw),
                speed_mbps=parse_metric_int(speed_raw),
                band=band,
                is_wired=is_wired,
            )
        )

    return clients


def build_snapshot(
    summary_raw: str,
    networks_raw: str,
    aps_raw: str,
    clients_raw: str,
    fetched_at: datetime | None = None,
) -> ClusterSnapshot:
    """Parse all four show outputs into a complete ClusterSnapshot."""
    summary = parse_summary(summary_raw)
    networks = parse_networks(networks_raw)
    aps = parse_aps(aps_raw, sw_version=summary.version)
    clients = parse_clients(clients_raw)
    master_ap = next((ap.name for ap in aps if ap.is_conductor), None)
    return ClusterSnapshot(
        summary=summary,
        networks=networks,
        access_points=aps,
        clients=clients,
        fetched_at=fetched_at,
        master_ap_name=master_ap,
    )


_UPGRADE_MAC_RE = re.compile(r"^((?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2})\s+")


def parse_image_version(raw: str) -> FirmwareImageInfo:
    """Parse `show image version` output into partition builds and AP image classes."""
    cleaned = strip_cli_envelope(raw)
    primary_version: str | None = None
    primary_build_time: str | None = None
    backup_version: str | None = None
    backup_build_time: str | None = None
    image_classes: list[str] = []

    in_classes = False
    saw_classes_rule = False
    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if lower == "ap images classes" or lower == "ap image classes":
            in_classes = True
            saw_classes_rule = False
            continue
        if in_classes:
            if set(stripped.replace(" ", "")) <= {"-"} or lower == "class":
                saw_classes_rule = True
                continue
            if saw_classes_rule:
                image_classes.append(stripped)
            continue

        if ":" not in stripped:
            continue
        key, val = stripped.split(":", 1)
        k_norm = key.strip().lower()
        v_clean = val.strip()
        if not v_clean:
            continue
        if k_norm == "primary partition build version":
            primary_version = v_clean
        elif k_norm == "primary partition build time":
            primary_build_time = v_clean
        elif k_norm == "backup partition build version":
            backup_version = v_clean
        elif k_norm == "backup partition build time":
            backup_build_time = v_clean

    return FirmwareImageInfo(
        primary_version=primary_version,
        primary_build_time=primary_build_time,
        backup_version=backup_version,
        backup_build_time=backup_build_time,
        image_classes=tuple(dict.fromkeys(image_classes)),
    )


def parse_version_details(raw: str) -> tuple[str | None, str | None]:
    """Extract ArubaOS model/version line and last reboot cause from `show version`."""
    cleaned = strip_cli_envelope(raw)
    model_version_line: str | None = None
    last_reboot_cause: str | None = None
    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("ArubaOS (") and model_version_line is None:
            model_version_line = stripped
        elif stripped.lower().startswith("reboot time and cause:"):
            cause = stripped.split(":", 1)[1].strip()
            if cause:
                last_reboot_cause = cause
    return model_version_line, last_reboot_cause


def parse_upgrade_info(raw: str) -> tuple[bool, bool | None, tuple[ApUpgradeEntry, ...]]:
    """Parse `show upgrade info` table and swarm upgrade flags."""
    cleaned = strip_cli_envelope(raw)
    lines = cleaned.splitlines()
    upgrade_in_progress = False
    auto_reboot: bool | None = None
    ap_entries: list[ApUpgradeEntry] = []

    header_idx: int | None = None
    for idx, line in enumerate(lines):
        if "IP Address" in line and "AP Class" in line and "Status" in line:
            header_idx = idx
            break

    col_spans = _extract_column_spans(lines[header_idx]) if header_idx is not None else []
    col_starts = {name.lower(): start for name, start in col_spans}
    ordered_cols = [name.lower() for name, _ in col_spans]

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if _UPGRADE_MAC_RE.match(stripped) and header_idx is not None and idx > header_idx and ordered_cols:
            row_map: dict[str, str] = {}
            for pos_i, key in enumerate(ordered_cols):
                s_idx = col_starts[key]
                if pos_i + 1 < len(ordered_cols):
                    e_idx = col_starts[ordered_cols[pos_i + 1]]
                    row_map[key] = line[s_idx:e_idx].strip()
                else:
                    row_map[key] = line[s_idx:].strip()
            mac = normalize_mac(row_map.get("mac", ""))
            ip_addr = row_map.get("ip address", "").rstrip("*").strip()
            if mac or ip_addr:
                ap_entries.append(
                    ApUpgradeEntry(
                        mac=mac,
                        ip_address=ip_addr,
                        ap_class=row_map.get("ap class", "").strip(),
                        status=row_map.get("status", "").strip(),
                        image_info=row_map.get("image info", "").strip(),
                        error_detail=row_map.get("error detail", "").strip(),
                    )
                )
            continue

        if ":" in stripped and (header_idx is None or idx > header_idx):
            key, val = stripped.split(":", 1)
            k_norm = key.strip().lower()
            v_norm = val.strip().lower()
            if k_norm == "upgrade in process":
                upgrade_in_progress = v_norm in ("yes", "true", "1", "enable", "enabled")
            elif k_norm == "auto reboot":
                if v_norm in ("enable", "enabled", "yes", "true"):
                    auto_reboot = True
                elif v_norm in ("disable", "disabled", "no", "false"):
                    auto_reboot = False

    return upgrade_in_progress, auto_reboot, tuple(ap_entries)


def parse_allowed_aps(raw: str) -> tuple[str, ...]:
    """Parse normalized wired AP MAC addresses from `show allowed-aps`."""
    cleaned = strip_cli_envelope(raw)
    if not cleaned:
        return ()
    lower = cleaned.lower()
    if "% parse error" in lower or "invalid input" in lower or "incomplete command" in lower:
        return ()
    macs: list[str] = []
    seen: set[str] = set()
    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.fullmatch(r"(?:[0-9a-fA-F]{2}[:\-]){5}[0-9a-fA-F]{2}", stripped):
            norm = normalize_mac(stripped)
            if norm not in seen:
                seen.add(norm)
                macs.append(norm)
    return tuple(macs)


def build_firmware_info(
    image_version_raw: str,
    version_raw: str,
    upgrade_info_raw: str,
    allowed_aps_raw: str = "",
) -> FirmwareImageInfo:
    """Combine `show image version`, `show version`, `show upgrade info`, and `show allowed-aps`."""
    img = parse_image_version(image_version_raw)
    model_version_line, last_reboot_cause = parse_version_details(version_raw)
    upgrade_in_progress, auto_reboot, ap_statuses = parse_upgrade_info(upgrade_info_raw)
    allowed_ap_macs = parse_allowed_aps(allowed_aps_raw)
    classes = list(img.image_classes)
    for entry in ap_statuses:
        if entry.ap_class and entry.ap_class not in classes:
            classes.append(entry.ap_class)
    return FirmwareImageInfo(
        primary_version=img.primary_version,
        primary_build_time=img.primary_build_time,
        backup_version=img.backup_version,
        backup_build_time=img.backup_build_time,
        image_classes=tuple(classes),
        model_version_line=model_version_line,
        last_reboot_cause=last_reboot_cause,
        upgrade_in_progress=upgrade_in_progress,
        auto_reboot=auto_reboot,
        ap_statuses=ap_statuses,
        allowed_ap_macs=allowed_ap_macs,
    )


def parse_image_check_xml(raw_xml: str) -> FirmwareCheckResult:
    """Parse InstantOS `swarm.cgi?opcode=image-check-status` XML safely."""
    text = (raw_xml or "").strip()
    if not text:
        return FirmwareCheckResult()
    lower_text = text.lower()
    if "<!doctype" in lower_text or "<!entity" in lower_text:
        return FirmwareCheckResult()
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return FirmwareCheckResult()

    values: dict[str, str | None] = {}
    for elem in root.findall(".//data"):
        key = elem.attrib.get("name", "").strip().lower()
        val = (elem.text or "").strip()
        if key:
            values[key] = val or None

    return FirmwareCheckResult(
        server_state=values.get("image server state"),
        server_message=values.get("image server message"),
        new_image_version=values.get("new image version"),
        new_image_url=values.get("new image url"),
        new_drt_version=values.get("new drt version"),
        new_drt_url=values.get("new drt url"),
    )


def enrich_snapshot_with_firmware(
    snapshot: ClusterSnapshot,
    firmware_info: FirmwareImageInfo | None,
    firmware_check: FirmwareCheckResult | None = None,
) -> ClusterSnapshot:
    """Attach cluster/AP firmware, MAC, AP class, and reboot cause to a snapshot."""
    if firmware_info is None and firmware_check is None:
        return snapshot

    by_ip: dict[str, ApUpgradeEntry] = {}
    by_mac: dict[str, ApUpgradeEntry] = {}
    if firmware_info is not None:
        for entry in firmware_info.ap_statuses:
            if entry.ip_address:
                by_ip[entry.ip_address.rstrip("*").strip()] = entry
            if entry.mac:
                by_mac[normalize_mac(entry.mac)] = entry

    default_class = (
        firmware_info.image_classes[0]
        if firmware_info and len(firmware_info.image_classes) == 1
        else None
    )
    single_ap = len(snapshot.access_points) == 1
    updated_aps: list[AccessPoint] = []
    for ap in snapshot.access_points:
        entry = by_ip.get(ap.ip_address.rstrip("*").strip()) or (
            by_mac.get(normalize_mac(ap.mac)) if ap.mac else None
        )
        ap_err: str | None = ap.upgrade_error
        if entry is not None:
            ap_err = None if entry.error_detail.lower() in ("", "none", "-") else entry.error_detail
        reboot_cause = ap.last_reboot_cause
        if entry is not None and entry.reboot_cause:
            reboot_cause = entry.reboot_cause
        elif not reboot_cause and firmware_info is not None and (ap.is_conductor or single_ap):
            reboot_cause = firmware_info.last_reboot_cause

        updated_aps.append(
            replace(
                ap,
                mac=(entry.mac if entry and entry.mac else ap.mac),
                ap_class=(entry.ap_class if entry and entry.ap_class else None) or ap.ap_class or default_class,
                primary_firmware=(entry.primary_version if entry and entry.primary_version else None)
                or ap.primary_firmware
                or (firmware_info.primary_version if firmware_info else None)
                or ap.sw_version,
                backup_firmware=(entry.backup_version if entry and entry.backup_version else None)
                or ap.backup_firmware
                or (firmware_info.backup_version if firmware_info else None),
                upgrade_status=(entry.status if entry and entry.status else None) or ap.upgrade_status,
                upgrade_error=ap_err,
                last_reboot_cause=reboot_cause,
            )
        )

    return replace(
        snapshot,
        access_points=updated_aps,
        firmware_info=firmware_info or snapshot.firmware_info,
        firmware_check=firmware_check or snapshot.firmware_check,
    )
