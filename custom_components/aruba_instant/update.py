# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Update platform for Aruba Instant Virtual Controller firmware status and upgrades."""
from __future__ import annotations

import html
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
_INSTANTOS_4PART_RE = re.compile(r"(?<![\d.])(\d+)\.(\d+)\.(\d+)\.(\d+)(?![\d.])")


_INSTANT_PORTAL_BASE = (
    "https://arubanetworking.hpe.com/techdocs/instant-mode/oxygen/"
    "Instant%20Consolidated%20Release%20Notes%20Portal"
)


def _build_aruba_release_url(version: str | None) -> str | None:
    """Build HPE Instant Consolidated Release Notes URL for AOS 8.6 / 8.10+ trains."""
    match = _INSTANTOS_4PART_RE.search((version or "").strip())
    if not match:
        return None
    major_i, minor_i, _patch_i, _maint_i = (int(g) for g in match.groups())
    if major_i != 8 or (minor_i < 10 and minor_i != 6):
        return None
    if minor_i == 11:
        return f"{_INSTANT_PORTAL_BASE}/GUID-D0F825FC-9AF1-42A6-95D8-9A628C99660F.html"
    return (
        f"{_INSTANT_PORTAL_BASE}/"
        f"instant-aos-{major_i}-{minor_i}-x-x/"
        f"instant-aos-{major_i}-{minor_i}-x-x.html"
    )


def _safe_code(value: str) -> str:
    """Sanitize a string for safe inline Markdown backtick rendering."""
    cleaned = " ".join(str(value).replace("`", "'").split())
    return f"`{cleaned}`"


def _safe_html_text(value: str) -> str:
    """Collapse whitespace and escape HTML tokens for safe HA Markdown rendering."""
    return html.escape(" ".join(str(value).split()))


