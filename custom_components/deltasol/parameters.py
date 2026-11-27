"""Positive, profile-driven VBus relay capabilities.

Reference: RESOL BX Plus 2.08 RPT package SHA256
1bcc59cac3d4176c68de255fced3b95840bada06db33b0bdae0cddb007a9f079.
Reference indices are a layout fingerprint, never blind write targets.
EM subaddresses use 0x6650 + SA (resol-vbus JSON live-data server).
The BX Plus manual specifies five local relays and two EM slots.
"""

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from threading import RLock
from time import monotonic, sleep

from .configuration import (
    BX_PLUS_DIFFERENTIAL_SETTINGS,
    BX_PLUS_HEATING_SETTINGS,
    BX_PLUS_HQM_SETTINGS,
    BX_PLUS_MIXER_SETTINGS,
    BX_PLUS_PUMP_SETTINGS,
    BX_PLUS_SETTINGS,
    ConfigurationCapability,
    Setting,
    encode_action,
)
from .vbus import (
    ERROR_CODES,
    ParameterObservation,
    VBusError,
    VBusTransientError,
    open_vbus,
)

# Initial conservative reconnect guard; tune against physical KM2 evidence.
SESSION_GAP_SECONDS = 1.5
RELAY_RETRY_DELAYS = (2, 5)
SESSION_FAILURE_GAPS = (2, 5, 10, 20, 30)

MODE_VALUES = {"Auto": 2, "Off": 0, "On": 4, "Min": 1, "Max": 3}
OBSERVED_MODES = {value: name for name, value in MODE_VALUES.items()}


@dataclass(frozen=True)
class RelayGroup:
    """One controller-owned relay group and its physical device."""

    key: str
    address: int
    prefix: str
    indices: tuple[int, ...]
    flags: tuple[tuple[str, int], ...] = ()

    def parameter(self, number, suffix):
        return f"{self.prefix}_R{number}_{suffix}"


@dataclass(frozen=True)
class ControllerProfile:
    """Verified parameter semantics for a controller family."""

    name: str
    address: int
    schema_index: int
    groups: tuple[RelayGroup, ...]
    module_presence_lookup_optional_changeset: int | None = None
    configuration_changeset: int | None = None
    settings: tuple[Setting, ...] = ()

    def settings_for(self, changeset):
        return self.settings if changeset == self.configuration_changeset else ()


PROFILES = (
    ControllerProfile(
        "BX Plus 2.08",
        0x7112,
        86,
        (
            RelayGroup(
                "controller", 0x7112, "Relais_Regler", (1977, 1991, 2005, 2019, 2033)
            ),
            RelayGroup(
                "M1",
                0x6651,
                "Relais_Modul1",
                (2173, 2187, 2201, 2215, 2229),
                (("Modul1_Aktiviert", 191), ("Modul1_Vorhanden", 193)),
            ),
            RelayGroup(
                "M2",
                0x6652,
                "Relais_Modul2",
                (2243, 2257, 2271, 2285, 2299),
                (("Modul2_Aktiviert", 197), ("Modul2_Vorhanden", 199)),
            ),
        ),
        # Full physical scan on 2734DABC: all five activation flags read,
        # all volatile presence flags unresolved. Require positive observed
        # physical module identity + exact activation and relay mappings.
        module_presence_lookup_optional_changeset=0x2734DABC,
        configuration_changeset=0x2734DABC,
        settings=(
            *BX_PLUS_SETTINGS,
            *BX_PLUS_MIXER_SETTINGS,
            *BX_PLUS_HQM_SETTINGS,
            *BX_PLUS_HEATING_SETTINGS,
            *BX_PLUS_PUMP_SETTINGS,
            *BX_PLUS_DIFFERENTIAL_SETTINGS,
        ),
    ),
)


@dataclass(frozen=True)
class DeviceIdentity:
    """Identity from the existing telemetry, not the user-facing device name."""

    channel: int
    address: int
    source: str
    serial: str


def device_inventory(telemetry):
    """Group the existing telemetry by observed packet source and device identity."""
    inventory = {}
    for key, endpoint in (telemetry or {}).items():
        parts = key.split("__", 1)[0].split("_")
        if len(parts) not in (4, 5) or len(parts[0]) != 2 or len(parts[2]) != 4:
            continue
        try:
            channel, address = int(parts[0], 16), int(parts[2], 16)
        except ValueError:
            continue
        serial = (endpoint.product_details or {}).get("serial")
        if not serial or not endpoint.bus_src:
            continue
        identity = DeviceIdentity(channel, address, endpoint.bus_src, serial)
        inventory.setdefault(identity, []).append(key)
    return inventory


