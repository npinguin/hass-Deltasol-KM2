"""Optional single RESOL dashboard; native entities remain the command owner.

Panel lifecycle follows the MIT Audi Connect vehicle dashboard pattern
(copyright 2019 Arjen van Rhijn), adapted for one RESOL application.
See DASHBOARD-LICENSE.txt.
"""

from __future__ import annotations

import asyncio
import re
from hashlib import sha256
from math import isclose
from pathlib import Path

import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED, ConfigEntryState
from homeassistant.core import Context, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .catalog import CatalogError, configuration_evidence, is_sensitive
from .configuration import (
    ActorReferences,
    WeeklyReview,
    format_weekly_day,
    parse_weekly_day,
)
from .const import CONF_DASHBOARD, CONF_ENGINEERING, DOMAIN
from .diagnostics import async_get_config_entry_diagnostics, menu_parameter_reason

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
        "Anlage_Wf": "solid_fuel"
        if "_Feststoffkessel_" in setting.name
        else "function",
        "Solar_Sp": "store",
        "Solar_Kol": "collector",
        "Heizung_Heizkreis": "circuit",
        "Heizung_Wf": "heating_function",
        "Wmz": "meter",
    }.get(family, "solar_function"), number


def capability_role(setting, state, depth):
    """Presentation only; derive roles from contracts and HA device classes."""
    if setting:
        if setting.weekly_slot is not None:
            return "schedule", 50
        if setting.name.endswith("_Betriebsart"):
            return "mode", 100
        if depth == "controls":
            return "target", 90
        if setting.name.endswith("_Aktiviert_Modus"):
            return "configured_activity", 80
        return "preference", 10
    if state:
        kind = state.attributes.get("device_class")
        if kind in ("problem", "safety"):
            return "attention", 120
        if kind == "temperature":
            return "temperature", 95
        if kind == "power":
            return "power", 90
        if kind == "energy":
            return "energy", 85
        if kind == "volume_flow_rate":
            return "flow", 75
    return "measurement" if depth == "status" else "technical", 20


def capability_availability(state, setting, cap, telemetry_current, parameter_current):
    """Current state is distinct from discovery and static reference evidence."""
    if not telemetry_current:
        return "telemetry_unavailable"
    if setting and not parameter_current:
        return "parameter_unavailable"
    if setting and cap is None:
        return "requirements_not_confirmed"
    if state is None or state.state == "unknown":
        return "unknown"
    if state.state == "unavailable":
        return "unavailable"
    return "available"


def inherit_object_section(asset):
    """Only inherit a section within the same positively classified function.

    Do not move a controller-wide temperature into a hydraulic function
    based on its label or its physical controller's parentage.
    """
    if not asset["object_confirmed"]:
        return
    groups = {
        row["section"]
        for row in asset["capabilities"]
        if row["section"] not in ("overview", "schedule")
    }
    if len(groups) != 1:
        return
    section = next(iter(groups))
    for row in asset["capabilities"]:
        if row["section"] == "overview":
            row["section"] = section


def project_assignments(observations, output_sources, engineering):
    """One read-only relation per native reference, with no new entity."""
    grouped = {}
    for kind, name, number in observations:
        grouped.setdefault((kind, name), set()).add(number)
    result = []
    for (kind, name), numbers in sorted(grouped.items()):
        # Conflicting reads never resolve by registry order.
        if len(numbers) != 1:
            continue
        number = next(iter(numbers))
        source = output_sources.get(number) if kind.startswith("relay_") else None
        result.append(
            {
                "kind": kind,
                "number": number,
                "source_object": source,
                "resolution": "linked" if source else "native_reference_only",
                **({"parameter": name} if engineering else {}),
            }
        )
    return result


def relay_output_sources(relays, inventory, visible_devices):
    """Map exact native relay layouts to readable, currently observed devices."""
    result = {}
    for number in range(1, 46):
        role = next(iter(ActorReferences.output_layout(("relay", number))), None)
        matches = [
            cap
            for cap in relays.values()
            if cap.group.parameter(cap.number, "AktorFunktion") == role
            and cap.matches(inventory)
        ]
        if len(matches) != 1:
            continue
        device = matches[0].device
        ids = visible_devices.get(f"{device.serial}_{device.source}", set())
        if len(ids) == 1:
            result[number] = next(iter(ids))
    return result


def weekly_owner_label(name):
    """Human-readable label for a positively observed native schedule reference."""
    match = re.fullmatch(
        r"(Anlage_Wf|Heizung_Wf|Heizung_Heizkreis)([0-9]+)"
        r"(?:_Raumthermostat([0-9]+))?_SchaltuhrNr",
        name,
    )
    if not match:
        return "RESOL program reference"
    family, number, room = match.groups()
    label = {
        "Anlage_Wf": "Arrangement function",
        "Heizung_Wf": "Heating function",
        "Heizung_Heizkreis": "Heating circuit",
    }[family]
    return f"{label} {number}" + (f" / Room thermostat {room}" if room else "")


