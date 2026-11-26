"""Native Home Assistant diagnostics; export cached state without I/O."""

import re

from .const import CONF_VBUS_PASSWORD
from .discovery import compare_captures
from .parameters import (
    OBSERVED_MODES,
    device_inventory,
    find_device,
    find_profile,
)

from .vbus import ERROR_CODES


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
                    "registration_source": cap.registration_source if valid else None,
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
    capture = runtime.capture
    capture_current = bool(
        telemetry.last_update_success
        and capture
        and capture.controller == controller
        and relay
        and relay.last_update_success
        and discovery
        and discovery.controller == controller
        and discovery.changeset == capture.changeset
    )
    configuration = (
        capture.export(capture_current) if capture else {"status": "not_started"}
    )
    configuration["checkpoint_error"] = runtime.checkpoint_error
    if capture and configuration.get("error"):
        configuration["error"] = ERROR_CODES.get(
            configuration["error"], "parameter_error"
        )
    if capture and capture_current:
        configuration["comparison"] = compare_captures(
            runtime.previous_capture, capture
        )
    settings = {}
    if discovery:
        for capability in discovery.configuration.values():
            valid = (
                current
                and capability.controller == controller
                and find_device(inventory, capability.device.address)
                == capability.device
            )
            settings[capability.setting.name] = {
                "status": "supported" if valid else "stale",
                "device_address": f"0x{capability.device.address:04X}",
                "kind": capability.setting.kind,
                "write_access": "profile_verified" if valid else "not_confirmed",
                "storage_action": "save" if capability.setting.save else "volatile",
                "value": capability.value if valid else None,
            }
    # Do not present historical inventory values as live control readback.
    # The capture is a snapshot; the existing coordinator owns current controls.
    for name, item in configuration.get("parameters", {}).items():
        if item["status"] != "read":
            continue
        control = settings.get(name)
        if control and control["status"] == "supported":
            item["write_access"] = "profile_verified"
            item["applicability"] = "confirmed"
            item["configuration_device_address"] = control["device_address"]
            item["storage_action"] = control["storage_action"]
        else:
            item["write_access"] = "not_verified"
            item["write_access_reason"] = "no_verified_live_configuration_contract"
    return {
        "configuration_discovery": configuration,
        "configuration_controls": settings,
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
