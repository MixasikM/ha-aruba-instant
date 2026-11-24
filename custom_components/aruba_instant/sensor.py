# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Sensor platform for Aruba Instant cluster, per-AP radios, and roaming context."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    TOPOLOGY_CLUSTER,
    UPTIME_BOOT_DRIFT_TOLERANCE_SECONDS,
)
from .coordinator import ArubaDataUpdateCoordinator, ArubaInstantConfigEntry
from .engine.filters import ClientDecision, evaluate_client
from .engine.models import AccessPoint, WirelessClient, normalize_mac
from .entity import ArubaCoordinatorEntity, build_ap_device_info, build_hub_device_info

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ArubaInstantConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Aruba cluster/site, AP, and optional client roaming sensors."""
    runtime_data = entry.runtime_data
    coordinator = runtime_data.coordinator
    site_name = runtime_data.site_name
    topology_mode = runtime_data.topology_mode
    hub_info = build_hub_device_info(entry.entry_id, site_name)

    static_entities: list[SensorEntity] = [
        ArubaClusterTotalClientsSensor(coordinator, site_name, hub_info)
    ]
    if topology_mode == TOPOLOGY_CLUSTER:
        static_entities.extend(
            [
                ArubaClusterMasterApSensor(coordinator, site_name, hub_info),
                ArubaClusterFirmwareSensor(coordinator, site_name, hub_info),
            ]
        )
    async_add_entities(static_entities)

    added_ssids: set[str] = set()
    added_ap_keys: set[str] = set()
    added_roaming_keys: set[tuple[str, str]] = set()

    # Restore each client roaming sensor independently using DeviceEntry name (not translated entity name)
    if coordinator.enable_roaming_sensors:
        registry = er.async_get(hass)
        dev_registry = dr.async_get(hass)
        prefix = f"{entry.entry_id}_"
        restored_roaming: list[SensorEntity] = []
        for reg_entry in registry.entities.get_entries_for_config_entry_id(entry.entry_id):
            if reg_entry.domain != SENSOR_DOMAIN or not reg_entry.unique_id.startswith(prefix):
                continue
            suffix = reg_entry.unique_id[len(prefix) :]
            sensor_kind: str | None = None
            if suffix.endswith("_access_point"):
                sensor_kind = "access_point"
                raw_mac = suffix.removesuffix("_access_point")
            elif suffix.endswith("_ssid"):
                sensor_kind = "ssid"
                raw_mac = suffix.removesuffix("_ssid")
            else:
                continue

            mac = normalize_mac(raw_mac)
            key = (mac, sensor_kind)
            if key in added_roaming_keys:
                continue
            device_id = getattr(reg_entry, "device_id", None)
            dev_entry = dev_registry.async_get(device_id) if device_id else None
            device_name = (
                (getattr(dev_entry, "name_by_user", None) or dev_entry.name)
                if dev_entry is not None
                else None
            )
            coordinator.restore_client(mac, device_name)
            client = coordinator.get_client(mac)
            if client is not None:
                added_roaming_keys.add(key)
                enabled = reg_entry.disabled_by is None
                ent: SensorEntity = (
                    ArubaClientAccessPointSensor(coordinator, client, enabled)
                    if sensor_kind == "access_point"
                    else ArubaClientSsidSensor(coordinator, client, enabled)
                )
                ent.async_on_remove(lambda k=key: added_roaming_keys.discard(k))
                restored_roaming.append(ent)
        if restored_roaming:
            async_add_entities(restored_roaming)

    @callback
    def _async_discover_dynamic_sensors() -> None:
        """Add sensors for newly discovered SSIDs, APs, radios, and optional roaming clients."""
        snapshot = coordinator.data
        if snapshot is None:
            return
        new_entities: list[SensorEntity] = []

        # 1. Per-SSID client count sensors
        for ssid in snapshot.all_ssids:
            if ssid in added_ssids:
                continue
            added_ssids.add(ssid)
            ent = ArubaSsidClientsSensor(coordinator, site_name, ssid, hub_info)
            ent.async_on_remove(lambda s=ssid: added_ssids.discard(s))
            new_entities.append(ent)

        # 2. Per-AP telemetry sensors
        for ap in snapshot.access_points:
            ap_key = ap.stable_id
            ap_info = build_ap_device_info(coordinator, ap)
            base_sensors: list[tuple[str, type[SensorEntity]]] = [
                ("clients", ArubaApClientsSensor),
                ("5ghz_util", ArubaAp5GhzUtilSensor),
                ("24ghz_util", ArubaAp24GhzUtilSensor),
                ("5ghz_channel", ArubaAp5GhzChannelSensor),
                ("24ghz_channel", ArubaAp24GhzChannelSensor),
                ("firmware", ArubaApFirmwareSensor),
                ("uptime", ArubaApUptimeSensor),
            ]
            for metric_key, cls in base_sensors:
                uid_key = f"{ap_key}:{metric_key}"
                if uid_key not in added_ap_keys:
                    added_ap_keys.add(uid_key)
                    ent = cls(coordinator, ap, ap_info)  # type: ignore[call-arg]
                    ent.async_on_remove(lambda k=uid_key: added_ap_keys.discard(k))
                    new_entities.append(ent)

            if ap.radio_6ghz_channel or ap.radio_6ghz_utilization is not None:
                for metric_key_6g, cls_6g in (
                    ("6ghz_util", ArubaAp6GhzUtilSensor),
                    ("6ghz_channel", ArubaAp6GhzChannelSensor),
                ):
                    uid_6g = f"{ap_key}:{metric_key_6g}"
                    if uid_6g not in added_ap_keys:
                        added_ap_keys.add(uid_6g)
                        ent_6g = cls_6g(coordinator, ap, ap_info)  # type: ignore[call-arg]
                        ent_6g.async_on_remove(lambda k=uid_6g: added_ap_keys.discard(k))
                        new_entities.append(ent_6g)

        # 3. Opt-in diagnostic client roaming sensors
        if coordinator.enable_roaming_sensors:
            cfg = coordinator.filter_config
            for client in snapshot.clients:
                mac = normalize_mac(client.mac)
                decision = evaluate_client(client, cfg)
                if decision == ClientDecision.IGNORE:
                    continue
                enabled = decision == ClientDecision.TRACK_ENABLED
                for sensor_kind, cls in (
                    ("access_point", ArubaClientAccessPointSensor),
                    ("ssid", ArubaClientSsidSensor),
                ):
                    rkey = (mac, sensor_kind)
                    if rkey not in added_roaming_keys:
                        added_roaming_keys.add(rkey)
                        r_ent = cls(coordinator, client, enabled)
                        r_ent.async_on_remove(lambda k=rkey: added_roaming_keys.discard(k))
                        new_entities.append(r_ent)

        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_discover_dynamic_sensors))
    _async_discover_dynamic_sensors()


class ArubaClusterTotalClientsSensor(ArubaCoordinatorEntity, SensorEntity):
    """Total connected clients across the cluster or Multi-AP site."""

    _attr_translation_key = "total_clients"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        site_name: str,
        hub_info: dr.DeviceInfo,
    ) -> None:
        entry_id = coordinator.config_entry.entry_id
        super().__init__(
            coordinator=coordinator,
            platform_domain=SENSOR_DOMAIN,
            object_id_slug=f"{site_name}_total_clients",
            unique_id=f"{entry_id}_total_clients",
            device_info=hub_info,
        )

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return len(self.coordinator.data.clients)


class ArubaClusterMasterApSensor(ArubaCoordinatorEntity, SensorEntity):
    """Current Conductor / Master AP name in Virtual Controller cluster mode."""

    _attr_translation_key = "master_ap"

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        site_name: str,
        hub_info: dr.DeviceInfo,
    ) -> None:
        entry_id = coordinator.config_entry.entry_id
        super().__init__(
            coordinator=coordinator,
            platform_domain=SENSOR_DOMAIN,
            object_id_slug=f"{site_name}_master_ap",
            unique_id=f"{entry_id}_master_ap",
            device_info=hub_info,
        )

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.master_ap_name


class ArubaClusterFirmwareSensor(ArubaCoordinatorEntity, SensorEntity):
    """Active firmware version and partition/DRT details for the Virtual Controller cluster."""

    _attr_translation_key = "cluster_firmware"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        site_name: str,
        hub_info: dr.DeviceInfo,
    ) -> None:
        entry_id = coordinator.config_entry.entry_id
        super().__init__(
            coordinator=coordinator,
            platform_domain=SENSOR_DOMAIN,
            object_id_slug=f"{site_name}_firmware",
            unique_id=f"{entry_id}_firmware",
            device_info=hub_info,
        )

    @property
    def native_value(self) -> str | None:
        snap = self.coordinator.data
        if snap is None:
            return None
        if snap.firmware_info and snap.firmware_info.primary_version:
            return snap.firmware_info.primary_version
        return snap.summary.version

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        snap = self.coordinator.data
        if snap is None or snap.firmware_info is None:
            return {}
        fw = snap.firmware_info
        chk = snap.firmware_check
        return {
            "backup_firmware": fw.backup_version,
            "primary_build_time": fw.primary_build_time,
            "backup_build_time": fw.backup_build_time,
            "image_classes": list(fw.image_classes) if fw.image_classes else None,
            "model_version": fw.model_version_line,
            "last_reboot_cause": fw.last_reboot_cause,
            "upgrade_in_progress": fw.upgrade_in_progress,
            "drt_version": chk.new_drt_version if chk else None,
        }


class ArubaSsidClientsSensor(ArubaCoordinatorEntity, SensorEntity):
    """Active client count for a specific discovered SSID."""

    _attr_translation_key = "ssid_clients"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        site_name: str,
        ssid: str,
        hub_info: dr.DeviceInfo,
    ) -> None:
        self._ssid = ssid
        self._attr_translation_placeholders = {"ssid": ssid}
        entry_id = coordinator.config_entry.entry_id
        super().__init__(
            coordinator=coordinator,
            platform_domain=SENSOR_DOMAIN,
            object_id_slug=f"{site_name}_ssid_{ssid}_clients",
            unique_id=f"{entry_id}_ssid_{ssid}_clients",
            device_info=hub_info,
        )

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        for net in self.coordinator.data.networks:
            if net.essid == self._ssid:
                return net.clients
        return sum(1 for c in self.coordinator.data.clients if c.essid == self._ssid)


class _BaseArubaApSensor(ArubaCoordinatorEntity, SensorEntity):
    """Base entity for per-AP telemetry sensors."""

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        ap: AccessPoint,
        ap_info: dr.DeviceInfo,
        metric_slug: str,
        uid_suffix: str,
    ) -> None:
        self._ap_id = ap.stable_id
        entry_id = coordinator.config_entry.entry_id
        super().__init__(
            coordinator=coordinator,
            platform_domain=SENSOR_DOMAIN,
            object_id_slug=f"{ap.name}_{metric_slug}",
            unique_id=f"{entry_id}_ap_{self._ap_id}_{uid_suffix}",
            device_info=ap_info,
            fallback_slug=f"{ap.stable_id}_{metric_slug}",
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
        """Mark AP telemetry unavailable when the AP itself is offline."""
        if not super().available:
            return False
        ap = self._current_ap()
        return ap is not None and ap.online


class ArubaApClientsSensor(_BaseArubaApSensor):
    """Connected clients count on a physical Access Point."""

    _attr_translation_key = "ap_clients"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        ap: AccessPoint,
        ap_info: dr.DeviceInfo,
    ) -> None:
        super().__init__(coordinator, ap, ap_info, "clients", "clients")

    @property
    def native_value(self) -> int | None:
        ap = self._current_ap()
        return ap.clients if ap is not None else None


class ArubaAp5GhzUtilSensor(_BaseArubaApSensor):
    """5 GHz channel utilization percentage on a physical Access Point."""

    _attr_translation_key = "utilization_5ghz"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        ap: AccessPoint,
        ap_info: dr.DeviceInfo,
    ) -> None:
        super().__init__(coordinator, ap, ap_info, "5ghz_utilization", "5ghz_util")

    @property
    def native_value(self) -> int | None:
        ap = self._current_ap()
        return ap.radio_5ghz_utilization if ap is not None else None


class ArubaAp24GhzUtilSensor(_BaseArubaApSensor):
    """2.4 GHz channel utilization percentage on a physical Access Point."""

    _attr_translation_key = "utilization_2_4ghz"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        ap: AccessPoint,
        ap_info: dr.DeviceInfo,
    ) -> None:
        super().__init__(coordinator, ap, ap_info, "2_4ghz_utilization", "24ghz_util")

    @property
    def native_value(self) -> int | None:
        ap = self._current_ap()
        return ap.radio_2_4ghz_utilization if ap is not None else None


class ArubaAp6GhzUtilSensor(_BaseArubaApSensor):
    """6 GHz channel utilization percentage on Wi-Fi 6E/7 Access Points."""

    _attr_translation_key = "utilization_6ghz"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        ap: AccessPoint,
        ap_info: dr.DeviceInfo,
    ) -> None:
        super().__init__(coordinator, ap, ap_info, "6ghz_utilization", "6ghz_util")

    @property
    def native_value(self) -> int | None:
        ap = self._current_ap()
        return ap.radio_6ghz_utilization if ap is not None else None


class ArubaAp5GhzChannelSensor(_BaseArubaApSensor):
    """5 GHz radio channel sensor on a physical Access Point (recorded for ARM history)."""

    _attr_translation_key = "channel_5ghz"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        ap: AccessPoint,
        ap_info: dr.DeviceInfo,
    ) -> None:
        super().__init__(coordinator, ap, ap_info, "5ghz_channel", "5ghz_channel")

    @property
    def native_value(self) -> str | None:
        ap = self._current_ap()
        if ap is None or not ap.radio_5ghz_channel or ap.radio_5ghz_channel == "--":
            return None
        return ap.radio_5ghz_channel


class ArubaAp24GhzChannelSensor(_BaseArubaApSensor):
    """2.4 GHz radio channel sensor on a physical Access Point (recorded for ARM history)."""

    _attr_translation_key = "channel_2_4ghz"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        ap: AccessPoint,
        ap_info: dr.DeviceInfo,
    ) -> None:
        super().__init__(coordinator, ap, ap_info, "2_4ghz_channel", "24ghz_channel")

    @property
    def native_value(self) -> str | None:
        ap = self._current_ap()
        if ap is None or not ap.radio_2_4ghz_channel or ap.radio_2_4ghz_channel == "--":
            return None
        return ap.radio_2_4ghz_channel


class ArubaAp6GhzChannelSensor(_BaseArubaApSensor):
    """6 GHz radio channel sensor on Wi-Fi 6E/7 Access Points (recorded for ARM history)."""

    _attr_translation_key = "channel_6ghz"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        ap: AccessPoint,
        ap_info: dr.DeviceInfo,
    ) -> None:
        super().__init__(coordinator, ap, ap_info, "6ghz_channel", "6ghz_channel")

    @property
    def native_value(self) -> str | None:
        ap = self._current_ap()
        if ap is None or not ap.radio_6ghz_channel or ap.radio_6ghz_channel == "--":
            return None
        return ap.radio_6ghz_channel


class ArubaApFirmwareSensor(_BaseArubaApSensor):
    """Per-AP active firmware, backup partition, AP image class, serial, and MAC diagnostics."""

    _attr_translation_key = "ap_firmware"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        ap: AccessPoint,
        ap_info: dr.DeviceInfo,
    ) -> None:
        super().__init__(coordinator, ap, ap_info, "firmware", "firmware")

    @property
    def native_value(self) -> str | None:
        ap = self._current_ap()
        if ap is None:
            return None
        return ap.primary_firmware or ap.sw_version

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ap = self._current_ap()
        if ap is None:
            return {}
        return {
            "backup_firmware": ap.backup_firmware,
            "ap_class": ap.ap_class,
            "upgrade_status": ap.upgrade_status,
            "upgrade_error": ap.upgrade_error,
            "serial_number": ap.serial if ap.serial and ap.serial not in ("-", "--") else None,
            "mac_address": dr.format_mac(ap.mac) if ap.mac else None,
        }


class ArubaApUptimeSensor(_BaseArubaApSensor):
    """Last-boot timestamp sensor with 120s jitter suppression to prevent Recorder bloat."""

    _attr_translation_key = "uptime"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        ap: AccessPoint,
        ap_info: dr.DeviceInfo,
    ) -> None:
        super().__init__(coordinator, ap, ap_info, "uptime", "uptime")
        self._cached_boot_dt: datetime | None = None

    @property
    def native_value(self) -> datetime | None:
        ap = self._current_ap()
        if ap is None or ap.uptime_seconds is None:
            return None
        ref_now = (
            self.coordinator.data.fetched_at
            if self.coordinator.data and self.coordinator.data.fetched_at
            else dt_util.utcnow()
        )
        if ref_now.tzinfo is None:
            ref_now = ref_now.replace(tzinfo=timezone.utc)
        computed_boot = ref_now - timedelta(seconds=ap.uptime_seconds)
        if (
            self._cached_boot_dt is None
            or abs((computed_boot - self._cached_boot_dt).total_seconds())
            > UPTIME_BOOT_DRIFT_TOLERANCE_SECONDS
        ):
            self._cached_boot_dt = computed_boot
        return self._cached_boot_dt

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ap = self._current_ap()
        if ap is None or not ap.last_reboot_cause:
            return {}
        return {"last_reboot_cause": ap.last_reboot_cause}


class _BaseClientRoamingSensor(ArubaCoordinatorEntity, SensorEntity):
    """Optional diagnostic sensor tracking a client's active AP or SSID."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        client: WirelessClient,
        enabled_default: bool,
        metric_slug: str,
    ) -> None:
        self._mac = normalize_mac(client.mac)
        formatted_mac = dr.format_mac(self._mac)
        display_name = client.name.strip() or formatted_mac
        entry_id = coordinator.config_entry.entry_id
        device_info = dr.DeviceInfo(
            connections={(dr.CONNECTION_NETWORK_MAC, formatted_mac)},
            name=display_name,
        )
        super().__init__(
            coordinator=coordinator,
            platform_domain=SENSOR_DOMAIN,
            object_id_slug=f"{display_name}_{metric_slug}",
            unique_id=f"{entry_id}_{self._mac}_{metric_slug}",
            device_info=device_info,
            fallback_slug=f"{formatted_mac}_{metric_slug}",
        )
        self._attr_entity_registry_enabled_default = enabled_default


class ArubaClientAccessPointSensor(_BaseClientRoamingSensor):
    """Diagnostic sensor reporting the client's current Access Point."""

    _attr_translation_key = "client_access_point"

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        client: WirelessClient,
        enabled_default: bool,
    ) -> None:
        super().__init__(coordinator, client, enabled_default, "access_point")

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.is_client_connected(self._mac):
            return None
        client = self.coordinator.get_client(self._mac)
        return client.access_point or None if client is not None else None


class ArubaClientSsidSensor(_BaseClientRoamingSensor):
    """Diagnostic sensor reporting the client's current ESSID."""

    _attr_translation_key = "client_ssid"

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        client: WirelessClient,
        enabled_default: bool,
    ) -> None:
        super().__init__(coordinator, client, enabled_default, "ssid")

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.is_client_connected(self._mac):
            return None
        client = self.coordinator.get_client(self._mac)
        return client.essid or None if client is not None else None
