"""Native Home Assistant diagnostics; export cached state without I/O."""

import re

from .const import CONF_VBUS_PASSWORD
from .parameters import (
    MODE_VALUES,
    OBSERVED_MODES,
    RELAY_LAYOUT,
    controller_identity,
)

# Only fixed, integration-defined messages can leave the integration. Never
# export arbitrary exception text, as HTTP failures may contain credentials.
ERROR_CODES = {
    "VBus gateway rejected connection or credentials": "authentication",
    "Invalid VBus password": "invalid_credentials",
    "VBus connection failed or timed out": "connection",
    "VBus response timed out": "timeout",
    "VBus connection closed": "connection_closed",
    "VBus release not confirmed": "release_unconfirmed",
    "Controller telemetry is unavailable": "telemetry_unavailable",
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
    observed = controller_identity(telemetry.data)
    current = bool(
        relay
        and relay.last_update_success
        and telemetry.last_update_success
        and observed
    )
    if not configured:
        status = "not_configured"
    elif not api.supports_parameter_access:
        status = "unsupported_gateway"
    elif not telemetry.last_update_success:
        status = "telemetry_unavailable"
    elif not observed:
        status = "unsupported_or_ambiguous_controller"
    elif relay is None or not relay.last_update_success:
        status = "unavailable"
    elif not relay.data:
        status = "no_supported_relays"
    else:
        status = "supported"

    values = (relay.data or {}).values() if relay else ()
    cached = {cap.number: cap for cap in values}
    relays = {}
    for name, (number, _) in RELAY_LAYOUT.items():
        cap = cached.get(number)
        valid = bool(current and cap and observed == (cap.source, cap.serial))
        relays[f"R{number}"] = {
            "parameter": name,
            "status": (
                "supported"
                if valid
                else "not_discovered"
                if current
                else "not_confirmed"
            ),
            "writable_options": list(MODE_VALUES) if valid else [],
            "mode": OBSERVED_MODES.get(cap.value) if valid else None,
            "last_observed_mode": OBSERVED_MODES.get(cap.value) if cap else None,
            "changeset": f"{cap.changeset & 0xFFFFFFFF:08X}" if valid else None,
            "parameter_index": cap.index if valid else None,
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
            "observed_controller_address": "0x7112" if observed else None,
            "reference_profile": "BX Plus 2.08" if observed else None,
            "last_error": last_error,
            "relays": relays,
        },
    }
