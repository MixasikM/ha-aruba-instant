# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""DataUpdateCoordinator and entity-registry filter sync for Aruba Instant."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, TypeAlias

from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_RECONFIGURE, ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    ACTIVE_UPGRADE_FIRMWARE_REFRESH_SECONDS,
    CONF_CONSIDER_HOME,
    CONF_ENABLE_ROAMING_SENSORS,
    CONF_ENABLE_WRITE_CONTROLS,
    CONF_EXCLUDE_MACS,
    CONF_INCLUDE_WIRED_CLIENTS,
    CONF_MIN_SIGNAL,
    CONF_REQUIRE_IP,
    CONF_SCAN_INTERVAL,
    CONF_TRACK_NEW_CLIENTS,
    CONF_TRACK_UNNAMED_CLIENTS,
    CONF_TRACKED_SSIDS,
    CONF_UNSELECTED_SSID_MODE,
    DEFAULT_CONSIDER_HOME,
    DEFAULT_FIRMWARE_REFRESH_SECONDS,
    DEFAULT_MIN_SIGNAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_TRANSIENT_POLL_FAILURES,
    UNSELECTED_SSID_DISABLE,
    UNSELECTED_SSID_IGNORE,
)
from .engine.filters import ClientDecision, FilterConfig, evaluate_client, parse_mac_set
from .engine.models import (
    ClusterSnapshot,
    FirmwareCheckResult,
    FirmwareImageInfo,
    WirelessClient,
    normalize_mac,
)
from .engine.parsers import enrich_snapshot_with_firmware
from .ports.cluster_client import (
    ArubaAuthError,
    ArubaClientError,
    ArubaMultiAuthError,
    BaseArubaClusterClient,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class ArubaRuntimeData:
    """Runtime objects stored on `ConfigEntry.runtime_data`."""

    coordinator: ArubaDataUpdateCoordinator
    client: BaseArubaClusterClient
    cluster_device_id: str
    site_name: str
    topology_mode: str


ArubaInstantConfigEntry: TypeAlias = ConfigEntry[ArubaRuntimeData]


class ArubaDataUpdateCoordinator(DataUpdateCoordinator[ClusterSnapshot]):
    """Coordinator polling an Aruba Instant cluster or Multi-AP site."""

    config_entry: ArubaInstantConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ArubaInstantConfigEntry,
        client: BaseArubaClusterClient,
        site_name: str,
        topology_mode: str,
    ) -> None:
        scan_seconds = int(
            config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"Aruba {site_name}",
            update_interval=timedelta(seconds=max(5, scan_seconds)),
        )
        self.client = client
        self.site_name = site_name
        self.topology_mode = topology_mode
        self.last_seen: dict[str, datetime] = {}
        self.last_known_essid: dict[str, str] = {}
        self.known_clients: dict[str, WirelessClient] = {}
        self.discovered_ssids: set[str] = set()
        self._consecutive_failures: int = 0
        self._firmware_info: FirmwareImageInfo | None = None
        self._firmware_check: FirmwareCheckResult | None = None
        self._last_firmware_fetch: datetime | None = None
        self._store: Store[dict[str, Any]] = Store(
            hass,
            version=1,
            key=f"{DOMAIN}.{config_entry.entry_id}.clients",
        )

    @property
    def consider_home_delta(self) -> timedelta:
        """Return consider_home hysteresis duration."""
        seconds = int(
            self.config_entry.options.get(CONF_CONSIDER_HOME, DEFAULT_CONSIDER_HOME)
        )
        return timedelta(seconds=max(0, seconds))

    @property
    def enable_roaming_sensors(self) -> bool:
        """Return True if optional client roaming sensors are enabled."""
        return bool(self.config_entry.options.get(CONF_ENABLE_ROAMING_SENSORS, False))

    @property
    def enable_write_controls(self) -> bool:
        """Return True if opt-in write controls (switches, reboot button, firmware install) are enabled."""
        return bool(self.config_entry.options.get(CONF_ENABLE_WRITE_CONTROLS, False))

    async def async_load_client_store(self) -> None:
        """Restore persisted per-MAC last_seen timestamps and ESSIDs from HA storage."""
        try:
            payload = await self._store.async_load()
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        clients_map = payload.get("clients", {})
        if not isinstance(clients_map, dict):
            return
        for raw_mac, meta in clients_map.items():
            if not isinstance(meta, dict):
                continue
            norm = normalize_mac(str(raw_mac))
            seen_iso = meta.get("last_seen")
            if isinstance(seen_iso, str) and seen_iso:
                try:
                    parsed_dt = datetime.fromisoformat(seen_iso)
                    if parsed_dt.tzinfo is None:
                        parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
                    self.last_seen[norm] = parsed_dt
                except ValueError:
                    pass
            essid = str(meta.get("essid") or "").strip()
            if essid:
                self.last_known_essid[norm] = essid

    def _serialize_client_store(self) -> dict[str, Any]:
        """Serialize persisted last_seen & last_known_essid map for HA Store."""
        out: dict[str, dict[str, str]] = {}
        for mac in sorted(set(self.last_seen) | set(self.last_known_essid)):
            item: dict[str, str] = {}
            if mac in self.last_seen:
                item["last_seen"] = self.last_seen[mac].isoformat()
            if mac in self.last_known_essid:
                item["essid"] = self.last_known_essid[mac]
            if item:
                out[mac] = item
        return {"clients": out}

    def schedule_store_save(self) -> None:
        """Schedule debounced persistence of client last_seen metadata."""
        self._store.async_delay_save(self._serialize_client_store, 300)

    async def async_flush_client_store(self) -> None:
        """Persist client metadata immediately (e.g., on unload or purge)."""
        await self._store.async_save(self._serialize_client_store())

    @property
    def filter_config(self) -> FilterConfig:
        """Build pure FilterConfig from current entry options."""
        opts = self.config_entry.options
        tracked = tuple(str(s) for s in opts.get(CONF_TRACKED_SSIDS, []) if str(s))
        excluded = frozenset(parse_mac_set(opts.get(CONF_EXCLUDE_MACS, "")))
        return FilterConfig(
            tracked_ssids=tracked,
            unselected_ssid_mode=str(
                opts.get(CONF_UNSELECTED_SSID_MODE, UNSELECTED_SSID_IGNORE)
            ),
            track_new_clients=bool(opts.get(CONF_TRACK_NEW_CLIENTS, True)),
            track_unnamed_clients=bool(opts.get(CONF_TRACK_UNNAMED_CLIENTS, True)),
            include_wired_clients=bool(opts.get(CONF_INCLUDE_WIRED_CLIENTS, False)),
            require_ip=bool(opts.get(CONF_REQUIRE_IP, False)),
            min_signal=int(opts.get(CONF_MIN_SIGNAL, DEFAULT_MIN_SIGNAL)),
            exclude_macs=excluded,
        )

    def restore_client(self, mac: str, display_name: str | None = None) -> None:
        """Restore a previously registered client so away devices report `not_home` on startup."""
        norm = normalize_mac(mac)
        if norm not in self.last_seen:
            # Start retention clock on upgrade/restore while keeping restored away state off
            offset = max(self.consider_home_delta + timedelta(seconds=60), timedelta(hours=1))
            self.last_seen[norm] = dt_util.utcnow() - offset
            self.schedule_store_save()
        if norm in self.known_clients:
            return
        cleaned_name = (display_name or "").strip()
        if cleaned_name.lower() == format_mac(norm).lower():
            cleaned_name = ""
        self.known_clients[norm] = WirelessClient(
            mac=norm,
            ip_address="",
            name=cleaned_name,
            os="",
            essid="",
            access_point="",
            channel="--",
            phy_type="--",
            role="",
            ipv6_address=None,
            signal=None,
            speed_mbps=None,
            band="",
            is_wired=False,
        )

    def is_client_connected(self, mac: str) -> bool:
        """Return True if client is currently active or within consider_home hysteresis."""
        norm = normalize_mac(mac)
        if self.data is not None:
            for client in self.data.clients:
                if normalize_mac(client.mac) == norm:
                    return True
        seen_at = self.last_seen.get(norm)
        if seen_at is None:
            return False
        delta = self.consider_home_delta
        if delta.total_seconds() <= 0:
            return False
        return (dt_util.utcnow() - seen_at) < delta

    def get_client(self, mac: str) -> WirelessClient | None:
        """Return latest live client record, or last known metadata if offline."""
        norm = normalize_mac(mac)
        if self.data is not None:
            for client in self.data.clients:
                if normalize_mac(client.mac) == norm:
                    return client
        return self.known_clients.get(norm)

    @callback
    def sync_registry_filters(self) -> None:
        """Apply updated SSID / MAC / roaming-sensor filters to existing registry entries."""
        registry = er.async_get(self.hass)
        entries = registry.entities.get_entries_for_config_entry_id(
            self.config_entry.entry_id
        )
        cfg = self.filter_config
        prefix = f"{self.config_entry.entry_id}_"
        roaming_enabled = self.enable_roaming_sensors

        for reg_entry in entries:
            uid = reg_entry.unique_id
            if not uid.startswith(prefix):
                continue
            suffix = uid[len(prefix) :]

            # Detect per-client tracker vs per-client roaming sensor
            mac: str | None = None
            is_roaming_sensor = False
            if reg_entry.domain == "device_tracker":
                mac = normalize_mac(suffix)
            elif reg_entry.domain == "sensor" and (
                suffix.endswith("_access_point") or suffix.endswith("_ssid")
            ):
                is_roaming_sensor = True
                raw_mac = suffix.removesuffix("_access_point").removesuffix("_ssid")
                mac = normalize_mac(raw_mac)

            elif reg_entry.domain == Platform.BUTTON and suffix.endswith("_reboot"):
                if not self.enable_write_controls and reg_entry.disabled_by is None:
                    registry.async_update_entity(
                        reg_entry.entity_id,
                        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
                    )
                elif self.enable_write_controls and reg_entry.disabled_by == er.RegistryEntryDisabler.INTEGRATION:
                    registry.async_update_entity(reg_entry.entity_id, disabled_by=None)
                continue
            elif reg_entry.domain == Platform.SWITCH and suffix.endswith("_enabled"):
                if not self.enable_write_controls and reg_entry.disabled_by is None:
                    registry.async_update_entity(
                        reg_entry.entity_id,
                        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
                    )
                elif self.enable_write_controls and reg_entry.disabled_by == er.RegistryEntryDisabler.INTEGRATION:
                    registry.async_update_entity(reg_entry.entity_id, disabled_by=None)
                continue

            if mac is None:
                continue

            if is_roaming_sensor and not roaming_enabled:
                if reg_entry.disabled_by is None:
                    registry.async_update_entity(
                        reg_entry.entity_id,
                        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
                    )
                continue

            if mac in cfg.exclude_macs:
                if cfg.unselected_ssid_mode == UNSELECTED_SSID_IGNORE:
                    registry.async_remove(reg_entry.entity_id)
                elif reg_entry.disabled_by is None:
                    registry.async_update_entity(
                        reg_entry.entity_id,
                        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
                    )
                continue

            known = self.known_clients.get(mac)
            if known is None or not known.essid:
                # Offline/away client without current ESSID: preserve existing disabled state
                continue

            decision = evaluate_client(known, cfg)
            if decision == ClientDecision.IGNORE:
                registry.async_remove(reg_entry.entity_id)
            elif decision == ClientDecision.CREATE_DISABLED:
                if reg_entry.disabled_by is None:
                    registry.async_update_entity(
                        reg_entry.entity_id,
                        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
                    )
            elif decision == ClientDecision.TRACK_ENABLED:
                if reg_entry.disabled_by == er.RegistryEntryDisabler.INTEGRATION:
                    registry.async_update_entity(reg_entry.entity_id, disabled_by=None)

    async def _async_update_data(self) -> ClusterSnapshot:
        """Fetch latest snapshot from Aruba cluster or Multi-AP pool."""
        try:
            snapshot = await self.client.async_fetch_snapshot()
        except ArubaAuthError as exc:
            if isinstance(exc, ArubaMultiAuthError):
                failed_hosts = list(exc.failed_hosts)
            elif exc.host:
                failed_hosts = [exc.host]
            else:
                failed_hosts = []

            if not any(
                self.config_entry.async_get_active_flows(
                    self.hass, {SOURCE_REAUTH, SOURCE_RECONFIGURE}
                )
            ):
                self.config_entry.async_start_reauth(
                    self.hass, data={"failed_hosts": failed_hosts}
                )
            host_label = ", ".join(failed_hosts) if failed_hosts else self.site_name
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="ap_auth_failed",
                translation_placeholders={"host": host_label},
            ) from exc
        except ArubaClientError as exc:
            self._consecutive_failures += 1
            if (
                self.data is not None
                and self._consecutive_failures <= MAX_TRANSIENT_POLL_FAILURES
            ):
                _LOGGER.debug(
                    "Transient poll failure %d/%d for %s (%s); keeping previous snapshot",
                    self._consecutive_failures,
                    MAX_TRANSIENT_POLL_FAILURES + 1,
                    self.site_name,
                    exc,
                )
                return self.data
            raise UpdateFailed(str(exc)) from exc

        self._consecutive_failures = 0
        now = snapshot.fetched_at or dt_util.utcnow()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        refresh_interval = (
            ACTIVE_UPGRADE_FIRMWARE_REFRESH_SECONDS
            if self._firmware_info is not None and self._firmware_info.upgrade_in_progress
            else DEFAULT_FIRMWARE_REFRESH_SECONDS
        )
        if (
            self._last_firmware_fetch is None
            or (now - self._last_firmware_fetch).total_seconds() >= refresh_interval
        ):
            fetch_fw = getattr(self.client, "async_fetch_firmware_details", None)
            if callable(fetch_fw):
                try:
                    fw_info, fw_check = await fetch_fw()
                    if fw_info is not None or fw_check is not None:
                        self._firmware_info = fw_info
                        self._firmware_check = fw_check
                    self._last_firmware_fetch = now
                except ArubaAuthError:
                    self._last_firmware_fetch = now
                except ArubaClientError as exc:
                    self._last_firmware_fetch = now
                    _LOGGER.debug(
                        "Auxiliary firmware refresh skipped for %s: %s",
                        self.site_name,
                        exc,
                    )
            else:
                self._last_firmware_fetch = now

        if self._firmware_info is not None or self._firmware_check is not None:
            snapshot = enrich_snapshot_with_firmware(
                snapshot,
                self._firmware_info,
                self._firmware_check,
            )

        self._sync_device_registry_metadata(snapshot)

        for ssid in snapshot.all_ssids:
            self.discovered_ssids.add(ssid)

        clients_updated = False
        for client in snapshot.clients:
            norm = normalize_mac(client.mac)
            self.last_seen[norm] = now
            self.known_clients[norm] = client
            if client.essid and client.essid != "--":
                self.last_known_essid[norm] = client.essid
            clients_updated = True
        if clients_updated:
            self.schedule_store_save()

        return snapshot

    async def async_refresh_firmware_now(self) -> None:
        """Force an immediate firmware & image-check refresh outside normal 30-min cadence."""
        self._last_firmware_fetch = None
        await self.async_refresh()

    @callback
    def _sync_device_registry_metadata(self, snapshot: ClusterSnapshot) -> None:
        """Update Hub and AP DeviceEntry hardware/firmware fields only when values change."""
        dev_reg = dr.async_get(self.hass)
        entry_id = self.config_entry.entry_id

        hub_device = dev_reg.async_get_device_by_identifier(
            (DOMAIN, f"hub_{entry_id}"), entry_id
        )
        hub_sw = (
            (snapshot.firmware_info.primary_version if snapshot.firmware_info else None)
            or snapshot.summary.version
        )
        if hub_device is not None and hub_sw and getattr(hub_device, "sw_version", None) != hub_sw:
            dev_reg.async_update_device(hub_device.id, sw_version=hub_sw)

        for ap in snapshot.access_points:
            ap_dev = dev_reg.async_get_device_by_identifier(
                (DOMAIN, f"ap_{entry_id}_{ap.stable_id}"), entry_id
            )
            if ap_dev is None:
                continue
            updates: dict[str, Any] = {}
            target_sw = ap.primary_firmware or ap.sw_version
            if target_sw and getattr(ap_dev, "sw_version", None) != target_sw:
                updates["sw_version"] = target_sw
            target_hw = ap.ap_class or None
            if target_hw and getattr(ap_dev, "hw_version", None) != target_hw:
                updates["hw_version"] = target_hw
            target_serial = ap.serial if ap.serial and ap.serial not in ("-", "--") else None
            if target_serial and getattr(ap_dev, "serial_number", None) != target_serial:
                updates["serial_number"] = target_serial
            if updates:
                dev_reg.async_update_device(ap_dev.id, **updates)
