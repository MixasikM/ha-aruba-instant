# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Binary sensor platform for Aruba Access Point online status."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    DOMAIN as BINARY_SENSOR_DOMAIN,
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ArubaDataUpdateCoordinator, ArubaInstantConfigEntry
from .engine.models import AccessPoint
from .entity import ArubaCoordinatorEntity, build_ap_device_info

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ArubaInstantConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary_sensor entities for each discovered Aruba Access Point."""
    coordinator = entry.runtime_data.coordinator
    added_keys: set[str] = set()

    @callback
    def _async_discover_aps() -> None:
        if coordinator.data is None:
            return
        new_entities: list[BinarySensorEntity] = []
        for ap in coordinator.data.access_points:
            ap_id = ap.stable_id
            online_key = f"{ap_id}:online"
            if online_key not in added_keys:
                added_keys.add(online_key)
                entity = ArubaApOnlineBinarySensor(coordinator, ap)
                entity.async_on_remove(lambda k=online_key: added_keys.discard(k))
                new_entities.append(entity)

            cond_key = f"{ap_id}:conductor"
            if cond_key not in added_keys:
                added_keys.add(cond_key)
                cond_entity = ArubaApConductorBinarySensor(coordinator, ap)
                cond_entity.async_on_remove(lambda k=cond_key: added_keys.discard(k))
                new_entities.append(cond_entity)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_discover_aps))
    _async_discover_aps()


class _BaseArubaApBinarySensor(ArubaCoordinatorEntity, BinarySensorEntity):
    """Shared helper base for per-AP binary sensors."""

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        ap: AccessPoint,
        metric_slug: str,
        uid_suffix: str,
    ) -> None:
        self._ap_id = ap.stable_id
        entry_id = coordinator.config_entry.entry_id
        super().__init__(
            coordinator=coordinator,
            platform_domain=BINARY_SENSOR_DOMAIN,
            object_id_slug=f"{ap.name}_{metric_slug}",
            unique_id=f"{entry_id}_ap_{self._ap_id}_{uid_suffix}",
            device_info=build_ap_device_info(coordinator, ap),
            fallback_slug=f"{ap.stable_id}_{metric_slug}",
        )

    def _current_ap(self) -> AccessPoint | None:
        if self.coordinator.data is None:
            return None
        for ap in self.coordinator.data.access_points:
            if ap.stable_id == self._ap_id:
                return ap
        return None


class ArubaApOnlineBinarySensor(_BaseArubaApBinarySensor):
    """Connectivity binary sensor for a physical Aruba Access Point."""

    _attr_translation_key = "ap_online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        ap: AccessPoint,
    ) -> None:
        super().__init__(coordinator, ap, "online", "online")

    @property
    def is_on(self) -> bool | None:
        """Return True if this Access Point is online."""
        if self.coordinator.data is None:
            return None
        ap = self._current_ap()
        return ap.online if ap is not None else False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose hardware identity attributes (IP, MAC, Serial, Model, AP Class, Conductor)."""
        ap = self._current_ap()
        if ap is None:
            return {}
        return {
            "ip_address": ap.ip_address or None,
            "mac_address": dr.format_mac(ap.mac) if ap.mac else None,
            "serial_number": ap.serial if ap.serial and ap.serial not in ("-", "--") else None,
            "model": ap.model or None,
            "ap_class": ap.ap_class or None,
            "is_conductor": ap.is_conductor,
        }


class ArubaApConductorBinarySensor(_BaseArubaApBinarySensor):
    """Diagnostic indicator showing whether this AP is the active Conductor / Master AP."""

    _attr_translation_key = "ap_conductor"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        ap: AccessPoint,
    ) -> None:
        super().__init__(coordinator, ap, "conductor", "conductor")

    @property
    def available(self) -> bool:
        """Mark conductor status unavailable if this AP is offline."""
        if not super().available:
            return False
        ap = self._current_ap()
        return ap is not None and ap.online

    @property
    def is_on(self) -> bool | None:
        """Return True if this Access Point is currently the active Conductor."""
        ap = self._current_ap()
        return ap.is_conductor if ap is not None else None
