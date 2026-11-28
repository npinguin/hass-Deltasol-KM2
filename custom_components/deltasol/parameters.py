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

from .catalog import CatalogError
from .configuration import (
    BX_PLUS_ADDITIONAL_OPERATING_SETTINGS,
    BX_PLUS_DIFFERENTIAL_SETTINGS,
    BX_PLUS_HEATING_COMFORT_SETTINGS,
    BX_PLUS_HEATING_OPERATING_SETTINGS,
    BX_PLUS_HEATING_SETTINGS,
    BX_PLUS_HQM_SETTINGS,
    BX_PLUS_LOADING_SETTINGS,
    BX_PLUS_MIXER_SETTINGS,
    BX_PLUS_PUMP_SETTINGS,
    BX_PLUS_SETTINGS,
    BX_PLUS_SOLAR_COLLECTOR_SETTINGS,
    BX_PLUS_SOLAR_LOADING_LOGIC_SETTINGS,
    BX_PLUS_SOLAR_STORE_SETTINGS,
    BX_PLUS_SOLID_FUEL_MIXER_SETTINGS,
    BX_PLUS_THERMOSTAT_SETTINGS,
    BX_PLUS_TIMING_SETTINGS,
    BX_PLUS_TRANSFER_SETTINGS,
    ConfigurationCapability,
    Setting,
    bind_setting,
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

    def settings_for(self, changeset, catalog=None):
        if changeset != self.configuration_changeset:
            return ()
        if catalog is None:
            # Existing exact firmware contracts remain usable before the
            # catalogue is acquired; they are independently source-verified.
            return self.settings
        if not catalog.matches(self.address, changeset):
            return ()
        try:
            definitions = catalog.by_name
        except CatalogError:
            return ()
        bound = []
        for setting in self.settings:
            try:
                bound.append(bind_setting(setting, definitions))
            except CatalogError:
                # A contradictory definition cannot authorize this setting.
                # Unrelated, independently validated contracts still work.
                continue
        return tuple(bound)


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
            *BX_PLUS_HEATING_COMFORT_SETTINGS,
            *BX_PLUS_HEATING_OPERATING_SETTINGS,
            *BX_PLUS_PUMP_SETTINGS,
            *BX_PLUS_DIFFERENTIAL_SETTINGS,
            *BX_PLUS_TRANSFER_SETTINGS,
            *BX_PLUS_SOLID_FUEL_MIXER_SETTINGS,
            *BX_PLUS_ADDITIONAL_OPERATING_SETTINGS,
            *BX_PLUS_SOLAR_STORE_SETTINGS,
            *BX_PLUS_SOLAR_COLLECTOR_SETTINGS,
            *BX_PLUS_SOLAR_LOADING_LOGIC_SETTINGS,
            *BX_PLUS_THERMOSTAT_SETTINGS,
            *BX_PLUS_TIMING_SETTINGS,
            *BX_PLUS_LOADING_SETTINGS,
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
    # Latest bounded selector observations are scheduling hints, not write
    # authorization and not changes to the historical Discovery capture.
    selector_hints: dict[str, ParameterObservation] = field(default_factory=dict)
    selector_cursor: int = 0


SELECTORS_PER_REFRESH = 4


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
    ambiguous = set()
    rejected = set()
    for setting in settings:
        if setting.optional_role:
            dependency = setting.optional_role
            role = observed.get(dependency.name)
            if role is None or not (
                (role.status == "not_resolved" and role.index is None)
                or (
                    role.status == "read"
                    and role.index == dependency.index
                    and dependency.matches({dependency.name: role.value})
                )
            ):
                continue
        device = find_device(inventory, setting.owner_address or profile.address)
        if (
            device is None
            or device.serial != controller.serial
            or not setting.accepts(values)
        ):
            continue
        capability = ConfigurationCapability(
            setting, controller, device, changeset, values[setting.name]
        )
        uid = capability.unique_id
        if uid in result:
            ambiguous.add(uid)
        result[uid] = capability
        if requested is not None and not setting.accepts(values, requested):
            rejected.add(uid)
    if rejected.difference(ambiguous):
        raise VBusError("Configuration conditional constraint rejected")
    return {uid: cap for uid, cap in result.items() if uid not in ambiguous}


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
    telemetry,
    lock=None,
    *,
    guard=None,
    initial=False,
    configuration_hints=None,
    catalog=None,
    previous=None,
):
    """Retry reads, releasing async ownership between attempts."""

    async def read():
        if guard is not None:
            guard()
        arguments = (telemetry.data,)
        if configuration_hints is not None or catalog is not None:
            arguments += (configuration_hints,)
        if catalog is not None:
            arguments += (catalog, previous)
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
            "other_release_offers": 0,
            "last_release_confirmation": None,
            "failure_counts": {},
            "phase_timings": {},
            "last_failure": None,
            "last_acquisition": None,
            "last_failed_acquisition": None,
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
        acquisition = (
            session.acquisition_stats
            if real_session and isinstance(session.acquisition_stats, dict)
            else None
        )
        self._health = {
            **previous,
            "last_acquisition": acquisition or previous["last_acquisition"],
            "last_failed_acquisition": (
                acquisition
                if acquisition is not None and not acquisition["accepted_offers"]
                else previous["last_failed_acquisition"]
            ),
            "sessions": previous["sessions"] + 1,
            "successful_sessions": previous["successful_sessions"] + (error is None),
            "failed_sessions": previous["failed_sessions"] + (error is not None),
            "release_attempts": previous["release_attempts"]
            + (session.release_attempts if real_session else 0),
            "release_failures": previous["release_failures"]
            + (int(session.release_failed) if real_session else 0),
            "other_live_packets": previous["other_live_packets"]
            + (session.other_live_packets if real_session else 0),
            "other_release_offers": previous["other_release_offers"]
            + (session.other_release_offers if real_session else 0),
            "last_release_confirmation": (
                session.release_confirmation
                if real_session and "release" in phases
                else previous["last_release_confirmation"]
            ),
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

    def discover(
        self, telemetry, configuration_hints=None, catalog=None, previous=None
    ):
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
            contracts = profile.settings_for(changeset, catalog)
            selector_hints = {}
            cursor = 0
            # A completed capture is historical. Rotate a small number of
            # exact, authorized selectors through normal polling so a native
            # installer change is discovered without a new 12,812-item scan.
            if catalog is not None:
                selectors = sorted(
                    {
                        (setting.installed_by.name, setting.installed_by.index)
                        for setting in contracts
                        if setting.installed_by
                    },
                    key=lambda item: item[1],
                )
                if (
                    previous is not None
                    and previous.controller == controller
                    and previous.changeset == changeset
                ):
                    selector_hints.update(previous.selector_hints)
                    cursor = previous.selector_cursor
                if selectors:
                    cursor %= len(selectors)
                    count = min(SELECTORS_PER_REFRESH, len(selectors))
                    batch = dict(
                        selectors[(cursor + i) % len(selectors)] for i in range(count)
                    )
                    selector_hints.update(
                        session.read_parameter_observations(
                            profile.address,
                            batch,
                            changeset,
                            expected=batch,
                            schema=profile.schema_index,
                        )
                    )
                    cursor = (cursor + count) % len(selectors)
            configuration_hints = {
                **(configuration_hints or {}),
                **selector_hints,
            }
            # Historical/live hints never authorize writes. _read_settings
            # independently checks live conditions and identity.
            settings = tuple(
                setting
                for setting in contracts
                if setting.installed_by is None
                or (
                    (observation := configuration_hints.get(setting.installed_by.name))
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
            selector_hints,
            cursor,
        )

    def read_configuration_batch(
        self, telemetry, controller, names, changeset, packed=None
    ):
        """One bounded read batch using the existing session and identity gates."""
        profile, actual = find_profile(device_inventory(telemetry))
        if profile is None or actual != controller:
            raise VBusError("Controller identity changed during discovery")
        expected = dict(names)
        for flags in (packed or {}).values():
            for name, index in flags.items():
                if name in expected and expected[name] != index:
                    raise VBusError("Conflicting configuration parameter layout")
                expected[name] = index
        with self._session(profile.address) as session:
            observations = session.read_parameter_observations(
                profile.address,
                expected,
                changeset,
                expected=expected,
                schema=profile.schema_index,
                packed=packed,
            )
        return {name: observations[name] for name in names}

    def set_configuration(self, telemetry, capability, value, catalog=None):
        """Same serialized owner; save once and confirm via a new VBus session."""
        inventory = device_inventory(telemetry)
        profile, controller = find_profile(inventory)
        if (
            profile is None
            or controller != capability.controller
            or find_device(inventory, capability.device.address) != capability.device
        ):
            raise VBusError("Configuration capability or controller identity changed")
        contracts = profile.settings_for(capability.changeset, catalog)
        # An existing source-verified control may have been registered just
        # before initial catalogue acquisition. Use its newly bound contract
        # only if the authorization and every encoding constraint are identical.
        matches = tuple(
            candidate
            for candidate in contracts
            if candidate == capability.setting
            or (
                capability.setting.value_definition is None
                and replace(candidate, value_definition=None) == capability.setting
            )
        )
        if len(matches) != 1:
            raise VBusError("Configuration parameter is no longer supported")
        setting = matches[0]
        wanted = encode_action(setting, value)
        # A logical setting can have several firmware contracts (unit variant,
        # assignment evidence). Recheck competing live contracts, not just the
        # one in an older entity snapshot. First read selectors to bound work.
        alternatives = tuple(
            candidate for candidate in contracts if candidate.name == setting.name
        )

        def current_contract(session, requested=None):
            selectors = {
                candidate.installed_by.name: candidate.installed_by.index
                for candidate in alternatives
                if candidate.installed_by
            }
            observed = (
                session.read_parameter_observations(
                    profile.address,
                    selectors,
                    capability.changeset,
                    expected=selectors,
                    schema=profile.schema_index,
                )
                if selectors
                else {}
            )
            values = {
                name: row.value
                for name, row in observed.items()
                if row.status == "read" and row.index == selectors[name]
            }
            candidates = tuple(
                candidate
                for candidate in alternatives
                if candidate.installed_by is None
                or candidate.installed_by.matches(values)
            )
            return _read_settings(
                session,
                profile,
                capability.changeset,
                candidates,
                controller,
                inventory,
                requested=requested,
            ).get(capability.unique_id)

        # Hold the existing owner lock across both sessions. Never retry a save
        # when acknowledgement, release or verification is uncertain.
        with self._lock:
            with self._session(profile.address) as session:
                current = current_contract(session, wanted)
                if (
                    current is None
                    or current.device != capability.device
                    or current.setting != setting
                ):
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
                verified = current_contract(session)
                if (
                    verified is None
                    or verified.device != capability.device
                    or verified.setting != setting
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