def find_device(inventory, address):
    """Require exactly one observed identity on the supported local bus."""
    matches = [device for device in inventory if device.address == address]
    return matches[0] if len(matches) == 1 and matches[0].channel == 0 else None


def find_profile(inventory):
    """Do not write if the observed controller identity is ambiguous."""
    matches = [
        (profile, device)
        for profile in PROFILES
        if (device := find_device(inventory, profile.address))
    ]
    return matches[0] if len(matches) == 1 else (None, None)


@dataclass(frozen=True)
class RelayCapability:
    """One positively discovered output with its current permitted modes."""

    group: RelayGroup
    number: int
    index: int
    changeset: int
    controller: DeviceIdentity
    device: DeviceIdentity
    speed_control: bool
    value: int
    registration_source: str = "controller"

    @property
    def name(self):
        return self.group.parameter(self.number, "Handbetrieb")

    @property
    def key(self):
        prefix = "" if self.group.key == "controller" else f"{self.group.key}_"
        return f"{prefix}R{self.number}"

    @property
    def unique_id(self):
        return f"{self.controller.serial}_{self.controller.address:04X}_{self.key}_mode"

    @property
    def options(self):
        return (
            ("Auto", "Off", "Min", "Max")
            if self.speed_control
            else ("Auto", "Off", "On")
        )

    @property
    def current_option(self):
        mode = OBSERVED_MODES.get(self.value)
        return mode if mode in self.options else None

    def matches(self, inventory):
        return (
            find_device(inventory, self.controller.address) == self.controller
            and find_device(inventory, self.device.address) == self.device
        )


@dataclass(frozen=True)
class RelayDiscovery:
    """One coordinator snapshot: capabilities and group discovery results."""

    profile: ControllerProfile | None
    capabilities: dict[str, RelayCapability]
    group_status: dict[str, str]
    module_flags: dict[str, dict[str, ParameterObservation]] = field(
        default_factory=dict
    )
    controller: DeviceIdentity | None = None
    changeset: int | None = None
    observed_at: datetime | None = None
    configuration: dict[str, ConfigurationCapability] = field(default_factory=dict)


def _checked_parameters(session, profile, changeset, expected):
    """Resolve dynamically and verify the reference layout."""
    found = session.read_parameter_observations(
        profile.address, expected, changeset, strict=True, expected=expected
    )
    if any(
        result.status == "layout_mismatch"
        or (result.index is not None and result.index != expected[name])
        for name, result in found.items()
    ):
        raise VBusError("Unrecognized controller parameter layout")
    return {
        name: result.value for name, result in found.items() if result.status == "read"
    }


def _read_settings(
    session,
    profile,
    changeset,
    settings,
    controller,
    inventory,
    requested=None,
    relay_capabilities=None,
):
    """Re-read exact hashes and conditional dependencies, never probe writes."""
    # Reuse relay registration evidence during ordinary polling. Before a
    # direct configuration action, independently re-read the specific relay
    # (including the existing EM registration exception) in the same session.
    eligible = []
    for setting in settings:
        if setting.owner_relay is not None:
            group = next(
                (g for g in profile.groups if g.address == setting.owner_address),
                None,
            )
            device = find_device(inventory, setting.owner_address)
            if group is None or device is None or device.serial != controller.serial:
                continue
            if relay_capabilities is None:
                _, relays = _read_group(
                    session,
                    profile,
                    changeset,
                    group,
                    controller,
                    device,
                    (setting.owner_relay,),
                )
            else:
                relays = relay_capabilities
            if not any(
                cap.group == group
                and cap.number == setting.owner_relay
                and cap.device == device
                and cap.speed_control
                and cap.current_option == "Auto"
                for cap in relays.values()
            ):
                continue
        eligible.append(setting)
    settings = eligible
    expected = {}
    for setting in settings:
        for name, index in setting.expected.items():
            if name in expected and expected[name] != index:
                raise VBusError("Conflicting configuration parameter layout")
            expected[name] = index
    if not expected:
        return {}
    observed = session.read_parameter_observations(
        profile.address,
        expected,
        changeset,
        expected=expected,
        schema=profile.schema_index,
    )
    values = {
        name: result.value
        for name, result in observed.items()
        if result.status == "read" and result.index == expected[name]
    }
    result = {}
    for setting in settings:
        device = find_device(inventory, setting.owner_address or profile.address)
        if (
            device is None
            or device.serial != controller.serial
            or not setting.accepts(values)
        ):
            continue
        if requested is not None and not setting.accepts(values, requested):
            raise VBusError("Configuration conditional constraint rejected")
        capability = ConfigurationCapability(
            setting, controller, device, changeset, values[setting.name]
        )
        result[capability.unique_id] = capability
    return result


