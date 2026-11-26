# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Config flow and Options flow for Aruba Instant / AOS Wi-Fi."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
import re
from typing import Any
import uuid

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import section
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .adapters.multi_ap_client import build_node_client
from .adapters.rest_client import ArubaRestClient
from .adapters.ssh_client import ArubaSshClient
from .const import (
    CONF_AP_ID,
    CONF_AP_NAME,
    CONF_APPLY_TO_ALL_NODES,
    CONF_CONFIRM_ACTION,
    CONF_CONSIDER_HOME,
    CONF_ENABLE_ROAMING_SENSORS,
    CONF_ENABLE_WRITE_CONTROLS,
    CONF_EXCLUDE_MACS,
    CONF_HOSTS,
    CONF_INCLUDE_WIRED_CLIENTS,
    CONF_LEGACY_SSH,
    CONF_MIN_SIGNAL,
    CONF_NETWORK_NAME,
    CONF_NODES,
    CONF_REQUIRE_IP,
    CONF_REST_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SEPARATE_CREDENTIALS,
    CONF_SITE_NAME,
    CONF_SSH_PORT,
    CONF_TARGET_HOST,
    CONF_TOPOLOGY_MODE,
    CONF_TRACK_NEW_CLIENTS,
    CONF_TRACK_UNNAMED_CLIENTS,
    CONF_TRACKED_SSIDS,
    CONF_TRANSPORT,
    CONF_UNSELECTED_SSID_MODE,
    CONF_VERIFY_SSL,
    CONFIRM_ENABLE_REST,
    CONFIRM_USE_SSH_READONLY,
    DEFAULT_CONSIDER_HOME,
    DEFAULT_LEGACY_SSH,
    DEFAULT_MIN_SIGNAL,
    DEFAULT_REST_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SITE_NAME,
    DEFAULT_SSH_PORT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    TOPOLOGY_CLUSTER,
    TOPOLOGY_MULTI_AP,
    TRANSPORT_AUTO,
    TRANSPORT_REST,
    TRANSPORT_SSH,
    UNSELECTED_SSID_DISABLE,
    UNSELECTED_SSID_IGNORE,
)
from .engine.models import ClusterSnapshot
from .ports.cluster_client import (
    ArubaAuthError,
    ArubaConnectionError,
    ArubaReadOnlyCliError,
    ArubaRestApiDisabledError,
)

_LOGGER = logging.getLogger(__name__)
_HOST_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)


def _clean_host(raw: str) -> str:
    """Normalize a user-supplied IP or hostname without stripping IPv6 brackets cleanly."""
    cleaned = _HOST_SCHEME_RE.sub("", (raw or "").strip()).split("/", 1)[0].strip()
    if not cleaned or any(ch.isspace() or ch in "@?#'\"%" for ch in cleaned):
        return ""
    if cleaned.startswith("[") and "]" in cleaned:
        inner = cleaned[1 : cleaned.index("]")]
        return inner if inner and not any(ch.isspace() or ch in "@?#'\"%" for ch in inner) else ""
    # Strip trailing :port on IPv4/hostname (not bare IPv6 with multiple colons)
    if cleaned.count(":") == 1:
        host_part, port_part = cleaned.split(":", 1)
        if port_part.isdigit():
            cleaned = host_part
    return cleaned.strip()


def _normalize_hosts(raw_hosts: Any) -> list[str]:
    """Normalize list or comma/newline-separated host entries while preserving order."""
    items: list[str] = []
    if isinstance(raw_hosts, str):
        parts = [p for chunk in raw_hosts.replace(";", ",").replace("\n", ",").split(",") for p in chunk.split()]
        items.extend(parts)
    elif isinstance(raw_hosts, (list, tuple, set)):
        for elem in raw_hosts:
            if isinstance(elem, str):
                parts = [p for chunk in elem.replace(";", ",").replace("\n", ",").split(",") for p in chunk.split()]
                items.extend(parts)

    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        host = _clean_host(item)
        if host and host.lower() not in seen:
            seen.add(host.lower())
            result.append(host)
    return result


def _advanced_section_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    """Build reusable collapsible advanced network settings schema."""
    d = defaults or {}
    return vol.Schema(
        {
            vol.Optional(
                CONF_REST_PORT, default=int(d.get(CONF_REST_PORT, DEFAULT_REST_PORT))
            ): NumberSelector(
                NumberSelectorConfig(min=1, max=65535, step=1, mode=NumberSelectorMode.BOX)
            ),
            vol.Optional(
                CONF_SSH_PORT, default=int(d.get(CONF_SSH_PORT, DEFAULT_SSH_PORT))
            ): NumberSelector(
                NumberSelectorConfig(min=1, max=65535, step=1, mode=NumberSelectorMode.BOX)
            ),
            vol.Optional(
                CONF_VERIFY_SSL, default=bool(d.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL))
            ): BooleanSelector(),
            vol.Optional(
                CONF_LEGACY_SSH, default=bool(d.get(CONF_LEGACY_SSH, DEFAULT_LEGACY_SSH))
            ): BooleanSelector(),
        }
    )


