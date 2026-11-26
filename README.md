# Aruba Instant / AOS Wi-Fi for Home Assistant

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="custom_components/aruba_instant/brand/dark_logo@2x.png">
    <source media="(prefers-color-scheme: light)" srcset="custom_components/aruba_instant/brand/logo@2x.png">
    <img alt="Aruba Instant / AOS Wi-Fi for Home Assistant" src="custom_components/aruba_instant/brand/logo@2x.png" width="560">
  </picture>
</p>

A modern, UI-configured replacement for the removed legacy built-in Home Assistant `aruba` integration — built for HPE Aruba Instant (`200 / 300 / 500 / 600 Series`, e.g. `IAP-205`, `IAP-315`, `IAP-325`, `AP-505`, `AP-515`, `AP-635`) clusters and `AOS 10` access points, delivering fast local Wi-Fi presence detection and real-time AP telemetry.

[![Open your Home Assistant instance and open a repository in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=MixasikM&repository=ha-aruba-instant&category=integration)

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.11.0%2B-18BCF2.svg)](https://www.home-assistant.io/)
[![Release](https://img.shields.io/badge/Version-0.3.1-blue.svg)](https://github.com/MixasikM/ha-aruba-instant/releases)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

> **Disclaimer:** This is an independent, unofficial community integration and is not affiliated with, endorsed by, or sponsored by Hewlett Packard Enterprise (HPE) or Aruba Networking. *Aruba*, *Aruba Instant*, *ArubaOS*, and related product names are trademarks of Hewlett Packard Enterprise Development LP. Use at your own risk.

---

## Features

- **Fast local presence & clean startup restoration:** Tracks Wi-Fi clients locally with configurable `consider_home` hysteresis (`180s` default) to absorb brief phone Wi-Fi sleep, and restores offline trackers from the Entity Registry at startup as `not_home` rather than `unavailable`.
- **Transient-fault resilience:** Tolerates up to 2 consecutive transient poll failures during brief controller failovers without flashing entities `unavailable`.
- **Flexible deployment topologies & automatic controller failover:** Supports **Virtual Controller clusters**, **single standalone APs**, and **Multi-AP `AOS 8` / `AOS 10` pools**. Even if you enter the IP address of an ordinary member AP instead of a shared cluster IP, Home Assistant discovers the whole cluster, uses the shared cluster IP when reachable (or follows whichever AP is currently leading the cluster if the shared IP is unreachable), and switches over seamlessly if leadership moves to another AP.
- **Offline Access Point awareness:** Unplugged APs still present in your cluster's allowed AP list stay cleanly visible as **Offline** (`off`) with their last known IP address—even across Home Assistant restarts—while APs you permanently remove from the cluster allowlist are cleaned up automatically.
- **Smart client & SSID filtering:** Pre-fills **Tracked SSIDs** from your APs and filters by **Minimum SNR**, **Require assigned IPv4 address**, **Excluded MAC addresses**, unnamed clients, and **Include wired AP downlink clients** so IoT clutter never floods `device_tracker`.
- **Cluster, SSID & per-AP telemetry:** Exposes cluster-wide client counts, active Conductor / Master AP (both hub and per-AP `binary_sensor.aruba_<ap>_conductor`), per-SSID client counts, AP connectivity and hardware identity, per-AP client counts, `2.4 / 5 / 6 GHz` channel utilization and recorded radio channel history (`sensor.aruba_<ap>_*_channel`), AP boot timestamp (`Last restart`) with reboot cause, plus low-cadence firmware, backup partition, DRT, and native `UpdateEntity` diagnostics.
- **Recorder-friendly live roaming attributes & safe cleanup:** Publishes `essid`, `access_point`, `band`, `channel`, `phy_type`, `signal`, `speed_mbps`, `os`, `role`, and `ipv6` while keeping `signal` and `speed_mbps` live in the UI and excluded from Recorder history (`_unrecorded_attributes`), plus opt-in per-client roaming sensors and `person.*`-safe stale client cleanup (`aruba_instant.purge_stale_clients`).
- **Isolated `aruba_*` entity namespace & opt-in write controls:** Assigns deterministic `aruba_*` entity IDs (`device_tracker.aruba_*`, `sensor.aruba_*`, `binary_sensor.aruba_*`, `update.aruba_*`, plus opt-in `button.aruba_*` AP reboot, `switch.aruba_*` SSID toggle, and `aruba_instant.upgrade_firmware`) to prevent entity ID collisions across your Home Assistant setup.
- **Read-only polling & redacted diagnostics:** Runs strictly read-only local polling without modifying your AP configuration, and includes one-click anonymized **Download diagnostics** with credentials, session tokens, MACs, IPs, AP names, and SSIDs automatically redacted.
- **Full UI & multi-language support:** Guided setup (`ConfigFlow`), live tuning (`OptionsFlow`), targeted per-AP **Reauth**, and **Reconfigure** localized in **English**, **Deutsch**, and **Русский**.

---

## Supported Hardware

| Topology / Mode | Supported Software | Transport Options |
| :--- | :--- | :--- |
| **Virtual Controller Cluster (IAP)** | InstantOS `8.4 – 8.13` | `Auto` (prefers HTTPS REST `:4343`, falls back to SSH `:22`) |
| **Single Standalone Instant AP** | InstantOS `8.4 – 8.13` | `Auto`, HTTPS REST `:4343`, or SSH `:22` |
| **Older Instant Clusters** | InstantOS `6.4 / 6.5 / 8.0–8.3` | SSH `:22` (`asyncssh` with automatic SHA-1 KEX fallback) |
| **Multi-AP Standalone / `AOS 10`** | `AOS 8` / `AOS 10` APs (no Virtual Controller) | Parallel Multi-AP polling across AP hosts (supports shared or per-AP credentials and mixed REST/SSH nodes) |

> **Note — Not for Aruba *Instant On*:** This integration targets enterprise/campus **HPE Aruba Instant (`AOS 8.x`)** and **`AOS 10`** access points exposing a local HTTPS REST API (`:4343`) or SSH CLI (`:22`). Cloud-managed **Aruba Instant On** SMB devices do not expose these local interfaces.
>
> **Safe alongside Aruba IoT & BLE Telemetry:** Polling is strictly read-only and never alters IoT radio profiles or BLE WebSocket telemetry streams configured on your APs.

---

## Installation

### Option 1: Home Assistant Marketplace / HACS (Recommended)

Click the button below to open **Aruba Instant / AOS Wi-Fi** directly in your Home Assistant instance:

[![Open your Home Assistant instance and open a repository in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=MixasikM&repository=ha-aruba-instant&category=integration)

Or install it from the default catalog in the Home Assistant UI:
1. Open **Settings -> Marketplace** (or **HACS -> Integrations**).
2. Search for **Aruba Instant / AOS Wi-Fi** and install it (`Home Assistant 2026.11.0+`).
3. Proceed to **Setup & Configuration** below *(restarting Home Assistant is only needed when updating an already loaded version)*.

> **Fallback — Custom Repository:** If your local catalog index has not synced yet, open **Settings -> Marketplace** (or **HACS -> Integrations**) -> top-right menu -> **Custom repositories**, add `https://github.com/MixasikM/ha-aruba-instant` with category **Integration**, and install **Aruba Instant / AOS Wi-Fi**.

### Option 2: Manual Installation

1. Download the latest release from [GitHub Releases](https://github.com/MixasikM/ha-aruba-instant/releases).
2. Copy `custom_components/aruba_instant` into your Home Assistant `/config/custom_components/aruba_instant` directory.
3. Restart Home Assistant (`2026.11.0+`).

---

## Setup & Configuration

1. Go to **Settings -> Devices & Services -> Add Integration** and search for **Aruba Instant / AOS Wi-Fi**.
2. Choose your topology:
   - **Virtual Controller cluster or Single AP**: enter your Virtual Controller VIP (recommended for Instant clusters) or standalone AP IP/hostname.
   - **Multiple Standalone / AOS 10 APs (no Virtual Controller)**: set a **Network / site name** and add every AP IP/hostname for parallel polling. Supports shared credentials (with automatic per-AP prompt fallback if an AP uses a different password) or **Access Points use different credentials**, mixed REST/SSH nodes, and day-2 **Reconfigure** (**Add / Edit / Remove Access Point**) without breaking `person.*` bindings.
3. Select **Transport mode** (**Auto**, **HTTPS REST API (`:4343`)**, or **SSH Read-Only (`:22`)**), enter credentials, and optionally adjust **Advanced network settings** (**HTTPS REST port** `4343`, **SSH port** `22`, **Verify SSL certificate** `false` by default, **Legacy SSH algorithm fallback** `false`).
   - *Tip for `AOS 10` / Aruba Central-managed APs:* Central-managed or read-only accounts cannot enable local REST settings via CLI — select **SSH Read-Only (`:22`)** directly.
4. If the AP's REST API is disabled, the setup flow offers to enable it with your explicit confirmation or continue in read-only SSH mode.
5. Configure **SSID & client tracking filters** (also editable live anytime via **Configure**):
   - **Tracked SSIDs** — pre-selected from discovered networks; leave empty to track all SSIDs, keep only human/client SSIDs selected to exclude IoT SSIDs, or type custom/scheduled SSIDs
   - **Unselected SSID behavior** — **Ignore completely (do not create entities)** vs. **Create disabled in Entity Registry**
   - **Automatically track newly discovered clients** — default `true`
   - **Track clients without a hostname** — default `true`
   - **Include wired AP downlink clients** — default `false`
   - **Require assigned IPv4 address** — default `false` (ignores `0.0.0.0`)
   - **Minimum SNR signal threshold** — default `0` (disabled; range `0–99`)
   - **Excluded MAC addresses** — comma-separated MAC list
   - **Create per-client roaming sensors (AP & SSID)** — default `false`
   - **Enable AP & SSID write controls** — default `false` (keep off for strictly read-only polling; opt-in enables SSID toggle switches, AP reboot buttons, and firmware upgrade actions)
   - **Polling interval (seconds)** — default `15s` (range `5–300s`)
   - **Consider home duration (seconds)** — default `180s` (range `0–1800s`)

---

## Entities & Attributes

All entities use a deterministic **`aruba_`** entity ID prefix (`device_tracker.aruba_*`, `sensor.aruba_*`, `binary_sensor.aruba_*`, `update.aruba_*`, `button.aruba_*`, `switch.aruba_*`) so they never collide with other trackers or area-prefixed entities.

### Cluster / Site Hub (`Aruba <Cluster / Site>`)
- `sensor.aruba_<cluster>_total_clients` — Total active clients across the cluster/site
- `sensor.aruba_<cluster>_master_ap` — Current Conductor / Master AP name *(Virtual Controller / Cluster mode only)*
- `sensor.aruba_<cluster>_firmware` — Active primary firmware build with `backup_firmware`, `image_classes`, `model_version`, `last_reboot_cause`, `upgrade_in_progress`, and `drt_version` attributes *(Virtual Controller / Cluster mode only)*
- `update.aruba_<cluster>_firmware` — Native HA update entity for cluster firmware (`on`, `off`, or `unknown` when offline / check unavailable) with live upgrade `in_progress` tracking, single-line `release_summary`, on-click `RELEASE_NOTES` firmware summary & upgrade guidance, and automatic InstantOS `8.6` / `8.10+` release-train `release_url` links *(Virtual Controller / Cluster mode only)*
- `sensor.aruba_<cluster>_ssid_<ssid>_clients` — Active client count per discovered SSID
- `switch.aruba_<cluster>_ssid_<ssid>_enabled` — Opt-in SSID broadcast toggle *(created only when **Enable AP & SSID write controls** is enabled)*

### Access Point Devices (`Aruba <AP>`)
- `binary_sensor.aruba_<ap>_online` — Reachability / online status in the cluster or Multi-AP pool (`ip_address`, `mac_address`, `serial_number`, `model`, `ap_class`, `is_conductor` attributes)
- `binary_sensor.aruba_<ap>_conductor` — Diagnostic indicator showing whether this AP currently holds the Conductor / Master role
- `sensor.aruba_<ap>_ip_address` — AP management IPv4/IPv6 address *(diagnostic; retained when an AP temporarily goes offline)*
- `sensor.aruba_<ap>_clients` — Connected clients on this AP
- `sensor.aruba_<ap>_5ghz_utilization` — 5 GHz channel utilization (`%`)
- `sensor.aruba_<ap>_2_4ghz_utilization` — 2.4 GHz channel utilization (`%`)
- `sensor.aruba_<ap>_6ghz_utilization` — 6 GHz channel utilization (`%`, created when a 6 GHz radio is reported)
- `sensor.aruba_<ap>_5ghz_channel` — Active 5 GHz radio channel *(diagnostic, recorded for channel history)*
- `sensor.aruba_<ap>_2_4ghz_channel` — Active 2.4 GHz radio channel *(diagnostic, recorded for channel history)*
- `sensor.aruba_<ap>_6ghz_channel` — Active 6 GHz radio channel *(diagnostic, recorded when a 6 GHz radio is reported)*
- `sensor.aruba_<ap>_firmware` — Active AP firmware build (`backup_firmware`, `ap_class`, `upgrade_status`, `upgrade_error`, `serial_number`, `mac_address` attributes)
- `sensor.aruba_<ap>_uptime` — AP last boot timestamp (`timestamp`, diagnostic; suppresses up to `120s` drift, includes `last_reboot_cause` attribute)
- `button.aruba_<ap>_reboot` — Opt-in AP restart button *(created only when **Enable AP & SSID write controls** is enabled)*

### Services / Actions
- `aruba_instant.purge_stale_clients` — Inspect (`dry_run: true` default) or clean up stale `device_tracker.aruba_*` entries older than `stale_days` (optionally filtered by `ssid`). Uses persistent `last_seen` storage and protects trackers bound to `person.*` entities (`skip_person_bound: true` default).
- `aruba_instant.upgrade_firmware` — Trigger an InstantOS upgrade from a self-hosted HTTP/HTTPS firmware URL *(requires **Enable AP & SSID write controls** enabled)*.

### Client Devices (`device_tracker.aruba_<client>`)
- **State:** `home` / `not_home` (away devices are restored from the Entity Registry at startup as `not_home` rather than `unavailable`; up to 2 consecutive transient poll failures preserve the last snapshot before marking `unavailable`)
- **`extra_state_attributes`:**
  `essid`, `access_point`, `band` (`2.4 GHz` / `5 GHz` / `6 GHz` / `Wired`), `channel`, `phy_type`, `signal` (SNR dB), `speed_mbps`, `os`, `role`, `ipv6` (`signal` and `speed_mbps` remain live in the UI while excluded from Home Assistant Recorder history via `_unrecorded_attributes`)
- **Optional diagnostic sensors** (when **Create per-client roaming sensors (AP & SSID)** is enabled):
  `sensor.aruba_<client>_access_point`, `sensor.aruba_<client>_ssid`

> **Two-stage departure timing (Aruba WLAN aging + HA `consider_home`):** When a Wi-Fi client leaves without sending an explicit 802.11 deauth frame, departure detection runs in two stages: (1) Aruba InstantOS retains the association in its active client table until the WLAN SSID profile's station inactivity timer expires (`inactivity-timeout` under `wlan ssid-profile`, `1000s` factory default in InstantOS), and (2) Home Assistant's **Consider home duration** (`180s` default in **Configure**) adds a sleep-hysteresis buffer after the client leaves the AP client table. Both stages can be tuned independently depending on how quickly you want `not_home` transitions.
>
> **iOS / Android MAC Randomization Tip:** Modern phones often generate a different **Private / Randomized Wi-Fi MAC** per SSID (or rotate MACs). Disable private MAC randomization (**Use Device / Phone MAC**) for your home SSID(s) so `device_tracker.aruba_*` stays bound cleanly to your `person.*` entity.

---

## Troubleshooting

- **Self-signed certificate errors on `:4343`:** Keep **Verify SSL certificate** disabled (`false`) unless you uploaded a trusted CA certificate to your Aruba Virtual Controller or AP.
- **HTTP `503` even when REST API is enabled:** Aruba Instant APs limit concurrent REST sessions (typically 5–8 slots). Avoid sharing one admin login across multiple external polling scripts; the integration caches one session and logs out cleanly on reload or unload.
- **Phones flapping `not_home` during brief Wi-Fi sleep:** Increase **Consider home duration (seconds)** in **Configure** (e.g., `240`–`300` seconds; default is `180`). Conversely, decrease to `60` or `0` if Aruba's AP aging already provides enough buffer.
- **Older InstantOS 6.x / early 8.x SSH handshake errors:** Enable **Legacy SSH algorithm fallback** in **Advanced network settings** (`Auto` / `SSH` also retries SHA-1 KEX negotiation automatically).
- **Changed password on one AP in a Multi-AP pool:** Use the integration's **Reauth** flow to update credentials only for the failing AP (or apply across all nodes) without removing entities or breaking `person.*` links.
- **How cluster failover works without a dedicated Virtual Controller IP:** You don't have to guess which AP is currently in charge. Point the integration at any AP in the cluster—on the first check it finds the active cluster leader, learns the addresses of every other AP in the cluster, and saves them locally. If your cluster has a working shared Virtual Controller IP, it upgrades to that shared IP automatically; if that shared IP is unreachable across your VLANs or firewall, polling continues directly through the active leader, periodically re-checks the shared IP in the background, raises a non-blocking notice in **Settings -> System -> Repairs** after repeated failures, and clears the warning automatically as soon as the shared IP becomes reachable again.

---

## Removal / Uninstall

1. Go to **Settings -> Devices & Services -> Aruba Instant / AOS Wi-Fi** and delete the integration entry (entries). Home Assistant removes all associated devices, entities, and stored config entry data automatically.
2. Open **Settings -> Marketplace** (or **HACS -> Integrations**), select **Aruba Instant / AOS Wi-Fi**, click the menu -> **Remove**, and restart Home Assistant if prompted (for manual installs, delete `/config/custom_components/aruba_instant` and restart).

---

## License & Attribution

Licensed under the **Apache License, Version 2.0** ([LICENSE](LICENSE)).
See [NOTICE](NOTICE) for required copyright and attribution notices (`Copyright 2026 MixasikM`).
