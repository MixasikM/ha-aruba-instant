# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Binary sensor platform for Aruba Access Point online status."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    DOMAIN as BINARY_SENSOR_DOMAIN,
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
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
    added_aps: set[str] = set()

    @callback
    def _async_discover_aps() -> None:
        if coordinator.data is None:
            return
        new_entities: list[ArubaApOnlineBinarySensor] = []
        for ap in coordinator.data.access_points:
            ap_id = ap.stable_id
            if ap_id in added_aps:
                continue
            added_aps.add(ap_id)
            entity = ArubaApOnlineBinarySensor(coordinator, ap)
            entity.async_on_remove(lambda aid=ap_id: added_aps.discard(aid))
            new_entities.append(entity)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_discover_aps))
    _async_discover_aps()


class ArubaApOnlineBinarySensor(ArubaCoordinatorEntity, BinarySensorEntity):
    """Connectivity binary sensor for a physical Aruba Access Point."""

    _attr_translation_key = "ap_online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        ap: AccessPoint,
    ) -> None:
        self._ap_id = ap.stable_id
        entry_id = coordinator.config_entry.entry_id
        super().__init__(
            coordinator=coordinator,
            platform_domain=BINARY_SENSOR_DOMAIN,
            object_id_slug=f"{ap.name}_online",
            unique_id=f"{entry_id}_ap_{self._ap_id}_online",
            device_info=build_ap_device_info(coordinator, ap),
            fallback_slug=f"{ap.stable_id}_online",
        )

    @property
    def is_on(self) -> bool | None:
        """Return True if this Access Point is online."""
        if self.coordinator.data is None:
            return None
        for ap in self.coordinator.data.access_points:
            if ap.stable_id == self._ap_id:
                return ap.online
        return False
