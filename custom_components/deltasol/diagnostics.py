"""Native Home Assistant diagnostics; export cached state without I/O."""

import re

from .const import CONF_VBUS_PASSWORD
from .parameters import (
    OBSERVED_MODES,
    device_inventory,
    find_profile,
)

# Only fixed, integration-defined messages can leave the integration. Never
# export arbitrary exception text, as HTTP failures may contain credentials.
ERROR_CODES = {
    "VBus gateway rejected connection or credentials": "authentication",
    "Invalid VBus password": "invalid_credentials",
    "VBus connection failed or timed out": "connection",
    "VBus gateway connection failed": "gateway_connection",
    "VBus gateway handshake failed": "gateway_handshake",
    "VBus bus acquisition failed": "bus_acquisition",
    "VBus command send failed": "command_send",
    "VBus parameter response failed": "parameter_response",
    "VBus write outcome uncertain": "write_outcome_uncertain",
    "VBus response timed out": "timeout",
    "VBus connection closed": "connection_closed",
    "VBus release not confirmed": "release_unconfirmed",
    "Controller telemetry is unavailable": "telemetry_unavailable",
    "Unrecognized controller parameter layout": "unsupported_layout",
    "Relay options changed; rediscover first": "options_changed",
    "Unrecognized BX Plus parameter layout": "unsupported_layout",
    "Unrecognized BX Plus relay parameter layout": "unsupported_layout",
    "Controller changeset is unknown": "unknown_changeset",
    "Controller changeset changed; rediscover first": "changeset_changed",
    "Controller changeset changed before write": "changeset_changed",
    "Controller changeset changed during write": "changeset_changed",
    "Controller changeset changed during discovery": "changeset_changed",
    "Relay parameter is no longer supported": "unsupported_parameter",
    "Controller refused parameter read": "read_refused",
    "Unsupported current relay mode": "unsupported_mode",
    "Unsupported relay mode": "unsupported_mode",
    "VBus write acknowledgement differs": "acknowledgement_mismatch",
    "VBus write readback differs": "readback_mismatch",
    "Relay capability or controller identity changed": "identity_changed",
}


def safe_version(value):
    """Restrict gateway metadata to numeric firmware versions."""
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,5}(\.[0-9]{1,5}){0,4}", value):
        return value
    return None


async def async_get_config_entry_diagnostics(hass, entry):
    """Return a privacy-safe native diagnostics download for this config entry."""
    runtime = entry.runtime_data
    telemetry = runtime.coordinator
    relay = runtime.relay_coordinator
    api = telemetry.api
    configured = bool(entry.options.get(CONF_VBUS_PASSWORD))
    inventory = device_inventory(telemetry.data)
    profile, controller = find_profile(inventory)
    discovery = relay.data if relay else None
    current = bool(
        relay
        and relay.last_update_success
        and telemetry.last_update_success
        and controller
    )
    if not configured:
        status = "not_configured"
    elif not api.supports_parameter_access:
        status = "unsupported_gateway"
    elif not telemetry.last_update_success:
        status = "telemetry_unavailable"
    elif not controller:
        status = "unsupported_or_ambiguous_controller"
    elif not current or discovery is None:
        status = "unavailable"
    elif not discovery.capabilities:
        status = "no_supported_relays"
    else:
        status = "supported"

    devices = []
    for device, keys in sorted(
        inventory.items(), key=lambda item: (item[0].channel, item[0].address)
    ):
        group = (
            next((g for g in profile.groups if g.address == device.address), None)
            if profile
            else None
        )
        devices.append(
            {
                "channel": f"{device.channel:02X}",
                "source_address": f"0x{device.address:04X}",
                "telemetry_fields": len(keys),
                "profile_group": group.key if group else None,
                "status": (
                    discovery.group_status.get(group.key, "not_confirmed")
                    if current and discovery and group
                    else "observed_only"
                    if telemetry.last_update_success
                    else "stale"
                ),
            }
        )

    cached = (
        {cap.key: cap for cap in discovery.capabilities.values()} if discovery else {}
    )
    relays = {}
    if profile:
        for group in profile.groups:
            for number in range(1, len(group.indices) + 1):
                key = (
                    f"R{number}"
                    if group.key == "controller"
                    else f"{group.key}_R{number}"
                )
                cap = cached.get(key)
                valid = bool(current and cap and cap.matches(inventory))
                relays[key] = {
                    "parameter": group.parameter(number, "Handbetrieb"),
                    "device_address": f"0x{group.address:04X}",
                    "status": "supported"
                    if valid
                    else (
                        discovery.group_status.get(group.key, "not_discovered")
                        if current
                        and discovery
                        and discovery.group_status.get(group.key) != "supported"
                        else "not_discovered"
                        if current
                        else "not_confirmed"
                    ),
                    "writable_options": list(cap.options) if valid else [],
                    "mode": OBSERVED_MODES.get(cap.value) if valid else None,
                    "last_observed_mode": OBSERVED_MODES.get(cap.value)
                    if cap
                    else None,
                    "mode_value": cap.value if valid else None,
                    "speed_control": cap.speed_control if valid else None,
                    "changeset": f"{cap.changeset & 0xFFFFFFFF:08X}" if valid else None,
                    "parameter_index": cap.index if valid else None,
                }

    module_flags = {}
    if discovery:
        flags_current = bool(current and discovery.controller == controller)
        for group, flags in discovery.module_flags.items():
            module_flags[group] = {
                name: {
                    "status": observation.status if flags_current else "stale",
                    "parameter_index": observation.index if flags_current else None,
                    "raw_value": observation.value if flags_current else None,
                }
                for name, observation in flags.items()
            }

    last_error = None
    if relay and not relay.last_update_success:
        last_error = ERROR_CODES.get(str(relay.last_exception), "parameter_error")
    return {
        "gateway": {
            "type": api.product
            if api.product in ("km2", "dl2plus", "dl2", "dl3")
            else None,
            "firmware": safe_version((api.product_details or {}).get("version")),
        },
        "telemetry": {
            "available": telemetry.last_update_success,
            "cached_sensor_count": len(telemetry.data or {}),
            "last_error": None if telemetry.last_update_success else "telemetry_error",
        },
        "parameter_access": {
            "configured": configured,
            "status": status,
            "observed_controller_address": f"0x{controller.address:04X}"
            if controller
            else None,
            "reference_profile": profile.name if profile else None,
            "devices": devices,
            "last_error": last_error,
            "relays": relays,
            "module_flags": module_flags,
            "discovery_observed_at": discovery.observed_at.isoformat()
            if discovery and discovery.observed_at
            else None,
            "discovery_changeset": f"{discovery.changeset & 0xFFFFFFFF:08X}"
            if discovery and discovery.changeset is not None
            else None,
        },
    }
