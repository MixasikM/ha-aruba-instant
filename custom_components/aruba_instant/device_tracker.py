# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Device tracker platform (`ScannerEntity`) for Aruba Instant / AOS Wi-Fi."""
from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import (
    DOMAIN as DEVICE_TRACKER_DOMAIN,
    ScannerEntity,
    SourceType,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ArubaDataUpdateCoordinator, ArubaInstantConfigEntry
from .engine.filters import ClientDecision, evaluate_client
from .engine.models import WirelessClient, normalize_mac
from .entity import ArubaCoordinatorEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ArubaInstantConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Aruba Wi-Fi client device trackers."""
    coordinator = entry.runtime_data.coordinator
    tracked: dict[str, ArubaClientScannerEntity] = {}
    prefix = f"{entry.entry_id}_"

    # Restore previously registered trackers using DeviceEntry name (not translated entity name)
    registry = er.async_get(hass)
    dev_registry = dr.async_get(hass)
    initial_entities: list[ArubaClientScannerEntity] = []
    for reg_entry in registry.entities.get_entries_for_config_entry_id(entry.entry_id):
        if reg_entry.domain != DEVICE_TRACKER_DOMAIN:
            continue
        if not reg_entry.unique_id.startswith(prefix):
            continue
        mac = normalize_mac(reg_entry.unique_id[len(prefix) :])
        device_id = getattr(reg_entry, "device_id", None)
        dev_entry = dev_registry.async_get(device_id) if device_id else None
        device_name = (
            (getattr(dev_entry, "name_by_user", None) or dev_entry.name)
            if dev_entry is not None
            else None
        )
        coordinator.restore_client(mac, device_name)
        restored_client = coordinator.get_client(mac)
        if restored_client is not None and mac not in tracked:
            entity = ArubaClientScannerEntity(
                coordinator=coordinator,
                client=restored_client,
                enabled_default=reg_entry.disabled_by is None,
            )
            entity.async_on_remove(lambda m=mac: tracked.pop(m, None))
            tracked[mac] = entity
            initial_entities.append(entity)

    if initial_entities:
        async_add_entities(initial_entities)

    @callback
    def _async_update_clients() -> None:
        """Register newly seen clients passing filter criteria."""
        if coordinator.data is None:
            return
        cfg = coordinator.filter_config
        new_entities: list[ArubaClientScannerEntity] = []
        for client in coordinator.data.clients:
            mac = normalize_mac(client.mac)
            if mac in tracked:
                continue
            decision = evaluate_client(client, cfg)
            if decision == ClientDecision.IGNORE:
                continue
            entity = ArubaClientScannerEntity(
                coordinator=coordinator,
                client=client,
                enabled_default=(decision == ClientDecision.TRACK_ENABLED),
            )
            entity.async_on_remove(lambda m=mac: tracked.pop(m, None))
            tracked[mac] = entity
            new_entities.append(entity)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_update_clients))
    _async_update_clients()


class ArubaClientScannerEntity(ArubaCoordinatorEntity, ScannerEntity):
    """Tracked Wi-Fi (or optionally wired) client on an Aruba network."""

    _attr_translation_key = "wifi_client"
    _unrecorded_attributes = frozenset({"signal", "speed_mbps"})

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        client: WirelessClient,
        enabled_default: bool,
    ) -> None:
        self._mac = normalize_mac(client.mac)
        formatted_mac = dr.format_mac(self._mac)
        display_name = client.name.strip() or formatted_mac
        self._unique_id = f"{coordinator.config_entry.entry_id}_{self._mac}"
        device_info = dr.DeviceInfo(
            connections={(dr.CONNECTION_NETWORK_MAC, formatted_mac)},
            name=display_name,
        )
        super().__init__(
            coordinator=coordinator,
            platform_domain=DEVICE_TRACKER_DOMAIN,
            object_id_slug=client.name.strip() or formatted_mac,
            unique_id=self._unique_id,
            device_info=device_info,
            fallback_slug=formatted_mac,
        )
        # Override ScannerEntity's False default based on user filter policy
        self._attr_entity_registry_enabled_default = enabled_default

    @property
    def unique_id(self) -> str:
        """Return entry-scoped unique ID, overriding ScannerEntity.unique_id."""
        return self._unique_id

    @property
    def source_type(self) -> SourceType:
        """Return router scanner source type."""
        return SourceType.ROUTER

    @property
    def is_connected(self) -> bool:
        """Return True when client is active or within consider_home hysteresis."""
        return self.coordinator.is_client_connected(self._mac)

    @property
    def mac_address(self) -> str:
        """Return formatted MAC address."""
        return dr.format_mac(self._mac)

    @property
    def ip_address(self) -> str | None:
        """Return current client IPv4 address if assigned."""
        client = self.coordinator.get_client(self._mac)
        if client is None or client.ip_address in ("", "0.0.0.0", "--"):
            return None
        return client.ip_address

    @property
    def hostname(self) -> str | None:
        """Return client hostname."""
        client = self.coordinator.get_client(self._mac)
        if client is None or not client.name.strip():
            return None
        return client.name.strip()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose Aruba Wi-Fi roaming attributes without overriding final state_attributes."""
        if not self.is_connected:
            return {}
        client = self.coordinator.get_client(self._mac)
        if client is None:
            return {}
        return {
            "essid": client.essid or None,
            "access_point": client.access_point or None,
            "band": client.band or None,
            "channel": client.channel if client.channel not in ("", "--") else None,
            "phy_type": client.phy_type if client.phy_type not in ("", "--") else None,
            "signal": client.signal,
            "speed_mbps": client.speed_mbps,
            "os": client.os or None,
            "role": client.role or None,
            "ipv6": client.ipv6_address,
        }