def _is_http_url(url: str | None) -> bool:
    """Return True if url starts with http:// or https://."""
    return bool(url and url.strip().lower().startswith(("http://", "https://")))


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
    def release_url(self) -> str | None:
        """Return version-specific HPE Instant Consolidated Release Notes URL for AOS 8.10+."""
        if self._is_update_available():
            return _build_aruba_release_url(self.latest_version) or _build_aruba_release_url(
                self.installed_version
            )
        return _build_aruba_release_url(self.installed_version)

    @property
    def in_progress(self) -> bool:
        """Return True while `show upgrade info` reports an active upgrade."""
        snap = self.coordinator.data
        if snap is None or snap.firmware_info is None:
            return False
        return bool(snap.firmware_info.upgrade_in_progress)

    @property
    def supported_features(self) -> UpdateEntityFeature:
        """Advertise PROGRESS, on-demand RELEASE_NOTES, and opt-in INSTALL."""
        features = UpdateEntityFeature.PROGRESS | UpdateEntityFeature.RELEASE_NOTES
        snap = self.coordinator.data
        if (
            self.coordinator.enable_write_controls
            and snap is not None
            and self._is_update_available()
            and snap.firmware_check is not None
            and _is_http_url(snap.firmware_check.new_image_url)
            and snap.firmware_info is not None
            and len(snap.firmware_info.image_classes) == 1
        ):
            features |= UpdateEntityFeature.INSTALL
        return features

    def _is_update_available(self) -> bool:
        """Return True when latest_version is strictly newer than installed_version."""
        installed = (self.installed_version or "").strip()
        latest = (self.latest_version or "").strip()
        if not installed or not latest:
            return False
        return self.version_is_newer(latest, installed)

    @property
    def release_summary(self) -> str | None:
        """Short single-line cluster firmware summary (max 255 chars)."""
        snap = self.coordinator.data
        if snap is None:
            return None
        fw = snap.firmware_info
        chk = snap.firmware_check
        installed = (self.installed_version or "").strip()
        latest = (self.latest_version or "").strip()
        parts: list[str] = []
        if self._is_update_available():
            parts.append(f"Available: {latest}")
        elif installed:
            parts.append(f"Installed: {installed}")
        if fw and fw.backup_version:
            parts.append(f"Backup: {fw.backup_version}")
        if fw and fw.image_classes:
            parts.append(f"Class: {', '.join(fw.image_classes)}")
        if chk and chk.new_drt_version:
            parts.append(f"DRT: {chk.new_drt_version}")
        if not parts:
            return None
        return " · ".join(parts)[:255]

    async def async_release_notes(self) -> str | None:
        """Return formatted cluster firmware summary and clear UI upgrade guidance."""
        snap = self.coordinator.data
        if snap is None:
            return None
        fw = snap.firmware_info
        chk = snap.firmware_check
        installed = (self.installed_version or "").strip()
        latest = (self.latest_version or "").strip()
        update_available = self._is_update_available()
        classes = fw.image_classes if fw else ()
        can_install = UpdateEntityFeature.INSTALL in self.supported_features

        if update_available and can_install:
            alert = (
                "<ha-alert alert-type='warning'>"
                "Installing this update will flash and <strong>automatically reboot all access points</strong> "
                "in the cluster, briefly interrupting Wi-Fi (~2–4 minutes). Your current version stays available "
                "on the backup partition."
                "</ha-alert>"
            )
        elif update_available and not self.coordinator.enable_write_controls:
            alert = (
                "<ha-alert alert-type='info'>"
                "<strong>Read-only mode:</strong> Installing firmware from Home Assistant is disabled. "
                "To enable updates here (all cluster APs will reboot automatically), open "
                "<strong>Settings → Devices &amp; Services → Aruba Instant / AOS Wi-Fi → Configure</strong> "
                "and turn on <strong>Enable AP &amp; SSID write controls</strong> — or upgrade directly in your "
                "Aruba Instant WebUI under <strong>Maintenance → Firmware</strong>."
                "</ha-alert>"
            )
        elif update_available and len(classes) > 1:
            alert = (
                "<ha-alert alert-type='info'>"
                "<strong>Multiple AP hardware models detected:</strong> One-click install is disabled when a "
                "cluster mixes different AP hardware classes. Upgrade directly in your Aruba Instant WebUI "
                "under <strong>Maintenance → Firmware</strong>."
                "</ha-alert>"
            )
        elif update_available:
            alert = (
                "<ha-alert alert-type='info'>"
                "A newer InstantOS build is reported, but no direct download URL was provided by the controller. "
                "Upgrade directly in your Aruba Instant WebUI under <strong>Maintenance → Firmware</strong>."
                "</ha-alert>"
            )
        elif chk is None:
            alert = (
                "<ha-alert alert-type='info'>"
                "Cloud update availability has not been reported yet (for example in SSH read-only mode or before "
                "the image check completes). You can inspect or upgrade firmware directly in your Aruba Instant WebUI "
                "under <strong>Maintenance → Firmware</strong>."
                "</ha-alert>"
            )
        elif (chk.server_state or "").strip().lower() != "success":
            state_txt = html.escape((chk.server_state or "unknown").strip())
            msg_txt = html.escape((chk.server_message or "").strip())
            detail = f"{state_txt}: {msg_txt}" if msg_txt else state_txt
            alert = (
                "<ha-alert alert-type='info'>"
                f"Cloud image check did not return update availability ({detail}). You can still inspect or "
                "upgrade firmware manually in your Aruba Instant WebUI under "
                "<strong>Maintenance → Firmware</strong>."
                "</ha-alert>"
            )
        else:
            alert = (
                "<ha-alert alert-type='info'>"
                "This cluster is running the latest reported InstantOS build."
                "</ha-alert>"
            )

        lines: list[str] = [alert, "", "### Cluster Firmware Summary"]
        if installed:
            lines.append(f"- **Active firmware:** {_safe_code(installed)}")
        if update_available and latest:
            lines.append(f"- **Available firmware:** {_safe_code(latest)}")
        if fw and fw.backup_version:
            lines.append(f"- **Backup partition:** {_safe_code(fw.backup_version)}")
        if classes:
            lines.append(f"- **AP hardware class:** {_safe_code(', '.join(classes))}")
        if chk and chk.new_drt_version:
            lines.append(f"- **Available DRT:** {_safe_code(chk.new_drt_version)}")
        if fw and fw.last_reboot_cause:
            lines.append(f"- **Last reboot cause:** {_safe_html_text(fw.last_reboot_cause)}")
        footer_note = (
            f"*[Open full HPE Aruba InstantOS release notes]({release_url})*"
            if (release_url := self.release_url)
            else "*HPE Aruba InstantOS does not expose a changelog feed locally; see HPE Networking Support for full release notes.*"
        )
        lines.extend(["", footer_note])
        return "\n".join(lines)

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
        url = (
            snap.firmware_check.new_image_url.strip()
            if snap and snap.firmware_check and snap.firmware_check.new_image_url
            else None
        )
        classes = snap.firmware_info.image_classes if snap and snap.firmware_info else ()
        if not self._is_update_available() or not _is_http_url(url):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="action_failed",
                translation_placeholders={"error": "No newer HTTP/HTTPS firmware update is available"},
            )
        if len(classes) != 1:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="ambiguous_ap_class",
            )
        assert url is not None
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