def _read_group(
    session, profile, changeset, group, controller, device, numbers, module_flags=None
):
    """Read presence and registration before any module relay parameters."""
    registration_source = "controller"
    if group.flags:
        observed = session.read_parameter_observations(
            profile.address, dict(group.flags), changeset, expected=dict(group.flags)
        )
        evidence = {}
        flags = {}
        for name, expected_index in group.flags:
            result = observed[name]
            if result.index is not None and result.index != expected_index:
                result = replace(result, value=None, status="layout_mismatch")
            elif result.status == "read":
                if result.index == expected_index and result.value in (0, 1):
                    flags[name] = result.value
                else:
                    result = replace(result, value=None, status="invalid_value")
            evidence[name] = result
        if module_flags is not None:
            module_flags[group.key] = evidence
        if any(v == 0 for v in flags.values()):
            return "not_registered_or_present", {}
        if len(flags) == len(group.flags):
            registration_source = "controller_flags"
        else:
            # This is an exact-profile exception to an unsupported lookup,
            # never to explicit negative, refused, invalid or mismatched data.
            activation, presence = (name for name, _ in group.flags)
            if (
                changeset != profile.module_presence_lookup_optional_changeset
                or flags.get(activation) != 1
                or evidence[presence].status != "not_resolved"
                or evidence[presence].index is not None
                or device.address != group.address
                or device.channel != controller.channel
                or device.serial != controller.serial
            ):
                return "registration_unknown", {}
            registration_source = "activation_and_observed_module"

    expected = {}
    for number in numbers:
        index = group.indices[number - 1]
        expected[group.parameter(number, "Handbetrieb")] = index
        expected[group.parameter(number, "ODrehzahl")] = index - 4
    values = _checked_parameters(session, profile, changeset, expected)
    capabilities = {}
    for number in numbers:
        name = group.parameter(number, "Handbetrieb")
        value = values.get(name)
        speed = values.get(group.parameter(number, "ODrehzahl"))
        if value not in OBSERVED_MODES or speed not in (0, 1):
            continue
        cap = RelayCapability(
            group,
            number,
            expected[name],
            changeset,
            controller,
            device,
            bool(speed),
            value,
            registration_source,
        )
        capabilities[cap.unique_id] = cap
    return ("supported" if capabilities else "no_supported_relays"), capabilities


async def run_parameter_job(hass, function, *args):
    """Keep ownership until a cancelled executor operation has drained."""
    job = asyncio.ensure_future(hass.async_add_executor_job(function, *args))
    try:
        return await asyncio.shield(job)
    except asyncio.CancelledError:
        # Drain even after cancellation; the original cancellation remains
        # the caller's outcome, not any late worker response.
        drained = asyncio.gather(job, return_exceptions=True)
        while not drained.done():
            try:
                await asyncio.shield(drained)
            except asyncio.CancelledError:
                continue
        raise


async def refresh_relay_modes(
    telemetry, lock=None, *, guard=None, initial=False, configuration_hints=None
):
    """Retry reads, releasing async ownership between attempts."""

    async def read():
        if guard is not None:
            guard()
        arguments = (telemetry.data,)
        if configuration_hints is not None:
            arguments += (configuration_hints,)
        return await run_parameter_job(
            telemetry.hass, telemetry.api.fetch_relay_modes, *arguments
        )

    for delay in (None,) if initial else (*RELAY_RETRY_DELAYS, None):
        try:
            if not telemetry.last_update_success:
                raise VBusTransientError(
                    "Controller telemetry is unavailable", "telemetry"
                )
            if lock is None:
                return await read()
            async with lock:
                return await read()
        except VBusTransientError:
            if delay is None:
                raise
            # The shared session is already closed. A fresh attempt uses the
            # latest telemetry and revalidates identity, layout and registration.
            await asyncio.sleep(delay)


