# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Domain service actions (`purge_stale_clients`, `upgrade_firmware`) for Aruba Instant."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import voluptuous as vol

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SERVICE_PURGE_STALE_CLIENTS, SERVICE_UPGRADE_FIRMWARE
from .coordinator import ArubaInstantConfigEntry
from .engine.models import normalize_mac
from .ports.cluster_client import ArubaClientError

_PURGE_SCHEMA = vol.Schema(
    {
        vol.Optional("config_entry_id"): str,
        vol.Optional("stale_days", default=30): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=3650)
        ),
        vol.Optional("ssid"): str,
        vol.Optional("skip_person_bound", default=True): bool,
        vol.Optional("dry_run", default=True): bool,
    }
)

_UPGRADE_SCHEMA = vol.Schema(
    {
        vol.Optional("config_entry_id"): str,
        vol.Required("image_url"): str,
        vol.Optional("ap_class"): str,
        vol.Optional("auto_reboot", default=True): bool,
    }
)


def _resolve_target_entries(
    hass: HomeAssistant,
    call: ServiceCall,
    *,
    allow_multiple: bool,
) -> list[ArubaInstantConfigEntry]:
    """Resolve loaded config entry target(s) from optional `config_entry_id`."""
    loaded: list[ArubaInstantConfigEntry] = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if getattr(entry, "runtime_data", None) is not None
    ]
    entry_id = str(call.data.get("config_entry_id") or "").strip()
    if entry_id:
        match = [e for e in loaded if e.entry_id == entry_id]
        if not match:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_loaded",
            )
        return match

    if not loaded:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
        )
    if not allow_multiple and len(loaded) > 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="config_entry_required",
        )
    return loaded


def _collect_person_bound_trackers(hass: HomeAssistant) -> set[str]:
    """Return all entity IDs referenced in `person.*` `device_trackers` attributes."""
    bound: set[str] = set()
    states = getattr(hass, "states", None)
    if states is None or not hasattr(states, "async_all"):
        return bound
    for st in states.async_all("person"):
        trackers = (st.attributes or {}).get("device_trackers") or ()
        if isinstance(trackers, (list, tuple, set)):
            for eid in trackers:
                if isinstance(eid, str) and eid:
                    bound.add(eid)
    return bound


def _detach_or_remove_device(
    dev_reg: dr.DeviceRegistry,
    device_id: str,
    entry_id: str,
    ent_reg: er.EntityRegistry,
) -> None:
    """Safely detach a client device from this entry without deleting shared integrations."""
    if dev_reg.async_get(device_id) is None:
        return
    remaining_for_device = er.async_entries_for_device(
        ent_reg, device_id, include_disabled_entities=True
    )
    if remaining_for_device:
        return
    if hasattr(dev_reg, "async_update_device"):
        dev_reg.async_update_device(device_id, remove_config_entry_id=entry_id)
        return
    dev_reg.async_remove_device(device_id)


