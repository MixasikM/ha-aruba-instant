# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Constants for the Aruba Instant / AOS Wi-Fi integration."""
from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN = "aruba_instant"

PLATFORMS: list[Platform] = [
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
]

# Topology & transport modes
TOPOLOGY_CLUSTER = "cluster"
TOPOLOGY_MULTI_AP = "multi_ap"

TRANSPORT_AUTO = "auto"
TRANSPORT_REST = "rest"
TRANSPORT_SSH = "ssh"

# Config entry data keys
CONF_TOPOLOGY_MODE = "topology_mode"
CONF_SITE_NAME = "site_name"
CONF_NETWORK_NAME = "network_name"
CONF_NODES = "nodes"
CONF_HOSTS = "hosts"
CONF_TRANSPORT = "transport"
CONF_REST_PORT = "rest_port"
CONF_SSH_PORT = "ssh_port"
CONF_VERIFY_SSL = "verify_ssl"
CONF_LEGACY_SSH = "legacy_ssh"
CONF_AP_ID = "ap_id"
CONF_AP_NAME = "name"
CONF_SEPARATE_CREDENTIALS = "separate_credentials_per_ap"
CONF_APPLY_TO_ALL_NODES = "apply_to_all_nodes"
CONF_TARGET_HOST = "target_host"
CONF_CONFIRM_ACTION = "confirm_action"

CONFIRM_ENABLE_REST = "enable_rest"
CONFIRM_USE_SSH_READONLY = "ssh_readonly"

# Defaults
DEFAULT_REST_PORT = 4343
DEFAULT_SSH_PORT = 22
DEFAULT_VERIFY_SSL = False
DEFAULT_LEGACY_SSH = False
DEFAULT_SITE_NAME = "Site Wi-Fi"
DEFAULT_SCAN_INTERVAL = 15
DEFAULT_CONSIDER_HOME = 180
DEFAULT_MIN_SIGNAL = 0

# Options & filter keys
CONF_TRACKED_SSIDS = "tracked_ssids"
CONF_UNSELECTED_SSID_MODE = "unselected_ssid_mode"
CONF_TRACK_NEW_CLIENTS = "track_new_clients"
CONF_TRACK_UNNAMED_CLIENTS = "track_unnamed_clients"
CONF_INCLUDE_WIRED_CLIENTS = "include_wired_clients"
CONF_REQUIRE_IP = "require_ip"
CONF_MIN_SIGNAL = "min_signal"
CONF_EXCLUDE_MACS = "exclude_macs"
CONF_ENABLE_ROAMING_SENSORS = "enable_roaming_sensors"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_CONSIDER_HOME = "consider_home"

UNSELECTED_SSID_IGNORE = "ignore"
UNSELECTED_SSID_DISABLE = "disable"

UPTIME_BOOT_DRIFT_TOLERANCE_SECONDS = 120
MAX_TRANSIENT_POLL_FAILURES: Final = 2
