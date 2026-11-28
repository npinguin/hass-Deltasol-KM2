"""Optional single RESOL dashboard; native entities remain the command owner.

Panel lifecycle follows the MIT Audi Connect vehicle dashboard pattern
(copyright 2019 Arjen van Rhijn), adapted for one RESOL application.
See DASHBOARD-LICENSE.txt.
"""

from __future__ import annotations

import asyncio
from hashlib import sha256
from math import isclose
from pathlib import Path

import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .catalog import CatalogError
from .configuration import format_weekly_day, parse_weekly_day
from .const import CONF_DASHBOARD, CONF_ENGINEERING, DOMAIN
from .diagnostics import async_get_config_entry_diagnostics

_PANEL = "resol"
_READY = "deltasol_dashboard_registered"
_RESOURCES = "deltasol_dashboard_resources"
_LOCK = "deltasol_dashboard_lock"
_ACTIVE = "deltasol_dashboard_entries"


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
    return bool(
        enabled(entry) and user.is_admin and entry.options.get(CONF_ENGINEERING)
    )


def capability_section(setting, state):
    """Classify verified native contracts, not inferred hydraulic relationships."""
    if setting:
        name = setting.name
        if setting.weekly_slot is not None:
            return "schedule"
        if name.startswith("Heizung_") and any(
            part in name
            for part in ("_ThBwErwaermung", "_BwVorerwaermung", "_Zirkulation")
        ):
            return "hot_water"
        if name.startswith("Solar_"):
            return "solar"
        if name.startswith("Anlage_") and "_Feststoffkessel_" in name:
            return "heating"
        if name.startswith("Heizung_"):
            return "heating"
        if setting.native_slot and setting.native_slot[1] == "Wmz":
            return "energy"
    if state and state.attributes.get("device_class") in ("energy", "power"):
        return "energy"
    return "overview"


def object_kind(setting):
    """Presentation type of the existing device, not a new object identity."""
    if setting is None or setting.native_slot is None:
        return None
    _, family, number = setting.native_slot
    return {
        "Anlage_Wf": "solid_fuel" if "_Feststoffkessel_" in setting.name else "function",
        "Solar_Sp": "store",
        "Solar_Kol": "collector",
        "Heizung_Heizkreis": "circuit",
        "Heizung_Wf": "heating_function",
        "Wmz": "meter",
    }.get(family, "solar_function"), number