def _build_node_dict(
    host: str,
    username: str,
    password: str,
    transport: str,
    advanced: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a normalized node dictionary."""
    adv = advanced or {}
    return {
        CONF_HOST: _clean_host(host),
        CONF_USERNAME: username.strip(),
        CONF_PASSWORD: password,
        CONF_TRANSPORT: transport,
        CONF_REST_PORT: int(adv.get(CONF_REST_PORT, DEFAULT_REST_PORT)),
        CONF_SSH_PORT: int(adv.get(CONF_SSH_PORT, DEFAULT_SSH_PORT)),
        CONF_VERIFY_SSL: bool(adv.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)),
        CONF_LEGACY_SSH: bool(adv.get(CONF_LEGACY_SSH, DEFAULT_LEGACY_SSH)),
    }


def _build_node_form_schema(
    defaults: Mapping[str, Any] | None = None,
    *,
    password_required: bool = True,
    include_host: bool = True,
) -> vol.Schema:
    """Build reusable schema for single-AP / cluster connection and reconfigure forms."""
    d = defaults or {}
    fields: dict[Any, Any] = {}
    if include_host:
        fields[vol.Required(CONF_HOST, default=str(d.get(CONF_HOST, "")))] = TextSelector()
    fields[vol.Required(CONF_USERNAME, default=str(d.get(CONF_USERNAME, "admin")))] = (
        TextSelector()
    )
    pw_key = vol.Required(CONF_PASSWORD) if password_required else vol.Optional(CONF_PASSWORD)
    fields[pw_key] = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
    fields[
        vol.Required(CONF_TRANSPORT, default=str(d.get(CONF_TRANSPORT, TRANSPORT_AUTO)))
    ] = SelectSelector(
        SelectSelectorConfig(
            options=[TRANSPORT_AUTO, TRANSPORT_REST, TRANSPORT_SSH],
            mode=SelectSelectorMode.DROPDOWN,
            translation_key="transport",
        )
    )
    adv_defaults = d.get("advanced") if isinstance(d.get("advanced"), Mapping) else d
    fields[vol.Required("advanced")] = section(
        _advanced_section_schema(adv_defaults),
        {"collapsed": True},
    )
    return vol.Schema(fields)


def _extract_filter_options(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize filter & polling option fields shared by Filters and Options steps."""
    return {
        CONF_TRACKED_SSIDS: list(user_input.get(CONF_TRACKED_SSIDS, [])),
        CONF_UNSELECTED_SSID_MODE: str(
            user_input.get(CONF_UNSELECTED_SSID_MODE, UNSELECTED_SSID_IGNORE)
        ),
        CONF_TRACK_NEW_CLIENTS: bool(user_input.get(CONF_TRACK_NEW_CLIENTS, True)),
        CONF_TRACK_UNNAMED_CLIENTS: bool(user_input.get(CONF_TRACK_UNNAMED_CLIENTS, True)),
        CONF_INCLUDE_WIRED_CLIENTS: bool(
            user_input.get(CONF_INCLUDE_WIRED_CLIENTS, False)
        ),
        CONF_REQUIRE_IP: bool(user_input.get(CONF_REQUIRE_IP, False)),
        CONF_MIN_SIGNAL: int(user_input.get(CONF_MIN_SIGNAL, DEFAULT_MIN_SIGNAL)),
        CONF_EXCLUDE_MACS: str(user_input.get(CONF_EXCLUDE_MACS, "")),
        CONF_ENABLE_ROAMING_SENSORS: bool(
            user_input.get(CONF_ENABLE_ROAMING_SENSORS, False)
        ),
        CONF_ENABLE_WRITE_CONTROLS: bool(
            user_input.get(CONF_ENABLE_WRITE_CONTROLS, False)
        ),
        CONF_SCAN_INTERVAL: int(
            user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        ),
        CONF_CONSIDER_HOME: int(
            user_input.get(CONF_CONSIDER_HOME, DEFAULT_CONSIDER_HOME)
        ),
    }


def _enrich_node_with_snapshot(
    node: dict[str, Any],
    snapshot: ClusterSnapshot,
    resolved_transport: str | None = None,
) -> dict[str, Any]:
    """Persist discovered AP metadata (`ap_id`, `name`, transport) on a node."""
    updated = dict(node)
    if resolved_transport:
        updated[CONF_TRANSPORT] = resolved_transport
    if snapshot.access_points:
        matching = next(
            (ap for ap in snapshot.access_points if ap.ip_address == updated[CONF_HOST]),
            next((ap for ap in snapshot.access_points if ap.is_conductor), snapshot.access_points[0]),
        )
        updated[CONF_AP_ID] = matching.stable_id
        updated[CONF_AP_NAME] = matching.name
        peers: list[str] = []
        for raw_ip in (
            *(ap.ip_address for ap in snapshot.access_points if ap.ip_address),
            snapshot.summary.conductor_ip,
        ):
            if raw_ip and raw_ip not in ("-", "--") and raw_ip not in peers:
                peers.append(raw_ip)
        if peers:
            updated["known_peers"] = peers
    else:
        updated[CONF_AP_ID] = updated.get(CONF_AP_ID) or updated[CONF_HOST]
        updated[CONF_AP_NAME] = snapshot.summary.name or updated[CONF_HOST]
    return updated


def _build_filter_schema(
    discovered_ssids: list[str],
    current_options: Mapping[str, Any] | None = None,
    is_initial: bool = True,
) -> vol.Schema:
    """Build shared SSID and client filter schema for initial Setup & OptionsFlow."""
    opts = current_options or {}
    saved_ssids = [str(s) for s in opts.get(CONF_TRACKED_SSIDS, []) if str(s)]
    all_ssid_choices = sorted(set(discovered_ssids) | set(saved_ssids))
    default_ssids = list(discovered_ssids) if is_initial else saved_ssids

    return vol.Schema(
        {
            vol.Optional(CONF_TRACKED_SSIDS, default=default_ssids): SelectSelector(
                SelectSelectorConfig(
                    options=all_ssid_choices,
                    multiple=True,
                    custom_value=True,
                    mode=SelectSelectorMode.LIST,
                )
            ),
            vol.Required(
                CONF_UNSELECTED_SSID_MODE,
                default=str(opts.get(CONF_UNSELECTED_SSID_MODE, UNSELECTED_SSID_IGNORE)),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[UNSELECTED_SSID_IGNORE, UNSELECTED_SSID_DISABLE],
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key="unselected_ssid_mode",
                )
            ),
            vol.Required(
                CONF_TRACK_NEW_CLIENTS,
                default=bool(opts.get(CONF_TRACK_NEW_CLIENTS, True)),
            ): BooleanSelector(),
            vol.Required(
                CONF_TRACK_UNNAMED_CLIENTS,
                default=bool(opts.get(CONF_TRACK_UNNAMED_CLIENTS, True)),
            ): BooleanSelector(),
            vol.Required(
                CONF_INCLUDE_WIRED_CLIENTS,
                default=bool(opts.get(CONF_INCLUDE_WIRED_CLIENTS, False)),
            ): BooleanSelector(),
            vol.Required(
                CONF_REQUIRE_IP,
                default=bool(opts.get(CONF_REQUIRE_IP, False)),
            ): BooleanSelector(),
            vol.Required(
                CONF_MIN_SIGNAL,
                default=int(opts.get(CONF_MIN_SIGNAL, DEFAULT_MIN_SIGNAL)),
            ): NumberSelector(
                NumberSelectorConfig(min=0, max=99, step=1, mode=NumberSelectorMode.BOX)
            ),
            vol.Optional(
                CONF_EXCLUDE_MACS,
                default=str(opts.get(CONF_EXCLUDE_MACS, "")),
            ): TextSelector(),
            vol.Required(
                CONF_ENABLE_ROAMING_SENSORS,
                default=bool(opts.get(CONF_ENABLE_ROAMING_SENSORS, False)),
            ): BooleanSelector(),
            vol.Required(
                CONF_ENABLE_WRITE_CONTROLS,
                default=bool(opts.get(CONF_ENABLE_WRITE_CONTROLS, False)),
            ): BooleanSelector(),
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=int(opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
            ): NumberSelector(
                NumberSelectorConfig(min=5, max=300, step=1, mode=NumberSelectorMode.BOX)
            ),
            vol.Required(
                CONF_CONSIDER_HOME,
                default=int(opts.get(CONF_CONSIDER_HOME, DEFAULT_CONSIDER_HOME)),
            ): NumberSelector(
                NumberSelectorConfig(min=0, max=1800, step=10, mode=NumberSelectorMode.BOX)
            ),
        }
    )


class ArubaInstantConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle config, reauth, and reconfigure flows for Aruba Instant / AOS Wi-Fi."""

    VERSION = 1

    def __init__(self) -> None:
        self._topology_mode: str = TOPOLOGY_CLUSTER
        self._site_name: str = DEFAULT_SITE_NAME
        self._validated_nodes: list[dict[str, Any]] = []
        self._snapshots: list[ClusterSnapshot] = []
        self._pending_cred_nodes: list[dict[str, Any]] = []
        self._pending_rest_confirm_nodes: list[dict[str, Any]] = []
        self._cred_total: int = 0
        self._cred_index: int = 0
        self._current_cred_node: dict[str, Any] | None = None
        self._current_rest_node: dict[str, Any] | None = None
        self._post_validation_target: str = "filters"
        self._editing_node_index: int | None = None
        self._reauth_failed_hosts: list[str] = []

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> ArubaOptionsFlow:
        """Return the options flow handler."""
        return ArubaOptionsFlow()

    async def _async_probe_node(
        self, node: dict[str, Any]
    ) -> tuple[ClusterSnapshot, str]:
        """Validate connectivity and credentials for a single AP/VC node."""
        session = async_get_clientsession(
            self.hass, verify_ssl=bool(node.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL))
        )
        # During onboarding/reconfigure, raise ArubaRestApiDisabledError on 503 so user gets explicit consent
        client = build_node_client(session, node, fallback_on_rest_disabled=False)
        try:
            snap = await client.async_fetch_snapshot()
        finally:
            await client.async_close()

        configured_transport = str(node.get(CONF_TRANSPORT, TRANSPORT_AUTO))
        if configured_transport == TRANSPORT_AUTO:
            # Keep "auto" when REST succeeded so runtime SSH fallback remains active; lock to "ssh" if REST failed
            active = getattr(client, "active_transport", None)
            resolved_transport = TRANSPORT_SSH if active == TRANSPORT_SSH else TRANSPORT_AUTO
        else:
            resolved_transport = configured_transport
        return snap, resolved_transport

    def _reset_queue(self, post_target: str) -> None:
        """Reset transient multi-step node validation state."""
        self._validated_nodes = []
        self._snapshots = []
        self._pending_cred_nodes = []
        self._pending_rest_confirm_nodes = []
        self._cred_total = 0
        self._cred_index = 0
        self._current_cred_node = None
        self._current_rest_node = None
        self._post_validation_target = post_target

    def _check_duplicate_nodes(
        self,
        candidate_nodes: list[dict[str, Any]],
        ignore_entry_id: str | None = None,
    ) -> bool:
        """Return True if any candidate node host or ap_id is already configured in another entry."""
        new_hosts = {str(n[CONF_HOST]).lower() for n in candidate_nodes if n.get(CONF_HOST)}
        new_ap_ids = {str(n[CONF_AP_ID]).lower() for n in candidate_nodes if n.get(CONF_AP_ID)}
        for existing in self._async_current_entries():
            if ignore_entry_id and existing.entry_id == ignore_entry_id:
                continue
            existing_nodes = existing.data.get(CONF_NODES, [])
            existing_hosts = {
                str(n.get(CONF_HOST, "")).lower() for n in existing_nodes if n.get(CONF_HOST)
            }
            existing_ap_ids = {
                str(n.get(CONF_AP_ID, "")).lower() for n in existing_nodes if n.get(CONF_AP_ID)
            }
            if (new_hosts & existing_hosts) or (new_ap_ids & existing_ap_ids):
                return True
        return False

    async def _async_advance_node_queue(self) -> ConfigFlowResult:
        """Drive pending REST-consent and per-AP credential queues to completion."""
        if self._pending_rest_confirm_nodes:
            self._current_rest_node = self._pending_rest_confirm_nodes.pop(0)
            return await self.async_step_confirm_enable_rest()

        if self._pending_cred_nodes:
            self._current_cred_node = self._pending_cred_nodes.pop(0)
            self._cred_index += 1
            return await self.async_step_ap_credentials()

        if self._post_validation_target == "finish_reconfigure":
            return await self._async_finish_multi_ap_reconfigure()

        if self._post_validation_target == "finish_reconfigure_cluster":
            entry = self._get_reconfigure_entry()
            if self._check_duplicate_nodes(self._validated_nodes, ignore_entry_id=entry.entry_id):
                return self.async_abort(reason="already_configured")
            return self.async_update_reload_and_abort(
                entry,
                data_updates={CONF_NODES: self._validated_nodes},
            )

        # Initial setup complete -> deduplicate across entries and route to SSID/client filters
        if self._check_duplicate_nodes(self._validated_nodes):
            return self.async_abort(reason="already_configured")

        if self._topology_mode == TOPOLOGY_CLUSTER and self._snapshots:
            summary = self._snapshots[0].summary
            self._site_name = summary.name or self._validated_nodes[0][CONF_HOST]
            uid = summary.vc_ip or self._validated_nodes[0].get(CONF_AP_ID) or self._validated_nodes[0][CONF_HOST]
            await self.async_set_unique_id(f"cluster_{uid}".lower())
            self._abort_if_unique_id_configured()
        else:
            await self.async_set_unique_id(f"site_{uuid.uuid4().hex}")
            self._abort_if_unique_id_configured()

        return await self.async_step_filters()

    async def _async_finish_multi_ap_reconfigure(self) -> ConfigFlowResult:
        """Apply added or edited AP node to an existing Multi-AP config entry."""
        entry = self._get_reconfigure_entry()
        nodes = [dict(n) for n in entry.data.get(CONF_NODES, [])]
        new_node = self._validated_nodes[0]

        if self._editing_node_index is not None and 0 <= self._editing_node_index < len(nodes):
            if any(
                str(n[CONF_HOST]).lower() == new_node[CONF_HOST].lower()
                and i != self._editing_node_index
                for i, n in enumerate(nodes)
            ) or self._check_duplicate_nodes([new_node], ignore_entry_id=entry.entry_id):
                return self.async_abort(reason="already_configured")

            old_ap_id = str(nodes[self._editing_node_index].get(CONF_AP_ID) or "")
            new_ap_id = str(new_node.get(CONF_AP_ID) or "")
            if old_ap_id and new_ap_id and old_ap_id != new_ap_id:
                dev_reg = dr.async_get(self.hass)
                old_dev = dev_reg.async_get_device_by_identifier(
                    (DOMAIN, f"ap_{entry.entry_id}_{old_ap_id}"), entry.entry_id
                )
                if old_dev is not None:
                    dev_reg.async_remove_device(old_dev.id)

            nodes[self._editing_node_index] = new_node
            self._editing_node_index = None
        else:
            existing_hosts = {str(n[CONF_HOST]).lower() for n in nodes}
            if new_node[CONF_HOST].lower() in existing_hosts or self._check_duplicate_nodes(
                [new_node], ignore_entry_id=entry.entry_id
            ):
                return self.async_abort(reason="already_configured")
            nodes.append(new_node)

        return self.async_update_reload_and_abort(
            entry,
            data_updates={CONF_NODES: nodes},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Entry menu choosing Virtual Controller / Single AP vs Multiple Standalone APs."""
        return self.async_show_menu(
            step_id="user",
            menu_options=[TOPOLOGY_CLUSTER, TOPOLOGY_MULTI_AP],
        )

    async def async_step_cluster(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle Virtual Controller or Single AP configuration."""
        errors: dict[str, str] = {}
        self._topology_mode = TOPOLOGY_CLUSTER

        if user_input is not None:
            host = _clean_host(str(user_input.get(CONF_HOST, "")))
            if not host:
                errors[CONF_HOST] = "invalid_host"
            else:
                node = _build_node_dict(
                    host=host,
                    username=str(user_input[CONF_USERNAME]),
                    password=str(user_input[CONF_PASSWORD]),
                    transport=str(user_input.get(CONF_TRANSPORT, TRANSPORT_AUTO)),
                    advanced=user_input.get("advanced"),
                )
                self._reset_queue("filters")
                try:
                    snap, resolved = await self._async_probe_node(node)
                except ArubaRestApiDisabledError as exc:
                    rest_node = dict(node)
                    if exc.host:
                        rest_node["_rest_enable_host"] = exc.host
                    self._pending_rest_confirm_nodes = [rest_node]
                    return await self._async_advance_node_queue()
                except ArubaAuthError:
                    errors["base"] = "invalid_auth"
                except ArubaConnectionError:
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected error probing Aruba cluster %s", host)
                    errors["base"] = "unknown"
                else:
                    self._snapshots = [snap]
                    self._validated_nodes = [_enrich_node_with_snapshot(node, snap, resolved)]
                    return await self._async_advance_node_queue()

        schema = _build_node_form_schema(user_input, password_required=True, include_host=True)
        return self.async_show_form(step_id="cluster", data_schema=schema, errors=errors)

    async def async_step_multi_ap(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle Multiple Standalone / AOS 10 APs onboarding."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        self._topology_mode = TOPOLOGY_MULTI_AP

        if user_input is not None:
            site_name = str(user_input.get(CONF_NETWORK_NAME, DEFAULT_SITE_NAME)).strip() or DEFAULT_SITE_NAME
            hosts = _normalize_hosts(user_input.get(CONF_HOSTS))
            separate_creds = bool(user_input.get(CONF_SEPARATE_CREDENTIALS, False))
            username = str(user_input.get(CONF_USERNAME, "admin")).strip() or "admin"
            password = str(user_input.get(CONF_PASSWORD) or "")
            transport = str(user_input.get(CONF_TRANSPORT, TRANSPORT_AUTO))
            advanced = user_input.get("advanced")

            if not hosts:
                errors[CONF_HOSTS] = "empty_hosts"
            elif self._check_duplicate_nodes(
                [_build_node_dict(h, username, password, transport, advanced) for h in hosts]
            ):
                return self.async_abort(reason="already_configured")
            elif not separate_creds and not password:
                errors[CONF_PASSWORD] = "password_required"
            else:
                self._site_name = site_name
                self._reset_queue("filters")
                candidate_nodes = [
                    _build_node_dict(h, username, password, transport, advanced)
                    for h in hosts
                ]

                if separate_creds:
                    self._pending_cred_nodes = candidate_nodes
                    self._cred_total = len(candidate_nodes)
                    return await self._async_advance_node_queue()

                # Fast Path: probe all standalone APs concurrently with shared credentials
                results = await asyncio.gather(
                    *(self._async_probe_node(n) for n in candidate_nodes),
                    return_exceptions=True,
                )
                auth_failed_nodes: list[dict[str, Any]] = []
                conn_failed_hosts: list[str] = []

                for node, res in zip(candidate_nodes, results):
                    if isinstance(res, asyncio.CancelledError):
                        raise res
                    if isinstance(res, ArubaRestApiDisabledError):
                        rest_node = dict(node)
                        if res.host:
                            rest_node["_rest_enable_host"] = res.host
                        self._pending_rest_confirm_nodes.append(rest_node)
                    elif isinstance(res, ArubaAuthError):
                        auth_failed_nodes.append(node)
                    elif isinstance(res, BaseException):
                        conn_failed_hosts.append(node[CONF_HOST])
                    else:
                        snap, resolved = res
                        self._snapshots.append(snap)
                        self._validated_nodes.append(
                            _enrich_node_with_snapshot(node, snap, resolved)
                        )

                if conn_failed_hosts:
                    errors["base"] = "cannot_connect_hosts"
                    placeholders["failed_hosts"] = ", ".join(conn_failed_hosts)
                elif auth_failed_nodes and not self._validated_nodes and not self._pending_rest_confirm_nodes:
                    # Every AP rejected shared password -> let user fix typo right here
                    errors["base"] = "invalid_auth"
                else:
                    # Smart Auth-Mismatch Fallback: prompt only for APs that rejected shared creds
                    if auth_failed_nodes:
                        self._pending_cred_nodes = auth_failed_nodes
                        self._cred_total = len(auth_failed_nodes)
                    return await self._async_advance_node_queue()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_NETWORK_NAME,
                    default=(user_input or {}).get(CONF_NETWORK_NAME, DEFAULT_SITE_NAME),
                ): TextSelector(),
                vol.Required(
                    CONF_HOSTS,
                    default=(user_input or {}).get(CONF_HOSTS, []),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[],
                        multiple=True,
                        custom_value=True,
                    )
                ),
                vol.Required(
                    CONF_SEPARATE_CREDENTIALS,
                    default=bool((user_input or {}).get(CONF_SEPARATE_CREDENTIALS, False)),
                ): BooleanSelector(),
                vol.Required(
                    CONF_USERNAME,
                    default=(user_input or {}).get(CONF_USERNAME, "admin"),
                ): TextSelector(),
                vol.Optional(CONF_PASSWORD): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Required(
                    CONF_TRANSPORT,
                    default=(user_input or {}).get(CONF_TRANSPORT, TRANSPORT_AUTO),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[TRANSPORT_AUTO, TRANSPORT_REST, TRANSPORT_SSH],
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="transport",
                    )
                ),
                vol.Required("advanced"): section(
                    _advanced_section_schema((user_input or {}).get("advanced")),
                    {"collapsed": True},
                ),
            }
        )
        return self.async_show_form(
            step_id="multi_ap",
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_ap_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect credentials for an individual AP node (explicit split or auth mismatch)."""
        assert self._current_cred_node is not None
        errors: dict[str, str] = {}
        node = dict(self._current_cred_node)

        if user_input is not None:
            candidate = _build_node_dict(
                host=node[CONF_HOST],
                username=str(user_input[CONF_USERNAME]),
                password=str(user_input[CONF_PASSWORD]),
                transport=str(user_input.get(CONF_TRANSPORT, node[CONF_TRANSPORT])),
                advanced=user_input.get("advanced"),
            )
            try:
                snap, resolved = await self._async_probe_node(candidate)
            except ArubaRestApiDisabledError as exc:
                rest_node = dict(candidate)
                if exc.host:
                    rest_node["_rest_enable_host"] = exc.host
                self._pending_rest_confirm_nodes.append(rest_node)
                return await self._async_advance_node_queue()
            except ArubaAuthError:
                errors["base"] = "invalid_auth"
            except ArubaConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error probing AP %s", node[CONF_HOST])
                errors["base"] = "unknown"
            else:
                self._snapshots.append(snap)
                self._validated_nodes.append(
                    _enrich_node_with_snapshot(candidate, snap, resolved)
                )
                return await self._async_advance_node_queue()

        schema = _build_node_form_schema(
            user_input or node, password_required=True, include_host=False
        )
        return self.async_show_form(
            step_id="ap_credentials",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "host": node[CONF_HOST],
                "index": str(self._cred_index),
                "total": str(max(1, self._cred_total)),
            },
        )

    async def async_step_confirm_enable_rest(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask explicit user consent before running `allow-rest-api` over SSH."""
        assert self._current_rest_node is not None
        errors: dict[str, str] = {}
        node = dict(self._current_rest_node)
        target_host = str(node.pop("_rest_enable_host", None) or node[CONF_HOST])

        if user_input is not None:
            action = str(user_input[CONF_CONFIRM_ACTION])
            ssh_client = ArubaSshClient(
                host=target_host,
                username=node[CONF_USERNAME],
                password=node[CONF_PASSWORD],
                port=int(node.get(CONF_SSH_PORT, DEFAULT_SSH_PORT)),
                legacy_ssh=bool(node.get(CONF_LEGACY_SSH, DEFAULT_LEGACY_SSH)),
            )

            if action == CONFIRM_ENABLE_REST:
                session = async_get_clientsession(
                    self.hass,
                    verify_ssl=bool(node.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)),
                )
                rest_client = ArubaRestClient(
                    session=session,
                    host=target_host,
                    username=node[CONF_USERNAME],
                    password=node[CONF_PASSWORD],
                    port=int(node.get(CONF_REST_PORT, DEFAULT_REST_PORT)),
                    verify_ssl=bool(node.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)),
                )
                rest_client.set_known_peer_ips([node[CONF_HOST], target_host])
                try:
                    await ssh_client.async_enable_rest_api()
                    # Brief retry in case AP REST daemon takes ~1s to bind :4343 after write memory
                    for attempt in range(3):
                        try:
                            snap = await rest_client.async_fetch_snapshot()
                            break
                        except (ArubaConnectionError, ArubaRestApiDisabledError):
                            if attempt == 2:
                                raise
                            await asyncio.sleep(0.5)
                except ArubaReadOnlyCliError:
                    errors["base"] = "rest_enable_failed"
                except ArubaAuthError:
                    errors["base"] = "invalid_auth"
                except (ArubaConnectionError, ArubaRestApiDisabledError):
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Failed enabling REST API on %s", node[CONF_HOST])
                    errors["base"] = "unknown"
                else:
                    self._snapshots.append(snap)
                    self._validated_nodes.append(
                        _enrich_node_with_snapshot(node, snap, TRANSPORT_REST)
                    )
                    return await self._async_advance_node_queue()
                finally:
                    await rest_client.async_close()
                    await ssh_client.async_close()
            else:
                try:
                    snap = await ssh_client.async_fetch_snapshot()
                except ArubaAuthError:
                    errors["base"] = "invalid_auth"
                except ArubaConnectionError:
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Failed read-only SSH probe on %s", node[CONF_HOST])
                    errors["base"] = "unknown"
                else:
                    self._snapshots.append(snap)
                    self._validated_nodes.append(
                        _enrich_node_with_snapshot(node, snap, TRANSPORT_SSH)
                    )
                    return await self._async_advance_node_queue()
                finally:
                    await ssh_client.async_close()

        schema = vol.Schema(
            {
                vol.Required(CONF_CONFIRM_ACTION, default=CONFIRM_USE_SSH_READONLY): SelectSelector(
                    SelectSelectorConfig(
                        options=[CONFIRM_ENABLE_REST, CONFIRM_USE_SSH_READONLY],
                        mode=SelectSelectorMode.LIST,
                        translation_key="confirm_action",
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="confirm_enable_rest",
            data_schema=schema,
            errors=errors,
            description_placeholders={"host": target_host},
        )

    async def async_step_filters(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure dynamic SSID selection and client tracking filters."""
        discovered_ssids = sorted(
            {ssid for snap in self._snapshots for ssid in snap.all_ssids}
        )

        if user_input is not None:
            data = {
                CONF_TOPOLOGY_MODE: self._topology_mode,
                CONF_SITE_NAME: self._site_name,
                CONF_NODES: self._validated_nodes,
            }
            options = _extract_filter_options(user_input)
            return self.async_create_entry(
                title=f"Aruba {self._site_name}",
                data=data,
                options=options,
            )

        schema = _build_filter_schema(discovered_ssids, is_initial=True)
        return self.async_show_form(step_id="filters", data_schema=schema)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle targeted reauthentication triggered by coordinator."""
        self._reauth_failed_hosts = list(entry_data.get("failed_hosts") or [])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prompt for updated credentials for the failing AP (or whole Multi-AP fleet)."""
        entry = self._get_reauth_entry()
        nodes = [dict(n) for n in entry.data.get(CONF_NODES, [])]
        errors: dict[str, str] = {}

        all_hosts = [str(n[CONF_HOST]) for n in nodes]
        mapped_failed: list[str] = []
        for raw_fail in self._reauth_failed_hosts:
            owner = next(
                (str(n[CONF_HOST]) for n in nodes if n[CONF_HOST] == raw_fail),
                None,
            )
            if owner is None:
                owner = next(
                    (
                        str(n[CONF_HOST])
                        for n in nodes
                        if raw_fail in (n.get("known_peers") or ())
                    ),
                    None,
                )
            mapped_host = owner or (all_hosts[0] if len(nodes) == 1 else None)
            if mapped_host and mapped_host in all_hosts and mapped_host not in mapped_failed:
                mapped_failed.append(mapped_host)

        default_host = mapped_failed[0] if mapped_failed else (all_hosts[0] if all_hosts else "")

        selected_host = str(
            (user_input or {}).get(CONF_TARGET_HOST) or default_host
        )
        if selected_host not in all_hosts and all_hosts:
            selected_host = default_host
        selected_user = (user_input or {}).get(CONF_USERNAME)
        selected_apply_all = bool((user_input or {}).get(CONF_APPLY_TO_ALL_NODES, False))

        if user_input is not None:
            target_host = selected_host
            username = str(user_input[CONF_USERNAME]).strip()
            password = str(user_input[CONF_PASSWORD])

            updated_nodes: list[dict[str, Any]] = []
            probes: list[tuple[int, dict[str, Any]]] = []
            for i, node in enumerate(nodes):
                new_node = dict(node)
                if len(nodes) == 1 or selected_apply_all or node[CONF_HOST] == target_host:
                    new_node[CONF_USERNAME] = username
                    new_node[CONF_PASSWORD] = password
                    probes.append((i, new_node))
                updated_nodes.append(new_node)

            try:
                for idx_i, probe_node in probes:
                    snap, resolved = await self._async_probe_node(probe_node)
                    updated_nodes[idx_i] = _enrich_node_with_snapshot(
                        probe_node, snap, resolved
                    )
            except ArubaAuthError:
                errors["base"] = "invalid_auth"
            except (ArubaConnectionError, ArubaRestApiDisabledError):
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected reauth error for %s", target_host)
                errors["base"] = "unknown"
            else:
                if not selected_apply_all and len(nodes) > 1:
                    self._reauth_failed_hosts = [
                        h
                        for h in self._reauth_failed_hosts
                        if h != target_host
                        and not any(
                            n[CONF_HOST] == target_host and h in (n.get("known_peers") or ())
                            for n in nodes
                        )
                    ]
                else:
                    self._reauth_failed_hosts = []

                if self._reauth_failed_hosts:
                    # Persist updated node right away and advance to next failing AP
                    self.hass.config_entries.async_update_entry(
                        entry, data={**entry.data, CONF_NODES: updated_nodes}
                    )
                    user_input = None
                    selected_host = self._reauth_failed_hosts[0]
                    selected_user = None
                    selected_apply_all = False
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={CONF_NODES: updated_nodes},
                    )

        target_node = next((n for n in nodes if n[CONF_HOST] == selected_host), nodes[0] if nodes else {})
        fields: dict[Any, Any] = {}
        if len(nodes) > 1:
            fields[vol.Required(CONF_TARGET_HOST, default=selected_host)] = SelectSelector(
                SelectSelectorConfig(options=all_hosts, mode=SelectSelectorMode.DROPDOWN)
            )
        fields[
            vol.Required(
                CONF_USERNAME,
                default=str(selected_user if selected_user is not None else target_node.get(CONF_USERNAME, "admin")),
            )
        ] = TextSelector()
        fields[vol.Required(CONF_PASSWORD)] = TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        )
        if len(nodes) > 1:
            fields[vol.Required(CONF_APPLY_TO_ALL_NODES, default=selected_apply_all)] = (
                BooleanSelector()
            )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={"host": selected_host},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure cluster endpoint or show Multi-AP add/edit/remove menu."""
        entry = self._get_reconfigure_entry()
        topology = str(entry.data.get(CONF_TOPOLOGY_MODE, TOPOLOGY_CLUSTER))
        if topology == TOPOLOGY_CLUSTER:
            return await self.async_step_reconfigure_cluster()

        nodes = entry.data.get(CONF_NODES, [])
        menu_opts = ["add_ap", "edit_ap"]
        if len(nodes) > 1:
            menu_opts.append("remove_ap")
        return self.async_show_menu(step_id="reconfigure", menu_options=menu_opts)

    async def async_step_reconfigure_cluster(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update single-node Virtual Controller / Standalone AP connection settings."""
        entry = self._get_reconfigure_entry()
        nodes = [dict(n) for n in entry.data.get(CONF_NODES, [])]
        current = nodes[0] if nodes else {}
        errors: dict[str, str] = {}

        if user_input is not None:
            host = _clean_host(str(user_input.get(CONF_HOST, "")))
            if not host:
                errors[CONF_HOST] = "invalid_host"
            else:
                node = _build_node_dict(
                    host=host,
                    username=str(user_input[CONF_USERNAME]),
                    password=str(user_input.get(CONF_PASSWORD) or current.get(CONF_PASSWORD, "")),
                    transport=str(user_input.get(CONF_TRANSPORT, TRANSPORT_AUTO)),
                    advanced=user_input.get("advanced"),
                )
                self._reset_queue("finish_reconfigure_cluster")
                try:
                    snap, resolved = await self._async_probe_node(node)
                except ArubaRestApiDisabledError as exc:
                    rest_node = dict(node)
                    if exc.host:
                        rest_node["_rest_enable_host"] = exc.host
                    self._pending_rest_confirm_nodes = [rest_node]
                    return await self._async_advance_node_queue()
                except ArubaAuthError:
                    errors["base"] = "invalid_auth"
                except ArubaConnectionError:
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Reconfigure failed for %s", host)
                    errors["base"] = "unknown"
                else:
                    self._snapshots = [snap]
                    self._validated_nodes = [_enrich_node_with_snapshot(node, snap, resolved)]
                    return await self._async_advance_node_queue()

        schema = _build_node_form_schema(
            user_input or current, password_required=False, include_host=True
        )
        return self.async_show_form(
            step_id="reconfigure_cluster",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_add_ap(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add an AP node to an existing Multi-AP deployment."""
        entry = self._get_reconfigure_entry()
        nodes = [dict(n) for n in entry.data.get(CONF_NODES, [])]
        default_user = str(nodes[0].get(CONF_USERNAME, "admin")) if nodes else "admin"
        errors: dict[str, str] = {}

        if user_input is not None:
            host = _clean_host(str(user_input.get(CONF_HOST, "")))
            if not host:
                errors[CONF_HOST] = "invalid_host"
            elif any(str(n[CONF_HOST]).lower() == host.lower() for n in nodes):
                errors[CONF_HOST] = "duplicate_host"
            else:
                node = _build_node_dict(
                    host=host,
                    username=str(user_input[CONF_USERNAME]),
                    password=str(user_input[CONF_PASSWORD]),
                    transport=str(user_input.get(CONF_TRANSPORT, TRANSPORT_AUTO)),
                    advanced=user_input.get("advanced"),
                )
                self._reset_queue("finish_reconfigure")
                self._editing_node_index = None
                try:
                    snap, resolved = await self._async_probe_node(node)
                except ArubaRestApiDisabledError as exc:
                    rest_node = dict(node)
                    if exc.host:
                        rest_node["_rest_enable_host"] = exc.host
                    self._pending_rest_confirm_nodes = [rest_node]
                    return await self._async_advance_node_queue()
                except ArubaAuthError:
                    errors["base"] = "invalid_auth"
                except ArubaConnectionError:
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Failed adding AP %s", host)
                    errors["base"] = "unknown"
                else:
                    self._snapshots = [snap]
                    self._validated_nodes = [_enrich_node_with_snapshot(node, snap, resolved)]
                    return await self._async_advance_node_queue()

        defaults = dict(user_input or {CONF_USERNAME: default_user})
        schema = _build_node_form_schema(defaults, password_required=True, include_host=True)
        return self.async_show_form(step_id="add_ap", data_schema=schema, errors=errors)

    async def async_step_edit_ap(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select which AP node in `nodes[]` to edit."""
        entry = self._get_reconfigure_entry()
        nodes = [dict(n) for n in entry.data.get(CONF_NODES, [])]
        hosts = [str(n[CONF_HOST]) for n in nodes]

        if user_input is not None:
            target = str(user_input[CONF_TARGET_HOST])
            self._editing_node_index = next(
                (i for i, n in enumerate(nodes) if str(n[CONF_HOST]) == target),
                0,
            )
            return await self.async_step_edit_ap_details()

        schema = vol.Schema(
            {
                vol.Required(CONF_TARGET_HOST, default=hosts[0]): SelectSelector(
                    SelectSelectorConfig(options=hosts, mode=SelectSelectorMode.DROPDOWN)
                )
            }
        )
        return self.async_show_form(step_id="edit_ap", data_schema=schema)

    async def async_step_edit_ap_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit an existing Multi-AP node's IP, credentials, or transport."""
        entry = self._get_reconfigure_entry()
        nodes = [dict(n) for n in entry.data.get(CONF_NODES, [])]
        idx = self._editing_node_index if self._editing_node_index is not None else 0
        current = nodes[idx]
        errors: dict[str, str] = {}

        if user_input is not None:
            host = _clean_host(str(user_input.get(CONF_HOST, "")))
            if not host:
                errors[CONF_HOST] = "invalid_host"
            else:
                node = _build_node_dict(
                    host=host,
                    username=str(user_input[CONF_USERNAME]),
                    password=str(user_input.get(CONF_PASSWORD) or current.get(CONF_PASSWORD, "")),
                    transport=str(user_input.get(CONF_TRANSPORT, TRANSPORT_AUTO)),
                    advanced=user_input.get("advanced"),
                )
                self._reset_queue("finish_reconfigure")
                self._editing_node_index = idx
                try:
                    snap, resolved = await self._async_probe_node(node)
                except ArubaRestApiDisabledError as exc:
                    rest_node = dict(node)
                    if exc.host:
                        rest_node["_rest_enable_host"] = exc.host
                    self._pending_rest_confirm_nodes = [rest_node]
                    return await self._async_advance_node_queue()
                except ArubaAuthError:
                    errors["base"] = "invalid_auth"
                except ArubaConnectionError:
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Failed editing AP %s", host)
                    errors["base"] = "unknown"
                else:
                    self._snapshots = [snap]
                    self._validated_nodes = [_enrich_node_with_snapshot(node, snap, resolved)]
                    return await self._async_advance_node_queue()

        schema = _build_node_form_schema(
            user_input or current, password_required=False, include_host=True
        )
        return self.async_show_form(
            step_id="edit_ap_details",
            data_schema=schema,
            errors=errors,
            description_placeholders={"host": str(current.get(CONF_HOST, ""))},
        )

    async def async_step_remove_ap(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove an AP node and cleanly delete its DeviceEntry + AP entities."""
        entry = self._get_reconfigure_entry()
        nodes = [dict(n) for n in entry.data.get(CONF_NODES, [])]
        hosts = [str(n[CONF_HOST]) for n in nodes]

        if user_input is not None and len(nodes) > 1:
            target = str(user_input[CONF_TARGET_HOST])
            removed = next((n for n in nodes if str(n[CONF_HOST]) == target), None)
            remaining = [n for n in nodes if str(n[CONF_HOST]) != target]

            if removed is not None:
                ap_id = str(removed.get(CONF_AP_ID) or removed[CONF_HOST])
                dev_reg = dr.async_get(self.hass)
                ap_device = dev_reg.async_get_device_by_identifier(
                    (DOMAIN, f"ap_{entry.entry_id}_{ap_id}"), entry.entry_id
                )
                if ap_device is not None:
                    dev_reg.async_remove_device(ap_device.id)

            return self.async_update_reload_and_abort(
                entry,
                data_updates={CONF_NODES: remaining},
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_TARGET_HOST, default=hosts[-1]): SelectSelector(
                    SelectSelectorConfig(options=hosts, mode=SelectSelectorMode.DROPDOWN)
                )
            }
        )
        return self.async_show_form(step_id="remove_ap", data_schema=schema)


class ArubaOptionsFlow(OptionsFlowWithReload):
    """Manage SSID filters, roaming sensors, and polling intervals."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Display and persist updated integration options (reloads entry on change)."""
        entry = self.config_entry
        if user_input is not None:
            return self.async_create_entry(data=_extract_filter_options(user_input))

        discovered_ssids: list[str] = []
        runtime_data = getattr(entry, "runtime_data", None)
        if runtime_data is not None and runtime_data.coordinator is not None:
            coord = runtime_data.coordinator
            ssids = set(coord.discovered_ssids)
            if coord.data is not None:
                ssids.update(coord.data.all_ssids)
            discovered_ssids = sorted(ssids)

        schema = _build_filter_schema(
            discovered_ssids=discovered_ssids,
            current_options=entry.options,
            is_initial=False,
        )
        return self.async_show_form(step_id="init", data_schema=schema)
