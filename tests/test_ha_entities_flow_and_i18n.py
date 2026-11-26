# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Tests for HA ConfigFlow/OptionsFlow, entities, diagnostics, HACS/hassfest, i18n, and privacy."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Any

from PIL import Image
import pytest

from custom_components.aruba_instant import (
    async_remove_config_entry_device,
    async_remove_entry,
    async_setup,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.aruba_instant.adapters.rest_client import _redact_sid
from custom_components.aruba_instant.binary_sensor import (
    ArubaApConductorBinarySensor,
    ArubaApOnlineBinarySensor,
    async_setup_entry as async_setup_binary_sensor,
)
from custom_components.aruba_instant.button import (
    ArubaApRebootButton,
    async_setup_entry as async_setup_button,
)
from custom_components.aruba_instant.config_flow import (
    ArubaInstantConfigFlow,
    ArubaOptionsFlow,
    _clean_host,
)
from custom_components.aruba_instant.const import (
    CONF_APPLY_TO_ALL_NODES,
    CONF_CONFIRM_ACTION,
    CONF_CONSIDER_HOME,
    CONF_ENABLE_ROAMING_SENSORS,
    CONF_ENABLE_WRITE_CONTROLS,
    CONF_EXCLUDE_MACS,
    CONF_HOSTS,
    CONF_NETWORK_NAME,
    CONF_NODES,
    CONF_SEPARATE_CREDENTIALS,
    CONF_SITE_NAME,
    CONF_TARGET_HOST,
    CONF_TOPOLOGY_MODE,
    CONF_TRACK_NEW_CLIENTS,
    CONF_TRACKED_SSIDS,
    CONF_TRANSPORT,
    CONFIRM_ENABLE_REST,
    CONFIRM_USE_SSH_READONLY,
    DOMAIN,
    SERVICE_PURGE_STALE_CLIENTS,
    TOPOLOGY_CLUSTER,
    TOPOLOGY_MULTI_AP,
    TRANSPORT_AUTO,
    TRANSPORT_REST,
    TRANSPORT_SSH,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from custom_components.aruba_instant.coordinator import ArubaDataUpdateCoordinator
from custom_components.aruba_instant.device_tracker import (
    ArubaClientScannerEntity,
    async_setup_entry as async_setup_dt,
)
from custom_components.aruba_instant.diagnostics import async_get_config_entry_diagnostics
from custom_components.aruba_instant.entity import build_aruba_object_id
from custom_components.aruba_instant.ports.cluster_client import (
    ArubaAuthError,
    ArubaConnectionError,
    ArubaReadOnlyCliError,
    ArubaRestApiDisabledError,
)
from custom_components.aruba_instant.sensor import (
    ArubaApFirmwareSensor,
    ArubaApUptimeSensor,
    ArubaClientAccessPointSensor,
    ArubaClientSsidSensor,
    ArubaClusterFirmwareSensor,
    ArubaClusterMasterApSensor,
    ArubaSsidClientsSensor,
    async_setup_entry as async_setup_sensor,
)
from custom_components.aruba_instant.switch import (
    ArubaSsidEnabledSwitch,
    async_setup_entry as async_setup_switch,
)
from custom_components.aruba_instant.update import (
    ArubaClusterFirmwareUpdateEntity,
    async_setup_entry as async_setup_update,
)
from homeassistant.components.update import UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from tests.fixtures.aruba_8_10_samples import (
    IMAGE_CHECK_ERROR_XML,
    IMAGE_CHECK_SUCCESS_EMPTY_XML,
    IMAGE_CHECK_UPDATE_XML,
    SHOW_APS_OUTPUT,
    SHOW_CLIENTS_OUTPUT,
    SHOW_IMAGE_VERSION_OUTPUT,
    SHOW_NETWORK_OUTPUT,
    SHOW_SUMMARY_OUTPUT,
    SHOW_SUMMARY_STANDALONE_AOS10,
    SHOW_UPGRADE_INFO_OUTPUT,
    SHOW_VERSION_OUTPUT,
)
from custom_components.aruba_instant.engine.parsers import (
    build_firmware_info,
    build_snapshot,
    parse_image_check_xml,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
INTEGRATION_DIR = REPO_ROOT / "custom_components" / "aruba_instant"

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")


_HTML_TAG_RE = re.compile(r"<[a-z].*?>", re.IGNORECASE)
_SPDX_HEADER = (
    "# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)\n"
    "# SPDX-License-Identifier: Apache-2.0\n"
)


def _flatten_keys_and_placeholders(obj: Any, prefix: str = "") -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            full = f"{prefix}.{k}" if prefix else k
            out.update(_flatten_keys_and_placeholders(v, full))
    elif isinstance(obj, str):
        out[prefix] = set(_PLACEHOLDER_RE.findall(obj))
    return out


def _flatten_strings(obj: Any) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            items.extend((f"{k}.{sub_k}" if sub_k else k, val) for sub_k, val in _flatten_strings(v))
    elif isinstance(obj, str):
        items.append(("", obj))
    return items


def test_manifest_hacs_brand_and_i18n_parity() -> None:
    manifest = json.loads((INTEGRATION_DIR / "manifest.json").read_text(encoding="utf-8"))
    keys = list(manifest.keys())
    assert keys[0] == "domain"
    assert keys[1] == "name"
    assert keys[2:] == sorted(keys[2:]), "manifest.json keys after name must be alphabetical"
    assert manifest["integration_type"] == "hub"
    assert manifest["version"] == "0.3.0"
    assert manifest["categories"] == [
        "computers_network.routers_network",
        "presence_location.network_presence",
    ]

    hacs = json.loads((REPO_ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert "render_readme" not in hacs
    assert hacs["homeassistant"] == "2026.11.0"
    assert hacs["zip_release"] is True
    assert hacs["filename"] == "aruba_instant.zip"

    license_text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in license_text and "Version 2.0" in license_text
    notice_text = (REPO_ROOT / "NOTICE").read_text(encoding="utf-8")
    assert "MixasikM" in notice_text and "Apache License, Version 2.0" in notice_text

    for py_path in sorted([*INTEGRATION_DIR.rglob("*.py"), *(REPO_ROOT / "tests").rglob("*.py")]):
        assert py_path.read_text(encoding="utf-8").startswith(
            _SPDX_HEADER
        ), f"Missing Apache-2.0 SPDX header in {py_path}"

    brand_specs = (
        ("icon.png", (256, 256)),
        ("icon@2x.png", (512, 512)),
        ("logo.png", (512, 160)),
        ("logo@2x.png", (1024, 320)),
        ("dark_logo.png", (512, 160)),
        ("dark_logo@2x.png", (1024, 320)),
    )
    for fname, expected_size in brand_specs:
        with Image.open(INTEGRATION_DIR / "brand" / fname) as im:
            assert im.size == expected_size
            assert im.mode == "RGBA"
            assert not im.info

    strings_raw = (INTEGRATION_DIR / "strings.json").read_text(encoding="utf-8")
    en_raw = (INTEGRATION_DIR / "translations" / "en.json").read_text(encoding="utf-8")
    assert strings_raw == en_raw, "strings.json must match translations/en.json"

    base_obj = json.loads(strings_raw)
    base_map = _flatten_keys_and_placeholders(base_obj)
    for key_path, text_val in _flatten_strings(base_obj):
        assert not _HTML_TAG_RE.search(text_val), f"HTML-like tag rejected by hassfest at {key_path}: {text_val}"

    for lang in ("de", "ru"):
        lang_json = json.loads(
            (INTEGRATION_DIR / "translations" / f"{lang}.json").read_text(encoding="utf-8")
        )
        lang_map = _flatten_keys_and_placeholders(lang_json)
        assert set(lang_map.keys()) == set(base_map.keys()), f"Key mismatch in {lang}.json"
        for k, phs in base_map.items():
            assert lang_map[k] == phs, f"Placeholder mismatch at {lang}:{k}"
        for key_path, text_val in _flatten_strings(lang_json):
            assert not _HTML_TAG_RE.search(text_val), f"HTML-like tag at {lang}:{key_path}: {text_val}"


def test_public_release_files_zero_private_leaks() -> None:
    """Ensure all public release files contain zero RFC 1918 private IPs or local OS paths."""
    forbidden_re = re.compile(
        r"(?:"
        r"\b(?:10\.(?:\d{1,3}\.){2}\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
        r"|/(?:home|mnt|Users)/[A-Za-z0-9_.-]+"
        r"|[A-Z]:\\(?:Users|Projects)\\"
        r")"
    )
    public_targets = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "CHANGELOG.md",
        REPO_ROOT / "CONTRIBUTING.md",
        REPO_ROOT / "hacs.json",
        REPO_ROOT / "LICENSE",
        REPO_ROOT / "NOTICE",
        REPO_ROOT / "pyproject.toml",
        REPO_ROOT / ".gitignore",
        *INTEGRATION_DIR.rglob("*.py"),
        *INTEGRATION_DIR.rglob("*.json"),
        *INTEGRATION_DIR.rglob("*.yaml"),
        *(REPO_ROOT / "tests").rglob("*.py"),
        *(REPO_ROOT / ".github").rglob("*.yml"),
    ]
    for path in public_targets:
        text = path.read_text(encoding="utf-8")
        assert not forbidden_re.search(text), f"Private IP/local path leaked in {path}"

    readme_text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "Disclaimer" in readme_text and "Apache License, Version 2.0" in readme_text
    contrib_text = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "Apache License" in contrib_text
    doc_checks = [
        ("README.md", readme_text),
        ("CONTRIBUTING.md", contrib_text),
        *[
            (p.name, p.read_text(encoding="utf-8"))
            for p in sorted((REPO_ROOT / ".github" / "ISSUE_TEMPLATE").glob("*.yml"))
        ],
    ]
    for doc_name, doc_text in doc_checks:
        github_refs = set(re.findall(r"github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", doc_text))
        assert github_refs <= {"MixasikM/ha-aruba-instant"}, f"Unexpected external repo link in {doc_name}: {github_refs}"

    stray_suffixes = (".bak", ".orig")
    stray_names = {".idea", ".vscode", ".DS_Store"}
    for pub_dir in (INTEGRATION_DIR, REPO_ROOT / "tests", REPO_ROOT / ".github"):
        for item in pub_dir.rglob("*"):
            if "__pycache__" in item.parts:
                continue
            assert item.name not in stray_names, f"Stray IDE/OS artifact found: {item}"
            assert not item.name.endswith(stray_suffixes), f"Stray backup artifact found: {item}"


def test_aruba_object_id_helper() -> None:
    assert build_aruba_object_id("Alice-Phone") == "aruba_alice_phone"
    assert build_aruba_object_id("Aruba-AP-Lobby") == "aruba_ap_lobby"
    assert build_aruba_object_id("MainCluster_total_clients") == "aruba_maincluster_total_clients"
    assert build_aruba_object_id("!!!", fallback="aa:bb:cc:11:22:33") == "aruba_aa_bb_cc_11_22_33"


async def test_config_flow_cluster_503_explicit_consent_readonly_ssh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = ArubaInstantConfigFlow()
    flow.hass = HomeAssistant()

    class _Fake503Response:
        status = 503

        async def __aenter__(self) -> _Fake503Response:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def text(self) -> str:
            return "REST API Service is not enabled"

    class _Fake503Session:
        def post(self, *args: Any, **kwargs: Any) -> _Fake503Response:
            return _Fake503Response()

    # Exercise REAL unmocked _async_probe_node -> AutoTransportClient -> ArubaRestClient 503
    flow.hass._test_session = _Fake503Session()

    snap = build_snapshot(
        SHOW_SUMMARY_OUTPUT, SHOW_NETWORK_OUTPUT, SHOW_APS_OUTPUT, SHOW_CLIENTS_OUTPUT
    )

    async def fake_ssh_fetch(self: Any) -> Any:
        return snap

    monkeypatch.setattr(
        "custom_components.aruba_instant.adapters.ssh_client.ArubaSshClient.async_fetch_snapshot",
        fake_ssh_fetch,
    )

    menu_res = await flow.async_step_user()
    assert menu_res["type"] == "menu"

    res_503 = await flow.async_step_cluster(
        {
            CONF_HOST: "192.0.2.10",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
            CONF_TRANSPORT: TRANSPORT_AUTO,
            "advanced": {},
        }
    )
    assert res_503["step_id"] == "confirm_enable_rest"
    assert res_503["description_placeholders"]["host"] == "192.0.2.10"

    # Decline modifying AP config -> proceed straight to filters using cached credentials
    res_filters = await flow.async_step_confirm_enable_rest(
        {CONF_CONFIRM_ACTION: CONFIRM_USE_SSH_READONLY}
    )
    assert res_filters["step_id"] == "filters"

    created = await flow.async_step_filters(
        {
            CONF_TRACKED_SSIDS: ["CorpWiFi"],
            CONF_CONSIDER_HOME: 180,
        }
    )
    assert created["type"] == "create_entry"
    assert created["data"][CONF_NODES][0][CONF_TRANSPORT] == TRANSPORT_SSH
    assert created["data"][CONF_NODES][0]["ap_id"] == "SN00000001"

    # Also verify reconfigure_cluster routes 503 -> confirm_enable_rest cleanly
    existing = ConfigEntry(
        entry_id="cluster_rc",
        unique_id="cluster_192.0.2.10",
        data=created["data"],
        options=created["options"],
    )
    rc_flow = ArubaInstantConfigFlow()
    rc_flow.hass = flow.hass
    rc_flow.context = {"reconfigure_entry": existing}
    rc_503 = await rc_flow.async_step_reconfigure_cluster(
        {
            CONF_HOST: "192.0.2.10",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "",
            CONF_TRANSPORT: TRANSPORT_AUTO,
            "advanced": {},
        }
    )
    assert rc_503["step_id"] == "confirm_enable_rest"


async def test_config_flow_multi_ap_smart_auth_fallback_and_reconfigure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = ArubaInstantConfigFlow()
    flow.hass = HomeAssistant()

    snap = build_snapshot(
        SHOW_SUMMARY_STANDALONE_AOS10,
        SHOW_NETWORK_OUTPUT,
        SHOW_APS_OUTPUT,
        SHOW_CLIENTS_OUTPUT,
    )

    async def fake_probe(self: Any, node: dict[str, Any]) -> tuple[Any, str]:
        if node[CONF_HOST] == "192.0.2.12" and node[CONF_PASSWORD] != "serial_pw":
            raise ArubaAuthError("rejected", host="192.0.2.12")
        return snap, TRANSPORT_SSH

    monkeypatch.setattr(ArubaInstantConfigFlow, "_async_probe_node", fake_probe)

    res_cred = await flow.async_step_multi_ap(
        {
            CONF_NETWORK_NAME: "Office",
            CONF_HOSTS: ["192.0.2.11", "192.0.2.12"],
            CONF_SEPARATE_CREDENTIALS: False,
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "shared_pw",
            CONF_TRANSPORT: TRANSPORT_AUTO,
            "advanced": {},
        }
    )
    # 192.0.2.11 succeeded; only 192.0.2.12 triggers ap_credentials fallback
    assert res_cred["step_id"] == "ap_credentials"
    assert res_cred["description_placeholders"]["host"] == "192.0.2.12"

    res_filters = await flow.async_step_ap_credentials(
        {CONF_USERNAME: "admin", CONF_PASSWORD: "serial_pw", CONF_TRANSPORT: TRANSPORT_AUTO, "advanced": {}}
    )
    assert res_filters["step_id"] == "filters"
    entry_res = await flow.async_step_filters({CONF_TRACKED_SSIDS: ["CorpWiFi"]})
    assert len(entry_res["data"][CONF_NODES]) == 2

    # Test per-AP Reauth + Remove AP cleanup
    entry = ConfigEntry(
        entry_id="multi_1",
        data=entry_res["data"],
        options=entry_res["options"],
    )
    dev_reg = dr.async_get(flow.hass)
    ap_dev = dev_reg.async_get_or_create(
        config_entry_id="multi_1",
        identifiers={(DOMAIN, "ap_multi_1_SN00000001")},
        name="Aruba AP-Lobby",
    )

    reauth_flow = ArubaInstantConfigFlow()
    reauth_flow.hass = flow.hass
    reauth_flow.context = {"reauth_entry": entry}
    r_form = await reauth_flow.async_step_reauth({"failed_hosts": ["192.0.2.12"]})
    assert r_form["description_placeholders"]["host"] == "192.0.2.12"
    r_done = await reauth_flow.async_step_reauth_confirm(
        {
            CONF_TARGET_HOST: "192.0.2.12",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "serial_pw",
            CONF_APPLY_TO_ALL_NODES: False,
        }
    )
    assert r_done["type"] == "abort"

    reconf_flow = ArubaInstantConfigFlow()
    reconf_flow.hass = flow.hass
    reconf_flow.context = {"reconfigure_entry": entry}
    rem_done = await reconf_flow.async_step_remove_ap({CONF_TARGET_HOST: "192.0.2.11"})
    assert rem_done["type"] == "abort"
    assert len(entry.data[CONF_NODES]) == 1
    assert dev_reg.async_get_device_by_identifier((DOMAIN, "ap_multi_1_SN00000001"), "multi_1") is None
    assert ap_dev.id not in dev_reg.devices


async def test_coordinator_consider_home_reauth_entities_and_diagnostics() -> None:
    hass = HomeAssistant()
    now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    snap = build_snapshot(
        SHOW_SUMMARY_OUTPUT, SHOW_NETWORK_OUTPUT, SHOW_APS_OUTPUT, SHOW_CLIENTS_OUTPUT, fetched_at=now
    )

    class _MockClient:
        def __init__(self) -> None:
            self.mode = "ok"

        async def async_fetch_snapshot(self) -> Any:
            if self.mode == "auth":
                raise ArubaAuthError("bad creds", host="192.0.2.10")
            if self.mode == "conn_err":
                raise ArubaConnectionError("transient timeout", host="192.0.2.10")
            if self.mode == "empty":
                return build_snapshot(
                    SHOW_SUMMARY_OUTPUT, SHOW_NETWORK_OUTPUT, SHOW_APS_OUTPUT, "", fetched_at=now
                )
            return snap

        async def async_close(self) -> None:
            return None

    mock_client = _MockClient()
    entry = ConfigEntry(
        entry_id="cluster_1",
        unique_id="cluster_192.0.2.10",
        title="Aruba MainCluster",
        data={
            CONF_TOPOLOGY_MODE: TOPOLOGY_CLUSTER,
            CONF_SITE_NAME: "MainCluster",
            CONF_NODES: [
                {
                    CONF_HOST: "192.0.2.10",
                    CONF_USERNAME: "admin",
                    CONF_PASSWORD: "secret_password",
                    CONF_TRANSPORT: "rest",
                }
            ],
        },
        options={
            CONF_TRACKED_SSIDS: ["CorpWiFi", "Guest WiFi"],
            CONF_CONSIDER_HOME: 180,
            CONF_TRACK_NEW_CLIENTS: True,
            CONF_ENABLE_ROAMING_SENSORS: False,
        },
    )

    coord = ArubaDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        client=mock_client,  # type: ignore[arg-type]
        site_name="MainCluster",
        topology_mode=TOPOLOGY_CLUSTER,
    )
    await coord.async_config_entry_first_refresh()

    # Pre-seed an away phone whose RegistryEntry.original_name is "Aruba Wi-Fi" while DeviceEntry.name is "Away-Phone"
    dev_reg = dr.async_get(hass)
    away_dev = dev_reg.async_get_or_create(
        config_entry_id="cluster_1",
        connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:99:88:77")},
        name="Away-Phone",
    )
    reg = er.async_get(hass)
    reg.entities.items["device_tracker.aruba_away_phone"] = er.RegistryEntry(
        entity_id="device_tracker.aruba_away_phone",
        domain="device_tracker",
        platform=DOMAIN,
        unique_id="cluster_1_aa:bb:cc:99:88:77",
        config_entry_id="cluster_1",
        original_name="Aruba Wi-Fi",
        device_id=away_dev.id,
    )

    added_dt: list[ArubaClientScannerEntity] = []
    entry.runtime_data = type(
        "Runtime", (), {"coordinator": coord, "client": mock_client, "cluster_device_id": "dev_1", "site_name": "MainCluster", "topology_mode": TOPOLOGY_CLUSTER}
    )()
    await async_setup_dt(hass, entry, added_dt.extend)  # type: ignore[arg-type]

    by_eid = {e.entity_id: e for e in added_dt}
    assert "device_tracker.aruba_alice_phone" in by_eid
    assert "device_tracker.aruba_away_phone" in by_eid
    assert by_eid["device_tracker.aruba_alice_phone"].unique_id == "cluster_1_aa:bb:cc:11:22:33"
    assert by_eid["device_tracker.aruba_away_phone"].unique_id == "cluster_1_aa:bb:cc:99:88:77"
    assert by_eid["device_tracker.aruba_alice_phone"].entity_registry_enabled_default is True
    assert by_eid["device_tracker.aruba_alice_phone"].is_connected is True
    assert by_eid["device_tracker.aruba_alice_phone"].extra_state_attributes["access_point"] == "AP-Lab Desk"
    assert by_eid["device_tracker.aruba_away_phone"].is_connected is False
    assert by_eid["device_tracker.aruba_away_phone"].state == "not_home"
    assert by_eid["device_tracker.aruba_away_phone"].available is True
    assert ArubaClientScannerEntity._unrecorded_attributes == frozenset({"signal", "speed_mbps"})
    assert getattr(
        ArubaClientScannerEntity, "_Entity__combined_unrecorded_attributes", frozenset()
    ) >= frozenset({"signal", "speed_mbps"})
    # Restored away device keeps "Away-Phone" and starts stale retention clock without flipping online
    assert by_eid["device_tracker.aruba_away_phone"].hostname == "Away-Phone"
    assert by_eid["device_tracker.aruba_away_phone"]._attr_device_info.name == "Away-Phone"
    assert "aa:bb:cc:99:88:77" in coord.last_seen

    # Sensors: master_ap in cluster mode + 6ghz util on AP-Lobby + uptime 120s drift guard
    added_sensors: list[Any] = []
    await async_setup_sensor(hass, entry, added_sensors.extend)  # type: ignore[arg-type]
    sensor_eids = {s.entity_id: s for s in added_sensors}
    assert "sensor.aruba_maincluster_total_clients" in sensor_eids
    assert "sensor.aruba_maincluster_master_ap" in sensor_eids
    assert "sensor.aruba_ap_lobby_6ghz_utilization" in sensor_eids
    assert "sensor.aruba_ap_lab_desk_6ghz_utilization" not in sensor_eids
    assert any(isinstance(s, ArubaSsidClientsSensor) for s in added_sensors)
    assert any(isinstance(s, ArubaClusterMasterApSensor) for s in added_sensors)

    uptime_sensor = sensor_eids["sensor.aruba_ap_lobby_uptime"]
    assert isinstance(uptime_sensor, ArubaApUptimeSensor)
    boot1 = uptime_sensor.native_value
    assert boot1 is not None

    # First refresh with no cached snapshot fails immediately on ArubaConnectionError
    fresh_fail_client = _MockClient()
    fresh_fail_client.mode = "conn_err"
    fresh_coord = ArubaDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        client=fresh_fail_client,  # type: ignore[arg-type]
        site_name="MainCluster",
        topology_mode=TOPOLOGY_CLUSTER,
    )
    with pytest.raises(UpdateFailed):
        await fresh_coord.async_config_entry_first_refresh()

    # Subsequent transient poll failures preserve snapshot for 2 polls, fail on 3rd, and recover cleanly
    mock_client.mode = "conn_err"
    await coord.async_refresh()
    assert coord.last_update_success is True
    assert coord.data is snap
    await coord.async_refresh()
    assert coord.last_update_success is True
    assert coord.data is snap
    await coord.async_refresh()
    assert coord.last_update_success is False
    mock_client.mode = "ok"
    await coord.async_refresh()
    assert coord.last_update_success is True
    assert coord._consecutive_failures == 0

    # Auth failure forwards failed_hosts via async_start_reauth before raising ConfigEntryAuthFailed
    mock_client.mode = "auth"
    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()
    assert entry.reauth_calls[-1]["data"] == {"failed_hosts": ["192.0.2.10"]}

    # Diagnostics redaction check (including unique_id, title, site_name, role)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    dumped = json.dumps(diag)
    assert "secret_password" not in dumped
    assert "192.0.2.10" not in dumped
    assert "MainCluster" not in dumped
    assert "aa:bb:cc:11:22:33" not in dumped
    assert "SN00000001" not in dumped
    assert "CorpWiFi" not in dumped
    assert "Alice-Phone" not in dumped


async def test_options_flow_registry_sync_and_multi_ap_omits_master_ap() -> None:
    hass = HomeAssistant()
    now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    snap = build_snapshot(
        SHOW_SUMMARY_STANDALONE_AOS10,
        SHOW_NETWORK_OUTPUT,
        SHOW_APS_OUTPUT,
        SHOW_CLIENTS_OUTPUT,
        fetched_at=now,
    )

    class _MockClient:
        async def async_fetch_snapshot(self) -> Any:
            return snap

        async def async_close(self) -> None:
            return None

    entry = ConfigEntry(
        entry_id="multi_2",
        title="Aruba Office",
        data={
            CONF_TOPOLOGY_MODE: TOPOLOGY_MULTI_AP,
            CONF_SITE_NAME: "Office",
            CONF_NODES: [],
        },
        options={
            CONF_TRACKED_SSIDS: ["CorpWiFi"],
            CONF_CONSIDER_HOME: 180,
            CONF_ENABLE_ROAMING_SENSORS: False,
        },
    )
    coord = ArubaDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        client=_MockClient(),  # type: ignore[arg-type]
        site_name="Office",
        topology_mode=TOPOLOGY_MULTI_AP,
    )
    await coord.async_config_entry_first_refresh()
    entry.runtime_data = type(
        "Runtime",
        (),
        {
            "coordinator": coord,
            "client": _MockClient(),
            "cluster_device_id": "dev_hub",
            "site_name": "Office",
            "topology_mode": TOPOLOGY_MULTI_AP,
        },
    )()

    # Multi-AP omits Master AP sensor
    added_sensors: list[Any] = []
    await async_setup_sensor(hass, entry, added_sensors.extend)  # type: ignore[arg-type]
    assert not any(isinstance(s, ArubaClusterMasterApSensor) for s in added_sensors)

    # User-disabled entity must never be overwritten by sync_registry_filters
    reg = er.async_get(hass)
    reg.entities.items["device_tracker.aruba_alice_phone"] = er.RegistryEntry(
        entity_id="device_tracker.aruba_alice_phone",
        domain="device_tracker",
        platform=DOMAIN,
        unique_id="multi_2_aa:bb:cc:11:22:33",
        config_entry_id="multi_2",
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    coord.sync_registry_filters()
    assert (
        reg.entities.items["device_tracker.aruba_alice_phone"].disabled_by
        == er.RegistryEntryDisabler.USER
    )

    # OptionsFlowWithReload form & save
    opt_flow = ArubaOptionsFlow()
    opt_flow._test_config_entry = entry
    form = await opt_flow.async_step_init()
    assert form["step_id"] == "init"
    saved = await opt_flow.async_step_init(
        {
            CONF_TRACKED_SSIDS: ["CorpWiFi", "Guest WiFi"],
            CONF_ENABLE_ROAMING_SENSORS: True,
            CONF_CONSIDER_HOME: 240,
        }
    )
    assert saved["type"] == "create_entry"
    assert saved["data"][CONF_CONSIDER_HOME] == 240
    assert saved["data"][CONF_ENABLE_ROAMING_SENSORS] is True


async def test_integration_setup_unload_binary_sensors_roaming_and_enable_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise __init__ lifecycle, binary_sensor, roaming sensors, _clean_host, and enable-REST flows."""
    # Host normalization & invalid character guards + SID log redaction
    assert _clean_host("https://192.0.2.10:4343/rest") == "192.0.2.10"
    assert _clean_host("[2001:db8::1]:4343") == "2001:db8::1"
    assert _clean_host("foo bar") == ""
    assert _clean_host("user@192.0.2.10") == ""
    assert _clean_host("192.0.2.10?cmd=x") == ""
    assert _redact_sid("Error requesting https://192.0.2.10:4343/rest/show-cmd?sid=secret123&cmd=show") == (
        "Error requesting https://192.0.2.10:4343/rest/show-cmd?sid=***&cmd=show"
    )

    hass = HomeAssistant()
    now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    snap = build_snapshot(
        SHOW_SUMMARY_OUTPUT, SHOW_NETWORK_OUTPUT, SHOW_APS_OUTPUT, SHOW_CLIENTS_OUTPUT, fetched_at=now
    )
    closed = False

    class _FakeClusterClient:
        async def async_fetch_snapshot(self) -> Any:
            return snap

        async def async_close(self) -> None:
            nonlocal closed
            closed = True

    monkeypatch.setattr(
        "custom_components.aruba_instant.build_cluster_client",
        lambda *_args, **_kwargs: _FakeClusterClient(),
    )

    entry = ConfigEntry(
        entry_id="cluster_init",
        title="Aruba MainCluster",
        data={
            CONF_TOPOLOGY_MODE: TOPOLOGY_CLUSTER,
            CONF_SITE_NAME: "MainCluster",
            CONF_NODES: [{CONF_HOST: "192.0.2.10", CONF_USERNAME: "admin", CONF_PASSWORD: "pw", CONF_TRANSPORT: TRANSPORT_REST}],
        },
        options={
            CONF_TRACKED_SSIDS: ["CorpWiFi"],
            CONF_CONSIDER_HOME: 180,
            CONF_ENABLE_ROAMING_SENSORS: True,
        },
    )
    # Pre-seed a legacy bare-MAC offline tracker and a conflict-guard pair before setup
    reg_pre = er.async_get(hass)
    dev_reg_pre = dr.async_get(hass)
    bare_away_dev = dev_reg_pre.async_get_or_create(
        config_entry_id="cluster_init",
        connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:77:88:99")},
        name="Bare-Away-Phone",
    )
    reg_pre.entities.items["device_tracker.aruba_bare_away_phone"] = er.RegistryEntry(
        entity_id="device_tracker.aruba_bare_away_phone",
        domain="device_tracker",
        platform=DOMAIN,
        unique_id="aa:bb:cc:77:88:99",
        config_entry_id="cluster_init",
        device_id=bare_away_dev.id,
    )
    reg_pre.entities.items["device_tracker.aruba_alice_phone"] = er.RegistryEntry(
        entity_id="device_tracker.aruba_alice_phone",
        domain="device_tracker",
        platform=DOMAIN,
        unique_id="cluster_init_aa:bb:cc:11:22:33",
        config_entry_id="cluster_init",
    )
    reg_pre.entities.items["device_tracker.aruba_alice_phone_dup"] = er.RegistryEntry(
        entity_id="device_tracker.aruba_alice_phone_dup",
        domain="device_tracker",
        platform=DOMAIN,
        unique_id="aa:bb:cc:11:22:33",
        config_entry_id="cluster_init",
    )

    assert await async_setup_entry(hass, entry) is True  # type: ignore[arg-type]
    assert entry.runtime_data is not None
    assert reg_pre.entities.items["device_tracker.aruba_bare_away_phone"].unique_id == "cluster_init_aa:bb:cc:77:88:99"
    # Conflict guard skips colliding bare MAC without raising ValueError
    assert reg_pre.entities.items["device_tracker.aruba_alice_phone_dup"].unique_id == "aa:bb:cc:11:22:33"

    dt_entities: list[ArubaClientScannerEntity] = []
    await async_setup_dt(hass, entry, dt_entities.extend)  # type: ignore[arg-type]
    dt_by_eid = {e.entity_id: e for e in dt_entities}
    restored_bare = dt_by_eid["device_tracker.aruba_bare_away_phone"]
    assert restored_bare.unique_id == "cluster_init_aa:bb:cc:77:88:99"
    assert restored_bare.is_connected is False
    assert restored_bare.state == "not_home"
    assert restored_bare.available is True
    assert restored_bare.hostname == "Bare-Away-Phone"

    dev_reg = dr.async_get(hass)
    hub_dev = dev_reg.async_get_device(identifiers={(DOMAIN, "hub_cluster_init")})
    assert hub_dev is not None
    assert await async_remove_config_entry_device(hass, entry, hub_dev) is False  # type: ignore[arg-type]

    active_ap_dev = dev_reg.async_get_or_create(
        config_entry_id="cluster_init",
        identifiers={(DOMAIN, "ap_cluster_init_SN00000001")},
        name="Aruba AP-Lobby",
    )
    assert await async_remove_config_entry_device(hass, entry, active_ap_dev) is False  # type: ignore[arg-type]

    active_client_dev = dev_reg.async_get_or_create(
        config_entry_id="cluster_init",
        connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:11:22:33")},
        name="Alice-Phone",
    )
    assert await async_remove_config_entry_device(hass, entry, active_client_dev) is False  # type: ignore[arg-type]

    stale_client_dev = dev_reg.async_get_or_create(
        config_entry_id="cluster_init",
        connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:00:00:01")},
        name="Old-Phone",
    )
    assert await async_remove_config_entry_device(hass, entry, stale_client_dev) is True  # type: ignore[arg-type]

    # Binary sensor & roaming sensor assertions
    bin_entities: list[Any] = []
    await async_setup_binary_sensor(hass, entry, bin_entities.extend)  # type: ignore[arg-type]
    assert {b.entity_id for b in bin_entities} == {
        "binary_sensor.aruba_ap_lobby_online",
        "binary_sensor.aruba_ap_lab_desk_online",
        "binary_sensor.aruba_ap_floor2_online",
        "binary_sensor.aruba_ap_lobby_conductor",
        "binary_sensor.aruba_ap_lab_desk_conductor",
        "binary_sensor.aruba_ap_floor2_conductor",
    }
    assert all(b.is_on is True for b in bin_entities if isinstance(b, ArubaApOnlineBinarySensor))
    assert any(isinstance(b, ArubaApConductorBinarySensor) and b.is_on is True for b in bin_entities)

    sensors: list[Any] = []
    await async_setup_sensor(hass, entry, sensors.extend)  # type: ignore[arg-type]
    ap_roam = [s for s in sensors if isinstance(s, ArubaClientAccessPointSensor)]
    ssid_roam = [s for s in sensors if isinstance(s, ArubaClientSsidSensor)]
    assert any(s.entity_id == "sensor.aruba_alice_phone_access_point" and s.native_value == "AP-Lab Desk" for s in ap_roam)
    assert any(s.entity_id == "sensor.aruba_alice_phone_ssid" and s.native_value == "CorpWiFi" for s in ssid_roam)

    assert await async_unload_entry(hass, entry) is True  # type: ignore[arg-type]
    for cb in entry.unload_callbacks:
        res = cb()
        if hasattr(res, "__await__"):
            await res
    assert closed is True

    # confirm_enable_rest: read-only CLI rejection + successful SSH allow-rest-api enablement
    flow = ArubaInstantConfigFlow()
    flow.hass = hass

    async def fail_503(_self: Any, _node: dict[str, Any]) -> tuple[Any, str]:
        raise ArubaRestApiDisabledError("disabled", host="192.0.2.10")

    monkeypatch.setattr(ArubaInstantConfigFlow, "_async_probe_node", fail_503)
    res_confirm = await flow.async_step_cluster(
        {
            CONF_HOST: "192.0.2.10",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "pw",
            CONF_TRANSPORT: TRANSPORT_AUTO,
            "advanced": {},
        }
    )
    assert res_confirm["step_id"] == "confirm_enable_rest"

    async def raise_readonly(_self: Any) -> None:
        raise ArubaReadOnlyCliError("read-only CLI")

    async def noop_close(_self: Any) -> None:
        return None

    monkeypatch.setattr(
        "custom_components.aruba_instant.adapters.ssh_client.ArubaSshClient.async_enable_rest_api",
        raise_readonly,
    )
    monkeypatch.setattr(
        "custom_components.aruba_instant.adapters.ssh_client.ArubaSshClient.async_close",
        noop_close,
    )
    monkeypatch.setattr(
        "custom_components.aruba_instant.adapters.rest_client.ArubaRestClient.async_close",
        noop_close,
    )
    res_ro = await flow.async_step_confirm_enable_rest({CONF_CONFIRM_ACTION: CONFIRM_ENABLE_REST})
    assert res_ro["step_id"] == "confirm_enable_rest"
    assert res_ro["errors"]["base"] == "rest_enable_failed"

    async def ok_enable(_self: Any) -> None:
        return None

    async def ok_rest_fetch(_self: Any) -> Any:
        return snap

    monkeypatch.setattr(
        "custom_components.aruba_instant.adapters.ssh_client.ArubaSshClient.async_enable_rest_api",
        ok_enable,
    )
    monkeypatch.setattr(
        "custom_components.aruba_instant.adapters.rest_client.ArubaRestClient.async_fetch_snapshot",
        ok_rest_fetch,
    )
    res_ok = await flow.async_step_confirm_enable_rest({CONF_CONFIRM_ACTION: CONFIRM_ENABLE_REST})
    assert res_ok["step_id"] == "filters"
    assert flow._validated_nodes[0][CONF_TRANSPORT] == TRANSPORT_REST

    # Multi-AP add_ap and edit_ap_details reconfigure paths
    async def ok_probe(_self: Any, _node: dict[str, Any]) -> tuple[Any, str]:
        return snap, TRANSPORT_SSH

    monkeypatch.setattr(ArubaInstantConfigFlow, "_async_probe_node", ok_probe)
    multi_entry = ConfigEntry(
        entry_id="multi_rc",
        data={
            CONF_TOPOLOGY_MODE: TOPOLOGY_MULTI_AP,
            CONF_SITE_NAME: "Office",
            CONF_NODES: [
                {
                    CONF_HOST: "192.0.2.11",
                    CONF_USERNAME: "admin",
                    CONF_PASSWORD: "pw1",
                    CONF_TRANSPORT: TRANSPORT_SSH,
                    "ap_id": "SN00000001",
                    "name": "AP-Lobby",
                }
            ],
        },
        options={},
    )
    add_flow = ArubaInstantConfigFlow()
    add_flow.hass = hass
    add_flow.context = {"reconfigure_entry": multi_entry}
    add_done = await add_flow.async_step_add_ap(
        {
            CONF_HOST: "192.0.2.12",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "pw2",
            CONF_TRANSPORT: TRANSPORT_AUTO,
            "advanced": {},
        }
    )
    assert add_done["type"] == "abort"
    assert len(multi_entry.data[CONF_NODES]) == 2

    edit_flow = ArubaInstantConfigFlow()
    edit_flow.hass = hass
    edit_flow.context = {"reconfigure_entry": multi_entry}
    details_form = await edit_flow.async_step_edit_ap({CONF_TARGET_HOST: "192.0.2.12"})
    assert details_form["step_id"] == "edit_ap_details"
    edit_done = await edit_flow.async_step_edit_ap_details(
        {
            CONF_HOST: "192.0.2.15",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "",
            CONF_TRANSPORT: TRANSPORT_SSH,
            "advanced": {},
        }
    )
    assert edit_done["type"] == "abort"
    assert multi_entry.data[CONF_NODES][1][CONF_HOST] == "192.0.2.15"
    assert multi_entry.data[CONF_NODES][1][CONF_PASSWORD] == "pw2"


async def test_v020_firmware_channels_write_controls_and_purge_service() -> None:
    hass = HomeAssistant()
    now = datetime(2026, 11, 24, 22, 0, tzinfo=timezone.utc)
    snap = build_snapshot(
        SHOW_SUMMARY_OUTPUT, SHOW_NETWORK_OUTPUT, SHOW_APS_OUTPUT, SHOW_CLIENTS_OUTPUT, fetched_at=now
    )
    fw_info = build_firmware_info(
        SHOW_IMAGE_VERSION_OUTPUT, SHOW_VERSION_OUTPUT, SHOW_UPGRADE_INFO_OUTPUT
    )

    class _FullMockClient:
        def __init__(self) -> None:
            self.fw_check = parse_image_check_xml(IMAGE_CHECK_ERROR_XML)
            self.rebooted: list[str] = []
            self.toggled: list[tuple[str, bool]] = []

        async def async_fetch_snapshot(self) -> Any:
            return snap

        async def async_fetch_firmware_details(self) -> tuple[Any, Any]:
            return fw_info, self.fw_check

        async def async_reboot_ap(self, ap_ip: str) -> None:
            self.rebooted.append(ap_ip)

        async def async_set_ssid_enabled(self, profile_name: str, enabled: bool) -> None:
            self.toggled.append((profile_name, enabled))

        async def async_close(self) -> None:
            return None

    client = _FullMockClient()
    entry = ConfigEntry(
        entry_id="cluster_v2",
        unique_id="cluster_192.0.2.10",
        title="Aruba MainCluster",
        data={CONF_TOPOLOGY_MODE: TOPOLOGY_CLUSTER, CONF_SITE_NAME: "MainCluster", CONF_NODES: []},
        options={
            CONF_TRACKED_SSIDS: ["CorpWiFi", "Guest WiFi"],
            CONF_CONSIDER_HOME: 180,
            CONF_ENABLE_WRITE_CONTROLS: True,
        },
    )
    hass.config_entries._entries.append(entry)

    coord = ArubaDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        client=client,  # type: ignore[arg-type]
        site_name="MainCluster",
        topology_mode=TOPOLOGY_CLUSTER,
    )
    dev_reg = dr.async_get(hass)
    hub_dev = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"hub_{entry.entry_id}")},
        name="Aruba MainCluster",
    )
    await coord.async_config_entry_first_refresh()
    entry.runtime_data = type(
        "Runtime",
        (),
        {
            "coordinator": coord,
            "client": client,
            "cluster_device_id": hub_dev.id,
            "site_name": "MainCluster",
            "topology_mode": TOPOLOGY_CLUSTER,
        },
    )()

    # 1. Binary sensors: conductor + AP hardware attributes (MAC, Serial, IP, Class)
    bs_list: list[Any] = []
    await async_setup_binary_sensor(hass, entry, bs_list.extend)  # type: ignore[arg-type]
    bs_by_eid = {b.entity_id: b for b in bs_list}
    online_lobby = bs_by_eid["binary_sensor.aruba_ap_lobby_online"]
    assert isinstance(online_lobby, ArubaApOnlineBinarySensor)
    assert online_lobby.extra_state_attributes["mac_address"] == "02:00:00:10:00:01"
    assert online_lobby.extra_state_attributes["serial_number"] == "SN00000001"
    assert online_lobby.extra_state_attributes["ap_class"] == "Hercules"
    cond_lobby = bs_by_eid["binary_sensor.aruba_ap_lobby_conductor"]
    cond_lab = bs_by_eid["binary_sensor.aruba_ap_lab_desk_conductor"]
    assert isinstance(cond_lobby, ArubaApConductorBinarySensor)
    assert cond_lobby.is_on is True
    assert cond_lab.is_on is False

    # 2. Sensors: radio channels, AP/cluster firmware, last_reboot_cause
    sensors: list[Any] = []
    await async_setup_sensor(hass, entry, sensors.extend)  # type: ignore[arg-type]
    s_by_eid = {s.entity_id: s for s in sensors}
    assert s_by_eid["sensor.aruba_ap_lobby_5ghz_channel"].native_value == "36E"
    assert s_by_eid["sensor.aruba_ap_lobby_2_4ghz_channel"].native_value == "1"
    assert s_by_eid["sensor.aruba_ap_lobby_6ghz_channel"].native_value == "37w"
    assert s_by_eid["sensor.aruba_ap_lobby_ip_address"].native_value == "192.0.2.11"
    ap_fw = s_by_eid["sensor.aruba_ap_lobby_firmware"]
    assert isinstance(ap_fw, ArubaApFirmwareSensor)
    assert "8.10.0.24_97188" in str(ap_fw.native_value)
    assert ap_fw.extra_state_attributes["mac_address"] == "02:00:00:10:00:01"
    cl_fw = s_by_eid["sensor.aruba_maincluster_firmware"]
    assert isinstance(cl_fw, ArubaClusterFirmwareSensor)
    assert cl_fw.extra_state_attributes["drt_version"] == "1.0_97611"
    assert "Image Upgrade Successful" in s_by_eid["sensor.aruba_ap_lobby_uptime"].extra_state_attributes["last_reboot_cause"]

    # 3. UpdateEntity 3-state contract: error -> unknown, success-empty -> off, new version -> on
    updates: list[Any] = []
    await async_setup_update(hass, entry, updates.extend)  # type: ignore[arg-type]
    upd = updates[0]
    assert isinstance(upd, ArubaClusterFirmwareUpdateEntity)
    assert UpdateEntityFeature.PROGRESS in upd.supported_features
    assert upd.state == "unknown"

    client.fw_check = parse_image_check_xml(IMAGE_CHECK_SUCCESS_EMPTY_XML)
    await coord.async_refresh_firmware_now()
    assert upd.state == "off"

    client.fw_check = parse_image_check_xml(IMAGE_CHECK_UPDATE_XML)
    await coord.async_refresh_firmware_now()
    assert upd.state == "on"
    assert UpdateEntityFeature.INSTALL in upd.supported_features

    # 4. Write controls: button + switch, and auto-disable when turned off
    buttons: list[Any] = []
    switches: list[Any] = []
    await async_setup_button(hass, entry, buttons.extend)  # type: ignore[arg-type]
    await async_setup_switch(hass, entry, switches.extend)  # type: ignore[arg-type]
    btn_map = {b.entity_id: b for b in buttons}
    sw_map = {s.entity_id: s for s in switches}
    reboot_btn = btn_map["button.aruba_ap_lab_desk_reboot"]
    assert isinstance(reboot_btn, ArubaApRebootButton)
    await reboot_btn.async_press()
    assert client.rebooted == ["192.0.2.12"]

    guest_sw = sw_map["switch.aruba_maincluster_ssid_guest_wifi_enabled"]
    assert isinstance(guest_sw, ArubaSsidEnabledSwitch)
    await guest_sw.async_turn_off()
    assert client.toggled == [("Guest WiFi", False)]

    # Toggling enable_write_controls off disables existing button & switch registry entries
    ent_reg = er.async_get(hass)
    ent_reg.entities.items[reboot_btn.entity_id] = er.RegistryEntry(
        entity_id=reboot_btn.entity_id,
        domain="button",
        platform=DOMAIN,
        unique_id=str(reboot_btn.unique_id),
        config_entry_id=entry.entry_id,
    )
    entry.options = {**entry.options, CONF_ENABLE_WRITE_CONTROLS: False}
    coord.sync_registry_filters()
    assert ent_reg.entities.items[reboot_btn.entity_id].disabled_by == er.RegistryEntryDisabler.INTEGRATION

    # 5. Services: purge_stale_clients protects person-bound tracker & honors dry_run
    await async_setup(hass, {})
    old_seen = dt_util.utcnow() - timedelta(days=45)
    coord.last_seen["aa:bb:cc:44:55:01"] = old_seen
    coord.last_known_essid["aa:bb:cc:44:55:01"] = "Guest WiFi"
    coord.last_seen["aa:bb:cc:44:55:02"] = old_seen
    coord.last_known_essid["aa:bb:cc:44:55:02"] = "Guest WiFi"

    dev_guest = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:44:55:02")},
        name="Stale-Guest",
    )
    ent_reg.entities.items["device_tracker.aruba_person_phone"] = er.RegistryEntry(
        entity_id="device_tracker.aruba_person_phone",
        domain="device_tracker",
        platform=DOMAIN,
        unique_id=f"{entry.entry_id}_aa:bb:cc:44:55:01",
        config_entry_id=entry.entry_id,
    )
    ent_reg.entities.items["device_tracker.aruba_stale_guest"] = er.RegistryEntry(
        entity_id="device_tracker.aruba_stale_guest",
        domain="device_tracker",
        platform=DOMAIN,
        unique_id=f"{entry.entry_id}_aa:bb:cc:44:55:02",
        config_entry_id=entry.entry_id,
        device_id=dev_guest.id,
    )
    hass.states.async_set(
        "person.alex",
        "not_home",
        {"device_trackers": ["device_tracker.aruba_person_phone"]},
    )

    preview = await hass.services.async_call(
        DOMAIN,
        SERVICE_PURGE_STALE_CLIENTS,
        {"stale_days": 30, "ssid": "Guest WiFi", "dry_run": True},
        blocking=True,
        return_response=True,
    )
    assert preview["dry_run"] is True
    assert preview["purged_entities"] == ["device_tracker.aruba_stale_guest"]
    assert preview["skipped_person_entities"] == ["device_tracker.aruba_person_phone"]
    assert "device_tracker.aruba_stale_guest" in ent_reg.entities.items

    executed = await hass.services.async_call(
        DOMAIN,
        SERVICE_PURGE_STALE_CLIENTS,
        {"stale_days": 30, "ssid": "Guest WiFi", "dry_run": False},
        blocking=True,
        return_response=True,
    )
    assert executed["purged_entities"] == ["device_tracker.aruba_stale_guest"]
    assert "device_tracker.aruba_stale_guest" not in ent_reg.entities.items
    assert "device_tracker.aruba_person_phone" in ent_reg.entities.items
    assert dev_guest.id not in dev_reg.devices

    # Shared device with another integration's remaining entity must NOT be deleted
    shared_dev = dev_reg.async_get_or_create(
        config_entry_id="other_integration",
        connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:44:55:03")},
        name="Shared-Phone",
    )
    ent_reg.entities.items["device_tracker.other_shared_phone"] = er.RegistryEntry(
        entity_id="device_tracker.other_shared_phone",
        domain="device_tracker",
        platform="other",
        unique_id="other_1",
        config_entry_id="other_integration",
        device_id=shared_dev.id,
    )
    ent_reg.entities.items["device_tracker.aruba_shared_phone"] = er.RegistryEntry(
        entity_id="device_tracker.aruba_shared_phone",
        domain="device_tracker",
        platform=DOMAIN,
        unique_id=f"{entry.entry_id}_aa:bb:cc:44:55:03",
        config_entry_id=entry.entry_id,
        device_id=shared_dev.id,
    )
    coord.last_seen["aa:bb:cc:44:55:03"] = old_seen
    shared_res = await hass.services.async_call(
        DOMAIN,
        SERVICE_PURGE_STALE_CLIENTS,
        {"stale_days": 30, "dry_run": False},
        blocking=True,
        return_response=True,
    )
    assert "device_tracker.aruba_shared_phone" in shared_res["purged_entities"]
    assert shared_dev.id in dev_reg.devices

    # Offline AP lifecycle: keep offline AP with IP when still in allowlist, remove device when dropped
    from dataclasses import replace

    ap_lab_dev = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"ap_{entry.entry_id}_SN00000002")},
        name="Aruba AP-Lab Desk",
    )
    ap_branch_dev = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"ap_{entry.entry_id}_SN00000003")},
        name="Aruba AP-Branch",
    )
    assert "SN00000002" in coord.known_aps
    two_ap_snap = replace(snap, access_points=[ap for ap in snap.access_points if ap.stable_id == "SN00000001"])
    fw_allow_lab = replace(fw_info, allowed_ap_macs=("02:00:00:10:00:01", "02:00:00:10:00:02"))
    async def _next_snap() -> Any:
        return two_ap_snap

    async def _next_fw() -> tuple[Any, Any]:
        return (fw_allow_lab, client.fw_check)

    client.async_fetch_snapshot = _next_snap  # type: ignore[method-assign]
    client.async_fetch_firmware_details = _next_fw  # type: ignore[method-assign]
    await coord.async_refresh()
    aps_after = {ap.stable_id: ap for ap in coord.data.access_points}
    assert aps_after["SN00000002"].online is False
    assert aps_after["SN00000002"].ip_address == "192.0.2.12"
    assert s_by_eid["sensor.aruba_ap_lab_desk_ip_address"].available is True
    assert s_by_eid["sensor.aruba_ap_lab_desk_ip_address"].native_value == "192.0.2.12"
    assert s_by_eid["sensor.aruba_ap_lab_desk_clients"].available is False
    assert "SN00000003" not in aps_after
    assert ap_lab_dev.id in dev_reg.devices
    assert ap_branch_dev.id not in dev_reg.devices

    # User-triggered deletion of offline AP device immediately prunes known_aps
    assert await async_remove_config_entry_device(hass, entry, ap_lab_dev) is True
    assert "SN00000002" not in coord.known_aps

    # Entry removal cleans up .storage client & peer store
    store_key = f"{DOMAIN}.{entry.entry_id}.clients"
    assert store_key in hass.data.get("_stores", {})
    await async_remove_entry(hass, entry)
    assert store_key not in hass.data.get("_stores", {})

    # VC VIP unreachable Repairs issue lifecycle: create at >= 2 failures, clear when recovered
    class _VipWarningClient:
        active_host = "192.0.2.11"

        def __init__(self) -> None:
            self.vip_failures = 0

        def get_vc_vip_failure_count(self, vc_ip: str) -> int:
            return self.vip_failures if vc_ip == "192.0.2.10" else 0

        async def async_fetch_snapshot(self) -> Any:
            return snap

        async def async_close(self) -> None:
            return None

    vip_client = _VipWarningClient()
    vip_entry = ConfigEntry(
        entry_id="cluster_vip_warn",
        unique_id="cluster_192.0.2.12",
        title="Aruba MainCluster",
        data={CONF_TOPOLOGY_MODE: TOPOLOGY_CLUSTER, CONF_SITE_NAME: "MainCluster", CONF_NODES: []},
        options={CONF_TRACKED_SSIDS: ["CorpWiFi"]},
    )
    vip_coord = ArubaDataUpdateCoordinator(
        hass=hass,
        config_entry=vip_entry,
        client=vip_client,  # type: ignore[arg-type]
        site_name="MainCluster",
        topology_mode=TOPOLOGY_CLUSTER,
    )
    issue_key = (DOMAIN, f"vc_vip_unreachable_{vip_entry.entry_id}")
    vip_client.vip_failures = 1
    await vip_coord.async_refresh()
    assert issue_key not in hass.data.get("_issues", {})

    vip_client.vip_failures = 2
    await vip_coord.async_refresh()
    issue = hass.data.get("_issues", {}).get(issue_key)
    assert issue is not None
    assert issue["translation_key"] == "vc_vip_unreachable"
    assert issue["translation_placeholders"]["vc_ip"] == "192.0.2.10"
    assert issue["translation_placeholders"]["conductor_ip"] == "192.0.2.11"

    vip_client.vip_failures = 0
    await vip_coord.async_refresh()
    assert issue_key not in hass.data.get("_issues", {})