def capability_access(registered, setting, state, user, engineering, *, relay=False):
    """UI eligibility is additional to, never a substitute for, HA permissions."""
    domain = registered.entity_id.split(".", 1)[0]
    if domain == "button" or relay or (setting and not setting.save):
        depth = "engineering"
    elif domain in ("select", "number", "switch", "text"):
        depth = "settings"
        if setting and (
            setting.name.endswith("_Betriebsart")
            or (
                setting.kind == "number" and setting.name.endswith(("_TSoll", "_Tsoll"))
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
        and state.state != "unavailable"
        and (domain == "button" or state.state != "unknown")
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
        static_settings = (
            {setting.name: setting for setting in snapshot.profile.settings}
            if snapshot and snapshot.profile
            else {}
        )
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
            cap = controls.get(row.unique_id)
            setting = cap.setting if cap else None
            # Retain category when a previously discovered control is unavailable.
            if not setting and state:
                setting = static_settings.get(state.attributes.get("parameter"))
            relay_control = row.unique_id in relays or (
                row.entity_id.startswith("select.")
                and row.unique_id.endswith("_mode")
                and setting is None
            )
            access = capability_access(
                row, setting, state, user, engineering, relay=relay_control
            )
            if access is None:
                continue
            depth, control = access
            section = capability_section(setting, state)
            asset = objects.setdefault(
                device.id,
                {
                    "id": device.id,
                    "name": device.name_by_user or device.name or "RESOL",
                    "capabilities": [],
                    "object_kind": None,
                    "object_number": None,
                },
            )
            kind = object_kind(setting)
            if kind and (
                not asset["object_kind"] or kind[0] == "solid_fuel"
            ):
                asset["object_kind"], asset["object_number"] = kind
            asset["capabilities"].append(
                {
                    "entity_id": row.entity_id,
                    "name": row.name or row.original_name or "RESOL",
                    "kind": row.entity_id.split(".", 1)[0],
                    "section": section,
                    "depth": depth,
                    "availability": (
                        "available"
                        if state and state.state not in ("unknown", "unavailable")
                        else "not_confirmed"
                    ),
                    "native_label": setting.label if setting else None,
                    "control": control,
                    "schedule": bool(setting and setting.weekly_slot is not None),
                    "shared_references": (
                        [
                            static_settings[owner].label
                            if owner in static_settings
                            else "RESOL program reference"
                            for owner in cap.owners
                        ]
                        if cap and setting and setting.weekly_slot is not None
                        else []
                    ),
                }
            )
        if objects:
            installations.append(
                {
                    "id": entry.entry_id,
                    "name": f"RESOL {len(installations) + 1}",
                    "settings": bool(user.is_admin),
                    "engineering": engineering,
                    "health": {
                        "telemetry": bool(
                            entry.runtime_data.coordinator.last_update_success
                        ),
                        "parameter_access": bool(relay),
                        "catalogue": bool(
                            relay and relay._configuration_catalog() is not None
                        ),
                        "selectors_observed": len(snapshot.selector_hints)
                        if snapshot
                        else 0,
                        "selectors_total": snapshot.selector_total if snapshot else 0,
                        "checkpoint": entry.runtime_data.checkpoint_error,
                    },
                    "objects": sorted(
                        objects.values(), key=lambda asset: asset["name"]
                    ),
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


def scoped_control(hass, user, entry_id, entity_id):
    for installation in dashboard_objects(hass, user):
        if installation["id"] != entry_id:
            continue
        for asset in installation["objects"]:
            for row in asset["capabilities"]:
                if row["entity_id"] == entity_id and row["control"]:
                    return row
    raise HomeAssistantError("Control is unavailable or not permitted")


def service_request(row, value):
    kind = row["kind"]
    if kind == "switch" and type(value) is bool:
        return kind, "turn_on" if value else "turn_off", {}
    if kind == "select" and isinstance(value, str):
        return kind, "select_option", {"option": value}
    if kind == "number" and type(value) in (float, int):
        return kind, "set_value", {"value": value}
    if kind == "text" and isinstance(value, str):
        if row["schedule"]:
            value = format_weekly_day(parse_weekly_day(value))
        return kind, "set_value", {"value": value}
    if kind == "button" and value is None:
        return kind, "press", {}
    raise HomeAssistantError("Unsupported control value")


def observed_result(kind, state, value):
    if state is None or state.state == "unavailable":
        return "unavailable"
    if kind == "button":
        return "submitted"
    if state.state == "unknown":
        return "unavailable"
    if kind == "number":
        try:
            return (
                "observed"
                if isclose(float(state.state), value, abs_tol=1e-9)
                else "not_confirmed"
            )
        except (TypeError, ValueError):
            return "not_confirmed"
    if kind == "switch":
        value = "on" if value else "off"
    return "observed" if state.state == value else "not_confirmed"


@websocket_api.websocket_command(
    {
        vol.Required("type"): "deltasol/dashboard/action",
        vol.Required("entry_id"): str,
        vol.Required("entity_id"): str,
        vol.Required("value"): vol.Any(None, bool, int, float, str),
        vol.Optional("preview", default=False): bool,
        vol.Optional("expected"): str,
        vol.Optional("references"): [str],
    }
)
@websocket_api.async_response
async def ws_action(hass, connection, msg):
    """Use native HA services with caller identity; never write the bus here."""
    try:
        row = scoped_control(hass, connection.user, msg["entry_id"], msg["entity_id"])
        kind, service, data = service_request(row, msg["value"])
        if msg["preview"]:
            if not row["schedule"]:
                raise HomeAssistantError("Preview is only for weekly schedules")
            connection.send_result(
                msg["id"],
                {
                    "value": data["value"],
                    "references": row["shared_references"],
                    "expected": hass.states.get(row["entity_id"]).state,
                },
            )
            return
        if row["schedule"] and (
            msg.get("expected") != hass.states.get(row["entity_id"]).state
            or msg.get("references") != row["shared_references"]
        ):
            raise HomeAssistantError("Weekly program changed; review again")
        await hass.services.async_call(
            kind,
            service,
            {"entity_id": row["entity_id"], **data},
            blocking=True,
            context=Context(user_id=connection.user.id),
        )
        state = hass.states.get(row["entity_id"])
        result = observed_result(kind, state, data.get("value", msg["value"]))
        connection.send_result(
            msg["id"], {"outcome": result, "value": state.state if state else None}
        )
    except (HomeAssistantError, CatalogError, ValueError, TypeError):
        connection.send_error(
            msg["id"],
            "action_failed",
            "Action rejected or outcome uncertain; check live state",
        )


def module_url(directory):
    digest = sha256((directory / "dashboard.js").read_bytes()).hexdigest()[:16]
    return f"/deltasol_dashboard/dashboard.js?v={digest}"


async def async_setup_dashboard(hass, entry_id):
    """Register one panel; resources are process-scoped as in native HA."""
    async with hass.data.setdefault(_LOCK, asyncio.Lock()):
        active = hass.data.setdefault(_ACTIVE, set())
        if hass.data.get(_READY):
            active.add(entry_id)
            return
        directory = Path(__file__).parent / "frontend"
        if not hass.data.get(_RESOURCES):
            await hass.http.async_register_static_paths(
                [StaticPathConfig("/deltasol_dashboard", str(directory), False)]
            )
            websocket_api.async_register_command(hass, ws_dashboard_list)
            websocket_api.async_register_command(hass, ws_engineering)
            websocket_api.async_register_command(hass, ws_action)
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
        active.add(entry_id)


async def async_remove_dashboard(hass, entry_id):
    async with hass.data.setdefault(_LOCK, asyncio.Lock()):
        active = hass.data.setdefault(_ACTIVE, set())
        active.discard(entry_id)
        if active:
            return
        if hass.data.pop(_READY, False):
            frontend.async_remove_panel(hass, _PANEL, warn_if_unknown=False)