def capability_access(registered, setting, state, user, engineering, *, relay=False):
    """UI eligibility is additional to, never a substitute for, HA permissions."""
    domain = registered.entity_id.split(".", 1)[0]
    if (
        domain == "button"
        or relay
        or registered.entity_category == "diagnostic"
        or (setting and not setting.save)
    ):
        depth = "engineering"
    elif domain in ("select", "number", "switch", "text"):
        depth = "settings"
        if setting and (
            setting.name.endswith("_Betriebsart")
            or (
                setting.kind == "number"
                and setting.name.endswith(("_TSoll", "_Tsoll", "_TVorlaufVorgabe"))
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
        telemetry_current = entry.runtime_data.coordinator.last_update_success
        parameter_current = bool(relay and relay.last_update_success)
        controls = snapshot.configuration if snapshot else {}
        relays = snapshot.capabilities if snapshot else {}
        static_settings = (
            {setting.name: setting for setting in snapshot.profile.settings}
            if snapshot and snapshot.profile
            else {}
        )
        objects = {}
        assignments = {}
        visible_devices = {}
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
            availability = capability_availability(
                state, setting, cap, telemetry_current, parameter_current
            )
            if availability != "available" and not (
                row.entity_id.startswith("button.") and availability == "unknown"
            ):
                control = False
            section = capability_section(setting, state)
            role, priority = capability_role(setting, state, depth)
            asset = objects.setdefault(
                device.id,
                {
                    "id": device.id,
                    "name": device.name_by_user or device.name or "RESOL",
                    "renamed": bool(device.name_by_user),
                    "capabilities": [],
                    "object_kind": None,
                    "object_number": None,
                    "object_confirmed": False,
                },
            )
            for domain, identifier in device.identifiers:
                if domain == DOMAIN:
                    visible_devices.setdefault(identifier, set()).add(device.id)
            kind = object_kind(setting)
            confirmed = bool(cap and availability == "available")
            if confirmed:
                assignments.setdefault(device.id, set()).update(
                    cap.assignment_observations
                )
            if kind and (
                not asset["object_kind"]
                or (confirmed and not asset["object_confirmed"])
                or (confirmed == asset["object_confirmed"] and kind[0] == "solid_fuel")
            ):
                asset["object_kind"], asset["object_number"] = kind
                asset["object_confirmed"] = confirmed
            asset["capabilities"].append(
                {
                    "entity_id": row.entity_id,
                    "name": row.name or row.original_name or "RESOL",
                    "renamed": bool(row.name),
                    "kind": row.entity_id.split(".", 1)[0],
                    "section": section,
                    "depth": depth,
                    "role": role,
                    "priority": priority,
                    "linked_program": (
                        cap.program_reference
                        if cap and availability == "available"
                        else None
                    ),
                    "availability": availability,
                    "native_label": setting.label if setting else None,
                    **(
                        {
                            "parameter": setting.name,
                            "reference_index": setting.index,
                            "source_address": (
                                f"0x{cap.device.address:04X}" if cap else None
                            ),
                            "controller_address": (
                                f"0x{cap.controller.address:04X}" if cap else None
                            ),
                            "live_reference_context": (
                                dict(cap.reference_context)
                                if cap and availability == "available"
                                else {}
                            ),
                        }
                        if engineering and setting
                        else {
                            "parameter": relays[row.unique_id].name,
                            "reference_index": relays[row.unique_id].index,
                        }
                        if engineering and row.unique_id in relays
                        else {}
                    ),
                    "control": control,
                    "schedule": bool(setting and setting.weekly_slot is not None),
                    "program": setting.weekly_slot if setting else None,
                    "day": (
                        setting.name.rsplit("_Tag", 1)[-1].split("_", 1)[0]
                        if setting and setting.weekly_slot is not None
                        else None
                    ),
                    "shared_references": (
                        [weekly_owner_label(owner) for owner in cap.owners]
                        if cap and setting and setting.weekly_slot is not None
                        else []
                    ),
                    "reference_token": (
                        cap.weekly_review_token
                        if cap and setting and setting.weekly_slot is not None
                        else None
                    ),
                }
            )
        output_sources = (
            relay_output_sources(
                relays,
                entry.runtime_data.coordinator.observed_inventory,
                visible_devices,
            )
            if telemetry_current and parameter_current and assignments
            else {}
        )
        for asset in objects.values():
            inherit_object_section(asset)
            asset["relations"] = project_assignments(
                assignments.get(asset["id"], ()), output_sources, engineering
            )
        if objects:
            installations.append(
                {
                    "id": entry.entry_id,
                    "name": entry.title or "RESOL",
                    "settings": bool(user.is_admin),
                    "engineering": engineering,
                    "health": {
                        "telemetry": bool(
                            entry.runtime_data.coordinator.last_update_success
                        ),
                        "parameter_access": bool(relay),
                        "parameter_current": parameter_current,
                        "parameter_observed_at": (
                            snapshot.observed_at.isoformat()
                            if snapshot and snapshot.observed_at
                            else None
                        ),
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


@websocket_api.websocket_command(
    {vol.Required("type"): "deltasol/dashboard/subscribe"}
)
@callback
def ws_dashboard_subscribe(hass, connection, msg):
    """Publish the same scoped projection on native source updates, never poll."""
    watched = {}
    pending = None
    previous = None
    closed = False

    @callback
    def schedule(*_):
        nonlocal pending
        if not closed and pending is None:
            pending = hass.loop.call_later(0.1, publish)

    @callback
    def publish():
        nonlocal pending, previous
        pending = None
        if closed:
            return
        projection = dashboard_objects(hass, connection.user)
        visible = {item["id"] for item in projection}
        entries = {
            entry.entry_id: entry for entry in hass.config_entries.async_entries(DOMAIN)
        }
        # Rebind when the native entry is loaded, removed, or reloaded.
        for entry_id, (entry, runtime, removers) in list(watched.items()):
            current = entries.get(entry_id)
            target = current.runtime_data if current and entry_id in visible else None
            if current is not entry or target is not runtime:
                for remove in removers:
                    remove()
                del watched[entry_id]
        for entry_id, entry in entries.items():
            if entry_id in watched:
                continue
            runtime = entry.runtime_data if entry_id in visible else None
            removers = [entry.async_on_state_change(schedule)]
            if runtime:
                # Visible native entities already own the coordinator cadence.
                removers.append(runtime.coordinator.async_add_listener(schedule))
                if runtime.relay_coordinator:
                    removers.append(
                        runtime.relay_coordinator.async_add_listener(schedule)
                    )
                runtime.capture_listeners.add(schedule)
                removers.append(
                    lambda runtime=runtime: runtime.capture_listeners.discard(schedule)
                )
            watched[entry_id] = (entry, runtime, removers)
        if projection != previous:
            previous = projection
            connection.send_event(msg["id"], projection)

    subscriptions = [
        async_dispatcher_connect(hass, SIGNAL_CONFIG_ENTRY_CHANGED, schedule),
        hass.bus.async_listen("entity_registry_updated", schedule),
        hass.bus.async_listen("device_registry_updated", schedule),
    ]

    @callback
    def unsubscribe():
        nonlocal closed
        closed = True
        if pending:
            pending.cancel()
        for remove in subscriptions:
            remove()
        for _, _, removers in watched.values():
            for remove in removers:
                remove()
        watched.clear()

    connection.subscriptions[msg["id"]] = unsubscribe
    connection.send_result(msg["id"])
    publish()


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
    # Include the operational contracts even when no engineering capture can
    # be restored. This is an admin-only projection of the existing catalogue.
    relay = entry.runtime_data.relay_coordinator
    discovery = relay.data if relay else None
    catalog = relay._configuration_catalog() if relay else None
    if catalog and discovery and discovery.profile:
        profile = discovery.profile
        contracts, inventory = engineering_catalogue(profile, catalog, discovery)
        report["operational_contracts"] = contracts
        report["reference_inventory"] = inventory
        report["operational_contracts_source"] = {
            "changeset": f"{discovery.changeset:08X}",
            "digest": catalog.digest,
        }
    # Recheck opt-in and lifecycle after the asynchronous diagnostics call.
    if not engineering_enabled(entry, connection.user):
        connection.send_error(msg["id"], "not_allowed", "Engineering is not enabled")
        return
    connection.send_result(msg["id"], report)


def engineering_catalogue(profile, catalog, discovery):
    """Project native contracts and reference evidence, without a capture or I/O.

    Static variants describe possible prerequisites. Only native discovery and
    the typed writer determine live applicability and permission to act.
    """
    sensitive_indices = {
        item.index
        for item in catalog.definitions
        if item.index is not None and is_sensitive(item)
    }
    definitions = {
        item.name: item
        for item in catalog.definitions
        if not is_sensitive(item) and item.index not in sensitive_indices
    }
    bound, rejected = profile.binding_review(discovery.changeset, catalog)
    contracts = {}
    for setting in (*bound, *profile.settings):
        if setting.name not in definitions:
            continue
        contracts.setdefault(
            setting.name,
            {
                "label": setting.label,
                "catalogue_binding": "not_confirmed",
                "reasons": list(rejected.get(setting.name, ())),
                "variants": [],
            },
        )
    for setting in bound:
        if setting.name not in contracts:
            continue
        row = contracts[setting.name]
        row["catalogue_binding"] = "verified"
        variant = {
            "kind": setting.kind,
            "unit": setting.unit,
            "storage_action": "save" if setting.save else "volatile",
            "limits": list(setting.limits[:3]) if setting.limits else None,
            "options": [label for label, _ in setting.options],
            "native_slot": setting.native_slot,
            "prerequisites": [
                {"parameter": name, "reference_index": index}
                for name, index in sorted(setting.expected.items())
                if name != setting.name
                and name in definitions
                and index not in sensitive_indices
            ],
            "relative_constraints": [
                {"parameter": rule.name, "operation": rule.operation}
                for rule in setting.relative_constraints
                if rule.name in definitions
            ],
            "requires_live_validation": True,
        }
        if variant not in row["variants"]:
            row["variants"].append(variant)

    inventory = {}
    for name, definition in definitions.items():
        evidence = configuration_evidence(definition, definitions)
        if name in contracts:
            evidence["write_access_reason"] = (
                "live_capability_not_confirmed"
                if contracts[name]["catalogue_binding"] == "verified"
                else "catalogue_contract_not_confirmed"
            )
        elif reason := menu_parameter_reason(profile, definition):
            evidence["write_access_reason"] = reason
        inventory[name] = {
            **evidence,
            "reference_index": definition.index,
            "type_id": definition.type_id,
            "storage": definition.storage,
            "metadata": definition.metadata,
            "status": "reference_only",
            "write_access": "not_confirmed",
        }
    return contracts, inventory


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
        vol.Optional("reference_token"): str,
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
                    "reference_token": row["reference_token"],
                    "expected": hass.states.get(row["entity_id"]).state,
                },
            )
            return
        if row["schedule"] and (
            msg.get("expected") != hass.states.get(row["entity_id"]).state
            or msg.get("references") != row["shared_references"]
            or msg.get("reference_token") != row["reference_token"]
        ):
            raise HomeAssistantError("Weekly program changed; review again")
        if row["schedule"]:
            # Preserve the explicit precondition across HA service dispatch.
            # Ordinary native text.set_value remains available unchanged.
            kind, service = DOMAIN, "save_reviewed_weekly_program"
            data.update(
                entry_id=msg["entry_id"],
                expected=msg["expected"],
                reference_token=msg["reference_token"],
            )
        await hass.services.async_call(
            kind,
            service,
            {"entity_id": row["entity_id"], **data},
            blocking=True,
            context=Context(user_id=connection.user.id),
        )
        if row["schedule"]:
            kind = "text"
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


async def save_reviewed_weekly_program(hass, call):
    """HA action adapter: scoped admin, explicit review, existing native owner."""
    user = (
        await hass.auth.async_get_user(call.context.user_id)
        if call.context.user_id
        else None
    )
    if not user or not user.is_admin:
        raise HomeAssistantError("Weekly program configuration is not permitted")
    data = call.data
    row = scoped_control(hass, user, data["entry_id"], data["entity_id"])
    if not row["schedule"]:
        raise HomeAssistantError("Not a weekly program")
    entry = hass.config_entries.async_get_entry(data["entry_id"])
    registered = er.async_get(hass).async_get(row["entity_id"])
    if (
        not enabled(entry)
        or registered is None
        or registered.config_entry_id != entry.entry_id
        or not entry.runtime_data.relay_coordinator
    ):
        raise HomeAssistantError("Weekly program is no longer available")
    await entry.runtime_data.relay_coordinator.async_set_configuration(
        registered.unique_id,
        data["value"],
        review=WeeklyReview(
            registered.unique_id, data["expected"], data["reference_token"]
        ),
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
            websocket_api.async_register_command(hass, ws_dashboard_subscribe)
            websocket_api.async_register_command(hass, ws_engineering)
            websocket_api.async_register_command(hass, ws_action)

            async def reviewed_weekly_action(call):
                await save_reviewed_weekly_program(hass, call)

            hass.services.async_register(
                DOMAIN,
                "save_reviewed_weekly_program",
                reviewed_weekly_action,
                schema=vol.Schema(
                    {
                        vol.Required("entry_id"): str,
                        vol.Required("entity_id"): str,
                        vol.Required("value"): str,
                        vol.Required("expected"): str,
                        vol.Required("reference_token"): str,
                    }
                ),
            )
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
