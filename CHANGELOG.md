# Changelog

All notable changes to this project will be documented in this file.

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
