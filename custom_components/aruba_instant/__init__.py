# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""The Aruba Instant / AOS Wi-Fi integration."""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .adapters.multi_ap_client import MultiApClusterClient, build_node_client
from .const import (
    CONF_NODES,
    CONF_SITE_NAME,
    CONF_TOPOLOGY_MODE,
    CONF_VERIFY_SSL,
    DEFAULT_SITE_NAME,
    DOMAIN,
    PLATFORMS,
    TOPOLOGY_CLUSTER,
)
from .coordinator import (
    ArubaDataUpdateCoordinator,
    ArubaInstantConfigEntry,
    ArubaRuntimeData,
)
from .engine.models import normalize_mac
from .ports.cluster_client import BaseArubaClusterClient
from .services import async_register_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register domain services once for Aruba Instant / AOS Wi-Fi."""
    await async_register_services(hass)
    return True


def build_cluster_client(
    hass: HomeAssistant,
    nodes: list[dict[str, Any]],
    topology_mode: str,
    site_name: str,
) -> BaseArubaClusterClient:
    """Create the configured single-node or Multi-AP composite client."""
    node_clients = [
        build_node_client(
            session=async_get_clientsession(
                hass, verify_ssl=bool(node.get(CONF_VERIFY_SSL, False))
            ),
            node=node,
        )
        for node in nodes
    ]
    if topology_mode == TOPOLOGY_CLUSTER and len(node_clients) == 1:
        return node_clients[0]

    fallback_session = async_get_clientsession(hass, verify_ssl=False)
    return MultiApClusterClient(
        session=fallback_session,
        nodes=nodes,
        site_name=site_name,
        node_clients=node_clients,
    )


async def _async_migrate_scanner_unique_ids(
    hass: HomeAssistant,
    entry: ArubaInstantConfigEntry,
) -> None:
    """Migrate legacy bare-MAC device_tracker unique_ids to entry-scoped unique_ids."""
    prefix = f"{entry.entry_id}_"
    ent_reg = er.async_get(hass)

    @callback
    def _migrate_entry(reg_entry: er.RegistryEntry) -> dict[str, Any] | None:
        if reg_entry.domain != "device_tracker" or reg_entry.unique_id.startswith(prefix):
            return None
        norm_mac = normalize_mac(reg_entry.unique_id)
        if len(norm_mac) != 17 or norm_mac.count(":") != 5:
            return None
        new_uid = f"{prefix}{norm_mac}"
        if ent_reg.async_get_entity_id(reg_entry.domain, reg_entry.platform, new_uid):
            return None
        return {"new_unique_id": new_uid}

    await er.async_migrate_entries(hass, entry.entry_id, _migrate_entry)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ArubaInstantConfigEntry,
) -> bool:
    """Set up Aruba Instant / AOS Wi-Fi from a config entry."""
    await _async_migrate_scanner_unique_ids(hass, entry)

    nodes = [dict(n) for n in entry.data.get(CONF_NODES, [])]
    topology_mode = str(entry.data.get(CONF_TOPOLOGY_MODE, TOPOLOGY_CLUSTER))
    site_name = str(entry.data.get(CONF_SITE_NAME) or entry.title or DEFAULT_SITE_NAME)

    client = build_cluster_client(hass, nodes, topology_mode, site_name)
    coordinator = ArubaDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        client=client,
        site_name=site_name,
        topology_mode=topology_mode,
    )
    await coordinator.async_load_client_store()

    entry.async_on_unload(client.async_close)

    await coordinator.async_config_entry_first_refresh()

    dev_reg = dr.async_get(hass)
    sw_version = (
        (
            coordinator.data.firmware_info.primary_version
            if coordinator.data is not None and coordinator.data.firmware_info
            else None
        )
        or (coordinator.data.summary.version if coordinator.data is not None else None)
    )
    hub_device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"hub_{entry.entry_id}")},
        manufacturer="Hewlett Packard Enterprise / Aruba",
        model=(
            "Instant Virtual Controller"
            if topology_mode == TOPOLOGY_CLUSTER
            else "Multi-AP Standalone / AOS 10"
        ),
        name=f"Aruba {site_name}" if not site_name.lower().startswith("aruba ") else site_name,
        sw_version=sw_version,
    )

    entry.runtime_data = ArubaRuntimeData(
        coordinator=coordinator,
        client=client,
        cluster_device_id=hub_device.id,
        site_name=site_name,
        topology_mode=topology_mode,
    )

    coordinator.sync_registry_filters()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ArubaInstantConfigEntry,
) -> bool:
    """Unload a config entry, flush client store, and release transport sessions."""
    runtime_data = getattr(entry, "runtime_data", None)
    if runtime_data is not None and runtime_data.coordinator is not None:
        await runtime_data.coordinator.async_flush_client_store()
    ir.async_delete_issue(hass, DOMAIN, f"vc_vip_unreachable_{entry.entry_id}")
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(
    hass: HomeAssistant,
    entry: ArubaInstantConfigEntry,
) -> None:
    """Remove persisted client/cluster Store file when config entry is deleted."""
    ir.async_delete_issue(hass, DOMAIN, f"vc_vip_unreachable_{entry.entry_id}")
    store = Store[dict[str, Any]](hass, version=1, key=f"{DOMAIN}.{entry.entry_id}.clients")
    await store.async_remove()


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: ArubaInstantConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow users to remove stale AP or client devices no longer present."""
    runtime_data = config_entry.runtime_data
    if (DOMAIN, f"hub_{config_entry.entry_id}") in device_entry.identifiers:
        return False

    snapshot = runtime_data.coordinator.data
    if snapshot is not None:
        active_ap_ids = {
            f"ap_{config_entry.entry_id}_{ap.stable_id}"
            for ap in snapshot.access_points
            if ap.online
        }
        if any((DOMAIN, ap_ident) in device_entry.identifiers for ap_ident in active_ap_ids):
            return False

        active_macs = {normalize_mac(c.mac) for c in snapshot.clients}
        for conn_type, conn_val in device_entry.connections:
            if conn_type == dr.CONNECTION_NETWORK_MAC and normalize_mac(conn_val) in active_macs:
                return False

    ap_prefix = f"ap_{config_entry.entry_id}_"
    removed_meta = False
    for domain, ident in device_entry.identifiers:
        if domain == DOMAIN and ident.startswith(ap_prefix):
            sid = ident[len(ap_prefix) :]
            if runtime_data.coordinator.known_aps.pop(sid, None) is not None:
                removed_meta = True
            runtime_data.coordinator._checked_missing_aps.discard(sid)
    if removed_meta:
        runtime_data.coordinator.schedule_store_save()

    return True
