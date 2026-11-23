# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Pure Python CLI & REST output parsers for HPE Aruba Instant / AOS Wi-Fi."""
from __future__ import annotations

from datetime import datetime
import re

from .models import (
    AccessPoint,
    ClusterSnapshot,
    ClusterSummary,
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
            networks.append(
                WirelessNetwork(
                    profile_name=profile_name,
                    essid=essid,
                    clients=clients_val,
                    network_type=net_type,
                    band=m_fallback.group(4) or "",
                    status=m_fallback.group(5) or "Enabled",
                    active=(m_fallback.group(6) or "yes").lower() != "no",
                )
            )
            continue

        active_str = slice_col(row, "active").lower()
        networks.append(
            WirelessNetwork(
                profile_name=profile_name,
                essid=essid,
                clients=int(clients_str),
                network_type=(net_type_slice or "employee"),
                band=slice_col(row, "band"),
                status=slice_col(row, "status") or "Enabled",
                active=active_str != "no",
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
