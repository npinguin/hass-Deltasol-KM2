"""Optional single RESOL dashboard; native entities remain the command owner.

Panel lifecycle follows the MIT Audi Connect vehicle dashboard pattern
(copyright 2019 Arjen van Rhijn), adapted for one RESOL application.
See DASHBOARD-LICENSE.txt.
"""

from __future__ import annotations

import asyncio
from hashlib import sha256
from pathlib import Path

import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import CONF_DASHBOARD, CONF_ENGINEERING, DOMAIN
from .diagnostics import async_get_config_entry_diagnostics

_PANEL = "resol"
_READY = "deltasol_dashboard_registered"
_RESOURCES = "deltasol_dashboard_resources"
_LOCK = "deltasol_dashboard_lock"


def enabled(entry):
    return bool(
        entry
        and entry.domain == DOMAIN
        and entry.state is ConfigEntryState.LOADED
        and entry.options.get(CONF_DASHBOARD, False)
        and getattr(entry, "runtime_data", None)
        and not entry.runtime_data.closing
    )


def engineering_enabled(entry, user):
    return bool(enabled(entry) and user.is_admin and entry.options.get(CONF_ENGINEERING))


def capability_section(setting, state):
    """Classify verified native contracts, not inferred hydraulic relationships."""
    if setting:
        name = setting.name
        if setting.weekly_slot is not None:
            return "schedule"
        if name.startswith("Heizung_") and any(
            part in name for part in ("_ThBwErwaermung", "_BwVorerwaermung", "_Zirkulation")
        ):
            return "hot_water"
        if name.startswith("Solar_"):
            return "solar"
        if name.startswith("Heizung_"):
            return "heating"
        if setting.native_slot and setting.native_slot[1] == "Wmz":
            return "energy"
    if state and state.attributes.get("device_class") in ("energy", "power"):
        return "energy"
    return "overview"


def capability_access(registered, cap, state, user, engineering):
    """UI eligibility is additional to, never a substitute for, HA permissions."""
    domain = registered.entity_id.split(".", 1)[0]
    setting = cap.setting if cap and hasattr(cap, "setting") else None
    if domain == "button" or (domain == "select" and cap and setting is None):
        depth = "engineering"
    elif setting and not setting.save:
        depth = "engineering"
    elif domain in ("select", "number", "switch", "text"):
        depth = "settings"
        if setting and (
            setting.name.endswith("_Betriebsart")
            or (
                setting.kind == "number"
                and setting.name.endswith(("_TSoll", "_Tsoll"))
            )
        ):
            depth = "controls"
    else:
        depth = "status"
    if depth == "engineering" and not engineering:
        return None
    if depth == "settings" and not user.is_admin:
        # Readable preferences are still visible to permitted residents.
        return depth, False
    control = bool(
        domain in ("select", "number", "switch", "text", "button")
        and state
        and state.state not in ("unknown", "unavailable")
        and user.permissions.check_entity(registered.entity_id, POLICY_CONTROL)
    )
    return depth, control


def dashboard_objects(hass, user):
    """One scoped projection of existing device and entity registry objects."""
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    installations = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if not enabled(entry):
            continue
        engineering = engineering_enabled(entry, user)
        relay = entry.runtime_data.relay_coordinator
        snapshot = relay.data if relay else None
        controls = snapshot.configuration if snapshot else {}
        relays = snapshot.capabilities if snapshot else {}
        objects = {}
        for row in er.async_entries_for_config_entry(registry, entry.entry_id):
            if (
                row.platform != DOMAIN
                or row.config_entry_id != entry.entry_id
                or row.disabled_by is not None
                or row.hidden_by is not None
                or not row.device_id
                or not user.permissions.check_entity(row.entity_id, POLICY_READ)
            ):
                continue
            device = devices.async_get(row.device_id)
            if (
                not device
                or entry.entry_id not in device.config_entries
                or not any(domain == DOMAIN for domain, _ in device.identifiers)
            ):
                continue
            state = hass.states.get(row.entity_id)
            cap = controls.get(row.unique_id) or relays.get(row.unique_id)
            access = capability_access(row, cap, state, user, engineering)
            if access is None:
                continue
            depth, control = access
            setting = cap.setting if cap and hasattr(cap, "setting") else None
            # Retain category when a previously discovered control is unavailable.
            if not setting and state and snapshot and snapshot.profile:
                name = state.attributes.get("parameter")
                setting = next(
                    (s for s in snapshot.profile.settings if s.name == name), None
                )
            section = capability_section(setting, state)
            asset = objects.setdefault(
                device.id,
                {
                    "id": device.id,
                    "name": device.name_by_user or device.name or "RESOL",
                    "capabilities": [],
                },
            )
            asset["capabilities"].append(
                {
                    "entity_id": row.entity_id,
                    "name": row.name or row.original_name or "RESOL",
                    "kind": row.entity_id.split(".", 1)[0],
                    "section": section,
                    "depth": depth,
                    "control": control,
                }
            )
        if objects:
            installations.append(
                {
                    "id": entry.entry_id,
                    "name": f"RESOL {len(installations) + 1}",
                    "settings": bool(user.is_admin),
                    "engineering": engineering,
                    "objects": sorted(objects.values(), key=lambda asset: asset["name"]),
                }
            )
    return installations


@websocket_api.websocket_command({vol.Required("type"): "deltasol/dashboard/list"})
@websocket_api.async_response
async def ws_dashboard_list(hass, connection, msg):
    connection.send_result(msg["id"], dashboard_objects(hass, connection.user))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "deltasol/dashboard/engineering",
        vol.Required("entry_id"): str,
    }
)
@websocket_api.async_response
async def ws_engineering(hass, connection, msg):
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if not engineering_enabled(entry, connection.user):
        connection.send_error(msg["id"], "not_allowed", "Engineering is not enabled")
        return
    report = await async_get_config_entry_diagnostics(hass, entry)
    connection.send_result(msg["id"], report)


def module_url(directory):
    digest = sha256((directory / "dashboard.js").read_bytes()).hexdigest()[:16]
    return f"/deltasol_dashboard/dashboard.js?v={digest}"


async def async_setup_dashboard(hass):
    """Register one panel; resources are process-scoped as in native HA."""
    async with hass.data.setdefault(_LOCK, asyncio.Lock()):
        if hass.data.get(_READY):
            return
        directory = Path(__file__).parent / "frontend"
        if not hass.data.get(_RESOURCES):
            await hass.http.async_register_static_paths(
                [StaticPathConfig("/deltasol_dashboard", str(directory), False)]
            )
            websocket_api.async_register_command(hass, ws_dashboard_list)
            websocket_api.async_register_command(hass, ws_engineering)
            hass.data[_RESOURCES] = True
        url = await hass.async_add_executor_job(module_url, directory)
        await panel_custom.async_register_panel(
            hass,
            config={},
            webcomponent_name="deltasol-dashboard",
            frontend_url_path=_PANEL,
            module_url=url,
            sidebar_title="RESOL",
            sidebar_icon="mdi:solar-power",
            require_admin=False,
            embed_iframe=False,
        )
        hass.data[_READY] = True


async def async_remove_dashboard(hass, entry_id):
    async with hass.data.setdefault(_LOCK, asyncio.Lock()):
        if any(
            entry.entry_id != entry_id and enabled(entry)
            for entry in hass.config_entries.async_entries(DOMAIN)
        ):
            return
        if hass.data.pop(_READY, False):
            frontend.async_remove_panel(hass, _PANEL, warn_if_unknown=False)
