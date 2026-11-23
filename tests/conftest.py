# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Pytest configuration, async test hook, and Home Assistant / asyncssh test stubs."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
import importlib.util
import inspect
import logging
import re
import sys
import types
from typing import Any

import pytest
import voluptuous as vol

# Install lightweight HA & asyncssh stubs only when full packages are unavailable
_HAS_HA = importlib.util.find_spec("homeassistant") is not None
_HAS_ASYNCSSH = importlib.util.find_spec("asyncssh") is not None


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    """Run async test functions with asyncio.run when pytest-asyncio plugin is inactive."""
    if pyfuncitem.config.pluginmanager.hasplugin("asyncio"):
        return None
    test_fn = pyfuncitem.obj
    if inspect.iscoroutinefunction(test_fn):
        kwargs = {
            arg: pyfuncitem.funcargs[arg]
            for arg in pyfuncitem._fixtureinfo.argnames
        }
        asyncio.run(test_fn(**kwargs))
        return True
    return None


def _install_asyncssh_stub() -> None:
    if _HAS_ASYNCSSH or "asyncssh" in sys.modules:
        return

    mod = types.ModuleType("asyncssh")

    class Error(Exception):
        pass

    class PermissionDenied(Error):
        pass

    class KeyExchangeFailed(Error):
        pass

    async def connect(**kwargs: Any) -> Any:
        raise NotImplementedError("Monkeypatch asyncssh.connect in unit tests")

    mod.Error = Error  # type: ignore[attr-defined]
    mod.PermissionDenied = PermissionDenied  # type: ignore[attr-defined]
    mod.KeyExchangeFailed = KeyExchangeFailed  # type: ignore[attr-defined]
    mod.connect = connect  # type: ignore[attr-defined]
    sys.modules["asyncssh"] = mod


