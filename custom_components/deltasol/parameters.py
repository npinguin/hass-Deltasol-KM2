"""Positive, profile-driven VBus relay capabilities.

Reference: RESOL BX Plus 2.08 RPT package SHA256
1bcc59cac3d4176c68de255fced3b95840bada06db33b0bdae0cddb007a9f079.
Reference indices are a layout fingerprint, never blind write targets.
EM subaddresses use 0x6650 + SA (resol-vbus JSON live-data server).
The BX Plus manual specifies five local relays and two EM slots.
"""

from contextlib import contextmanager
from dataclasses import dataclass, replace
from threading import Lock
from time import monotonic, sleep

from .vbus import VBusError, open_vbus

# Initial conservative reconnect guard; tune against physical KM2 evidence.
SESSION_GAP_SECONDS = 1.5

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


def _checked_parameters(session, profile, changeset, expected):
    """Resolve dynamically and verify the reference layout."""
    found = session.read_parameters(profile.address, expected, changeset)
    if any(index != expected[name] for name, (index, _) in found.items()):
        raise VBusError("Unrecognized controller parameter layout")
    return {name: value for name, (_, value) in found.items()}


def _read_group(session, profile, changeset, group, controller, device, numbers):
    """Read presence and registration before any module relay parameters."""
    if group.flags:
        flags = _checked_parameters(session, profile, changeset, dict(group.flags))
        if len(flags) != len(group.flags) or any(
            v not in (0, 1) for v in flags.values()
        ):
            return "registration_unknown", {}
        if any(v != 1 for v in flags.values()):
            return "not_registered_or_present", {}

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
        )
        capabilities[cap.unique_id] = cap
    return ("supported" if capabilities else "no_supported_relays"), capabilities


class RelayParameters:
    """One serialized owner for all relay groups on the controller."""

    def __init__(self, host, password):
        self.host = host
        self.password = password
        self._lock = Lock()
        self._next_session = 0.0

    @contextmanager
    def _session(self, address):
        """Serialize polling and writes, including gateway recovery time."""
        with self._lock:
            delay = self._next_session - monotonic()
            if delay > 0:
                sleep(delay)
            try:
                with (
                    open_vbus(self.host, self.password) as session,
                    session.bus(address),
                ):
                    yield session
            finally:
                self._next_session = monotonic() + SESSION_GAP_SECONDS

    def discover(self, telemetry):
        inventory = device_inventory(telemetry)
        profile, controller = find_profile(inventory)
        if profile is None:
            return RelayDiscovery(None, {}, {})
        capabilities = {}
        status = {}
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
                )
                capabilities.update(relays)
        return RelayDiscovery(profile, capabilities, status)

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
            if current.options != capability.options or option not in current.options:
                raise VBusError("Relay options changed; rediscover first")
            wanted = MODE_VALUES[option]
            if current.value != wanted:
                value = session.write_verified(
                    profile.address, current.index, wanted, changeset
                )
                current = replace(current, value=value)
        return current
