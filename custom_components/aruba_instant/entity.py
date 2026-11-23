# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Base entity and shared DeviceInfo helpers ensuring deterministic aruba_* entity IDs."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import DOMAIN
from .coordinator import ArubaDataUpdateCoordinator
from .engine.models import AccessPoint


def build_aruba_object_id(raw_part: str, fallback: str = "device") -> str:
    """Build a lowercase ASCII object_id guaranteed to start cleanly with `aruba_`."""
    slug = slugify(raw_part).strip("_")
    if not slug or slug == "aruba":
        slug = slugify(fallback).strip("_") or "device"
    elif slug.startswith("aruba_"):
        slug = slug[len("aruba_") :].strip("_") or "device"
    return f"aruba_{slug[:180].rstrip('_')}"


def build_hub_device_info(entry_id: str, site_name: str) -> DeviceInfo:
    """Build Cluster/Site Hub DeviceInfo."""
    display = f"Aruba {site_name}" if not site_name.lower().startswith("aruba ") else site_name
    return DeviceInfo(
        identifiers={(DOMAIN, f"hub_{entry_id}")},
        name=display,
    )


def build_ap_device_info(
    coordinator: ArubaDataUpdateCoordinator,
    ap: AccessPoint,
) -> DeviceInfo:
    """Build Physical AP DeviceInfo linked to the Hub via `via_device_id`."""
    entry_id = coordinator.config_entry.entry_id
    runtime_data = coordinator.config_entry.runtime_data
    display = f"Aruba {ap.name}" if not ap.name.lower().startswith("aruba ") else ap.name
    return DeviceInfo(
        identifiers={(DOMAIN, f"ap_{entry_id}_{ap.stable_id}")},
        via_device_id=runtime_data.cluster_device_id,
        manufacturer="Hewlett Packard Enterprise / Aruba",
        model=ap.model or None,
        serial_number=ap.serial if ap.serial and ap.serial not in ("-", "--") else None,
        sw_version=ap.sw_version,
        name=display,
    )


class ArubaCoordinatorEntity(CoordinatorEntity[ArubaDataUpdateCoordinator]):
    """Shared base entity for Aruba cluster, AP, and client entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ArubaDataUpdateCoordinator,
        platform_domain: str,
        object_id_slug: str,
        unique_id: str,
        device_info: DeviceInfo,
        *,
        fallback_slug: str = "device",
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = unique_id
        self._attr_device_info = device_info
        # Pre-set entity_id before platform registration so HA assigns
        # internal_integration_suggested_object_id -> aruba_* in all locales
        self.entity_id = (
            f"{platform_domain}.{build_aruba_object_id(object_id_slug, fallback=fallback_slug)}"
        )
