# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Update platform for Aruba Instant Virtual Controller firmware status and upgrades."""
from __future__ import annotations

import re
from typing import Any

from homeassistant.components.update import (
    DOMAIN as UPDATE_DOMAIN,
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, TOPOLOGY_CLUSTER
from .coordinator import ArubaDataUpdateCoordinator, ArubaInstantConfigEntry
from .entity import ArubaCoordinatorEntity, build_hub_device_info
from .ports.cluster_client import ArubaClientError

PARALLEL_UPDATES = 0

_VERSION_TOKEN_RE = re.compile(r"(\d+(?:[._]\d+)*)")


def _normalize_aruba_version(version: str) -> tuple[int, ...]:
    """Convert Aruba build version strings like '8.10.0.24_97188 LSR' to numeric tuple."""
    match = _VERSION_TOKEN_RE.search((version or "").strip())
    if not match:
        return ()
    parts: list[int] = []
    for chunk in match.group(1).replace("_", ".").split("."):
        if chunk.isdigit():
            parts.append(int(chunk))
    return tuple(parts)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ArubaInstantConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up cluster firmware UpdateEntity in Virtual Controller mode."""
    runtime_data = entry.runtime_data
    if runtime_data.topology_mode != TOPOLOGY_CLUSTER:
        return

    hub_info = build_hub_device_info(entry.entry_id, runtime_data.site_name)
    async_add_entities(
        [
            ArubaClusterFirmwareUpdateEntity(
                runtime_data.coordinator,
                runtime_data.site_name,
                hub_info,
            )
        ]
    )


class ArubaClusterFirmwareUpdateEntity(ArubaCoordinatorEntity, UpdateEntity):
    """Virtual Controller firmware entity reporting installed, check status, and progress."""

    _attr_translation_key = "cluster_firmware_update"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_title = "Aruba InstantOS"

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        site_name: str,
        hub_info: Any,
    ) -> None:
        entry_id = coordinator.config_entry.entry_id
        super().__init__(
            coordinator=coordinator,
            platform_domain=UPDATE_DOMAIN,
            object_id_slug=f"{site_name}_firmware",
            unique_id=f"{entry_id}_firmware_update",
            device_info=hub_info,
        )

    @property
    def installed_version(self) -> str | None:
        """Return currently active cluster firmware version."""
        snap = self.coordinator.data
        if snap is None:
            return None
        if snap.firmware_info and snap.firmware_info.primary_version:
            return snap.firmware_info.primary_version
        return snap.summary.version

    @property
    def latest_version(self) -> str | None:
        """Return latest available version, installed_version if up-to-date, or None when unknown."""
        snap = self.coordinator.data
        if snap is None or snap.firmware_check is None:
            return None
        chk = snap.firmware_check
        state = (chk.server_state or "").strip().lower()
        if state != "success":
            return None
        if chk.new_image_version:
            return chk.new_image_version
        return self.installed_version

    @property
    def in_progress(self) -> bool:
        """Return True while `show upgrade info` reports an active upgrade."""
        snap = self.coordinator.data
        if snap is None or snap.firmware_info is None:
            return False
        return bool(snap.firmware_info.upgrade_in_progress)

    @property
    def supported_features(self) -> UpdateEntityFeature:
        """Always advertise PROGRESS so HA Core reads `in_progress`, plus opt-in INSTALL."""
        features = UpdateEntityFeature.PROGRESS
        snap = self.coordinator.data
        if (
            self.coordinator.enable_write_controls
            and snap is not None
            and snap.firmware_check is not None
            and snap.firmware_check.new_image_url
            and snap.firmware_info is not None
            and len(snap.firmware_info.image_classes) == 1
        ):
            features |= UpdateEntityFeature.INSTALL
        return features

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        """Compare Aruba `_build` suffixes cleanly without AwesomeVersion false positives."""
        latest_tuple = _normalize_aruba_version(latest_version)
        installed_tuple = _normalize_aruba_version(installed_version)
        if latest_tuple and installed_tuple:
            return latest_tuple > installed_tuple
        return latest_version.strip() != installed_version.strip()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose backup partition, image class, check status, and DRT metadata."""
        snap = self.coordinator.data
        if snap is None:
            return {}
        fw = snap.firmware_info
        chk = snap.firmware_check
        return {
            "backup_firmware": fw.backup_version if fw else None,
            "image_classes": list(fw.image_classes) if fw and fw.image_classes else None,
            "image_server_state": chk.server_state if chk else None,
            "image_server_message": chk.server_message if chk else None,
            "drt_version": chk.new_drt_version if chk else None,
        }

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Trigger cluster firmware upgrade when write controls are enabled."""
        if not self.coordinator.enable_write_controls:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_controls_disabled",
            )
        snap = self.coordinator.data
        url = snap.firmware_check.new_image_url if snap and snap.firmware_check else None
        classes = snap.firmware_info.image_classes if snap and snap.firmware_info else ()
        if not url or len(classes) != 1:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="ambiguous_ap_class",
            )
        try:
            await self.coordinator.client.async_upgrade_firmware(
                url,
                ap_class=classes[0],
                auto_reboot=True,
            )
            await self.coordinator.async_refresh_firmware_now()
        except ArubaClientError as exc:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="action_failed",
                translation_placeholders={"error": str(exc)},
            ) from exc
