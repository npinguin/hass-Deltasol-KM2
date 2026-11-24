"""Controller parameter semantics and positive capability discovery.

BX Plus 2.08 reference: RESOL BXPLUS_2.08/bxplus_208.xml and
BXPlus_208-Menu.xml (RPT package SHA256
1bcc59cac3d4176c68de255fced3b95840bada06db33b0bdae0cddb007a9f079).
The reference indices are a compatibility fingerprint, never blind write targets.
Handbetrieb enum: Aus=0, Min=1, Auto=2, Max=3, Ein=4.
Expose only the requested manual relay modes from the vendor relay menu.
"""

from dataclasses import dataclass, replace
from threading import Lock

from .vbus import VBusError, open_vbus

# The exact RPT device definition identifies this controller address.
BX_PLUS_ADDRESS = 0x7112
# Names and indices are independently resolved on each session. Match the
# known layout before trusting mode semantics, including the Schema marker.
RELAY_LAYOUT = {
    f"Relais_Regler_R{number}_Handbetrieb": (number, index)
    for number, index in enumerate((1977, 1991, 2005, 2019, 2033), 1)
}
SCHEMA_INDEX = 86
MODE_VALUES = {"Auto": 2, "Off": 0, "On": 3}
OBSERVED_MODES = {0: "Off", 1: "Min", 2: "Auto", 3: "On", 4: "Ein"}


@dataclass(frozen=True)
class RelayCapability:
    """A positively discovered relay parameter and its actual mode."""

    number: int
    name: str
    index: int
    changeset: int
    source: str
    serial: str
    value: int

    @property
    def unique_id(self):
        """Stable identity independent of firmware parameter indices."""
        return f"{self.serial}_7112_R{self.number}_mode"

    @property
    def current_option(self):
        """Do not misrepresent an observed out-of-scope manual mode."""
        mode = OBSERVED_MODES.get(self.value)
        return mode if mode in MODE_VALUES else None


def controller_identity(telemetry):
    """Use the observed VBus packet identity, not gateway model or labels."""
    identities = set()
    for key, endpoint in (telemetry or {}).items():
        parts = key.split("__", 1)[0].split("_")
        if len(parts) not in (4, 5) or parts[2].upper() != "7112":
            continue
        serial = (endpoint.product_details or {}).get("serial")
        if not serial or not endpoint.bus_src:
            return None
        identities.add((parts[0], endpoint.bus_src, serial))
    if len(identities) != 1:
        return None
    channel, source, serial = identities.pop()
    return (source, serial) if channel == "00" else None


class RelayParameters:
    """Single serialized parameter owner for the existing API."""

    def __init__(self, host, password):
        self.host = host
        self.password = password
        self._lock = Lock()

    def discover(self, telemetry):
        """Only publish supported modes on the observed compatible controller."""
        identity = controller_identity(telemetry)
        if not identity:
            return {}
        source, serial = identity
        with self._lock, open_vbus(self.host, self.password) as session:
            changeset, values = session.discover(
                BX_PLUS_ADDRESS, ("Schema", *RELAY_LAYOUT)
            )
        if values.get("Schema", (None,))[0] != SCHEMA_INDEX:
            raise VBusError("Unrecognized BX Plus parameter layout")
        for name, (index, _) in values.items():
            if name in RELAY_LAYOUT and index != RELAY_LAYOUT[name][1]:
                raise VBusError("Unrecognized BX Plus relay parameter layout")
        capabilities = {}
        for name, (number, _) in RELAY_LAYOUT.items():
            if name not in values:
                continue
            index, value = values[name]
            if value not in OBSERVED_MODES:
                continue
            capability = RelayCapability(
                number, name, index, changeset, source, serial, value
            )
            capabilities[capability.unique_id] = capability
        return capabilities

    def set_mode(self, telemetry, capability, option):
        """Recheck identity, changeset, parameter and value before every write."""
        if option not in MODE_VALUES:
            raise VBusError("Unsupported relay mode")
        if (
            controller_identity(telemetry) != (capability.source, capability.serial)
            or RELAY_LAYOUT.get(capability.name)
            != (capability.number, capability.index)
        ):
            raise VBusError("Relay capability or controller identity changed")
        with (
            self._lock,
            open_vbus(self.host, self.password) as session,
            session.bus(BX_PLUS_ADDRESS),
        ):
            if session.read(BX_PLUS_ADDRESS, 0) != capability.changeset:
                raise VBusError("Controller changeset changed; rediscover first")
            if session.resolve(BX_PLUS_ADDRESS, "Schema") != SCHEMA_INDEX:
                raise VBusError("Unrecognized BX Plus parameter layout")
            index = session.resolve(BX_PLUS_ADDRESS, capability.name)
            if index != capability.index:
                raise VBusError("Relay parameter is no longer supported")
            if session.read(BX_PLUS_ADDRESS, 0) != capability.changeset:
                raise VBusError("Controller changeset changed before write")
            current = session.read(BX_PLUS_ADDRESS, index)
            if current not in OBSERVED_MODES:
                raise VBusError("Unsupported current relay mode")
            wanted = MODE_VALUES[option]
            if current != wanted:
                current = session.write_verified(
                    BX_PLUS_ADDRESS, index, wanted, capability.changeset
                )
        return replace(capability, value=current)