async def async_register_services(hass: HomeAssistant) -> None:
    """Register `aruba_instant` services once during integration setup."""
    if not hass.services.has_service(DOMAIN, SERVICE_PURGE_STALE_CLIENTS):

        async def _handle_purge_stale_clients(call: ServiceCall) -> ServiceResponse:
            entries = _resolve_target_entries(hass, call, allow_multiple=True)
            stale_days = int(call.data.get("stale_days", 30))
            ssid_filter = str(call.data.get("ssid") or "").strip().lower()
            skip_person_bound = bool(call.data.get("skip_person_bound", True))
            dry_run = bool(call.data.get("dry_run", True))
            cutoff = dt_util.utcnow() - timedelta(days=stale_days)

            ent_reg = er.async_get(hass)
            dev_reg = dr.async_get(hass)
            person_bound = _collect_person_bound_trackers(hass) if skip_person_bound else set()

            purged: list[str] = []
            skipped_person: list[str] = []

            for entry in entries:
                coord = entry.runtime_data.coordinator
                prefix = f"{entry.entry_id}_"
                reg_entries = list(
                    ent_reg.entities.get_entries_for_config_entry_id(entry.entry_id)
                )
                entry_changed = False

                # Index roaming sensor entries by normalized MAC for cascade cleanup
                roaming_by_mac: dict[str, list[er.RegistryEntry]] = {}
                for re_item in reg_entries:
                    if (
                        re_item.domain == Platform.SENSOR
                        and re_item.unique_id.startswith(prefix)
                    ):
                        suf = re_item.unique_id[len(prefix) :]
                        if suf.endswith("_access_point") or suf.endswith("_ssid"):
                            raw_m = suf.removesuffix("_access_point").removesuffix("_ssid")
                            roaming_by_mac.setdefault(normalize_mac(raw_m), []).append(re_item)

                for reg_entry in reg_entries:
                    if (
                        reg_entry.domain != Platform.DEVICE_TRACKER
                        or not reg_entry.unique_id.startswith(prefix)
                    ):
                        continue
                    mac = normalize_mac(reg_entry.unique_id[len(prefix) :])
                    if coord.is_client_connected(mac):
                        continue

                    seen_at = coord.last_seen.get(mac)
                    if seen_at is None:
                        continue
                    if seen_at.tzinfo is None:
                        seen_at = seen_at.replace(tzinfo=timezone.utc)
                    if seen_at > cutoff:
                        continue

                    if ssid_filter:
                        known = coord.known_clients.get(mac)
                        client_ssid = (
                            coord.last_known_essid.get(mac)
                            or (known.essid if known else "")
                            or ""
                        ).strip().lower()
                        if client_ssid != ssid_filter:
                            continue

                    if skip_person_bound and reg_entry.entity_id in person_bound:
                        skipped_person.append(reg_entry.entity_id)
                        continue

                    purged.append(reg_entry.entity_id)
                    if not dry_run:
                        device_id = getattr(reg_entry, "device_id", None)
                        ent_reg.async_remove(reg_entry.entity_id)
                        for extra_sensor in roaming_by_mac.get(mac, ()):
                            ent_reg.async_remove(extra_sensor.entity_id)
                        if device_id:
                            _detach_or_remove_device(dev_reg, device_id, entry.entry_id, ent_reg)
                        coord.last_seen.pop(mac, None)
                        coord.last_known_essid.pop(mac, None)
                        coord.known_clients.pop(mac, None)
                        entry_changed = True

                if entry_changed:
                    await coord.async_flush_client_store()

            return {
                "dry_run": dry_run,
                "purged_entities": purged,
                "skipped_person_entities": skipped_person,
            }

        hass.services.async_register(
            DOMAIN,
            SERVICE_PURGE_STALE_CLIENTS,
            _handle_purge_stale_clients,
            schema=_PURGE_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_UPGRADE_FIRMWARE):

        async def _handle_upgrade_firmware(call: ServiceCall) -> None:
            entries = _resolve_target_entries(hass, call, allow_multiple=False)
            entry = entries[0]
            coord = entry.runtime_data.coordinator
            if not coord.enable_write_controls:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="write_controls_disabled",
                )

            image_url = str(call.data["image_url"]).strip()
            auto_reboot = bool(call.data.get("auto_reboot", True))
            ap_class = str(call.data.get("ap_class") or "").strip() or None
            if ap_class is None:
                fw_info = coord.data.firmware_info if coord.data is not None else None
                classes = fw_info.image_classes if fw_info is not None else ()
                if len(classes) == 1:
                    ap_class = classes[0]
                else:
                    raise ServiceValidationError(
                        translation_domain=DOMAIN,
                        translation_key="ambiguous_ap_class",
                    )

            try:
                await coord.client.async_upgrade_firmware(
                    image_url,
                    ap_class=ap_class,
                    auto_reboot=auto_reboot,
                )
                await coord.async_refresh_firmware_now()
            except ArubaClientError as exc:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="action_failed",
                    translation_placeholders={"error": str(exc)},
                ) from exc

        hass.services.async_register(
            DOMAIN,
            SERVICE_UPGRADE_FIRMWARE,
            _handle_upgrade_firmware,
            schema=_UPGRADE_SCHEMA,
        )
