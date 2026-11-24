# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Button platform for opt-in per-AP restart actions."""
from __future__ import annotations

from homeassistant.components.button import (
    DOMAIN as BUTTON_DOMAIN,
    ButtonDeviceClass,
    ButtonEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import ArubaDataUpdateCoordinator, ArubaInstantConfigEntry
from .engine.models import AccessPoint
from .entity import ArubaCoordinatorEntity, build_ap_device_info
from .ports.cluster_client import ArubaClientError

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ArubaInstantConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up opt-in per-AP reboot buttons when write controls are enabled."""
    coordinator = entry.runtime_data.coordinator
    if not coordinator.enable_write_controls:
        return

    added_aps: set[str] = set()

    @callback
    def _async_discover_buttons() -> None:
        if coordinator.data is None or not coordinator.enable_write_controls:
            return
        new_entities: list[ArubaApRebootButton] = []
        for ap in coordinator.data.access_points:
            ap_id = ap.stable_id
            if ap_id in added_aps:
                continue
            added_aps.add(ap_id)
            ent = ArubaApRebootButton(coordinator, ap)
            ent.async_on_remove(lambda aid=ap_id: added_aps.discard(aid))
            new_entities.append(ent)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_discover_buttons))
    _async_discover_buttons()


class ArubaApRebootButton(ArubaCoordinatorEntity, ButtonEntity):
    """Opt-in restart button for an individual Aruba Access Point."""

    _attr_translation_key = "ap_reboot"
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        ap: AccessPoint,
    ) -> None:
        self._ap_id = ap.stable_id
        entry_id = coordinator.config_entry.entry_id
        super().__init__(
            coordinator=coordinator,
            platform_domain=BUTTON_DOMAIN,
            object_id_slug=f"{ap.name}_reboot",
            unique_id=f"{entry_id}_ap_{self._ap_id}_reboot",
            device_info=build_ap_device_info(coordinator, ap),
            fallback_slug=f"{ap.stable_id}_reboot",
        )

    def _current_ap(self) -> AccessPoint | None:
        if self.coordinator.data is None:
            return None
        for ap in self.coordinator.data.access_points:
            if ap.stable_id == self._ap_id:
                return ap
        return None

    @property
    def available(self) -> bool:
        """Disable reboot button if AP is offline or write controls are disabled."""
        if not super().available or not self.coordinator.enable_write_controls:
            return False
        ap = self._current_ap()
        return ap is not None and ap.online and bool(ap.ip_address)

    async def async_press(self) -> None:
        """Trigger a single-AP reboot after checking opt-in write permission."""
        if not self.coordinator.enable_write_controls:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_controls_disabled",
            )
        ap = self._current_ap()
        if ap is None or not ap.ip_address:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="action_failed",
                translation_placeholders={"error": "Target Access Point is unavailable"},
            )
        try:
            await self.coordinator.client.async_reboot_ap(ap.ip_address)
        except ArubaClientError as exc:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="action_failed",
                translation_placeholders={"error": str(exc)},
            ) from exc
