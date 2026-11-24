# Changelog

All notable changes to this project will be documented in this file.

## [0.2.0] - 2026-11-24

### Added
- **Per-AP Conductor indicator (`binary_sensor.aruba_<ap>_conductor`):** Shows directly inside each AP device card whether that physical AP currently holds the active Conductor / Master role (works in both Virtual Controller clusters and hybrid Multi-AP pools).
- **Per-AP hardware identity & radio channel history:**
  - Recorded `2.4 GHz`, `5 GHz`, and `6 GHz` radio channel sensors (`sensor.aruba_<ap>_*_channel`) to visualize ARM channel changes over time.
  - Per-AP `ip_address`, `mac_address`, `serial_number`, `model`, `ap_class`, and `is_conductor` attributes on `binary_sensor.aruba_<ap>_online`, plus dynamic `DeviceRegistry` sync for `sw_version`, `hw_version` (AP class), and `serial_number`.
- **Firmware, partition, DRT & reboot cause diagnostics:**
  - Parses `show image version`, `show version`, and `show upgrade info` at low cadence (`30 min`, or `30 s` while an upgrade is active) to expose `sensor.aruba_<ap>_firmware`, `sensor.aruba_<cluster>_firmware`, **Backup Partition** version, **AP Image Class**, **upgrade status**, and `last_reboot_cause` on `Last restart`.
  - Native **`update.aruba_<cluster>_firmware`** entity (`UpdateEntity`) reusing the existing `:4343` session on `swarm.cgi?opcode=image-check-status` with accurate 3-state reporting (`on`, `off`, or `unknown` on `image_check_internal` / offline APs) plus DRT metadata.
- **Opt-in AP & SSID write controls (`enable_write_controls`, default `false`):**
  - `button.aruba_<ap>_reboot` to restart an individual AP safely (`POST /rest/reboot` or standalone SSH `reload`).
  - `switch.aruba_<cluster>_ssid_<ssid>_enabled` keyed cleanly by `profile_name` to toggle guest or scheduled SSIDs (`POST /rest/ssid` or SSH CLI).
  - `aruba_instant.upgrade_firmware` service action for self-hosted HTTP/HTTPS firmware images.
- **`person.*`-safe stale client cleanup (`aruba_instant.purge_stale_clients`):** Persistent `last_seen` / `essid` tracking across restarts with `dry_run` preview, optional SSID filter, cascading roaming sensor cleanup, and strict protection for trackers bound to `person.*`.

### Changed
- Updated `README.md` MY Home Assistant badges to the native `2026.11+` `marketplace_repository` redirect and added an **Uninstall / Removal** section.
- Added firmware/DRT URL redaction keys to `diagnostics.py`.

## [0.1.5] - 2026-11-23

### Added
- Initial release for **Home Assistant `2026.11.0+`** under **Apache License 2.0** (`NOTICE` attribution included).
- Asynchronous transport adapters supporting **HTTPS REST API (`:4343`)**, native **`asyncssh` (`:22`)** wide PTY (`512x1000`), and **Auto** mode with periodic REST cooldown recovery.
- Full topology support: **InstantOS Virtual Controller clusters**, **Single Standalone APs**, and **Multi-AP Standalone / `AOS 10`** deployments (with per-AP credentials and mixed REST/SSH nodes).
- Interactive UI onboarding (`ConfigFlow`), live filter/timer tuning (`OptionsFlowWithReload`), targeted per-AP `Reauth`, and Multi-AP `Reconfigure` (`add_ap`, `edit_ap`, `remove_ap`).
- Explicit user confirmation flow (`confirm_enable_rest`) before executing `allow-rest-api` CLI commands over SSH, with graceful fallback to strictly read-only SSH.
- Dynamic SSID & client filtering (`tracked_ssids`, `unselected_ssid_mode`, `track_new_clients`, `track_unnamed_clients`, `include_wired_clients`, `require_ip`, `min_signal`, `exclude_macs`, `enable_roaming_sensors`), `consider_home` hysteresis (`180s` default), and away-device startup restoration.
- Strict `aruba_*` entity ID prefixing (`device_tracker.aruba_*`, `sensor.aruba_*`, `binary_sensor.aruba_*`), `via_device_id` hub-to-AP hierarchy, `2.4 GHz` / `5 GHz` / `6 GHz` radio utilization, AP uptime drift protection, and roaming `extra_state_attributes`.
- Full localization in **English**, **Deutsch**, and **Русский**, comprehensive PII/credential redaction in diagnostics, custom trademark-free brand icons, and `pytest` unit tests.
