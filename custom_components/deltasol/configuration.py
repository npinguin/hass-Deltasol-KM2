"""Typed configuration capabilities; the existing parameter owner performs I/O.

Evidence for the first supported profile: BX Plus RPT 2.08, exact digest
in catalog.py, live changeset 2734DABC, RESOL BX Plus manual Basic settings
and MIT resol-vbus BaseConfigurationOptimizer / ConnectionCustomizer.
The latter confirms enumeration ordinals and save transactions, not current
indices. No site-specific settings or proprietary catalogue are bundled.
"""

import re
import struct
from dataclasses import dataclass, replace
from decimal import Decimal
from hashlib import sha256
from operator import eq, ge, gt, le, lt, ne

from .catalog import (
    CatalogError,
    Definition,
    encode_numeric_value,
    explicit_options,
    is_sensitive,
    numeric_encoding,
    ordinal_options,
)
from .vbus import VBusError


def decode_native_day(payload):
    """Decode the day layout corroborated by BX Plus 2.08 native SET exports.

    Six ordered start/end quarter-hour pairs, unused pairs FF FF.
    RESOL BX Plus manual 11202383 documents the native 00:00-23:45
    quarter-hour range for weekly timer channels. Reject malformed and
    overlapping windows; do not invent a 24:00 sentinel or cross-day format.
    This codec alone never establishes the VBus subindex mapping or write access.
    """
    if not isinstance(payload, bytes) or len(payload) != 12:
        return None
    windows = []
    previous_end = 0
    unused = False
    for start, end in zip(payload[::2], payload[1::2], strict=True):
        if (start, end) == (255, 255):
            unused = True
            continue
        if unused or not previous_end <= start < end <= 95:
            return None
        windows.append({"start_minute": start * 15, "end_minute": end * 15})
        previous_end = end
    return windows


