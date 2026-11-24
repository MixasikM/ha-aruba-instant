# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Switch platform for opt-in WLAN SSID enable/disable controls."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN, SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import ArubaDataUpdateCoordinator, ArubaInstantConfigEntry
from .engine.models import WirelessNetwork
from .entity import ArubaCoordinatorEntity, build_hub_device_info
from .ports.cluster_client import ArubaClientError

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ArubaInstantConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up opt-in per-SSID toggle switches when write controls are enabled."""
    runtime_data = entry.runtime_data
    coordinator = runtime_data.coordinator
    if not coordinator.enable_write_controls:
        return

    hub_info = build_hub_device_info(entry.entry_id, runtime_data.site_name)
    added_profiles: set[str] = set()

    @callback
    def _async_discover_switches() -> None:
        if coordinator.data is None or not coordinator.enable_write_controls:
            return
        new_entities: list[ArubaSsidEnabledSwitch] = []
        for net in coordinator.data.networks:
            profile = (net.profile_name or net.essid or "").strip()
            if not profile or profile in added_profiles:
                continue
            added_profiles.add(profile)
            ent = ArubaSsidEnabledSwitch(
                coordinator,
                runtime_data.site_name,
                net,
                hub_info,
            )
            ent.async_on_remove(lambda p=profile: added_profiles.discard(p))
            new_entities.append(ent)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_discover_switches))
    _async_discover_switches()


class ArubaSsidEnabledSwitch(ArubaCoordinatorEntity, SwitchEntity):
    """Opt-in switch toggling broadcast status of a configured Aruba WLAN SSID profile."""

    _attr_translation_key = "ssid_enabled"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        site_name: str,
        network: WirelessNetwork,
        hub_info: dr.DeviceInfo,
    ) -> None:
        self._profile_name = (network.profile_name or network.essid).strip()
        display_ssid = (network.essid or self._profile_name).strip()
        self._attr_translation_placeholders = {"ssid": display_ssid}
        entry_id = coordinator.config_entry.entry_id
        super().__init__(
            coordinator=coordinator,
            platform_domain=SWITCH_DOMAIN,
            object_id_slug=f"{site_name}_ssid_{display_ssid}_enabled",
            unique_id=f"{entry_id}_ssid_{self._profile_name}_enabled",
            device_info=hub_info,
            fallback_slug=f"{site_name}_ssid_{self._profile_name}_enabled",
        )

    def _current_network(self) -> WirelessNetwork | None:
        if self.coordinator.data is None:
            return None
        for net in self.coordinator.data.networks:
            if (net.profile_name or net.essid).strip() == self._profile_name:
                return net
        return None

    @property
    def is_on(self) -> bool | None:
        """Return True if the SSID profile is currently enabled."""
        net = self._current_network()
        if net is None:
            return None
        return net.active

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose Aruba profile name alongside active status."""
        net = self._current_network()
        if net is None:
            return {}
        return {
            "profile_name": net.profile_name,
            "essid": net.essid or None,
            "network_type": net.network_type or None,
        }

    async def _async_set_state(self, enabled: bool) -> None:
        if not self.coordinator.enable_write_controls:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_controls_disabled",
            )
        try:
            await self.coordinator.client.async_set_ssid_enabled(self._profile_name, enabled)
            await self.coordinator.async_request_refresh()
        except ArubaClientError as exc:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="action_failed",
                translation_placeholders={"error": str(exc)},
            ) from exc

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the WLAN SSID profile."""
        await self._async_set_state(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the WLAN SSID profile."""
        await self._async_set_state(False)
