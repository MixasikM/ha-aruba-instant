# Contributing to Aruba Instant / AOS Wi-Fi for Home Assistant

Thank you for helping improve **Aruba Instant / AOS Wi-Fi**! Bug reports, hardware/firmware compatibility notes, translations, and pull requests are welcome.

## Reporting Bugs & Hardware Compatibility

When opening an issue or sharing compatibility results for an Aruba AP model or firmware release, please include:

- **AP model(s) & firmware:** e.g. `IAP-325` / `AP-505` / `AP-635` running `InstantOS 8.x`, `6.x`, or `AOS 10`
- **Topology & transport:** `Virtual Controller / Single AP` vs. `Multiple Standalone / AOS 10 APs`, and `Auto`, `HTTPS REST (:4343)`, or `SSH (:22)`
- **Diagnostics file:** Download from **Settings → Devices & Services → Aruba Instant / AOS Wi-Fi → ⋮ → Download diagnostics**

> **Privacy rule:** Never paste real home IP addresses, real MAC addresses, BSSIDs, serial numbers, passwords/tokens, or real SSIDs into issues, pull requests, or test fixtures. **Download diagnostics** automatically redacts sensitive fields. For manual CLI samples or test data, replace values with documentation test ranges (`192.0.2.x`, `198.51.100.x`, `2001:db8::/32`), synthetic MACs (`aa:bb:cc:11:22:33`), and generic names (`CorpWiFi`, `Guest WiFi`, `AP-Lobby`).

## Pull Request & Architecture Guidelines

Please keep changes aligned with the project architecture and Home Assistant `2026.11+` standards:

- **Hexagonal boundaries:** `custom_components/aruba_instant/engine/` and `custom_components/aruba_instant/ports/` must remain pure Python with zero `homeassistant` imports.
- **Read-only hardware safety:** Polling must strictly use read-only `show` commands; any configuration action (such as enabling `allow-rest-api`) must require explicit user confirmation in `ConfigFlow`.
- **Entity isolation & HA compliance:** Preserve the `aruba_` entity ID prefix, explicit `DeviceInfo` with `via_device_id`, and `_unrecorded_attributes` on high-churn telemetry (`signal`, `speed_mbps`).
- **Localization parity:** Keep 100% key and `{placeholder}` parity across `strings.json` (must match `translations/en.json`), `translations/de.json`, and `translations/ru.json`, with no HTML angle-bracket tags (`hassfest`).
- **License & SPDX header:** Add the standard two-line copyright and `SPDX-License-Identifier: Apache-2.0` header to every Python file under `custom_components/aruba_instant/` and `tests/`:
  ```python
  # Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
  # SPDX-License-Identifier: Apache-2.0
  ```

## Running Tests Locally

The unit test suite runs in under one second with lightweight Home Assistant stubs (no Docker container required):

```bash
python -m pip install pytest aiohttp voluptuous pillow propcache yarl asyncssh
pytest -v
```

Every pull request automatically runs `pytest`, `hassfest`, and `hacs/action` via GitHub Actions.

## License & Contribution Agreement

By submitting a pull request or patch to this repository, you agree that your contributions will be licensed under the **Apache License, Version 2.0** ([LICENSE](LICENSE), Section 5).