def encode_native_day(windows):
    """Encode only the established native SET representation, not a VBus write.

    Preserve the six-window limit and the documented native 00:00-23:45
    quarter-hour grid. Reject 24:00 instead of silently rounding.
    """
    if not isinstance(windows, (list, tuple)) or len(windows) > 6:
        raise ValueError("Invalid native day windows")
    payload = bytearray([255] * 12)
    for index, window in enumerate(windows):
        if not isinstance(window, dict) or set(window) != {
            "start_minute",
            "end_minute",
        }:
            raise ValueError("Invalid native day windows")
        start, end = window["start_minute"], window["end_minute"]
        if any(
            type(value) is not int or not 0 <= value <= 1425 or value % 15
            for value in (start, end)
        ):
            raise ValueError("Invalid native day windows")
        payload[2 * index : 2 * index + 2] = bytes((start // 15, end // 15))
    result = bytes(payload)
    if decode_native_day(result) is None:
        raise ValueError("Invalid native day windows")
    return result


def parse_weekly_day(value):
    """Parse a native daily schedule, never raw bytes or register values."""
    if not isinstance(value, str) or len(value) > 76:
        raise CatalogError("Invalid weekly schedule")
    windows = []
    for interval in value.split(",") if value.strip() else ():
        match = re.fullmatch(r"\s*(\d{2}):(\d{2})-(\d{2}):(\d{2})\s*", interval)
        if match is None:
            raise CatalogError("Invalid weekly schedule")
        h1, m1, h2, m2 = map(int, match.groups())
        if max(h1, h2) > 23 or max(m1, m2) > 59:
            raise CatalogError("Invalid weekly schedule")
        windows.append({"start_minute": h1 * 60 + m1, "end_minute": h2 * 60 + m2})
    try:
        return struct.unpack("<iii", encode_native_day(windows))
    except ValueError as error:
        raise CatalogError("Invalid weekly schedule") from error


def format_weekly_day(words):
    if (
        not isinstance(words, tuple)
        or len(words) != 3
        or any(type(word) is not int or not -(2**31) <= word < 2**31 for word in words)
    ):
        return None
    windows = decode_native_day(struct.pack("<iii", *words))
    if windows is None:
        return None

    def clock(minutes):
        return f"{minutes // 60:02d}:{minutes % 60:02d}"

    return ", ".join(
        f"{clock(window['start_minute'])}-{clock(window['end_minute'])}"
        for window in windows
    )


@dataclass(frozen=True)
class Dependency:
    """A positively verified raw selector condition, read again before action."""

    name: str
    index: int
    allowed: tuple[int, ...]

    def matches(self, values):
        raw = values.get(self.name)
        return type(raw) is int and raw in self.allowed


@dataclass(frozen=True)
class RangeDependency:
    """Verified raw numeric dependency, checked again before an action."""

    name: str
    index: int
    minimum: int
    maximum: int

    def matches(self, values):
        raw = values.get(self.name)
        return type(raw) is int and self.minimum <= raw <= self.maximum


# Exact topology table in the BX Plus 2.08 native model; zero/1 are not
# installed solar topologies. Counts are encoded in the first two fields.
BX_PLUS_SOLAR_SYSTEMS = frozenset(
    (
        10101,
        20101,
        20102,
        30101,
        30102,
        10201,
        10202,
        10203,
        20201,
        20202,
        20203,
        20204,
        20205,
        30201,
        30202,
        30203,
        30204,
        10301,
        10302,
        10303,
        20301,
        20302,
        20304,
        20305,
        10401,
        10402,
        10403,
    )
)


@dataclass(frozen=True)
class PeerComparison:
    """Compare two observed parameters in the same verified encoding."""

    name: str
    index: int
    other: str
    other_index: int
    operation: str
    margin: int = 0

    @property
    def expected(self):
        return {self.name: self.index, self.other: self.other_index}

    def matches(self, values):
        left, right = values.get(self.name), values.get(self.other)
        compare = {"eq": eq, "gt": gt, "ge": ge, "le": le, "ne": ne}.get(self.operation)
        return (
            compare is not None
            and type(left) is int
            and type(right) is int
            and compare(
                left,
                right
                + (self.margin if self.operation in ("gt", "ge") else -self.margin),
            )
        )


@dataclass(frozen=True)
class WeeklyProgram:
    """A live reference to an existing allocated native weekly program."""

    name: str
    index: int
    require_days: bool = False

    @staticmethod
    def layout(slot):
        root = f"Wsu{slot}"
        index = 18401 + 13 * slot
        allocation = {
            root + "_Belegt": index,
            root + "_Schaltuhr_Woche_Initialisiert": index + 4,
        }
        days = {
            f"{root}_Schaltuhr_Woche_Tag{day}_Daten": index + 5 + offset
            for offset, day in enumerate(("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"))
        }
        return allocation, days

    def slot(self, values):
        slot = values.get(self.name)
        return slot if type(slot) is int and 1 <= slot <= 50 else None

    def matches(self, values):
        slot = self.slot(values)
        if slot is None:
            return False
        allocation, days = self.layout(slot)
        return all(values.get(name) == 1 for name in allocation) and (
            not self.require_days
            or all(
                isinstance(values.get(name), bytes)
                and decode_native_day(values[name]) is not None
                for name in days
            )
        )


@dataclass(frozen=True)
class ActorReferences:
    """Resolve native actor IDs separately from encoded physical references."""

    name: str
    index: int
    references: tuple[tuple[str, int], ...]
    physical: tuple[str, str, int] | None = None

    @property
    def expected(self):
        return {
            self.name: self.index,
            self.name.removesuffix("_Nr") + "_Funktion": self.index + 1,
            **dict(self.references),
            **({self.physical[1]: self.physical[2]} if self.physical else {}),
        }

    @staticmethod
    def actor_layout(actor):
        if type(actor) is not int or not 1 <= actor <= 100:
            return {}
        base = 2660 + 5 * (actor - 1)
        return {
            f"Aktor{actor}_Funktion": base,
            f"Aktor{actor}_RelaisNr": base + 1,
            f"Aktor{actor}_AusgangNr": base + 2,
        }

    @staticmethod
    def decode_reference(raw):
        if type(raw) is not int or not 0 < raw <= 0x7FFF:
            return None
        kind, number = divmod(raw, 256)
        if kind in (0, 1) and 1 <= number <= 45:
            return ("relay", number)
        if kind == 2 and 1 <= number <= 4:
            return ("analog", number)
        return None

    @staticmethod
    def output_layout(output):
        kind, number = output
        if kind == "analog" and 1 <= number <= 4:
            return {f"Ausgang_Regler_A{number}_AktorFunktion": 2602 + 15 * (number - 1)}
        if kind != "relay" or not 1 <= number <= 45:
            return {}
        if number <= 14:
            return {f"Relais_Regler_R{number}_AktorFunktion": 1971 + 14 * (number - 1)}
        if number <= 39:
            module, relay = divmod(number - 15, 5)
            return {
                f"Relais_Modul{module + 1}_R{relay + 1}_AktorFunktion": 2167
                + 14 * (number - 15),
                f"Modul{module + 1}_Aktiviert": 191 + 6 * module,
                f"Modul{module + 1}_Vorhanden": 193 + 6 * module,
            }
        return {
            f"Relais_Virtuell_R{number - 39}_AktorFunktion": 2517 + 14 * (number - 40)
        }

    def owner_outputs(self, values):
        names = tuple(self.actor_layout(values.get(self.name)))
        if not names:
            return ()
        _, relay, analog = (values.get(name) for name in names)
        if (
            type(relay) is not int
            or type(analog) is not int
            or not 0 <= relay <= 45
            or not 0 <= analog <= 4
        ):
            return ()
        return tuple(
            output for output in (("relay", relay), ("analog", analog)) if output[1]
        )

    def output_dependencies(self, values):
        outputs = set(self.owner_outputs(values))
        outputs.update(
            output
            for name, _ in self.references
            if (output := self.decode_reference(values.get(name))) is not None
        )
        return {
            name: index
            for output in outputs
            for name, index in self.output_layout(output).items()
        }

    @staticmethod
    def assigned_role(raw):
        # Native AktorFunktion is a 16-bit role plus instance, not a plain enum.
        return type(raw) is int and 0 <= raw <= 0xFFFF and 2 <= raw & 0xFF <= 70

    @classmethod
    def output_role(cls, output, values):
        layout = cls.output_layout(output)
        if not layout:
            return None
        role = values.get(next(iter(layout)))
        if not cls.assigned_role(role):
            return None
        for name in tuple(layout)[1:]:
            if values.get(name) == 1:
                continue
            if name.endswith("_Vorhanden") and values.get(name + "#verified") == 1:
                continue
            return None
        return role

    def matches(self, values):
        names = tuple(self.actor_layout(values.get(self.name)))
        owners = self.owner_outputs(values)
        if not names or not owners:
            return False
        role = values.get(names[0])
        if (
            not self.assigned_role(role)
            or role != values.get(self.name.removesuffix("_Nr") + "_Funktion")
            or any(self.output_role(output, values) != role for output in owners)
        ):
            return False
        refs = []
        for name, _ in self.references:
            raw = values.get(name)
            if type(raw) is not int:
                return False
            if raw == 0:
                continue
            output = self.decode_reference(raw)
            if (
                output is None
                or output in owners
                or self.output_role(output, values) is None
            ):
                return False
            refs.append(output)
        if self.physical:
            kind, name, _ = self.physical
            if (kind, values.get(name)) not in owners:
                return False
        return (bool(refs) or not self.references) and len(refs) == len(set(refs))

    def context(self, values):
        layout = {
            **self.expected,
            **self.actor_layout(values.get(self.name)),
            **self.output_dependencies(values),
        }
        return tuple(
            (name, values.get(name + "#verified", values.get(name)))
            for name in sorted(layout)
        )


@dataclass(frozen=True)
class FunctionSelection:
    """Validate the native mask against observed function numbers, not UI slots."""

    name: str
    index: int
    heating: bool = False

    @property
    def slots(self):
        prefix, base, stride, count = (
            ("Heizung_Heizkreis", 13684, 495, 7)
            if self.heating
            else ("Anlage_Wf", 6559, 251, 16)
        )
        return tuple((f"{prefix}{n + 1}", base + stride * n) for n in range(count))

    @property
    def expected(self):
        result = {self.name: self.index}
        for root, base in self.slots:
            result.update({root + "_Type": base, root + "_Nr": base + 1})
            if self.heating:
                result.update(
                    {
                        root + "_System": base + 13,
                        root + "_Sensor1_Nr": base + 112,
                        root + "_Relais1_Nr": base + 80,
                    }
                )
        return result

    def matches(self, values):
        mask = values.get(self.name)
        if type(mask) is not int or not 0 < mask < (1 << len(self.slots)):
            return False
        available = 0
        for slot, (root, _) in enumerate(self.slots, 1):
            kind = values.get(root + "_Type")
            if type(kind) is not int:
                return False
            if kind != (1 if self.heating else 6):
                continue
            nr = values.get(root + "_Nr")
            if type(nr) is not int or not 1 <= nr <= len(self.slots):
                return False
            bit = 1 << (nr - 1)
            if available & bit:
                return False
            if self.heating and mask & bit:
                # Runtime indexes the internal circuit registry directly.
                if nr != slot or values.get(root + "_System") not in (0, 2):
                    return False
                for field, maximum in (("Sensor1_Nr", 65), ("Relais1_Nr", 100)):
                    assigned = values.get(root + "_" + field)
                    if type(assigned) is not int or not 1 <= assigned <= maximum:
                        return False
            available |= bit
        return mask & available == mask

    def context(self, values):
        return tuple((name, values.get(name)) for name in sorted(self.expected))


def _dependency_layout(dependencies):
    result = {}
    for dep in dependencies:
        if isinstance(
            dep,
            (
                PeerComparison,
                AnyDependencies,
                SolarSelection,
                ActorReferences,
                FunctionSelection,
            ),
        ):
            result.update(dep.expected)
        else:
            result[dep.name] = dep.index
    return result


@dataclass(frozen=True)
class AnyDependencies:
    """At least one complete positively verified native assignment group."""

    groups: tuple[
        tuple[
            Dependency
            | RangeDependency
            | PeerComparison
            | WeeklyProgram
            | ActorReferences
            | FunctionSelection,
            ...,
        ],
        ...,
    ]

    @property
    def expected(self):
        return _dependency_layout(dep for group in self.groups for dep in group)

    def matches(self, values):
        return any(all(dep.matches(values) for dep in group) for group in self.groups)


@dataclass(frozen=True)
class SolarSelection:
    """Require a valid native topology and each selected live assignment."""

    name: str
    index: int
    stores: bool = False
    target_unit: int | None = None

    @property
    def assignments(self):
        if self.stores:
            return tuple(
                (
                    RangeDependency(
                        f"Solar_Sp{n}_Sensor1_Nr", 3576 + 36 * (n - 1), 1, 65
                    ),
                    Dependency(
                        f"Solar_Sp{n}_Aktiviert_Modus", 3604 + 36 * (n - 1), (1, 2)
                    ),
                    *self.target_dependencies(n),
                )
                for n in range(1, 5)
            )
        return tuple(
            (RangeDependency(f"Solar_Kol{n}_Sensor1_Nr", 3536 + 14 * (n - 1), 1, 65),)
            for n in range(1, 4)
        )

    def target_dependencies(self, slot):
        if self.target_unit is None:
            return ()
        offset = (slot - 1) * 36
        root = f"Solar_Sp{slot}"
        alias, lo, hi, step = (
            ("_C", 40, 950, 10) if self.target_unit == 0 else ("_F", 400, 2040, 20)
        )
        target, maximum = root + "_Tsoll", root + "_Tmax"
        ti, mi = 3588 + offset, 3591 + offset
        return (
            Dependency("TemperatureHysteresisSelector", 60, (self.target_unit,)),
            raw_steps(target, ti, lo, hi, step),
            raw_steps(maximum, mi, lo, hi, step),
            PeerComparison(target, ti, target + alias, ti + self.target_unit + 1, "eq"),
            PeerComparison(
                maximum, mi, maximum + alias, mi + self.target_unit + 1, "eq"
            ),
            PeerComparison(target, ti, maximum, mi, "le"),
        )

    @property
    def expected(self):
        return {
            "Solar_SystemId": 3416,
            self.name: self.index,
            **_dependency_layout(dep for group in self.assignments for dep in group),
        }

    def matches(self, values):
        system = values.get("Solar_SystemId")
        mask = values.get(self.name)
        if type(system) is not int or system not in BX_PLUS_SOLAR_SYSTEMS:
            return False
        count = (system // 100) % 100 if self.stores else system // 10000
        return (
            type(mask) is int
            and 0 < mask < (1 << count)
            and all(
                all(dep.matches(values) for dep in group)
                for bit, group in enumerate(self.assignments)
                if mask & (1 << bit)
            )
        )


@dataclass(frozen=True)
class RequestedCondition:
    """Require at least one live condition for a particular requested value."""

    raw: int
    alternatives: tuple[
        Dependency
        | RangeDependency
        | PeerComparison
        | WeeklyProgram
        | AnyDependencies
        | SolarSelection
        | ActorReferences
        | FunctionSelection,
        ...,
    ]

    def matches(self, raw, values):
        return raw != self.raw or any(dep.matches(values) for dep in self.alternatives)


@dataclass(frozen=True)
class RelativeConstraint:
    """Verified relation between values with the same raw encoding."""

    name: str
    index: int
    operation: str
    margin: int = 0

    def matches(self, raw, values, current=None):
        other = values.get(self.name)
        if self.operation in ("solar_store_priority", "solar_cooling_priority"):
            return (
                type(raw) is int
                and type(other) is int
                and other in BX_PLUS_SOLAR_SYSTEMS
                and (0 if self.operation == "solar_cooling_priority" else 1)
                <= raw
                <= (other // 100) % 100
            )
        if self.operation == "preserve_order":
            return (
                type(raw) is int
                and type(current) is int
                and type(other) is int
                and (
                    (current < other and raw < other)
                    or (current > other and raw > other)
                )
            )
        compare = {"lt": lt, "le": le, "gt": gt, "ge": ge, "ne": ne}.get(self.operation)
        return (
            compare is not None
            and type(raw) is int
            and type(other) is int
            and compare(
                raw,
                other
                + (self.margin if self.operation in ("gt", "ge") else -self.margin),
            )
        )


@dataclass(frozen=True)
class Setting:
    """Verified semantic contract for one adjustable setting, never a raw service."""

    name: str
    index: int
    label: str
    kind: str
    save: bool
    options: tuple[tuple[str, int], ...] = ()
    # Exact static engineering-unit limits, not inferred vendor metadata.
    limits: tuple[str, str, str, str] | None = None  # min, max, step, factor
    unit: str | None = None
    dependencies: tuple[
        Dependency
        | RangeDependency
        | SolarSelection
        | AnyDependencies
        | WeeklyProgram
        | ActorReferences
        | FunctionSelection,
        ...,
    ] = ()
    owner_address: int | None = None
    relative_constraints: tuple[RelativeConstraint, ...] = ()
    # Installed program selector: may be used as a Discovery hint, never as
    # authorization. The same dependency is always re-read live for an action.
    installed_by: Dependency | RangeDependency | None = None
    owner_relay: int | None = None
    # Each tuple describes a parent and its selected child. Read both live
    # before accepting any adjustment; never issue a write to a child alias.
    compound_mirrors: tuple[tuple[str, int, str, int], ...] = ()
    # Native speed control is an output property (BX Plus manual, p. 17).
    # A missing volatile role is permitted only with positive speed evidence;
    # explicit contrary role or lookup errors remain disqualifying.
    optional_role: Dependency | None = None
    # Exact encoding bounds for sibling settings that participate in a
    # compound transaction. Their current values must also be valid.
    peer_limits: tuple[tuple[str, tuple[str, str, str, str]], ...] = ()
    # Runtime metadata derived only from an authorized, exact catalogue.
    value_definition: Definition | None = None
    weekly_slot: int | None = None
    requested_conditions: tuple[RequestedCondition, ...] = ()
    native_options: tuple[int, ...] = ()

    @property
    def timer_reference(self):
        return next(
            (dep for dep in self.dependencies if isinstance(dep, WeeklyProgram)),
            None,
        )

    def weekly_references(self, requested=None):
        """Only consult requested predicates during an actual action."""

        def walk(dependencies):
            for dep in dependencies:
                if isinstance(dep, WeeklyProgram):
                    yield dep
                elif isinstance(dep, AnyDependencies):
                    for group in dep.groups:
                        yield from walk(group)

        yield from walk(self.dependencies)
        for condition in self.requested_conditions:
            if requested == condition.raw:
                yield from walk(condition.alternatives)

    def function_selections(self):
        def walk(dependencies):
            for dep in dependencies:
                if isinstance(dep, FunctionSelection):
                    yield dep
                elif isinstance(dep, AnyDependencies):
                    for group in dep.groups:
                        yield from walk(group)

        yield from walk(self.dependencies)
        for condition in self.requested_conditions:
            yield from walk(condition.alternatives)

    def actor_references(self):
        """Only established, fixed native references; never scan all actors."""

        def walk(dependencies):
            for dep in dependencies:
                if isinstance(dep, ActorReferences):
                    yield dep
                elif isinstance(dep, AnyDependencies):
                    for group in dep.groups:
                        yield from walk(group)

        yield from walk(self.dependencies)
        for condition in self.requested_conditions:
            yield from walk(condition.alternatives)

    @property
    def native_slot(self):
        """The native function owning the setting, not the connected equipment."""
        if not self.installed_by or self.owner_relay is not None:
            return None
        match = re.fullmatch(
            r"(Anlage_Wf|Solar_Wf|Heizung_Wf|Heizung_Heizkreis|Wmz)([1-9][0-9]*)_Type",
            self.installed_by.name,
        ) or re.fullmatch(
            r"(Solar_Sp|Solar_Kol)([1-9][0-9]*)_Sensor1_Nr",
            self.installed_by.name,
        )
        dedicated = re.fullmatch(
            r"Solar_Wf([1-9][0-9]*)_(Bypass|CsBypass|Zieltemperatur|Roehrenkollektor|ExtWT)_"
            r"(?:Sensor1_Nr|SensorEinstrahlung_Nr|KollektorMaske)",
            self.installed_by.name,
        )
        if dedicated:
            slot = f"Solar_Wf{dedicated[1]}_{dedicated[2]}"
            if self.name.startswith(slot + "_"):
                return slot, "Solar_" + dedicated[2], int(dedicated[1])
        if not match:
            return None
        slot = f"{match[1]}{match[2]}"
        if not self.name.startswith(slot + "_"):
            return None
        return slot, match[1], int(match[2])

    def numeric_definition(self):
        if self.kind != "number" or self.limits is None:
            raise CatalogError("Numeric setting bounds are not established")
        if self.value_definition is not None:
            return self.value_definition
        minimum, maximum, step, factor = self.limits
        return Definition(
            self.name,
            self.index,
            "unspecified",
            "Number",
            {
                "type_chain": ["Number"],
                "minimum": minimum,
                "maximum": maximum,
                "step": step,
                "storeFactor": factor,
            },
        )

    def encode(self, value):
        if self.kind == "text" and self.weekly_slot is not None:
            return parse_weekly_day(value)
        if self.kind == "switch":
            if type(value) is not bool:
                raise CatalogError("Boolean value required")
            return int(value)
        if self.kind == "select":
            if not isinstance(value, str) or value not in dict(self.options):
                raise CatalogError("Unsupported configuration option")
            return dict(self.options)[value]
        if self.kind == "number":
            return encode_numeric_value(self.numeric_definition(), value)
        raise CatalogError("Unsupported configuration type")

    def decode(self, raw):
        if self.kind == "text" and self.weekly_slot is not None:
            return format_weekly_day(raw)
        if type(raw) is not int:
            return None
        if self.kind == "switch":
            return bool(raw) if raw in (0, 1) else None
        if self.kind == "select":
            return {value: label for label, value in self.options}.get(raw)
        if self.kind == "number" and self.limits is not None:
            result = Decimal(raw) * Decimal(self.limits[3])
            try:
                self.encode(str(result))
            except CatalogError:
                return None
            return float(result)
        return None

    @property
    def expected(self):
        result = {self.name: self.index}
        installed = (self.installed_by,) if self.installed_by else ()
        for dependency in (*installed, *self.dependencies, *self.relative_constraints):
            if isinstance(
                dependency,
                (SolarSelection, AnyDependencies, ActorReferences, FunctionSelection),
            ):
                result.update(dependency.expected)
            else:
                result[dependency.name] = dependency.index
        for parent, index, child, child_index in self.compound_mirrors:
            result[parent] = index
            result[child] = child_index
        for condition in self.requested_conditions:
            result.update(_dependency_layout(condition.alternatives))
        if self.optional_role:
            result[self.optional_role.name] = self.optional_role.index
        return result

    def accepts(self, values, requested=None):
        raw = values.get(self.name) if requested is None else requested
        return (
            self.decode(raw) is not None
            and all(
                type(values.get(parent)) is int and values[parent] == values.get(child)
                for parent, _, child, _ in self.compound_mirrors
            )
            and all(
                replace(self, limits=limits, value_definition=None).decode(
                    values.get(name)
                )
                is not None
                for name, limits in self.peer_limits
            )
            and (self.installed_by is None or self.installed_by.matches(values))
            and all(dep.matches(values) for dep in self.dependencies)
            and (
                requested is None
                or all(rule.matches(raw, values) for rule in self.requested_conditions)
            )
            and all(
                rule.matches(raw, values, values.get(self.name))
                for rule in self.relative_constraints
            )
        )


def _normalized_numeric(definition, limits):
    """Interpret known firmware encodings, without inferring write permission.

    The approved contract supplies only explicitly corroborated exceptions
    (integer step absent from menu, time in seconds, HQM raw quant).
    """
    metadata = dict(definition.metadata)
    chain = metadata.get("type_chain", ())
    if "DayTime" in chain:
        # Native day-period editor has coarse 60-minute and fine 15-minute
        # increments (RPT quant step is the index, not a second wire factor).
        if metadata.get("quant_step") == "1" and metadata.get("quant") == "15":
            if not any(
                suffix in type_id
                for type_id in chain
                for suffix in ("tSommerTagEin", "tSommerTagAus")
            ):
                raise CatalogError("Unsupported clock increment variant")
            metadata.pop("quant_step")
        if limits[3] != "1":
            raise CatalogError("Unsupported clock encoding")
        for key in ("minimum", "maximum"):
            parts = str(metadata.get(key)).split(":")
            if len(parts) != 2 or not all(part.isdecimal() for part in parts):
                raise CatalogError("Unsupported clock encoding")
            hour, minute = map(int, parts)
            if hour > 23 or minute > 59:
                raise CatalogError("Invalid clock")
            metadata[key] = str(hour * 60 + minute)
        metadata["type_chain"] = [*chain, "Number"]
    elif "Time" in chain and definition.name.endswith(
        "_Funktionsblock_dtMindestausschaltzeit"
    ):
        # Native restart delay is already expressed in minutes. Its
        # two editor increments are 60 and 1, not seconds or a clock.
        if (
            limits != ("1", "1439", "1", "1")
            or metadata.get("quant") != "1"
            or metadata.get("quant_step") != "1"
        ):
            raise CatalogError("Unsupported restart delay encoding")
        metadata.pop("quant_step")
        metadata["type_chain"] = [*chain, "Number"]
    elif "Time" in chain and definition.name == "SDKarte_Logging_Intervall":
        # The native SD log interval is already encoded in whole seconds.
        if (
            limits != ("1", "1200", "1", "1")
            or metadata.get("quant") != "1"
            or metadata.get("quant_step") != "1"
        ):
            raise CatalogError("Unsupported SD logging interval encoding")
        metadata.pop("quant_step")
        metadata["type_chain"] = [*chain, "Number"]
    elif "Time" in chain:
        if limits[3] != "1":
            raise CatalogError("Unsupported duration factor")
        for key in ("minimum", "maximum", "quant"):
            parts = str(metadata.get(key)).split(":")
            if len(parts) != 2 or not all(part.isdecimal() for part in parts):
                raise CatalogError("Unsupported duration encoding")
            minutes, seconds = map(int, parts)
            if seconds >= 60:
                raise CatalogError("Invalid duration")
            metadata[key] = str(minutes * 60 + seconds)
        metadata["type_chain"] = [*chain, "Number"]
    elif (
        "Wmz_WmzX_Struct_Vt{0}_AlternativAnzeigeFaktor" in chain
        and metadata.get("quant") == "1"
        and metadata.get("storeFactor") == "0.0000001"
    ):
        metadata["quant"] = metadata["storeFactor"]
        metadata["type_chain"] = [*chain, "Number"]
    elif (
        re.fullmatch(
            r"Heizung_RelaisAnforderung[12]_Ausgang_Vorlauf_dVKorrektur",
            definition.name,
        )
        and limits == ("0", "1", "0.1", "0.1")
        and metadata.get("storeFactor") == "0.1"
        and "quant" not in metadata
        and "step" not in metadata
    ):
        # Native correction editor advances one raw tenth of a volt.
        metadata["step"] = "0.1"
    elif (
        re.fullmatch(
            r"Anlage_Wf[1-9][0-9]*_Funktionsblock_Volumenstrom(?:Ein|Aus)_(?:LpMin|GpMin)",
            definition.name,
        )
        and "FlowSettingBase" in chain
        and metadata.get("quant") == "1.0"
        and metadata.get("storeFactor") == "0.1"
        and limits[2:] == ("0.1", "0.1")
    ):
        # The native editor has a second, fine increment of one raw tenth.
        metadata["quant"] = "0.1"
    elif "step" not in metadata and "quant" not in metadata:
        # Exact integer step established by the existing approved contract.
        if Decimal(limits[2]) != 1 or Decimal(limits[3]) != 1:
            raise CatalogError("Missing numeric increment")
        metadata["step"] = "1"
    normalized = replace(definition, metadata=metadata)
    actual = numeric_encoding(normalized)
    if actual != tuple(Decimal(value) for value in limits):
        raise CatalogError("Catalogue numeric contract differs")
    return normalized


def bind_setting(setting, definitions):
    """Bind an approved semantic contract to vendor metadata, fail closed.

    Catalogue data defines encoding, never access or save semantics. The
    reference profile authorizes the setting and its conditional relationships.
    No arbitrary menu entry or unverified alias can become a writable entity.
    """
    for name, index in setting.expected.items():
        definition = definitions.get(name)
        if (
            definition is None
            or definition.index != index
            or is_sensitive(definition)
            or (
                definition.excluded
                and not definition.metadata.get("compound_parent_read_candidate")
            )
        ):
            raise CatalogError("Catalogue contract layout differs")
    target = definitions[setting.name]
    alias = target
    for parent, _, child, _ in setting.compound_mirrors:
        parent_definition = definitions[parent]
        child_definition = definitions[child]
        if (
            not parent_definition.metadata.get("compound_parent_read_candidate")
            or child_definition.metadata.get("compound_ref") != parent
            or child_definition.excluded
        ):
            raise CatalogError("Catalogue compound contract differs")
        if parent == setting.name:
            alias = child_definition
        if parent != setting.name:
            limits = dict(setting.peer_limits).get(parent)
            if limits is not None:
                _normalized_numeric(child_definition, limits)
    if target.metadata.get("compound_ref") or (
        target.storage == "abstract" and alias is target
    ):
        raise CatalogError("Catalogue write target is an alias or structure")
    if setting.save and target.storage not in ("unspecified", "persistent", "abstract"):
        raise CatalogError("Catalogue storage contract differs")
    if setting.kind == "number":
        normalized = _normalized_numeric(alias, setting.limits)
        return replace(setting, value_definition=normalized)
    if setting.kind == "text" and setting.weekly_slot is not None:
        if (
            "Blob" not in target.metadata.get("type_chain", ())
            or target.metadata.get("size_bytes") != 12
            or target.metadata.get("size_unverified")
            or target.metadata.get("unresolved_base")
            or target.metadata.get("rpt_storage") != "preferences"
        ):
            raise CatalogError("Catalogue weekly day contract differs")
    elif setting.kind == "switch":
        if "Boolean" not in target.metadata.get("type_chain", ()):
            raise CatalogError("Catalogue boolean contract differs")
    elif setting.kind == "select":
        options = explicit_options(target) or ordinal_options(target)
        if setting.limits == ("1", "3", "1", "1"):
            # Explicit native min/mean/max aggregation, stored as a number.
            _normalized_numeric(target, setting.limits)
            options = (1, 2, 3)
        exposed = {raw for _, raw in setting.options}
        if set(options) != set(setting.native_options or exposed) or not exposed <= set(
            options
        ):
            raise CatalogError("Catalogue enumeration contract differs")
    else:
        raise CatalogError("Unsupported configuration contract")
    return setting


def observed_function_assignments(setting, values):
    """Project already-read references without inferring wiring or activity.

    Sensor numbers belong to the native input registry, not automatically
    to a VBus telemetry field. Only a resolved actor can establish an
    output relation. No additional read or write is performed here.
    """
    slot = setting.native_slot
    if slot is None:
        return ()
    prefix = slot[0] + "_"
    relations = set()
    for name in setting.expected:
        raw = values.get(name)
        if (
            name.startswith(prefix)
            and re.search(r"_Sensor(?:[0-9]+|Schalter|Einstrahlung)_?Nr$", name)
            and type(raw) is int
            and 1 <= raw <= 65
        ):
            relations.add(("sensor_reference", name, raw))
    for reference in setting.actor_references():
        if not reference.name.startswith(prefix) or not reference.matches(values):
            continue
        for kind, number in reference.owner_outputs(values):
            relations.add((kind + "_assignment", reference.name, number))
        for name, _ in reference.references:
            output = reference.decode_reference(values.get(name))
            if output:
                kind, number = output
                relations.add((kind + "_dependency", name, number))
    return tuple(sorted(relations))


@dataclass(frozen=True)
class WeeklyReview:
    """Explicit reviewed precondition for one existing native owner action."""

    unique_id: str
    expected: str
    reference_token: str

    def validate(self, capability):
        """Reject stale review before any I/O, while the owner lock is held."""
        if (
            capability.unique_id != self.unique_id
            or capability.setting.weekly_slot is None
            or capability.value != self.expected
            or capability.weekly_review_token != self.reference_token
        ):
            raise ValueError("Weekly program changed; review again")


@dataclass(frozen=True)
class ConfigurationCapability:
    """A live, device-bound setting with verified semantic write evidence."""

    setting: Setting
    controller: object
    device: object
    changeset: int
    raw: int | tuple[int, int, int]
    owners: tuple[str, ...] = ()
    active_owners: tuple[str, ...] = ()
    program_reference: int | None = None
    reference_context: tuple[tuple[str, int | None], ...] = ()
    assignment_observations: tuple[tuple[str, str, int], ...] = ()

    @property
    def unique_id(self):
        return (
            f"{self.controller.serial}_{self.controller.address:04X}_"
            f"{self.setting.name}_configuration"
        )

    @property
    def value(self):
        return self.setting.decode(self.raw)

    @property
    def weekly_review_token(self):
        return sha256(repr((self.owners, self.active_owners)).encode()).hexdigest()


@dataclass(frozen=True)
class WeeklyOwner:
    """Native function reference and positive active timer conditions."""

    name: str
    index: int
    conditions: tuple[Dependency | RangeDependency, ...]
    alternate_conditions: tuple[Dependency | RangeDependency, ...] = ()

    @property
    def expected(self):
        return {
            self.name: self.index,
            **{d.name: d.index for d in self.conditions + self.alternate_conditions},
        }

    def active(self, values):
        return all(d.matches(values) for d in self.conditions) or (
            bool(self.alternate_conditions)
            and all(d.matches(values) for d in self.alternate_conditions)
        )

    def inactive(self, values):
        def disproved(conditions):
            return any(
                type(values.get(d.name)) is int and not d.matches(values)
                for d in conditions
            )

        return disproved(self.conditions) and (
            not self.alternate_conditions or disproved(self.alternate_conditions)
        )


def raw_steps(name, index, minimum, maximum, step):
    """Exact integer encoding of a companion preference, not an authorization."""
    return Dependency(name, index, tuple(range(minimum, maximum + 1, step)))


def encode_action(setting, value):
    """Return the validated wire value or a fixed, non-sensitive error."""
    try:
        return setting.encode(value)
    except CatalogError as err:
        raise VBusError("Invalid configuration value") from err