class RelayParameters:
    """One serialized owner for all relay groups on the controller."""

    def __init__(self, host, password):
        self.host = host
        self.password = password
        self._lock = RLock()
        self._next_session = 0.0
        self._failure_streak = 0
        self._health = {
            "sessions": 0,
            "successful_sessions": 0,
            "failed_sessions": 0,
            "release_attempts": 0,
            "release_failures": 0,
            "other_live_packets": 0,
            "failure_counts": {},
            "phase_timings": {},
            "last_failure": None,
        }

    def transport_health(self):
        """Return an immutable snapshot without blocking the event loop."""
        return self._health

    def cooldown_remaining(self):
        return max(0, self._next_session - monotonic())

    def _cooldown(self):
        if not self._failure_streak:
            return SESSION_GAP_SECONDS
        return SESSION_FAILURE_GAPS[
            min(self._failure_streak, len(SESSION_FAILURE_GAPS)) - 1
        ]

    def _record_session(self, session, error, elapsed, connect_seconds):
        previous = self._health
        phases = {"session": elapsed, "connect_and_auth": connect_seconds}
        real_session = session is not None and isinstance(session.phase_seconds, dict)
        if real_session:
            phases.update(session.phase_seconds)
        timings = dict(previous["phase_timings"])
        for phase, seconds in phases.items():
            old = timings.get(phase, {})
            timings[phase] = {
                "count": old.get("count", 0) + 1,
                "last_seconds": round(seconds, 3),
                "max_seconds": max(old.get("max_seconds", 0), round(seconds, 3)),
            }
        counts = dict(previous["failure_counts"])
        failure = previous["last_failure"]
        if error is not None:
            code = ERROR_CODES.get(str(error), "parameter_error")
            counts[code] = counts.get(code, 0) + 1
            failure = {
                "error": code,
                "phase": getattr(error, "phase", None),
                "observed_at": datetime.now(UTC).isoformat(),
            }
        self._health = {
            **previous,
            "sessions": previous["sessions"] + 1,
            "successful_sessions": previous["successful_sessions"] + (error is None),
            "failed_sessions": previous["failed_sessions"] + (error is not None),
            "release_attempts": previous["release_attempts"]
            + (session.release_attempts if real_session else 0),
            "release_failures": previous["release_failures"]
            + (int(session.release_failed) if real_session else 0),
            "other_live_packets": previous["other_live_packets"]
            + (session.other_live_packets if real_session else 0),
            "failure_counts": counts,
            "phase_timings": timings,
            "last_failure": failure,
            "consecutive_transport_failures": self._failure_streak,
            "cooldown_seconds": self._cooldown(),
        }

    @contextmanager
    def _session(self, address):
        """Serialize polling and writes, including gateway recovery time."""
        with self._lock:
            delay = self._next_session - monotonic()
            if delay > 0:
                sleep(delay)
            started = monotonic()
            connect_seconds = None
            session = None
            failure = None
            try:
                with open_vbus(self.host, self.password) as session:
                    connect_seconds = monotonic() - started
                    with session.bus(address):
                        yield session
            except BaseException as error:
                failure = error
                if (
                    isinstance(error, VBusTransientError)
                    or str(error) == "VBus write outcome uncertain"
                    or (session is not None and session.release_failed is True)
                ):
                    self._failure_streak += 1
                raise
            else:
                self._failure_streak = 0
            finally:
                elapsed = monotonic() - started
                self._next_session = monotonic() + self._cooldown()
                self._record_session(
                    session,
                    failure,
                    elapsed,
                    elapsed if connect_seconds is None else connect_seconds,
                )

    def discover(self, telemetry, configuration_hints=None):
        inventory = device_inventory(telemetry)
        profile, controller = find_profile(inventory)
        if profile is None:
            return RelayDiscovery(None, {}, {})
        capabilities = {}
        status = {}
        module_flags = {}
        with self._session(profile.address) as session:
            changeset = session.read(profile.address, 0)
            if not changeset:
                raise VBusError("Controller changeset is unknown")
            if "Schema" not in _checked_parameters(
                session, profile, changeset, {"Schema": profile.schema_index}
            ):
                raise VBusError("Unrecognized controller parameter layout")
            for group in profile.groups:
                device = find_device(inventory, group.address)
                if device is None or device.serial != controller.serial:
                    status[group.key] = "not_observed_or_ambiguous"
                    continue
                status[group.key], relays = _read_group(
                    session,
                    profile,
                    changeset,
                    group,
                    controller,
                    device,
                    range(1, len(group.indices) + 1),
                    module_flags,
                )
                capabilities.update(relays)
            # Historical inventory is only a scheduling hint for installed
            # families. _read_settings independently verifies live selectors,
            # layout, current values and identity before exposing controls.
            settings = tuple(
                setting
                for setting in profile.settings_for(changeset)
                if setting.installed_by is None
                or (
                    configuration_hints is not None
                    and (
                        observation := configuration_hints.get(
                            setting.installed_by.name
                        )
                    )
                    is not None
                    and observation.status == "read"
                    and observation.index == setting.installed_by.index
                    and setting.installed_by.matches(
                        {setting.installed_by.name: observation.value}
                    )
                )
            )
            configuration = _read_settings(
                session,
                profile,
                changeset,
                settings,
                controller,
                inventory,
                relay_capabilities=capabilities,
            )
        return RelayDiscovery(
            profile,
            capabilities,
            status,
            module_flags,
            controller,
            changeset,
            datetime.now(UTC),
            configuration,
        )

    def read_configuration_batch(self, telemetry, controller, names, changeset):
        """One bounded read batch using the existing session and identity gates."""
        profile, actual = find_profile(device_inventory(telemetry))
        if profile is None or actual != controller:
            raise VBusError("Controller identity changed during discovery")
        with self._session(profile.address) as session:
            return session.read_parameter_observations(
                profile.address,
                names,
                changeset,
                expected=names,
                schema=profile.schema_index,
            )

    def set_configuration(self, telemetry, capability, value):
        """Same serialized owner; save once and confirm via a new VBus session."""
        inventory = device_inventory(telemetry)
        profile, controller = find_profile(inventory)
        if (
            profile is None
            or controller != capability.controller
            or capability.setting not in profile.settings_for(capability.changeset)
            or find_device(inventory, capability.device.address) != capability.device
        ):
            raise VBusError("Configuration capability or controller identity changed")
        setting = capability.setting
        wanted = encode_action(setting, value)
        # Hold the existing owner lock across both sessions. Never retry a save
        # when acknowledgement, release or verification is uncertain.
        with self._lock:
            with self._session(profile.address) as session:
                current = _read_settings(
                    session,
                    profile,
                    capability.changeset,
                    (setting,),
                    controller,
                    inventory,
                    requested=wanted,
                ).get(capability.unique_id)
                if current is None or current.device != capability.device:
                    raise VBusError("Configuration parameter is no longer supported")
                if current.raw == wanted:
                    return current
                session.write_verified(
                    profile.address,
                    setting.index,
                    wanted,
                    capability.changeset,
                    save=setting.save,
                )
            with self._session(profile.address) as session:
                verified = _read_settings(
                    session,
                    profile,
                    capability.changeset,
                    (setting,),
                    controller,
                    inventory,
                ).get(capability.unique_id)
                if (
                    verified is None
                    or verified.device != capability.device
                    or verified.raw != wanted
                ):
                    raise VBusError("Configuration reconnect readback differs")
        return verified

    def set_mode(self, telemetry, capability, option):
        inventory = device_inventory(telemetry)
        profile, controller = find_profile(inventory)
        if (
            profile is None
            or controller != capability.controller
            or capability.group not in profile.groups
            or not capability.matches(inventory)
        ):
            raise VBusError("Relay capability or controller identity changed")
        if option not in capability.options:
            raise VBusError("Unsupported relay mode")
        with self._session(profile.address) as session:
            changeset = session.read(profile.address, 0)
            if changeset != capability.changeset:
                raise VBusError("Controller changeset changed; rediscover first")
            if "Schema" not in _checked_parameters(
                session, profile, changeset, {"Schema": profile.schema_index}
            ):
                raise VBusError("Unrecognized controller parameter layout")
            _, relays = _read_group(
                session,
                profile,
                changeset,
                capability.group,
                controller,
                capability.device,
                (capability.number,),
            )
            current = relays.get(capability.unique_id)
            if current is None or current.index != capability.index:
                raise VBusError("Relay parameter is no longer supported")
            if (
                current.registration_source != capability.registration_source
                or current.options != capability.options
                or option not in current.options
            ):
                raise VBusError("Relay options changed; rediscover first")
            wanted = MODE_VALUES[option]
            if current.value != wanted:
                value = session.write_verified(
                    profile.address, current.index, wanted, changeset
                )
                current = replace(current, value=value)
        return current