def _install_ha_stubs() -> None:
    if _HAS_HA or "homeassistant" in sys.modules:
        return

    ha = types.ModuleType("homeassistant")
    ha.__path__ = []  # type: ignore[attr-defined]

    # homeassistant.const
    const_mod = types.ModuleType("homeassistant.const")

    class Platform(str, Enum):
        DEVICE_TRACKER = "device_tracker"
        SENSOR = "sensor"
        BINARY_SENSOR = "binary_sensor"

    class EntityCategory(str, Enum):
        CONFIG = "config"
        DIAGNOSTIC = "diagnostic"

    const_mod.Platform = Platform  # type: ignore[attr-defined]
    const_mod.EntityCategory = EntityCategory  # type: ignore[attr-defined]
    const_mod.PERCENTAGE = "%"  # type: ignore[attr-defined]
    const_mod.CONF_HOST = "host"  # type: ignore[attr-defined]
    const_mod.CONF_USERNAME = "username"  # type: ignore[attr-defined]
    const_mod.CONF_PASSWORD = "password"  # type: ignore[attr-defined]

    # homeassistant.core
    core_mod = types.ModuleType("homeassistant.core")

    class HomeAssistant:
        def __init__(self) -> None:
            self.data: dict[str, Any] = {}
            self._test_session: Any = None
            self.config_entries = types.SimpleNamespace(
                _entries=[],
                async_entries=lambda domain=None: [],
                async_forward_entry_setups=self._async_noop,
                async_unload_platforms=self._async_true,
                async_update_entry=self._async_update_entry,
            )

        def _async_update_entry(self, entry: Any, **kwargs: Any) -> bool:
            if "data" in kwargs:
                entry.data = dict(kwargs["data"])
            if "options" in kwargs:
                entry.options = dict(kwargs["options"])
            return True

        async def _async_noop(self, *args: Any, **kwargs: Any) -> None:
            return None

        async def _async_true(self, *args: Any, **kwargs: Any) -> bool:
            return True

    def callback(fn: Callable[..., Any]) -> Callable[..., Any]:
        return fn

    core_mod.HomeAssistant = HomeAssistant  # type: ignore[attr-defined]
    core_mod.callback = callback  # type: ignore[attr-defined]

    # homeassistant.exceptions
    exc_mod = types.ModuleType("homeassistant.exceptions")

    class HomeAssistantError(Exception):
        pass

    class ConfigEntryAuthFailed(HomeAssistantError):
        def __init__(
            self,
            *args: Any,
            translation_domain: str | None = None,
            translation_key: str | None = None,
            translation_placeholders: dict[str, str] | None = None,
        ) -> None:
            super().__init__(*args)
            self.translation_domain = translation_domain
            self.translation_key = translation_key
            self.translation_placeholders = translation_placeholders

    exc_mod.HomeAssistantError = HomeAssistantError  # type: ignore[attr-defined]
    exc_mod.ConfigEntryAuthFailed = ConfigEntryAuthFailed  # type: ignore[attr-defined]

    # homeassistant.util & dt & slugify
    util_mod = types.ModuleType("homeassistant.util")
    util_mod.__path__ = []  # type: ignore[attr-defined]
    dt_mod = types.ModuleType("homeassistant.util.dt")
    dt_mod.utcnow = lambda: datetime.now(timezone.utc)  # type: ignore[attr-defined]

    def slugify(text: str | None) -> str:
        if not text:
            return ""
        s = re.sub(r"[^a-zA-Z0-9]+", "_", str(text).lower())
        return re.sub(r"_+", "_", s).strip("_")

    util_mod.slugify = slugify  # type: ignore[attr-defined]
    util_mod.dt = dt_mod  # type: ignore[attr-defined]

    # homeassistant.data_entry_flow
    def_mod = types.ModuleType("homeassistant.data_entry_flow")

    class FlowResult(dict):
        pass

    def section(schema: vol.Schema, options: dict[str, Any] | None = None) -> Any:
        return schema

    def_mod.FlowResult = FlowResult  # type: ignore[attr-defined]
    def_mod.section = section  # type: ignore[attr-defined]

    # homeassistant.config_entries
    ce_mod = types.ModuleType("homeassistant.config_entries")
    ce_mod.SOURCE_REAUTH = "reauth"  # type: ignore[attr-defined]
    ce_mod.SOURCE_RECONFIGURE = "reconfigure"  # type: ignore[attr-defined]

    class ConfigEntry:
        __class_getitem__ = classmethod(lambda cls, item: cls)

        def __init__(
            self,
            *,
            entry_id: str = "entry_1",
            title: str = "Aruba Test",
            data: dict[str, Any] | None = None,
            options: dict[str, Any] | None = None,
            unique_id: str | None = None,
            domain: str = "aruba_instant",
        ) -> None:
            self.entry_id = entry_id
            self.title = title
            self.data = dict(data or {})
            self.options = dict(options or {})
            self.unique_id = unique_id
            self.domain = domain
            self.runtime_data: Any = None
            self.reauth_calls: list[dict[str, Any]] = []
            self.unload_callbacks: list[Callable[[], Any]] = []

        def async_on_unload(self, cb: Callable[[], Any]) -> None:
            self.unload_callbacks.append(cb)

        def async_get_active_flows(
            self, hass: Any, sources: set[str] | None = None
        ) -> list[Any]:
            return []

        def async_start_reauth(
            self,
            hass: Any,
            context: dict[str, Any] | None = None,
            data: dict[str, Any] | None = None,
        ) -> None:
            self.reauth_calls.append({"context": context, "data": data})

        def as_dict(self) -> dict[str, Any]:
            return {
                "entry_id": self.entry_id,
                "title": self.title,
                "data": dict(self.data),
                "options": dict(self.options),
                "unique_id": self.unique_id,
                "domain": self.domain,
            }

    class ConfigFlow:
        def __init_subclass__(cls, **kwargs: Any) -> None:
            super().__init_subclass__()

        def __init__(self) -> None:
            self.hass: Any = None
            self.context: dict[str, Any] = {}
            self._unique_id: str | None = None

        async def async_set_unique_id(self, unique_id: str | None) -> None:
            self._unique_id = unique_id

        def _abort_if_unique_id_configured(self) -> None:
            for existing in self._async_current_entries():
                if existing.unique_id == self._unique_id:
                    raise RuntimeError("already_configured")

        def _async_current_entries(self) -> list[Any]:
            return getattr(self, "_test_entries", [])

        def _get_reauth_entry(self) -> ConfigEntry:
            return self.context["reauth_entry"]

        def _get_reconfigure_entry(self) -> ConfigEntry:
            return self.context["reconfigure_entry"]

        def async_show_menu(self, *, step_id: str, menu_options: list[str]) -> dict[str, Any]:
            return {"type": "menu", "step_id": step_id, "menu_options": menu_options}

        def async_show_form(
            self,
            *,
            step_id: str,
            data_schema: Any = None,
            errors: dict[str, str] | None = None,
            description_placeholders: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            return {
                "type": "form",
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": errors or {},
                "description_placeholders": description_placeholders or {},
            }

        def async_create_entry(
            self,
            *,
            title: str = "",
            data: dict[str, Any] | None = None,
            options: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            return {
                "type": "create_entry",
                "title": title,
                "data": dict(data or {}),
                "options": dict(options or {}),
            }

        def async_abort(self, *, reason: str) -> dict[str, Any]:
            return {"type": "abort", "reason": reason}

        def async_update_reload_and_abort(
            self,
            entry: ConfigEntry,
            *,
            data_updates: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            if data_updates:
                entry.data = {**entry.data, **data_updates}
            return {"type": "abort", "reason": "reconfigure_successful", "entry": entry}

    class OptionsFlow:
        def __init__(self) -> None:
            self._test_config_entry: ConfigEntry | None = None

        @property
        def config_entry(self) -> ConfigEntry:
            assert self._test_config_entry is not None
            return self._test_config_entry

        def async_show_form(
            self,
            *,
            step_id: str,
            data_schema: Any = None,
            errors: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            return {
                "type": "form",
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": errors or {},
            }

        def async_create_entry(self, *, data: dict[str, Any]) -> dict[str, Any]:
            return {"type": "create_entry", "data": dict(data)}

    class OptionsFlowWithReload(OptionsFlow):
        automatic_reload = True

    ce_mod.ConfigEntry = ConfigEntry  # type: ignore[attr-defined]
    ce_mod.ConfigFlow = ConfigFlow  # type: ignore[attr-defined]
    ce_mod.ConfigFlowResult = dict  # type: ignore[attr-defined]
    ce_mod.OptionsFlow = OptionsFlow  # type: ignore[attr-defined]
    ce_mod.OptionsFlowWithReload = OptionsFlowWithReload  # type: ignore[attr-defined]

    # homeassistant.helpers.*
    helpers_mod = types.ModuleType("homeassistant.helpers")
    helpers_mod.__path__ = []  # type: ignore[attr-defined]

    aio_mod = types.ModuleType("homeassistant.helpers.aiohttp_client")
    aio_mod.async_get_clientsession = lambda hass, verify_ssl=True: getattr(  # type: ignore[attr-defined]
        hass, "_test_session", None
    )

    dr_mod = types.ModuleType("homeassistant.helpers.device_registry")
    dr_mod.CONNECTION_NETWORK_MAC = "mac"  # type: ignore[attr-defined]

    def format_mac(value: str) -> str:
        cleaned = re.sub(r"[^0-9a-fA-F]", "", value).lower()
        return ":".join(cleaned[i : i + 2] for i in range(0, 12, 2)) if len(cleaned) == 12 else value.lower()

    @dataclass
    class DeviceInfo:
        identifiers: set[tuple[str, str]] | None = None
        connections: set[tuple[str, str]] | None = None
        via_device_id: str | None = None
        manufacturer: str | None = None
        model: str | None = None
        serial_number: str | None = None
        sw_version: str | None = None
        name: str | None = None

    @dataclass
    class DeviceEntry:
        id: str
        identifiers: set[tuple[str, str]] = field(default_factory=set)
        connections: set[tuple[str, str]] = field(default_factory=set)
        name: str | None = None
        name_by_user: str | None = None
        via_device_id: str | None = None

    class FakeDeviceRegistry:
        def __init__(self) -> None:
            self.devices: dict[str, DeviceEntry] = {}

        def async_get(self, device_id: str) -> DeviceEntry | None:
            return self.devices.get(device_id)

        def async_get_or_create(self, **kwargs: Any) -> DeviceEntry:
            idents = set(kwargs.get("identifiers") or set())
            conns = set(kwargs.get("connections") or set())
            for dev in self.devices.values():
                if (idents and (dev.identifiers & idents)) or (conns and (dev.connections & conns)):
                    # Mirror real HA DeviceRegistry behavior updating name when supplied
                    if kwargs.get("name"):
                        dev.name = kwargs["name"]
                    return dev
            dev_id = f"dev_{len(self.devices) + 1}"
            dev = DeviceEntry(
                id=dev_id,
                identifiers=idents,
                connections=conns,
                name=kwargs.get("name"),
                via_device_id=kwargs.get("via_device_id"),
            )
            self.devices[dev_id] = dev
            return dev

        def async_get_device(self, *, identifiers: set[tuple[str, str]]) -> DeviceEntry | None:
            for dev in self.devices.values():
                if dev.identifiers & identifiers:
                    return dev
            return None

        def async_remove_device(self, device_id: str) -> None:
            self.devices.pop(device_id, None)

    dr_mod.format_mac = format_mac  # type: ignore[attr-defined]
    dr_mod.DeviceInfo = DeviceInfo  # type: ignore[attr-defined]
    dr_mod.DeviceEntry = DeviceEntry  # type: ignore[attr-defined]
    dr_mod.async_get = lambda hass: hass.data.setdefault("_dr", FakeDeviceRegistry())  # type: ignore[attr-defined]

    er_mod = types.ModuleType("homeassistant.helpers.entity_registry")

    class RegistryEntryDisabler(str, Enum):
        USER = "user"
        INTEGRATION = "integration"

    @dataclass
    class RegistryEntry:
        entity_id: str
        domain: str
        platform: str
        unique_id: str
        config_entry_id: str
        disabled_by: RegistryEntryDisabler | None = None
        original_name: str | None = None
        device_id: str | None = None

    class FakeEntityCollection:
        def __init__(self) -> None:
            self.items: dict[str, RegistryEntry] = {}

        def get_entries_for_config_entry_id(self, entry_id: str) -> list[RegistryEntry]:
            return [e for e in self.items.values() if e.config_entry_id == entry_id]

    class FakeEntityRegistry:
        def __init__(self) -> None:
            self.entities = FakeEntityCollection()

        def async_get_entity_id(
            self, domain: str, platform: str, unique_id: str
        ) -> str | None:
            for reg_entry in self.entities.items.values():
                if (
                    reg_entry.domain == domain
                    and reg_entry.platform == platform
                    and reg_entry.unique_id == unique_id
                ):
                    return reg_entry.entity_id
            return None

        def async_update_entity(self, entity_id: str, **kwargs: Any) -> RegistryEntry:
            entry = self.entities.items[entity_id]
            if "new_unique_id" in kwargs:
                new_uid = kwargs["new_unique_id"]
                conflict = self.async_get_entity_id(entry.domain, entry.platform, new_uid)
                if conflict and conflict != entity_id:
                    raise ValueError(
                        f"Unique id '{new_uid}' is already in use by '{conflict}'"
                    )
                entry.unique_id = new_uid
            if "disabled_by" in kwargs:
                entry.disabled_by = kwargs["disabled_by"]
            return entry

        def async_remove(self, entity_id: str) -> None:
            self.entities.items.pop(entity_id, None)

    async def async_migrate_entries(
        hass: HomeAssistant,
        config_entry_id: str,
        entry_callback: Callable[[RegistryEntry], dict[str, Any] | None],
    ) -> None:
        ent_reg = er_mod.async_get(hass)
        for entry in list(ent_reg.entities.get_entries_for_config_entry_id(config_entry_id)):
            if (
                entry.entity_id in ent_reg.entities.items
                and (updates := entry_callback(entry)) is not None
            ):
                ent_reg.async_update_entity(entry.entity_id, **updates)

    er_mod.RegistryEntryDisabler = RegistryEntryDisabler  # type: ignore[attr-defined]
    er_mod.RegistryEntry = RegistryEntry  # type: ignore[attr-defined]
    er_mod.async_get = lambda hass: hass.data.setdefault("_er", FakeEntityRegistry())  # type: ignore[attr-defined]
    er_mod.async_migrate_entries = async_migrate_entries  # type: ignore[attr-defined]

    ep_mod = types.ModuleType("homeassistant.helpers.entity_platform")
    ep_mod.AddConfigEntryEntitiesCallback = Callable[[list[Any]], None]  # type: ignore[attr-defined]

    sel_mod = types.ModuleType("homeassistant.helpers.selector")

    class _BaseSel:
        def __init__(self, config: Any = None) -> None:
            self.config = config

        def __call__(self, value: Any) -> Any:
            return value

    class _ModeEnum(str, Enum):
        BOX = "box"
        DROPDOWN = "dropdown"
        LIST = "list"
        PASSWORD = "password"

    sel_mod.BooleanSelector = _BaseSel  # type: ignore[attr-defined]
    sel_mod.NumberSelector = _BaseSel  # type: ignore[attr-defined]
    sel_mod.NumberSelectorConfig = dict  # type: ignore[attr-defined]
    sel_mod.NumberSelectorMode = _ModeEnum  # type: ignore[attr-defined]
    sel_mod.SelectSelector = _BaseSel  # type: ignore[attr-defined]
    sel_mod.SelectSelectorConfig = dict  # type: ignore[attr-defined]
    sel_mod.SelectSelectorMode = _ModeEnum  # type: ignore[attr-defined]
    sel_mod.TextSelector = _BaseSel  # type: ignore[attr-defined]
    sel_mod.TextSelectorConfig = dict  # type: ignore[attr-defined]
    sel_mod.TextSelectorType = _ModeEnum  # type: ignore[attr-defined]

    uc_mod = types.ModuleType("homeassistant.helpers.update_coordinator")

    class UpdateFailed(Exception):
        pass

    class DataUpdateCoordinator:
        __class_getitem__ = classmethod(lambda cls, item: cls)

        def __init__(
            self,
            hass: HomeAssistant,
            logger: logging.Logger,
            *,
            config_entry: Any = None,
            name: str = "",
            update_interval: timedelta | None = None,
        ) -> None:
            self.hass = hass
            self.logger = logger
            self.config_entry = config_entry
            self.name = name
            self.update_interval = update_interval
            self.data: Any = None
            self.last_update_success = True
            self._listeners: list[Callable[[], None]] = []

        def async_add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
            self._listeners.append(cb)
            return lambda: self._listeners.remove(cb) if cb in self._listeners else None

        async def async_config_entry_first_refresh(self) -> None:
            self.data = await self._async_update_data()

        async def async_refresh(self) -> None:
            try:
                self.data = await self._async_update_data()
                self.last_update_success = True
            except UpdateFailed:
                self.last_update_success = False
            for cb in list(self._listeners):
                cb()

        async def _async_update_data(self) -> Any:
            raise NotImplementedError

    class _BaseEntity:
        _attr_has_entity_name = False
        _attr_entity_registry_enabled_default = True
        _attr_device_info: Any = None
        _attr_unique_id: str | None = None
        _entity_component_unrecorded_attributes: frozenset[str] = frozenset()
        _unrecorded_attributes: frozenset[str] = frozenset()
        _Entity__combined_unrecorded_attributes: frozenset[str] = frozenset()
        entity_id: str | None = None

        def __init_subclass__(cls, **kwargs: Any) -> None:
            super().__init_subclass__(**kwargs)
            cls._Entity__combined_unrecorded_attributes = (
                cls._entity_component_unrecorded_attributes
                | cls._unrecorded_attributes
            )

        @property
        def unique_id(self) -> str | None:
            return self._attr_unique_id

    class CoordinatorEntity(_BaseEntity):
        __class_getitem__ = classmethod(lambda cls, item: cls)

        def __init__(self, coordinator: Any) -> None:
            self.coordinator = coordinator
            self._on_remove: list[Callable[[], None]] = []

        def async_on_remove(self, func: Callable[[], None]) -> None:
            self._on_remove.append(func)

        @property
        def available(self) -> bool:
            return bool(self.coordinator.last_update_success)

        @property
        def entity_registry_enabled_default(self) -> bool:
            return bool(self._attr_entity_registry_enabled_default)

    uc_mod.DataUpdateCoordinator = DataUpdateCoordinator  # type: ignore[attr-defined]
    uc_mod.CoordinatorEntity = CoordinatorEntity  # type: ignore[attr-defined]
    uc_mod.UpdateFailed = UpdateFailed  # type: ignore[attr-defined]

    # homeassistant.components.*
    comps_mod = types.ModuleType("homeassistant.components")
    comps_mod.__path__ = []  # type: ignore[attr-defined]

    dt_comp = types.ModuleType("homeassistant.components.device_tracker")

    class SourceType(str, Enum):
        ROUTER = "router"

    class ScannerEntity(_BaseEntity):
        _attr_entity_registry_enabled_default = False
        _attr_source_type = SourceType.ROUTER

        @property
        def unique_id(self) -> str | None:
            return getattr(self, "mac_address", None)

        @property
        def state(self) -> str:
            return "home" if getattr(self, "is_connected", False) else "not_home"

    dt_comp.DOMAIN = "device_tracker"  # type: ignore[attr-defined]
    dt_comp.ScannerEntity = ScannerEntity  # type: ignore[attr-defined]
    dt_comp.SourceType = SourceType  # type: ignore[attr-defined]

    sensor_comp = types.ModuleType("homeassistant.components.sensor")

    class SensorDeviceClass(str, Enum):
        TIMESTAMP = "timestamp"

    class SensorStateClass(str, Enum):
        MEASUREMENT = "measurement"

    class SensorEntity(_BaseEntity):
        pass

    sensor_comp.DOMAIN = "sensor"  # type: ignore[attr-defined]
    sensor_comp.SensorDeviceClass = SensorDeviceClass  # type: ignore[attr-defined]
    sensor_comp.SensorStateClass = SensorStateClass  # type: ignore[attr-defined]
    sensor_comp.SensorEntity = SensorEntity  # type: ignore[attr-defined]

    bs_comp = types.ModuleType("homeassistant.components.binary_sensor")

    class BinarySensorDeviceClass(str, Enum):
        CONNECTIVITY = "connectivity"

    class BinarySensorEntity(_BaseEntity):
        pass

    bs_comp.DOMAIN = "binary_sensor"  # type: ignore[attr-defined]
    bs_comp.BinarySensorDeviceClass = BinarySensorDeviceClass  # type: ignore[attr-defined]
    bs_comp.BinarySensorEntity = BinarySensorEntity  # type: ignore[attr-defined]

    diag_comp = types.ModuleType("homeassistant.components.diagnostics")
    REDACTED = "**REDACTED**"

    def async_redact_data(data: Any, to_redact: Any) -> Any:
        if isinstance(data, list):
            return [async_redact_data(v, to_redact) for v in data]
        if not isinstance(data, dict):
            return data
        out: dict[str, Any] = {}
        for k, v in data.items():
            if v is not None and v != "" and k in to_redact:
                out[k] = REDACTED
            elif isinstance(v, (dict, list)):
                out[k] = async_redact_data(v, to_redact)
            else:
                out[k] = v
        return out

    diag_comp.REDACTED = REDACTED  # type: ignore[attr-defined]
    diag_comp.async_redact_data = async_redact_data  # type: ignore[attr-defined]

    sys.modules.update(
        {
            "homeassistant": ha,
            "homeassistant.const": const_mod,
            "homeassistant.core": core_mod,
            "homeassistant.exceptions": exc_mod,
            "homeassistant.util": util_mod,
            "homeassistant.util.dt": dt_mod,
            "homeassistant.data_entry_flow": def_mod,
            "homeassistant.config_entries": ce_mod,
            "homeassistant.helpers": helpers_mod,
            "homeassistant.helpers.aiohttp_client": aio_mod,
            "homeassistant.helpers.device_registry": dr_mod,
            "homeassistant.helpers.entity_registry": er_mod,
            "homeassistant.helpers.entity_platform": ep_mod,
            "homeassistant.helpers.selector": sel_mod,
            "homeassistant.helpers.update_coordinator": uc_mod,
            "homeassistant.components": comps_mod,
            "homeassistant.components.device_tracker": dt_comp,
            "homeassistant.components.sensor": sensor_comp,
            "homeassistant.components.binary_sensor": bs_comp,
            "homeassistant.components.diagnostics": diag_comp,
        }
    )


_install_asyncssh_stub()
_install_ha_stubs()
