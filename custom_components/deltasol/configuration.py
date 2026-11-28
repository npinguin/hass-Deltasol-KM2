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

    @property
    def expected(self):
        return {self.name: self.index, self.other: self.other_index}

    def matches(self, values):
        left, right = values.get(self.name), values.get(self.other)
        compare = {"eq": eq, "gt": gt, "le": le, "ne": ne}.get(self.operation)
        return (
            compare is not None
            and type(left) is int
            and type(right) is int
            and compare(left, right)
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

    @property
    def expected(self):
        return {
            self.name: self.index,
            self.name.removesuffix("_Nr") + "_Funktion": self.index + 1,
            **dict(self.references),
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
        return bool(refs) and len(refs) == len(set(refs))

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
            _raw_steps(target, ti, lo, hi, step),
            _raw_steps(maximum, mi, lo, hi, step),
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
        if self.operation == "solar_store_priority":
            return (
                type(raw) is int
                and type(other) is int
                and other in BX_PLUS_SOLAR_SYSTEMS
                and 1 <= raw <= (other // 100) % 100
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


def native_menu_review_reason(name):
    """Explain an unbound exact BX Plus menu preference, never authorize a write.

    Review categories are scope/evidence decisions, not native access flags.
    In particular, program options and remaining operating values stay open.
    """
    family = re.sub(r"(?<=\D)\d+", "X", name)
    if re.fullmatch(r"Relais_(?:ModulX|Regler)_RX_Handbetrieb", family):
        return "existing_relay_owner"
    if re.search(
        r"_Status_|_Brennerstarts$|_Betriebssekunden$|_Zaehlerstand(?:Heute|Jahr|Monat|Woche)?$"
        r"|^WmzX_(?:Volumen|Wert)|^Sensor_Regler_SX_Wert$|_Neustarts_Neustarts$"
        r"|_Estrichtrocknung_(?:Phase|TVorlaufSoll)$|_HeizkreisX_TVorlaufSoll$",
        family,
    ):
        return "runtime_or_history_preserved"
    if re.search(
        r"_Nr$|Maske$|^Schema$|^Solar_SystemId$|^ModulX_Aktiviert$|_RelaisX_Funktion$",
        family,
    ):
        return "hardware_or_topology_assignment"
    if re.match(
        r"MesseModus_|EntwicklerModus_|Produktionstest_|Heizung_Estrichtrocknung_",
        family,
    ):
        return "engineering_or_commissioning_only"
    if re.search(
        r"^WmzX_(?:FrostschutzArt|FrostschutzGehalt)$"
        r"|^Sensor(?:Einstrahlung|Flowrotor|GrundfosAnalog|GrundfosDigital|"
        r"Luftfeuchtigkeit|Schalter|VolumenstromImpuls)?_(?:ModulX|Regler)_"
        r"|^Heizung_RelaisAnforderungX_Ausgang_(?:TemperaturX|SpannungX|"
        r"ODauerSpannung|DauerSpannung|TMin|TMax|OVorlauf)$"
        r"|^WmzX_(?:OBezugsrelaisX|OVolumenstromSensor)$"
        r"|_Invertiert$|_VentilInvertiert$|_System$|_Heizsystem$|_Kuehlsystem$"
        r"|_Variante$|_Typ$|_Justierzeit$",
        family,
    ):
        return "commissioning_or_control_scheme"
    if re.search(
        r"Frostschutz|Kesselschutz|Druckueberwachung|Volumenstromueberwachung|"
        r"Taupunkt|Ueberwaerme|ThDesinfektion|Solar_Funktionskontrolle|"
        r"Blockierschutz|_TVorlauf(?:Min|Max)$|_TMax_Kesselruecklauf$"
        r"|_Onot$|_Tnot$|_OSpMaxAus$|_OSchornsteinfeger$|_TFrost$|_TSpeicherFrost$",
        family,
    ):
        return "protection_or_safety_configuration"
    if re.fullmatch(r"Solar_Sp5_Vorrang", name):
        return "unsupported_native_topology_slot"
    if family.endswith("_Handbetrieb"):
        return "manual_override_contract_unverified"
    if "_DrainBack_" in family:
        return "hydraulic_applicability_review_required"
    if re.search(
        r"_Aktiviert(?:_Modus)?$|_Modus$|_O[A-Z][A-Za-z]*X?$|_Option$|_ModusSchaltuhr$",
        family,
    ):
        return "program_option_review_required"
    return "native_operating_contract_unverified"


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

    @property
    def unique_id(self):
        return (
            f"{self.controller.serial}_{self.controller.address:04X}_"
            f"{self.setting.name}_configuration"
        )

    @property
    def value(self):
        return self.setting.decode(self.raw)


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


def _weekly_owners():
    for slot in range(1, 17):
        for prefix, base, stride, timer, ref, types in (
            ("Anlage_Wf", 6559, 251, 15, 61, (3, 5, 9)),
            ("Heizung_Wf", 11266, 151, 22, 80, (2,)),
        ):
            root = f"{prefix}{slot}"
            index = base + stride * (slot - 1)
            yield WeeklyOwner(
                root + "_SchaltuhrNr",
                index + ref,
                (
                    Dependency(root + "_Type", index, types),
                    Dependency(root + "_Aktiviert_Modus", index + 7, (1, 2)),
                    Dependency(root + "_OSchaltuhr", index + timer, (1,)),
                ),
                (
                    Dependency(root + "_Type", index, (4,)),
                    Dependency(root + "_Aktiviert_Modus", index + 7, (1, 2)),
                    Dependency(root + "_Zirkulation_Variante", index + 139, (1, 2, 4)),
                )
                if prefix == "Heizung_Wf"
                else (),
            )
    for slot in range(1, 8):
        root = f"Heizung_Heizkreis{slot}"
        index = 13684 + 495 * (slot - 1)
        conditions = (
            Dependency(root + "_Type", index, (1,)),
            Dependency(root + "_Aktiviert_Modus", index + 5, (1, 2)),
        )
        yield WeeklyOwner(
            root + "_SchaltuhrNr",
            index + 133,
            (
                *conditions,
                Dependency(root + "_System", index + 13, (0, 2)),
                Dependency(root + "_Heizsystem", index + 294, (1, 2, 3, 5)),
                Dependency(root + "_OSchaltuhr", index + 19, (1,)),
            ),
        )
        for room in range(1, 6):
            room_root = f"{root}_Raumthermostat{room}"
            offset = index + 136 + 32 * (room - 1)
            yield WeeklyOwner(
                room_root + "_SchaltuhrNr",
                offset + 29,
                (
                    *conditions,
                    Dependency(room_root + "_Option", offset - 1, (1,)),
                    Dependency(room_root + "_Aktiviert", offset, (1, 2)),
                    Dependency(room_root + "_OSchaltuhr", offset + 1, (1,)),
                    Dependency(room_root + "_Typ", offset + 3, (0,)),
                    RangeDependency(room_root + "_Sensor1_Nr", offset + 14, 1, 65),
                ),
            )


WEEKLY_OWNERS = tuple(_weekly_owners())


def weekly_settings_for(definitions):
    """Only bind schedules against the full exact native ownership layout."""
    for owner in WEEKLY_OWNERS:
        for name, index in owner.expected.items():
            definition = definitions.get(name)
            if (
                definition is None
                or definition.index != index
                or definition.excluded
                or is_sensitive(definition)
            ):
                return ()
        if "WochenschaltuhrNr" not in definitions[owner.name].metadata.get(
            "type_chain", ()
        ):
            return ()
    result = []
    for slot in range(1, 51):
        root = f"Wsu{slot}"
        index = 18401 + 13 * slot
        for offset, (day, label) in enumerate(
            (
                ("Mo", "Monday"),
                ("Di", "Tuesday"),
                ("Mi", "Wednesday"),
                ("Do", "Thursday"),
                ("Fr", "Friday"),
                ("Sa", "Saturday"),
                ("So", "Sunday"),
            )
        ):
            setting = Setting(
                f"{root}_Schaltuhr_Woche_Tag{day}_Daten",
                index + 5 + offset,
                f"Weekly program {slot} {label}",
                "text",
                True,
                installed_by=Dependency(root + "_Belegt", index, (1,)),
                dependencies=(
                    Dependency(
                        root + "_Schaltuhr_Woche_Initialisiert", index + 4, (1,)
                    ),
                ),
                weekly_slot=slot,
            )
            try:
                result.append(bind_setting(setting, definitions))
            except CatalogError:
                continue
    return tuple(result)


# All currently independently corroborated basic-menu settings that resolve
# on the reference controller. Expand the profile from evidence, not by
# treating every readable parameter as writable. Schema, topology, counters,
# clocks/date, safety and dependent thermal parameters are not granted by
# these contracts. The generic writer below is not limited to this group.
BX_PLUS_SETTINGS = (
    Setting("Display_Invertiert", 18260, "Invert controller display", "switch", True),
    Setting(
        "AutoSommerWinterzeit", 56, "Automatic daylight saving time", "switch", True
    ),
    Setting(
        "TemperatureHysteresisSelector",
        60,
        "Display temperature unit",
        "select",
        True,
        (("°C", 0), ("°F", 1)),
    ),
    Setting(
        "FlowSelector",
        61,
        "Display flow unit",
        "select",
        True,
        (("Litre", 0), ("Gallon", 1)),
    ),
    Setting(
        "PressureSelector",
        62,
        "Display pressure unit",
        "select",
        True,
        (("bar", 0), ("psi", 1)),
    ),
    Setting(
        "EnergySelector",
        63,
        "Display energy unit",
        "select",
        True,
        (("kWh", 0), ("MBTU", 1)),
    ),
)


# Only change logging preferences on a positively observed usable SD card.
# The native controller owns logging, storage and any actual file operations.
_SD_CARD_PRESENT = Dependency("SDKarte_Status_KarteVorhanden", 18227, (1,))
_SD_CARD_USABLE = (
    Dependency("SDKarte_Status_Schreibfehler", 18228, (0,)),
    Dependency("SDKarte_Status_KarteUnbekannt", 18229, (0,)),
    Dependency("SDKarte_Status_KarteUnformatiert", 18230, (0,)),
    Dependency("SDKarte_Status_KarteVoll", 18231, (0,)),
    Dependency("SDKarte_Status_KarteSchreibgeschuetzt", 18232, (0,)),
)
BX_PLUS_LOGGING_SETTINGS = (
    Setting(
        "SDKarte_Logging_Intervall",
        18234,
        "SD card logging interval",
        "number",
        True,
        limits=("1", "1200", "1", "1"),
        unit="s",
        installed_by=_SD_CARD_PRESENT,
        dependencies=_SD_CARD_USABLE,
    ),
    Setting(
        "SDKarte_Logging_Methode",
        18235,
        "SD card logging method",
        "select",
        True,
        options=(("Linear", 0), ("Cyclic (overwrite oldest)", 1)),
        installed_by=_SD_CARD_PRESENT,
        dependencies=_SD_CARD_USABLE,
    ),
)


# RESOL BX Plus manual (firmware 2.03, Arrangement / Mixer, interval
# 1..20 s), exact RPT 2.08 layout and MIT optimizer's Mischer type rule.
# Generate the same contract for every firmware slot, not per-site settings.
# The actual vendor archive test checks every target, selector and ordinal.
# A historical Type observation only reduces polling; live Type and activity
# are mandatory before registration and before each write.
BX_PLUS_MIXER_SETTINGS = tuple(
    Setting(
        f"Anlage_Wf{number}_Mischer_dtIntervall",
        6625 + offset,
        f"Arrangement function {number} mixer interval",
        "number",
        True,
        limits=("1", "20", "1", "1"),
        unit="s",
        dependencies=(
            Dependency(f"Anlage_Wf{number}_Aktiviert_Modus", 6566 + offset, (1,)),
        ),
        installed_by=Dependency(f"Anlage_Wf{number}_Type", 6559 + offset, (2,)),
    )
    for number in range(1, 17)
    for offset in ((number - 1) * 251,)
)


# BX Plus manual from firmware 2.03, HQM alternative unit/Unit/Factor:
# boolean, four exact RPT enum ordinals and 7-decimal conversion factor.
# The vendor factor uses a special type (not generic Number), with quant=1
# raw unit and store/display factor 1e-7. Verify all slots against exact
# 2.08 metadata in CI. Never change meter assignment, counter or fluid.
# RESOL VSF 20240922 (blob 4b3058e2): BX Plus HQM #n = 0x7130 + n.
# Field evidence in issues #8/#10 corroborates Wmz1 / HQM #1.
def _hqm_display_settings(number):
    prefix = f"Wmz{number}"
    offset = (number - 1) * 133
    installed = Dependency(f"{prefix}_Type", 17260 + offset, (1,))
    active = Dependency(f"{prefix}_Aktiviert_Modus", 17263 + offset, (1,))
    enabled = Dependency(f"{prefix}_OAlternativAnzeige", 17273 + offset, (1,))
    unit = Dependency(
        f"{prefix}_AlternativAnzeigeEinheit", 17274 + offset, (0, 1, 2, 3)
    )
    factor = RangeDependency(
        f"{prefix}_AlternativAnzeigeFaktor", 17275 + offset, 1, 1000000000
    )
    return (
        Setting(
            enabled.name,
            enabled.index,
            f"HQM {number} alternative display",
            "switch",
            True,
            dependencies=(active, unit, factor),
            installed_by=installed,
            owner_address=0x7130 + number,
        ),
        Setting(
            unit.name,
            unit.index,
            f"HQM {number} alternative display unit",
            "select",
            True,
            (("CO2", 0), ("Oil", 1), ("Gas", 2), ("Coal", 3)),
            dependencies=(active, enabled, factor),
            installed_by=installed,
            owner_address=0x7130 + number,
        ),
        Setting(
            factor.name,
            factor.index,
            f"HQM {number} alternative display conversion factor",
            "number",
            True,
            limits=("0.0000001", "100", "0.0000001", "0.0000001"),
            dependencies=(active, enabled, unit),
            installed_by=installed,
            owner_address=0x7130 + number,
        ),
    )


BX_PLUS_HQM_SETTINGS = tuple(
    setting for number in range(1, 8) for setting in _hqm_display_settings(number)
)


# Heating curve is available only in the two documented heating-system modes
# (Kennlinie and Raumeinfluss), not merely when a circuit exists.
BX_PLUS_HEATING_SETTINGS = tuple(
    Setting(
        f"Heizung_Heizkreis{number}_Heizkurve",
        13698 + offset,
        f"Heating circuit {number} heating curve",
        "number",
        True,
        limits=("0.3", "3", "0.1", "0.1"),
        dependencies=(
            Dependency(
                f"Heizung_Heizkreis{number}_Aktiviert_Modus", 13689 + offset, (1,)
            ),
            Dependency(f"Heizung_Heizkreis{number}_Heizsystem", 13978 + offset, (1, 5)),
        ),
        installed_by=Dependency(
            f"Heizung_Heizkreis{number}_Type", 13684 + offset, (1,)
        ),
    )
    for number in range(1, 8)
    for offset in ((number - 1) * 495,)
)


# BX Plus firmware 2.03 manual, Output submenu, p. 16: relative pump
# minimum and maximum are 20..100% when speed control is enabled.
# 2.08 exact RPT establishes names, indices and native speed flags.
# This is adjustment of the already-enabled native output, not an inferred
# pump assignment. Explicit contrary role evidence still blocks adjustment.
# Keep output assignment, signal type, protection and manual mode unchanged.
PUMP_ROLES = (
    (2, "Kollektorpumpe"),
    (4, "Primaerpumpe"),
    (5, "Sekundaerpumpe"),
    (6, "Speicherpumpe"),
    (9, "BypassPumpe"),
    (11, "ExtWTPrimaerpumpe"),
    (12, "ExtWTSekundaerpumpe"),
    (16, "Zwillingspumpe"),
    (24, "AnlageWaermeaustausch"),
    (25, "AnlageFeststoffkessel"),
    (34, "HeizungThDesinfektionUmwaelzpumpe"),
    (36, "HeizungThBwErwaermungLadepumpe"),
    (37, "HeizungBwVorerwaermungPumpe"),
    (39, "HeizungHeizkreisPumpe"),
    (44, "HeizungHeizkreisNachheizungLadepumpe"),
    (64, "AnlageWTUmwaelzpumpe"),
    (67, "HeizungRelaisPumpe"),
)


def _pump_settings(prefix, address, first_mode_index):
    for number in range(1, 6):
        name = f"{prefix}_R{number}"
        mode_index = first_mode_index + 14 * (number - 1)
        installed = Dependency(
            f"{name}_AktorFunktion",
            mode_index - 6,
            tuple(raw for raw, _ in PUMP_ROLES),
        )
        speed = Dependency(f"{name}_ODrehzahl", mode_index - 4, (1,))
        automatic = Dependency(f"{name}_Handbetrieb", mode_index, (2,))
        for label, suffix, index, other_suffix, other_index, operation in (
            (
                "minimum",
                "MinDrehzahl",
                mode_index - 3,
                "MaxDrehzahl",
                mode_index - 2,
                "le",
            ),
            (
                "maximum",
                "MaxDrehzahl",
                mode_index - 2,
                "MinDrehzahl",
                mode_index - 3,
                "ge",
            ),
        ):
            other = f"{name}_{other_suffix}"
            yield Setting(
                f"{name}_{suffix}",
                index,
                f"Relay {number} pump {label} speed",
                "number",
                True,
                limits=("20", "100", "1", "1"),
                unit="%",
                dependencies=(
                    speed,
                    automatic,
                    RangeDependency(other, other_index, 20, 100),
                ),
                owner_address=address,
                relative_constraints=(
                    RelativeConstraint(other, other_index, operation),
                ),
                optional_role=installed,
                owner_relay=number,
            )


BX_PLUS_PUMP_SETTINGS = tuple(
    setting
    for prefix, address, index in (
        ("Relais_Regler", 0x7112, 1977),
        ("Relais_Modul1", 0x6651, 2173),
        ("Relais_Modul2", 0x6652, 2243),
    )
    for setting in _pump_settings(prefix, address, index)
)


# Documented adjustable differential parameters (RESOL BX Plus manual,
# Arrangement: heat exchange, solid fuel boiler, return preheating;
# exact RPT 2.08 bounds supersede earlier printed firmware ranges).
# The MIT BaseConfigurationOptimizer identifies the compound parent as
# adjustable. Require independently resolved parent AND selected child,
# matching live values, exact installed type, active function and ordering.
# Operational source/sink thresholds are separate from protection limits.
# Keep certified protection, topology and output reassignment read-only.
# Two selector variants use the same logical entity ID and parent index.
DIFFERENTIAL_FAMILIES = (
    (
        "Waermeaustausch",
        5,
        6639,
        ("1", "30", "0.5"),
        ("0.5", "29.5", "0.5"),
        ("1.5", "40", "0.5"),
    ),
    (
        "Feststoffkessel",
        6,
        6663,
        ("2", "30", "1"),
        ("1", "29", "1"),
        ("3", "40", "1"),
    ),
    (
        "Ruecklaufanhebung",
        8,
        6695,
        ("2", "30", "0.5"),
        ("1", "29", "0.5"),
        None,
    ),
    (
        "Funktionsblock",
        9,
        6707,
        ("1", "50", "0.5"),
        ("0.5", "49.5", "0.5"),
        ("2", "100", "1"),
    ),
)


def _differential_settings():
    for slot in range(1, 17):
        offset = (slot - 1) * 251
        root = f"Anlage_Wf{slot}"
        for kind, ordinal, start, on, off, target in DIFFERENTIAL_FAMILIES:
            prefix = f"{root}_{kind}"
            installed = Dependency(f"{root}_Type", 6559 + offset, (ordinal,))
            active = Dependency(f"{root}_Aktiviert_Modus", 6566 + offset, (1, 2))
            extra = (
                (Dependency(f"{prefix}_ODifferenz", 6704 + offset, (1,)),)
                if ordinal == 9
                else ()
            )
            entries = tuple(
                (f"{prefix}_{suffix}", start + offset + delta, limits, label)
                for suffix, delta, limits, label in (
                    ("dTEin", 0, on, "switch-on differential"),
                    ("dTAus", 3, off, "switch-off differential"),
                    ("dTSoll", 6, target, "target differential"),
                )
                if limits is not None
            )
            for selector, suffix, unit, multiplier in (
                (0, "_K", "K", 1),
                (1, "_R", "°R", 2),
            ):
                mirrors = tuple(
                    (name, index, name + suffix, index + selector + 1)
                    for name, index, _, _ in entries
                )
                for position, (name, index, bounds, label) in enumerate(entries):
                    constraints = []
                    for other_position, (other, other_index, _, _) in enumerate(
                        entries
                    ):
                        if position == other_position:
                            continue
                        # on > off; target > on and off.
                        rank = (1, 0, 2)
                        constraints.append(
                            RelativeConstraint(
                                other,
                                other_index,
                                "gt" if rank[position] > rank[other_position] else "lt",
                            )
                        )
                    limits = tuple(
                        str(Decimal(bound) * multiplier) for bound in bounds
                    ) + ("0.1",)
                    yield Setting(
                        name,
                        index,
                        f"Arrangement function {slot} {kind} {label}",
                        "number",
                        True,
                        limits=limits,
                        unit=unit,
                        dependencies=(
                            active,
                            *extra,
                            Dependency(
                                "TemperatureHysteresisSelector", 60, (selector,)
                            ),
                        ),
                        relative_constraints=tuple(constraints),
                        installed_by=installed,
                        compound_mirrors=mirrors,
                        peer_limits=tuple(
                            (
                                other,
                                tuple(str(Decimal(v) * multiplier) for v in bounds)
                                + ("0.1",),
                            )
                            for other, _, bounds, _ in entries
                            if other != name
                        ),
                    )


BX_PLUS_DIFFERENTIAL_SETTINGS = tuple(_differential_settings())


def _transfer_temperature_settings():
    """Native operational thresholds, rise and enabled target temperature.

    BX Plus manual 2.03, pp. 17 and 57–58: source minimum and sink maximum
    refer to different sensors; they are not a same-sensor ordered pair.
    Exact 2.08 menu and selected aliases establish encoding. No topology
    changes, boiler-protection overrides or option activation are exposed.
    """
    for slot in range(1, 17):
        offset = (slot - 1) * 251
        root = f"Anlage_Wf{slot}"
        for family, ordinal, maximum, minimum, rise, c_low, f_low in (
            ("Waermeaustausch", 5, 6651, 6654, 6660, "10", "50"),
            ("Feststoffkessel", 6, 6672, 6675, 6678, "4", "40"),
        ):
            prefix = f"{root}_{family}"
            installed = Dependency(f"{root}_Type", 6559 + offset, (ordinal,))
            activity = Dependency(f"{root}_Aktiviert_Modus", 6566 + offset, (1, 2))
            entries = [
                ("TMin", minimum, "source minimum temperature", c_low, f_low),
                ("TMax", maximum, "sink maximum temperature", c_low, f_low),
            ]
            if ordinal == 6:
                entries.append(
                    ("Zieltemperatur_TZiel", 6682, "target temperature", "30", "86")
                )
            if ordinal == 5:
                name = f"{prefix}_HMinMax"
                index = 6657 + offset
                for selector, suffix, unit, bounds in (
                    (0, "_K", "K", ("0.5", "10", "0.5", "0.1")),
                    (1, "_R", "°R", ("1", "20", "1", "0.1")),
                ):
                    yield Setting(
                        name,
                        index,
                        f"Arrangement function {slot} heat exchange hysteresis",
                        "number",
                        True,
                        limits=bounds,
                        unit=unit,
                        dependencies=(
                            activity,
                            Dependency(
                                "TemperatureHysteresisSelector", 60, (selector,)
                            ),
                            RangeDependency(f"{root}_Sensor1_Nr", 6576 + offset, 1, 65),
                            RangeDependency(f"{root}_Sensor2_Nr", 6580 + offset, 1, 65),
                            RangeDependency(
                                f"{root}_Relais1_Nr", 6592 + offset, 1, 100
                            ),
                        ),
                        installed_by=installed,
                        compound_mirrors=(
                            (name, index, name + suffix, index + selector + 1),
                        ),
                    )
            for selector, suffix, unit, low, step in (
                (0, "_C", "°C", 0, "1"),
                (1, "_F", "°F", 1, "2"),
            ):
                selected = Dependency("TemperatureHysteresisSelector", 60, (selector,))
                for name, index, label, c_min, f_min in entries:
                    name = f"{prefix}_{name}"
                    index += offset
                    target = name.endswith("_TZiel")
                    extra = (
                        (Dependency(f"{prefix}_OZieltemperatur", 6681 + offset, (1,)),)
                        if target
                        else ()
                    )
                    upper = ("85", "186") if target else ("95", "204")
                    yield Setting(
                        name,
                        index,
                        f"Arrangement function {slot} {family} {label}",
                        "number",
                        True,
                        limits=((c_min, f_min)[low], upper[low], step, "0.1"),
                        unit=unit,
                        dependencies=(activity, selected, *extra),
                        installed_by=installed,
                        compound_mirrors=(
                            (name, index, name + suffix, index + selector + 1),
                        ),
                    )
                name = f"{prefix}_Anstieg"
                index = rise + offset
                bounds = (
                    ("1", "20", "0.5", "0.1")
                    if selector == 0
                    else ("2", "40", "1", "0.1")
                )
                yield Setting(
                    name,
                    index,
                    f"Arrangement function {slot} {family} modulation rise",
                    "number",
                    True,
                    limits=bounds,
                    unit="K" if selector == 0 else "°R",
                    dependencies=(activity, selected),
                    installed_by=installed,
                    compound_mirrors=(
                        (
                            name,
                            index,
                            name + ("_K" if selector == 0 else "_R"),
                            index + selector + 1,
                        ),
                    ),
                )


BX_PLUS_TRANSFER_SETTINGS = tuple(_transfer_temperature_settings())


def _solid_fuel_mixer_settings():
    """Adjust an already-configured mixer without changing topology."""
    for slot in range(1, 17):
        offset = (slot - 1) * 251
        root = f"Anlage_Wf{slot}"
        prefix = f"{root}_Feststoffkessel"
        installed = Dependency(f"{root}_Type", 6559 + offset, (6,))
        deps = (
            Dependency(f"{root}_Aktiviert_Modus", 6566 + offset, (1, 2)),
            Dependency(f"{prefix}_OMischer", 6685 + offset, (1,)),
        )
        yield Setting(
            f"{prefix}_Mischer_dtIntervall",
            6692 + offset,
            f"Arrangement function {slot} solid fuel mixer interval",
            "number",
            True,
            limits=("1", "20", "1", "1"),
            unit="s",
            dependencies=deps,
            installed_by=installed,
        )
        # Open and close use their own differential thresholds; read the
        # selected alias and require the exact firmware engineering bounds.
        for selector, suffix, unit, opened, closed in (
            (0, "_K", "K", ("0.5", "30", "0.5"), ("0", "29.5", "0.5")),
            (1, "_R", "°R", ("1", "60", "1"), ("1", "59", "1")),
        ):
            for name, index, bounds, label in (
                ("dTAuf", 6686, opened, "open"),
                ("dTZu", 6689, closed, "close"),
            ):
                name = f"{prefix}_Mischer_{name}"
                index += offset
                yield Setting(
                    name,
                    index,
                    f"Arrangement function {slot} solid fuel mixer {label} differential",
                    "number",
                    True,
                    limits=(*bounds, "0.1"),
                    unit=unit,
                    dependencies=(
                        *deps,
                        Dependency("TemperatureHysteresisSelector", 60, (selector,)),
                    ),
                    installed_by=installed,
                    compound_mirrors=(
                        (name, index, name + suffix, index + selector + 1),
                    ),
                )


BX_PLUS_SOLID_FUEL_MIXER_SETTINGS = tuple(_solid_fuel_mixer_settings())


def _thermostat_settings():
    """Existing function-block thermostats; preserve heating/cooling direction."""
    for slot in range(1, 17):
        offset = (slot - 1) * 251
        root = f"Anlage_Wf{slot}"
        prefix = f"{root}_Funktionsblock"
        installed = Dependency(f"{root}_Type", 6559 + offset, (9,))
        active = Dependency(f"{root}_Aktiviert_Modus", 6566 + offset, (1, 2))
        for thermostat in (1, 2):
            start = 6716 + offset + (thermostat - 1) * 6
            enabled = Dependency(
                f"{prefix}_OThermostat{thermostat}", 6704 + offset + thermostat, (1,)
            )
            on = f"{prefix}_TTh{thermostat}Ein"
            off = f"{prefix}_TTh{thermostat}Aus"
            for selector, suffix, unit, limits in (
                (0, "_C", "°C", ("-40", "250", "1", "0.1")),
                (1, "_F", "°F", ("-40", "482", "2", "0.1")),
            ):
                mirrors = (
                    (on, start, on + suffix, start + selector + 1),
                    (off, start + 3, off + suffix, start + selector + 4),
                )
                for name, index, other, other_index, label in (
                    (on, start, off, start + 3, "on"),
                    (off, start + 3, on, start, "off"),
                ):
                    yield Setting(
                        name,
                        index,
                        f"Function block {slot} thermostat {thermostat} {label}",
                        "number",
                        True,
                        limits=limits,
                        unit=unit,
                        dependencies=(
                            active,
                            enabled,
                            Dependency(
                                "TemperatureHysteresisSelector", 60, (selector,)
                            ),
                        ),
                        relative_constraints=(
                            RelativeConstraint(other, other_index, "preserve_order"),
                        ),
                        installed_by=installed,
                        compound_mirrors=mirrors,
                        peer_limits=((other, limits),),
                    )


BX_PLUS_THERMOSTAT_SETTINGS = tuple(_thermostat_settings())


def _timing_settings():
    """Native durations from documented adjustment channels, not raw timers.

    Shared weekly schedule blobs are intentionally not treated as durations.
    Keep topology, enable flags, safety timing and manual heating unchanged.
    """
    for slot in range(1, 17):
        ao = (slot - 1) * 251
        ho = (slot - 1) * 151
        so = (slot - 1) * 120
        arrangement = f"Anlage_Wf{slot}"
        heating = f"Heizung_Wf{slot}"
        solar = f"Solar_Wf{slot}"
        for suffix, index, flag, flag_index, label in (
            ("dtNachlauf", 6622, "ONachlauf", 6621, "overrun"),
            ("dtVerzoegerung", 6624, "OVerzoegerung", 6623, "delay"),
        ):
            yield Setting(
                f"{arrangement}_Parallelrelais_{suffix}",
                index + ao,
                f"Arrangement function {slot} parallel relay {label}",
                "number",
                True,
                limits=("1", "30", "1", "1"),
                unit="min",
                dependencies=(
                    Dependency(f"{arrangement}_Aktiviert_Modus", 6566 + ao, (1, 2)),
                    Dependency(
                        f"{arrangement}_Parallelrelais_{flag}", flag_index + ao, (1,)
                    ),
                    ActorReferences(
                        arrangement + "_Relais1_Nr",
                        6592 + ao,
                        ((arrangement + "_Bezugsrelais1_Nr", 6616 + ao),),
                    ),
                ),
                installed_by=Dependency(f"{arrangement}_Type", 6559 + ao, (1,)),
            )
        yield Setting(
            f"{arrangement}_Funktionsblock_OMindestausschaltzeit",
            6743 + ao,
            f"Arrangement function {slot} restart delay enabled",
            "switch",
            True,
            installed_by=Dependency(f"{arrangement}_Type", 6559 + ao, (9,)),
            dependencies=(
                AnyDependencies(
                    (
                        (
                            Dependency(
                                f"{arrangement}_Aktiviert_Modus", 6566 + ao, (1,)
                            ),
                        ),
                        (
                            Dependency(
                                f"{arrangement}_Aktiviert_Modus", 6566 + ao, (2,)
                            ),
                            RangeDependency(
                                f"{arrangement}_Aktiviert_SensorSchalter_Nr",
                                6568 + ao,
                                1,
                                65,
                            ),
                        ),
                    )
                ),
                RangeDependency(f"{arrangement}_Relais1_Nr", 6592 + ao, 1, 100),
            ),
            requested_conditions=(
                RequestedCondition(
                    1,
                    (
                        RangeDependency(
                            f"{arrangement}_Funktionsblock_dtMindestausschaltzeit",
                            6744 + ao,
                            1,
                            1439,
                        ),
                    ),
                ),
            ),
        )
        yield Setting(
            f"{arrangement}_Funktionsblock_dtMindestausschaltzeit",
            6744 + ao,
            f"Arrangement function {slot} restart delay",
            "number",
            True,
            limits=("1", "1439", "1", "1"),
            unit="min",
            installed_by=Dependency(f"{arrangement}_Type", 6559 + ao, (9,)),
            dependencies=(
                Dependency(f"{arrangement}_Aktiviert_Modus", 6566 + ao, (1, 2)),
                Dependency(
                    f"{arrangement}_Funktionsblock_OMindestausschaltzeit",
                    6743 + ao,
                    (1,),
                ),
                RangeDependency(f"{arrangement}_Relais1_Nr", 6592 + ao, 1, 100),
            ),
        )
        # The optional overrun is a separate Boolean preference of the
        # installed DHW function, not a command to start the loading pump.
        yield Setting(
            f"{heating}_ThBwErwaermung_ONachlaufzeit",
            11376 + ho,
            f"Heating function {slot} DHW loading pump overrun enabled",
            "switch",
            True,
            installed_by=Dependency(f"{heating}_Type", 11266 + ho, (2,)),
            dependencies=(
                Dependency(f"{heating}_ThBwErwaermung_OLadepumpe", 11375 + ho, (1,)),
                RangeDependency(f"{heating}_Relais2_Nr", 11329 + ho, 1, 100),
                RangeDependency(f"{heating}_NhRelais1_Nr", 11337 + ho, 1, 100),
                RangeDependency(f"{heating}_Sensor1_Nr", 11290 + ho, 1, 65),
                AnyDependencies(
                    (
                        (Dependency(f"{heating}_Aktiviert_Modus", 11273 + ho, (1,)),),
                        (
                            Dependency(f"{heating}_Aktiviert_Modus", 11273 + ho, (2,)),
                            RangeDependency(
                                f"{heating}_Aktiviert_SensorSchalter_Nr",
                                11275 + ho,
                                1,
                                65,
                            ),
                        ),
                    )
                ),
                AnyDependencies(
                    (
                        (
                            Dependency(
                                f"{heating}_ThBwErwaermung_Modus", 11378 + ho, (0,)
                            ),
                        ),
                        (
                            Dependency(
                                f"{heating}_ThBwErwaermung_Modus", 11378 + ho, (1,)
                            ),
                            RangeDependency(f"{heating}_Sensor2_Nr", 11294 + ho, 1, 65),
                        ),
                    )
                ),
            ),
            requested_conditions=(
                RequestedCondition(
                    1,
                    (
                        RangeDependency(
                            f"{heating}_ThBwErwaermung_Nachlaufzeit", 11377 + ho, 1, 10
                        ),
                    ),
                ),
            ),
        )
        yield Setting(
            f"{heating}_ThBwErwaermung_Nachlaufzeit",
            11377 + ho,
            f"Heating function {slot} DHW loading pump overrun",
            "number",
            True,
            limits=("1", "10", "1", "1"),
            unit="min",
            dependencies=(
                Dependency(f"{heating}_Aktiviert_Modus", 11273 + ho, (1, 2)),
                Dependency(f"{heating}_ThBwErwaermung_OLadepumpe", 11375 + ho, (1,)),
                Dependency(f"{heating}_ThBwErwaermung_ONachlaufzeit", 11376 + ho, (1,)),
            ),
            installed_by=Dependency(f"{heating}_Type", 11266 + ho, (2,)),
        )
        for suffix, index, bounds, unit, label in (
            ("dtLaufzeit", 11406, ("60", "900", "15", "1"), "s", "runtime"),
            ("dtPausenzeit", 11407, ("10", "60", "1", "1"), "min", "break time"),
            ("dtVerzoegerung", 11414, ("0", "3", "1", "1"), "s", "demand delay"),
        ):
            yield Setting(
                f"{heating}_Zirkulation_{suffix}",
                index + ho,
                f"Heating function {slot} circulation {label}",
                "number",
                True,
                limits=bounds,
                unit=unit,
                dependencies=(
                    Dependency(f"{heating}_Aktiviert_Modus", 11273 + ho, (1, 2)),
                    Dependency(f"{heating}_Zirkulation_Variante", 11405 + ho, (3, 4)),
                ),
                installed_by=Dependency(f"{heating}_Type", 11266 + ho, (4,)),
            )
        yield Setting(
            f"{solar}_Zwillingspumpe_Laufzeit",
            4465 + so,
            f"Solar function {slot} twin pump runtime",
            "number",
            True,
            limits=("1", "48", "1", "1"),
            unit="h",
            dependencies=(Dependency(f"{solar}_Aktiviert_Modus", 4415 + so, (1, 2)),),
            installed_by=Dependency(f"{solar}_Type", 4409 + so, (11,)),
        )


def _reference_settings():
    """Native reference-dependent options, never the assignments themselves."""
    for slot in range(1, 17):
        ao = (slot - 1) * 251
        root = f"Anlage_Wf{slot}"
        active = AnyDependencies(
            (
                (Dependency(root + "_Aktiviert_Modus", 6566 + ao, (1,)),),
                (
                    Dependency(root + "_Aktiviert_Modus", 6566 + ao, (2,)),
                    RangeDependency(
                        root + "_Aktiviert_SensorSchalter_Nr", 6568 + ao, 1, 65
                    ),
                ),
            )
        )
        parallel = ActorReferences(
            root + "_Relais1_Nr",
            6592 + ao,
            ((root + "_Bezugsrelais1_Nr", 6616 + ao),),
        )
        for suffix, index, duration, label in (
            ("ONachlauf", 6621, "dtNachlauf", "overrun"),
            ("OVerzoegerung", 6623, "dtVerzoegerung", "delay"),
        ):
            prefix = root + "_Parallelrelais"
            yield Setting(
                prefix + "_" + suffix,
                index + ao,
                f"Arrangement function {slot} parallel relay {label} enabled",
                "switch",
                True,
                installed_by=Dependency(root + "_Type", 6559 + ao, (1,)),
                dependencies=(active, parallel),
                requested_conditions=(
                    RequestedCondition(
                        1,
                        (
                            RangeDependency(
                                prefix + "_" + duration, index + ao + 1, 1, 30
                            ),
                        ),
                    ),
                ),
            )
        prefix = root + "_Funktionsblock"
        references = ActorReferences(
            root + "_Relais1_Nr",
            6592 + ao,
            tuple(
                (f"{prefix}_Bezugsrelais{number}_Nr", 6736 + ao + number)
                for number in range(1, 6)
            ),
        )
        installed = Dependency(root + "_Type", 6559 + ao, (9,))
        flag = Dependency(prefix + "_OBezugsrelais", 6735 + ao, (1,))
        mode = Dependency(prefix + "_Bezugsrelais_Modus", 6736 + ao, (0, 1, 2, 3))
        yield Setting(
            flag.name,
            flag.index,
            f"Arrangement function {slot} reference logic enabled",
            "switch",
            True,
            installed_by=installed,
            dependencies=(active, references),
            requested_conditions=(RequestedCondition(1, (mode,)),),
        )
        yield Setting(
            mode.name,
            mode.index,
            f"Arrangement function {slot} reference logic",
            "select",
            True,
            options=(("OR", 0), ("AND", 1), ("NOR", 2), ("NAND", 3)),
            installed_by=installed,
            dependencies=(active, references, flag),
        )


BX_PLUS_REFERENCE_SETTINGS = tuple(_reference_settings())


BX_PLUS_TIMING_SETTINGS = tuple(_timing_settings())


def _loading_temperature_settings():
    """Documented store-zone and DHW thermostat pairs for all firmware slots.

    The 2.03 manual describes the adjustable on/off temperature channels;
    exact 2.08 RPT reference positions, ordinal dependencies and independent
    Celsius/Fahrenheit limits are checked for every slot in the archive test.
    This changes neither the installed function nor its output assignment.
    """
    for slot in range(1, 17):
        for (
            family,
            kind,
            type_index,
            activity_index,
            on,
            on_index,
            off,
            off_index,
            extra,
        ) in (
            (
                f"Anlage_Wf{slot}",
                3,
                6559 + (slot - 1) * 251,
                6566 + (slot - 1) * 251,
                f"Anlage_Wf{slot}_Boilerladung_TBoilerEin",
                6632 + (slot - 1) * 251,
                f"Anlage_Wf{slot}_Boilerladung_TBoilerAus",
                6635 + (slot - 1) * 251,
                (),
            ),
            (
                f"Heizung_Wf{slot}",
                2,
                11266 + (slot - 1) * 151,
                11273 + (slot - 1) * 151,
                f"Heizung_Wf{slot}_ThBwErwaermung_TEin",
                11379 + (slot - 1) * 151,
                f"Heizung_Wf{slot}_ThBwErwaermung_TAus",
                11382 + (slot - 1) * 151,
                (
                    Dependency(
                        f"Heizung_Wf{slot}_ThBwErwaermung_Modus",
                        11378 + (slot - 1) * 151,
                        (0, 1),
                    ),
                ),
            ),
        ):
            installed = Dependency(f"{family}_Type", type_index, (kind,))
            activity = Dependency(f"{family}_Aktiviert_Modus", activity_index, (1, 2))
            for selector, suffix, unit, low, high in (
                (0, "_C", "°C", ("0", "94", "1", "0.1"), ("1", "95", "1", "0.1")),
                (1, "_F", "°F", ("32", "202", "2", "0.1"), ("34", "204", "2", "0.1")),
            ):
                mirrors = (
                    (on, on_index, on + suffix, on_index + selector + 1),
                    (off, off_index, off + suffix, off_index + selector + 1),
                )
                for (
                    name,
                    index,
                    other,
                    other_index,
                    limits,
                    other_limits,
                    operation,
                ) in (
                    (on, on_index, off, off_index, low, high, "lt"),
                    (off, off_index, on, on_index, high, low, "gt"),
                ):
                    yield Setting(
                        name,
                        index,
                        f"{family} loading {'on' if operation == 'lt' else 'off'} temperature",
                        "number",
                        True,
                        limits=limits,
                        unit=unit,
                        dependencies=(
                            activity,
                            *extra,
                            Dependency(
                                "TemperatureHysteresisSelector", 60, (selector,)
                            ),
                        ),
                        installed_by=installed,
                        relative_constraints=(
                            RelativeConstraint(other, other_index, operation),
                        ),
                        compound_mirrors=mirrors,
                        peer_limits=((other, other_limits),),
                    )


BX_PLUS_LOADING_SETTINGS = tuple(_loading_temperature_settings())


def _operating_pair(
    on,
    on_index,
    off,
    off_index,
    label,
    installed,
    dependencies,
    variants,
    *,
    rising,
    unit_selector=("TemperatureHysteresisSelector", 60),
):
    """Reuse a single selected-unit, ordered operating-pair contract."""
    for selector, alias, unit, on_limits, off_limits in variants:
        mirrors = (
            (on, on_index, on + alias, on_index + selector + 1),
            (off, off_index, off + alias, off_index + selector + 1),
        )
        for name, index, other, other_index, limits, peer, direction in (
            (on, on_index, off, off_index, on_limits, off_limits, rising),
            (off, off_index, on, on_index, off_limits, on_limits, not rising),
        ):
            yield Setting(
                name,
                index,
                f"{label} {'on' if name == on else 'off'}",
                "number",
                True,
                limits=limits,
                unit=unit,
                installed_by=installed,
                dependencies=(
                    *dependencies,
                    Dependency(*unit_selector, (selector,)),
                ),
                relative_constraints=(
                    RelativeConstraint(other, other_index, "gt" if direction else "lt"),
                ),
                compound_mirrors=mirrors,
                peer_limits=((other, peer),),
            )


def _additional_operating_settings():
    """Adjust documented, installed functions; preserve output and safety options."""
    pump = "Anlage_WTUmwaelzpumpe"
    pump_installed = RangeDependency(f"{pump}_Relais_Nr", 10614, 1, 100)
    pump_active = Dependency(f"{pump}_Aktiviert_Modus", 10607, (1, 2))
    for suffix, index, limits, unit, label, extra in (
        ("SollDrehzahl", 10646, ("20", "100", "1", "1"), "%", "speed", ()),
        (
            "Nachlaufzeit",
            10648,
            ("0", "60", "1", "1"),
            "s",
            "overrun",
            (Dependency(f"{pump}_ONachlauf", 10647, (1,)),),
        ),
        (
            "RuhemodusDauer",
            10651,
            ("0.5", "24", "0.5", "0.1"),
            "h",
            "idle duration",
            (Dependency(f"{pump}_ORuhemodus", 10650, (1,)),),
        ),
    ):
        yield Setting(
            f"{pump}_{suffix}",
            index,
            f"Heat exchanger circulation pump {label}",
            "number",
            True,
            limits=limits,
            unit=unit,
            installed_by=pump_installed,
            dependencies=(pump_active, *extra),
        )
    for slot in range(1, 17):
        ao = (slot - 1) * 251
        ho = (slot - 1) * 151
        arrangement = f"Anlage_Wf{slot}"
        heating = f"Heizung_Wf{slot}"
        active = Dependency(f"{arrangement}_Aktiviert_Modus", 6566 + ao, (1, 2))
        # The ordinary arrangement mixer target is independent of
        # protective maximum boiler return temperature (Type 11).
        name = f"{arrangement}_Mischer_TZiel"
        index = 6629 + ao
        for selector, alias, unit, limits in (
            (0, "_C", "°C", ("0", "130", "1", "0.1")),
            (1, "_F", "°F", ("32", "266", "2", "0.1")),
        ):
            yield Setting(
                name,
                index,
                f"Arrangement function {slot} mixer target temperature",
                "number",
                True,
                limits=limits,
                unit=unit,
                installed_by=Dependency(f"{arrangement}_Type", 6559 + ao, (2,)),
                dependencies=(
                    active,
                    RangeDependency(f"{arrangement}_Sensor1_Nr", 6576 + ao, 1, 65),
                    RangeDependency(f"{arrangement}_Relais1_Nr", 6592 + ao, 1, 100),
                    RangeDependency(f"{arrangement}_Relais2_Nr", 6600 + ao, 1, 100),
                    Dependency("TemperatureHysteresisSelector", 60, (selector,)),
                ),
                compound_mirrors=((name, index, name + alias, index + selector + 1),),
            )
        # BX Plus manual pp. 60-61: return mixing uses three temperature
        # sensors and two mixer outputs. The operating thresholds are
        # independent of automatic adjustment time and boiler maximum.
        prefix = f"{arrangement}_RLBeimischung"
        installed = Dependency(f"{arrangement}_Type", 6559 + ao, (11,))
        mixing_deps = (
            active,
            RangeDependency(f"{arrangement}_Sensor1_Nr", 6576 + ao, 1, 65),
            RangeDependency(f"{arrangement}_Sensor2_Nr", 6580 + ao, 1, 65),
            RangeDependency(f"{arrangement}_Sensor3_Nr", 6584 + ao, 1, 65),
            RangeDependency(f"{arrangement}_Relais1_Nr", 6592 + ao, 1, 100),
            RangeDependency(f"{arrangement}_Relais2_Nr", 6600 + ao, 1, 100),
        )
        yield from _operating_pair(
            f"{prefix}_dTEin",
            6759 + ao,
            f"{prefix}_dTAus",
            6762 + ao,
            f"Arrangement function {slot} return mixing differential",
            installed,
            mixing_deps,
            (
                (
                    0,
                    "_K",
                    "K",
                    ("1", "25", "0.5", "0.1"),
                    ("0.5", "24", "0.5", "0.1"),
                ),
                (1, "_R", "°R", ("2", "50", "1", "0.1"), ("1", "48", "1", "0.1")),
            ),
            rising=True,
        )
        name = f"{prefix}_dTSoll"
        index = 6765 + ao
        for selector, alias, unit, limits in (
            (0, "_K", "K", ("-20", "25", "1", "0.1")),
            (1, "_R", "°R", ("-40", "50", "2", "0.1")),
        ):
            yield Setting(
                name,
                index,
                f"Arrangement function {slot} return mixing target differential",
                "number",
                True,
                limits=limits,
                unit=unit,
                installed_by=installed,
                dependencies=(
                    *mixing_deps,
                    Dependency("TemperatureHysteresisSelector", 60, (selector,)),
                ),
                compound_mirrors=((name, index, name + alias, index + selector + 1),),
            )
        for suffix, index, limits, label in (
            ("Mischerlaufzeit", 6771, ("10", "600", "1", "1"), "mixer runtime"),
            ("Mischerstellzeit", 6772, ("1", "20", "1", "1"), "mixer interval"),
        ):
            yield Setting(
                f"{prefix}_{suffix}",
                index + ao,
                f"Arrangement function {slot} return mixing {label}",
                "number",
                True,
                limits=limits,
                unit="s",
                installed_by=installed,
                dependencies=mixing_deps,
            )
        prefix = f"{arrangement}_Funktionsblock"
        name = f"{prefix}_Anstieg"
        index = 6732 + ao
        for selector, alias, unit, limits in (
            (0, "_K", "K", ("0.5", "20", "0.5", "0.1")),
            (1, "_R", "°R", ("1", "40", "1", "0.1")),
        ):
            yield Setting(
                name,
                index,
                f"Arrangement function {slot} function block rise",
                "number",
                True,
                limits=limits,
                unit=unit,
                installed_by=Dependency(f"{arrangement}_Type", 6559 + ao, (9,)),
                dependencies=(
                    active,
                    Dependency(f"{prefix}_ODifferenz", 6704 + ao, (1,)),
                    Dependency("TemperatureHysteresisSelector", 60, (selector,)),
                ),
                compound_mirrors=((name, index, name + alias, index + selector + 1),),
            )
        irradiation = Dependency(f"{arrangement}_Type", 6559 + ao, (10,))
        for suffix, index, bounds, unit, label in (
            ("Einstrahlung", 6756, ("50", "1000", "10", "1"), "W/m²", "threshold"),
            ("dtDauer", 6757, ("0", "30", "1", "1"), "min", "duration"),
        ):
            yield Setting(
                f"{arrangement}_Strahlungsschalter_{suffix}",
                index + ao,
                f"Arrangement function {slot} irradiation {label}",
                "number",
                True,
                limits=bounds,
                unit=unit,
                dependencies=(active,),
                installed_by=irradiation,
            )
        prefix = f"{arrangement}_Ruecklaufanhebung"
        name = f"{prefix}_TSommerAus"
        index = 6701 + ao
        for selector, alias, unit, limits in (
            (0, "_C", "°C", ("10", "60", "1", "0.1")),
            (1, "_F", "°F", ("50", "140", "2", "0.1")),
        ):
            yield Setting(
                name,
                index,
                f"Arrangement function {slot} summer switch-off temperature",
                "number",
                True,
                limits=limits,
                unit=unit,
                dependencies=(
                    active,
                    Dependency(f"{prefix}_OSommerAus", 6694 + ao, (1,)),
                    Dependency("TemperatureHysteresisSelector", 60, (selector,)),
                ),
                installed_by=Dependency(f"{arrangement}_Type", 6559 + ao, (8,)),
                compound_mirrors=((name, index, name + alias, index + selector + 1),),
            )
        active = Dependency(f"{heating}_Aktiviert_Modus", 11273 + ho, (1, 2))
        prefix = f"{heating}_Zirkulation"
        yield from _operating_pair(
            f"{prefix}_TEin",
            11408 + ho,
            f"{prefix}_TAus",
            11411 + ho,
            f"Heating function {slot} circulation temperature",
            Dependency(f"{heating}_Type", 11266 + ho, (4,)),
            (
                active,
                Dependency(f"{prefix}_Variante", 11405 + ho, (0, 2)),
            ),
            (
                (0, "_C", "°C", ("10", "59", "1", "0.1"), ("11", "60", "1", "0.1")),
                (1, "_F", "°F", ("50", "138", "2", "0.1"), ("52", "140", "2", "0.1")),
            ),
            rising=False,
        )
        prefix = f"{heating}_BwVorerwaermung"
        installed = Dependency(f"{heating}_Type", 11266 + ho, (3,))
        yield from _operating_pair(
            f"{prefix}_dTEin",
            11399 + ho,
            f"{prefix}_dTAus",
            11402 + ho,
            f"Heating function {slot} DHW preheat differential",
            installed,
            (active, Dependency(f"{prefix}_ODifferenz", 11398 + ho, (1,))),
            (
                (
                    0,
                    "_K",
                    "K",
                    ("1", "50", "0.5", "0.1"),
                    ("0.5", "49.5", "0.5", "0.1"),
                ),
                (1, "_R", "°R", ("2", "100", "1", "0.1"), ("1", "99", "1", "0.1")),
            ),
            rising=True,
        )
        for suffix, index, bounds, unit, label in (
            ("Startdrehzahl", 11392, ("20", "100", "1", "1"), "%", "start speed"),
            ("Schrittweite", 11393, ("1", "100", "1", "1"), "%", "speed step"),
            ("dtVerzoegerung", 11397, ("1", "10", "1", "1"), "s", "delay"),
        ):
            yield Setting(
                f"{prefix}_{suffix}",
                index + ho,
                f"Heating function {slot} DHW preheat {label}",
                "number",
                True,
                limits=bounds,
                unit=unit,
                dependencies=(active,),
                installed_by=installed,
            )


BX_PLUS_ADDITIONAL_OPERATING_SETTINGS = tuple(_additional_operating_settings())


def _installed_activity(root, index, switch_index):
    """Native enabled or switch-controlled activity with the assigned input."""
    return AnyDependencies(
        (
            (Dependency(root + "_Aktiviert_Modus", index, (1,)),),
            (
                Dependency(root + "_Aktiviert_Modus", index, (2,)),
                RangeDependency(
                    root + "_Aktiviert_SensorSchalter_Nr", switch_index, 1, 65
                ),
            ),
        )
    )


def _raw_steps(name, index, minimum, maximum, step):
    """Exact integer encoding of a companion preference, not an authorization."""
    return Dependency(name, index, tuple(range(minimum, maximum + 1, step)))


def _installed_program_options():
    """Existing user options, separate from function installation and protection."""
    pump = "Anlage_WTUmwaelzpumpe"
    for suffix, index, label, companion in (
        (
            "ONachlauf",
            10647,
            "run-on",
            _raw_steps(pump + "_Nachlaufzeit", 10648, 0, 60, 1),
        ),
        (
            "ORuhemodus",
            10650,
            "idle mode",
            _raw_steps(pump + "_RuhemodusDauer", 10651, 5, 240, 5),
        ),
    ):
        yield Setting(
            pump + "_" + suffix,
            index,
            f"Heat exchanger circulation pump {label} enabled",
            "switch",
            True,
            installed_by=RangeDependency(pump + "_Relais_Nr", 10614, 1, 100),
            dependencies=(_installed_activity(pump, 10607, 10609),),
            requested_conditions=(RequestedCondition(1, (companion,)),),
        )

    for slot in range(1, 17):
        offset = (slot - 1) * 251
        root = f"Anlage_Wf{slot}"
        prefix = root + "_Ruecklaufanhebung"
        target = prefix + "_TSommerAus"
        index = 6701 + offset
        for selector, alias, lo, hi, step in (
            (0, "_C", 100, 600, 10),
            (1, "_F", 500, 1400, 20),
        ):
            yield Setting(
                prefix + "_OSommerAus",
                6694 + offset,
                f"Arrangement function {slot} summer cut-off enabled",
                "switch",
                True,
                installed_by=Dependency(root + "_Type", 6559 + offset, (8,)),
                dependencies=(
                    _installed_activity(root, 6566 + offset, 6568 + offset),
                    RangeDependency(root + "_Relais1_Nr", 6592 + offset, 1, 100),
                    RangeDependency(root + "_Sensor1_Nr", 6576 + offset, 1, 65),
                    RangeDependency(root + "_Sensor2_Nr", 6580 + offset, 1, 65),
                    RangeDependency(root + "_Sensor3_Nr", 6584 + offset, 1, 65),
                    Dependency("TemperatureHysteresisSelector", 60, (selector,)),
                ),
                compound_mirrors=(
                    (target, index, target + alias, index + selector + 1),
                ),
                requested_conditions=(
                    RequestedCondition(1, (_raw_steps(target, index, lo, hi, step),)),
                ),
            )

    for circuit in range(1, 8):
        offset = (circuit - 1) * 495
        root = f"Heizung_Heizkreis{circuit}"
        installed = Dependency(root + "_Type", 13684 + offset, (1,))
        activity = _installed_activity(root, 13689 + offset, 13691 + offset)
        heating = Dependency(root + "_System", 13697 + offset, (0, 2))
        base = (
            activity,
            heating,
            RangeDependency(root + "_Relais1_Nr", 13764 + offset, 1, 100),
            RangeDependency(root + "_Sensor1_Nr", 13796 + offset, 1, 65),
        )
        summer = (
            *base,
            RangeDependency(root + "_Sensor2_Nr", 13800 + offset, 1, 65),
        )
        mode = root + "_Heizgrenze"
        mode_index = 13740 + offset
        flag = root + "_OHeizgrenze"
        flag_index = 13739 + offset
        for selector, alias, low, high, step in (
            (0, "_C", 0, 400, 10),
            (1, "_F", 320, 1040, 20),
        ):
            selected = Dependency("TemperatureHysteresisSelector", 60, (selector,))
            day = root + "_TSommerTag"
            night = root + "_TSommerNacht"
            day_index = 13741 + offset
            night_index = 13744 + offset
            day_mirror = (day, day_index, day + alias, day_index + selector + 1)
            night_mirror = (
                night,
                night_index,
                night + alias,
                night_index + selector + 1,
            )
            day_valid = _raw_steps(day, day_index, low, high, step)
            night_valid = _raw_steps(night, night_index, low, high, step)
            clock = tuple(
                _raw_steps(root + "_" + suffix, index + offset, 0, 1425, 15)
                for suffix, index in (
                    ("tSommerTagEin", 13700),
                    ("tSommerTagAus", 13701),
                )
            )
            for current_mode in (0, 1):
                companions = (
                    (day_valid, night_valid, *clock) if current_mode else (day_valid,)
                )
                mirrors = (day_mirror, night_mirror) if current_mode else (day_mirror,)
                yield Setting(
                    flag,
                    flag_index,
                    f"Heating circuit {circuit} summer cut-off enabled",
                    "switch",
                    True,
                    installed_by=installed,
                    dependencies=(
                        *summer,
                        selected,
                        Dependency(mode, mode_index, (current_mode,)),
                    ),
                    compound_mirrors=mirrors,
                    requested_conditions=tuple(
                        RequestedCondition(1, (dep,)) for dep in companions
                    ),
                )
            yield Setting(
                mode,
                mode_index,
                f"Heating circuit {circuit} summer cut-off mode",
                "select",
                True,
                options=(("Day", 0), ("Day/Night", 1)),
                installed_by=installed,
                dependencies=(*summer, selected, Dependency(flag, flag_index, (1,))),
                compound_mirrors=(day_mirror, night_mirror),
                requested_conditions=(
                    RequestedCondition(0, (day_valid,)),
                    *(
                        RequestedCondition(1, (dep,))
                        for dep in (day_valid, night_valid, *clock)
                    ),
                ),
            )
        energy = root + "_Energiesparbetrieb"
        target = energy + "_dTAus"
        index = 14163 + offset
        for selector, alias, lo, hi, step in (
            (0, "_K", 10, 490, 10),
            (1, "_R", 20, 980, 20),
        ):
            yield Setting(
                root + "_OEnergiesparbetrieb",
                14158 + offset,
                f"Heating circuit {circuit} energy saving enabled",
                "switch",
                True,
                installed_by=installed,
                dependencies=(
                    *base,
                    RangeDependency(energy + "_Sensor1_Nr", 14160 + offset, 1, 65),
                    Dependency("TemperatureHysteresisSelector", 60, (selector,)),
                ),
                compound_mirrors=(
                    (target, index, target + alias, index + selector + 1),
                ),
                requested_conditions=tuple(
                    RequestedCondition(1, (dep,))
                    for dep in (
                        _raw_steps(target, index, lo, hi, step),
                        RangeDependency(energy + "_dtPause", 14166 + offset, 0, 60),
                        RangeDependency(energy + "_dtLaufzeit", 14167 + offset, 0, 60),
                    )
                ),
            )
        coupling = (
            (Dependency(root + "_Kopplung", 14039 + offset, (0,)),)
            if circuit != 1
            else ()
        )
        yield Setting(
            root + "_OUrlaub",
            14176 + offset,
            (
                "Heating circuit 1 automatic holiday participation (including coupled circuits)"
                if circuit == 1
                else f"Heating circuit {circuit} automatic holiday participation"
            ),
            "switch",
            True,
            installed_by=installed,
            dependencies=(
                activity,
                *coupling,
                Dependency("InbetriebnahmeAbgeschlossen", 68, (1,)),
                RangeDependency("Urlaubsfunktion_Tage", 19063, 0, 200),
                RangeDependency(root + "_Betriebsart", 14014 + offset, 0, 5),
            ),
        )

    for slot in range(1, 6):
        offset = (slot - 1) * 49
        root = f"Solar_Wf{slot}_ExtWT"
        target = root + "_ZieltemperaturSekundaer"
        rise = root + "_dTAnstieg"
        target_index = 4109 + offset
        rise_index = 4121 + offset
        for selector, alias, hysteresis, lo, hi, step, rlo, rhi, rstep in (
            (0, "_C", "_K", 150, 950, 10, 10, 300, 10),
            (1, "_F", "_R", 600, 2040, 20, 20, 600, 20),
        ):
            yield Setting(
                root + "_OZieltemperatur",
                4091 + offset,
                f"Solar exchanger {slot} target temperature enabled",
                "switch",
                True,
                installed_by=RangeDependency(
                    root + "_Sensor1_Nr", 4094 + offset, 1, 65
                ),
                dependencies=(
                    RangeDependency("Solar_Relais1_Nr", 3452, 1, 100),
                    SolarSelection(root + "_SpeicherMaske", 4089 + offset, stores=True),
                    RangeDependency(root + "_Relais1_Nr", 4102 + offset, 1, 100),
                    RangeDependency(root + "_Sensor2_Nr", 4098 + offset, 1, 65),
                    Dependency("TemperatureHysteresisSelector", 60, (selector,)),
                ),
                compound_mirrors=(
                    (
                        target,
                        target_index,
                        target + alias,
                        target_index + selector + 1,
                    ),
                    (rise, rise_index, rise + hysteresis, rise_index + selector + 1),
                ),
                requested_conditions=(
                    RequestedCondition(
                        1, (_raw_steps(target, target_index, lo, hi, step),)
                    ),
                    RequestedCondition(
                        1, (_raw_steps(rise, rise_index, rlo, rhi, rstep),)
                    ),
                ),
            )


BX_PLUS_PROGRAM_OPTIONS = tuple(_installed_program_options())


def _thermal_option_settings():
    """Native installed thermal predicates, with valid companion settings."""
    for slot in range(1, 17):
        ao = (slot - 1) * 251
        ho = (slot - 1) * 151
        arrangement = f"Anlage_Wf{slot}"
        root = arrangement + "_Funktionsblock"
        heating = f"Heizung_Wf{slot}"
        arrangement_activity = _installed_activity(arrangement, 6566 + ao, 6568 + ao)
        output = RangeDependency(arrangement + "_Relais1_Nr", 6592 + ao, 1, 100)
        sensors = tuple(
            RangeDependency(
                f"{arrangement}_Sensor{sensor}_Nr",
                6576 + ao + 4 * (sensor - 1),
                1,
                65,
            )
            for sensor in range(1, 5)
        )
        for selector, temperature_alias, diff_alias, factor in (
            (0, "_C", "_K", 1),
            (1, "_F", "_R", 2),
        ):
            selected = Dependency("TemperatureHysteresisSelector", 60, (selector,))
            thermal_groups = []
            for number in (1, 2):
                index = 6716 + ao + (number - 1) * 6
                on = f"{root}_TTh{number}Ein"
                off = f"{root}_TTh{number}Aus"
                high = 2500 if selector == 0 else 4820
                companions = (
                    (on, index, temperature_alias, -400, high, 10 * factor),
                    (off, index + 3, temperature_alias, -400, high, 10 * factor),
                )
                thermal_groups.append(
                    (
                        f"OThermostat{number}",
                        6704 + ao + number,
                        f"thermostat {number}",
                        (sensors[number - 1],),
                        companions,
                        (PeerComparison(on, index, off, index + 3, "ne"),),
                    )
                )
            on, off, target, rise = (
                root + "_dTEin",
                root + "_dTAus",
                root + "_dTSoll",
                root + "_Anstieg",
            )
            companions = (
                (on, 6707 + ao, diff_alias, 10 * factor, 500 * factor, 5 * factor),
                (off, 6710 + ao, diff_alias, 5 * factor, 495 * factor, 5 * factor),
                (
                    target,
                    6713 + ao,
                    diff_alias,
                    20 * factor,
                    1000 * factor,
                    10 * factor,
                ),
                (rise, 6732 + ao, diff_alias, 5 * factor, 200 * factor, 5 * factor),
            )
            thermal_groups.append(
                (
                    "ODifferenz",
                    6704 + ao,
                    "differential",
                    sensors[2:],
                    companions,
                    (
                        PeerComparison(on, 6707 + ao, off, 6710 + ao, "gt"),
                        PeerComparison(target, 6713 + ao, on, 6707 + ao, "gt"),
                    ),
                )
            )
            for suffix, index, label, inputs, companions, relations in thermal_groups:
                remaining = AnyDependencies(
                    tuple(
                        (
                            Dependency(root + "_" + other, other_index, (1,)),
                            *other_inputs,
                            *(
                                _raw_steps(name, idx, lo, hi, step)
                                for name, idx, _, lo, hi, step in other_companions
                            ),
                            *(
                                PeerComparison(
                                    name, idx, name + alias, idx + selector + 1, "eq"
                                )
                                for name, idx, alias, _, _, _ in other_companions
                            ),
                            *other_relations,
                        )
                        for (
                            other,
                            other_index,
                            _,
                            other_inputs,
                            other_companions,
                            other_relations,
                        ) in thermal_groups
                        if other != suffix
                    )
                )
                # A positively verified existing timer is also an AND predicate.
                remaining = AnyDependencies(
                    (
                        *remaining.groups,
                        (
                            Dependency(arrangement + "_OSchaltuhr", 6574 + ao, (1,)),
                            WeeklyProgram(
                                arrangement + "_SchaltuhrNr", 6620 + ao, True
                            ),
                        ),
                    )
                )
                yield Setting(
                    root + "_" + suffix,
                    index,
                    f"Function block {slot} {label} enabled",
                    "switch",
                    True,
                    installed_by=Dependency(arrangement + "_Type", 6559 + ao, (9,)),
                    dependencies=(arrangement_activity, output, *inputs, selected),
                    compound_mirrors=tuple(
                        (name, idx, name + alias, idx + selector + 1)
                        for name, idx, alias, _, _, _ in companions
                    ),
                    requested_conditions=(
                        *(
                            RequestedCondition(
                                1, (_raw_steps(name, idx, lo, hi, step),)
                            )
                            for name, idx, _, lo, hi, step in companions
                        ),
                        *(RequestedCondition(1, (rule,)) for rule in relations),
                        RequestedCondition(
                            0,
                            (Dependency(root + "_" + suffix, index, (0,)), remaining),
                        ),
                    ),
                )

            prefix = arrangement + "_Feststoffkessel"
            target = prefix + "_Zieltemperatur_TZiel"
            index = 6682 + ao
            minimum, maximum = (300, 850) if selector == 0 else (860, 1860)
            yield Setting(
                prefix + "_OZieltemperatur",
                6681 + ao,
                f"Arrangement function {slot} solid fuel target temperature enabled",
                "switch",
                True,
                installed_by=Dependency(arrangement + "_Type", 6559 + ao, (6,)),
                dependencies=(arrangement_activity, output, *sensors[:3], selected),
                compound_mirrors=(
                    (target, index, target + temperature_alias, index + selector + 1),
                ),
                requested_conditions=(
                    RequestedCondition(
                        1, (_raw_steps(target, index, minimum, maximum, 10 * factor),)
                    ),
                ),
            )

            prefix = heating + "_BwVorerwaermung"
            on, off = prefix + "_dTEin", prefix + "_dTAus"
            on_index, off_index = 11399 + ho, 11402 + ho
            yield Setting(
                prefix + "_ODifferenz",
                11398 + ho,
                f"Heating function {slot} DHW preheat differential enabled",
                "switch",
                True,
                installed_by=Dependency(heating + "_Type", 11266 + ho, (3,)),
                dependencies=(
                    _installed_activity(heating, 11273 + ho, 11275 + ho),
                    RangeDependency(heating + "_Relais1_Nr", 11321 + ho, 1, 100),
                    RangeDependency(heating + "_Sensor1_Nr", 11290 + ho, 1, 65),
                    RangeDependency(heating + "_Sensor2_Nr", 11294 + ho, 1, 65),
                    RangeDependency(heating + "_Sensor3_Nr", 11298 + ho, 1, 65),
                    RangeDependency(
                        heating + "_SensorDurchfluss1_Nr", 11315 + ho, 1, 65
                    ),
                    selected,
                ),
                compound_mirrors=(
                    (on, on_index, on + diff_alias, on_index + selector + 1),
                    (off, off_index, off + diff_alias, off_index + selector + 1),
                ),
                requested_conditions=(
                    RequestedCondition(
                        1,
                        (
                            _raw_steps(
                                on, on_index, 10 * factor, 500 * factor, 5 * factor
                            ),
                        ),
                    ),
                    RequestedCondition(
                        1,
                        (
                            _raw_steps(
                                off, off_index, 5 * factor, 495 * factor, 5 * factor
                            ),
                        ),
                    ),
                    RequestedCondition(
                        1, (PeerComparison(on, on_index, off, off_index, "gt"),)
                    ),
                ),
            )


BX_PLUS_THERMAL_OPTIONS = tuple(_thermal_option_settings())


def _weekly_timer_options():
    """Installed timer flags reuse native owner layout and allocation evidence."""
    for owner in WEEKLY_OWNERS:
        installed, *conditions = owner.conditions
        flag = next(dep for dep in conditions if dep.name.endswith("_OSchaltuhr"))
        context = tuple(dep for dep in conditions if dep != flag)
        root = owner.name.removesuffix("_SchaltuhrNr")
        reference = WeeklyProgram(owner.name, owner.index)
        enable = RequestedCondition(1, (replace(reference, require_days=True),))
        label = root.replace("_", " ") + " weekly timer enabled"
        if root.startswith("Anlage_Wf"):
            slot = int(root.removeprefix("Anlage_Wf"))
            ao = (slot - 1) * 251
            activity = _installed_activity(root, 6566 + ao, 6568 + ao)
            output = RangeDependency(root + "_Relais1_Nr", 6592 + ao, 1, 100)
            common = (activity, output, reference)
            yield Setting(
                flag.name,
                flag.index,
                label,
                "switch",
                True,
                installed_by=Dependency(installed.name, installed.index, (3, 5)),
                dependencies=(
                    *common,
                    RangeDependency(root + "_Sensor1_Nr", 6576 + ao, 1, 65),
                    RangeDependency(root + "_Sensor2_Nr", 6580 + ao, 1, 65),
                ),
                requested_conditions=(enable,),
            )
            # The thermal switches already describe each valid remaining
            # predicate, including its unit-dependent companion layout.
            for unit in (0, 1):
                selected = Dependency("TemperatureHysteresisSelector", 60, (unit,))
                groups = []
                for setting in BX_PLUS_THERMAL_OPTIONS:
                    if not (
                        setting.name.startswith(root + "_Funktionsblock_")
                        and selected in setting.dependencies
                    ):
                        continue
                    for rule in setting.requested_conditions:
                        if rule.raw != 0:
                            continue
                        for dep in rule.alternatives:
                            if isinstance(dep, AnyDependencies):
                                groups.extend(
                                    group
                                    for group in dep.groups
                                    if not any(
                                        isinstance(d, WeeklyProgram) for d in group
                                    )
                                )
                yield Setting(
                    flag.name,
                    flag.index,
                    label,
                    "switch",
                    True,
                    installed_by=Dependency(installed.name, installed.index, (9,)),
                    dependencies=(*common, selected),
                    requested_conditions=(
                        enable,
                        RequestedCondition(
                            0,
                            (
                                Dependency(flag.name, flag.index, (0,)),
                                AnyDependencies(tuple(dict.fromkeys(groups))),
                            ),
                        ),
                    ),
                )
        elif root.startswith("Heizung_Wf"):
            slot = int(root.removeprefix("Heizung_Wf"))
            ho = (slot - 1) * 151
            yield Setting(
                flag.name,
                flag.index,
                label,
                "switch",
                True,
                installed_by=installed,
                dependencies=(
                    _installed_activity(root, 11273 + ho, 11275 + ho),
                    RangeDependency(root + "_NhRelais1_Nr", 11337 + ho, 1, 100),
                    RangeDependency(root + "_Sensor1_Nr", 11290 + ho, 1, 65),
                    AnyDependencies(
                        (
                            (
                                Dependency(
                                    root + "_ThBwErwaermung_Modus", 11378 + ho, (0,)
                                ),
                            ),
                            (
                                Dependency(
                                    root + "_ThBwErwaermung_Modus", 11378 + ho, (1,)
                                ),
                                RangeDependency(
                                    root + "_Sensor2_Nr", 11294 + ho, 1, 65
                                ),
                            ),
                        )
                    ),
                    reference,
                ),
                requested_conditions=(enable,),
            )
        else:
            circuit = int(root.split("_")[1].removeprefix("Heizkreis"))
            offset = (circuit - 1) * 495
            circuit_root = f"Heizung_Heizkreis{circuit}"
            yield Setting(
                flag.name,
                flag.index,
                label,
                "switch",
                True,
                installed_by=installed,
                dependencies=(
                    *context,
                    _installed_activity(circuit_root, 13689 + offset, 13691 + offset),
                    Dependency(circuit_root + "_System", 13697 + offset, (0, 2)),
                    reference,
                ),
                requested_conditions=(enable,),
            )


BX_PLUS_WEEKLY_TIMER_OPTIONS = tuple(_weekly_timer_options())


def _coordination_options():
    """Existing operational heating, cooling and solar suppression preferences."""

    def solar_options(installed, common, flag, target_flag, mask, label):
        selection = SolarSelection(mask[0], mask[1], stores=True)
        targets = tuple(replace(selection, target_unit=unit) for unit in (0, 1))
        solar = (
            RangeDependency("Solar_Kol1_Sensor1_Nr", 3536, 1, 65),
            RangeDependency("Solar_Relais1_Nr", 3452, 1, 100),
            selection,
        )
        # An already configured Tset condition must be valid before Solar off
        # can be enabled; the user can also choose ordinary solar suppression.
        yield Setting(
            flag[0],
            flag[1],
            label + " solar off enabled",
            "switch",
            True,
            installed_by=installed,
            dependencies=(*common, *solar),
            requested_conditions=(
                RequestedCondition(
                    1,
                    (
                        Dependency(target_flag[0], target_flag[1], (0,)),
                        AnyDependencies(
                            tuple(
                                (
                                    Dependency(target_flag[0], target_flag[1], (1,)),
                                    target,
                                )
                                for target in targets
                            )
                        ),
                    ),
                ),
            ),
        )
        yield Setting(
            target_flag[0],
            target_flag[1],
            label + " solar off set-temperature condition",
            "switch",
            True,
            installed_by=installed,
            dependencies=(
                *common,
                *solar,
                Dependency(flag[0], flag[1], (1,)),
            ),
            requested_conditions=(RequestedCondition(1, targets),),
        )

    for slot in range(1, 17):
        ho = (slot - 1) * 151
        root = f"Heizung_Wf{slot}"
        installed = Dependency(root + "_Type", 11266 + ho, (2,))
        common = (
            _installed_activity(root, 11273 + ho, 11275 + ho),
            RangeDependency(root + "_NhRelais1_Nr", 11337 + ho, 1, 100),
            RangeDependency(root + "_Sensor1_Nr", 11290 + ho, 1, 65),
            AnyDependencies(
                (
                    (Dependency(root + "_ThBwErwaermung_Modus", 11378 + ho, (0,)),),
                    (
                        Dependency(root + "_ThBwErwaermung_Modus", 11378 + ho, (1,)),
                        RangeDependency(root + "_Sensor2_Nr", 11294 + ho, 1, 65),
                    ),
                )
            ),
        )
        yield Setting(
            root + "_OUrlaubAus",
            11281 + ho,
            f"Heating function {slot} DHW off during holiday",
            "switch",
            True,
            installed_by=installed,
            dependencies=(
                *common,
                Dependency("InbetriebnahmeAbgeschlossen", 68, (1,)),
                RangeDependency("Urlaubsfunktion_Tage", 19063, 0, 200),
            ),
        )
        yield from solar_options(
            installed,
            common,
            (root + "_OSolarAus", 11282 + ho),
            (root + "_SolarAus_OTSoll", 11284 + ho),
            (root + "_SolarAus_SpeicherMaske", 11283 + ho),
            f"Heating function {slot} DHW",
        )
        so = (slot - 1) * 120
        root = f"Solar_Wf{slot}"
        selection = SolarSelection(
            root + "_NhUnterdrueckung_SpeicherMaske", 4461 + so, stores=True
        )
        yield Setting(
            root + "_NhUnterdrueckung_OSpSoll",
            4462 + so,
            f"Solar function {slot} backup suppression set-temperature condition",
            "switch",
            True,
            installed_by=Dependency(root + "_Type", 4409 + so, (6,)),
            dependencies=(
                _installed_activity(root, 4415 + so, 4417 + so),
                RangeDependency(root + "_Relais1_Nr", 4436 + so, 1, 100),
                RangeDependency("Solar_Kol1_Sensor1_Nr", 3536, 1, 65),
                RangeDependency("Solar_Relais1_Nr", 3452, 1, 100),
                selection,
            ),
            requested_conditions=(
                RequestedCondition(
                    1, tuple(replace(selection, target_unit=unit) for unit in (0, 1))
                ),
            ),
        )

    for circuit in range(1, 8):
        offset = (circuit - 1) * 495
        root = f"Heizung_Heizkreis{circuit}"
        prefix = root + "_Nachheizung"
        installed = Dependency(root + "_Type", 13684 + offset, (1,))
        activity = _installed_activity(root, 13689 + offset, 13691 + offset)
        common = (
            activity,
            Dependency(root + "_System", 13697 + offset, (0, 2)),
            Dependency(root + "_ONachheizung", 13705 + offset, (1,)),
            _installed_activity(prefix, 13708 + offset, 13710 + offset),
            RangeDependency(root + "_Relais6_Nr", 14003 + offset, 1, 100),
        )
        thermal = AnyDependencies(
            (
                (
                    Dependency(prefix + "_Modus", 13706 + offset, (0,)),
                    RangeDependency(root + "_Sensor4_Nr", 13804 + offset, 1, 65),
                ),
                (
                    Dependency(prefix + "_Modus", 13706 + offset, (1,)),
                    RangeDependency(root + "_Sensor4_Nr", 13804 + offset, 1, 65),
                    RangeDependency(root + "_Sensor5_Nr", 13808 + offset, 1, 65),
                ),
            )
        )
        for suffix, index, companion, companion_index, label in (
            ("OFruehstart", 13714, "tFruehstart", 13715, "start optimization"),
            ("OBlockierung", 13716, "tBlockierung", 13717, "stop optimization"),
        ):
            yield Setting(
                prefix + "_" + suffix,
                index + offset,
                f"Heating circuit {circuit} {label} enabled",
                "switch",
                True,
                installed_by=installed,
                dependencies=(*common, thermal),
                requested_conditions=(
                    RequestedCondition(
                        1,
                        (
                            RangeDependency(
                                prefix + "_" + companion,
                                companion_index + offset,
                                0,
                                300,
                            ),
                        ),
                    ),
                ),
            )
        yield from solar_options(
            installed,
            (
                *common,
                AnyDependencies(
                    (
                        *thermal.groups,
                        (Dependency(prefix + "_Modus", 13706 + offset, (2,)),),
                    )
                ),
            ),
            (prefix + "_OSolarAus", 14141 + offset),
            (prefix + "_SolarAus_OTSoll", 14143 + offset),
            (prefix + "_SolarAus_SpeicherMaske", 14142 + offset),
            f"Heating circuit {circuit} supplementary heating",
        )
        cooling = root + "_Kuehlung"
        yield Setting(
            cooling + "_OTimer",
            14098 + offset,
            f"Heating circuit {circuit} cooling daily timer enabled",
            "switch",
            True,
            installed_by=installed,
            dependencies=(
                activity,
                Dependency(root + "_System", 13697 + offset, (1, 2)),
                RangeDependency(cooling + "_Relais1_Nr", 14059 + offset, 1, 100),
            ),
            requested_conditions=tuple(
                RequestedCondition(
                    1,
                    (
                        _raw_steps(
                            cooling + "_Timer_" + suffix, index + offset, 0, 1425, 15
                        ),
                    ),
                )
                for suffix, index in (("tEin", 14099), ("tAus", 14100))
            ),
        )


BX_PLUS_COORDINATION_OPTIONS = tuple(_coordination_options())


def _operating_mode_options():
    """Installed mode choices and options backed by assigned external inputs."""
    for slot in range(1, 17):
        offset = (slot - 1) * 151
        root = f"Heizung_Wf{slot}"
        installed = Dependency(root + "_Type", 11266 + offset, (2,))
        prefix = root + "_ThBwErwaermung"
        mode, mi = prefix + "_Modus", 11378 + offset
        secondary = RangeDependency(root + "_Sensor2_Nr", 11294 + offset, 1, 65)
        base = (
            _installed_activity(root, 11273 + offset, 11275 + offset),
            RangeDependency(root + "_NhRelais1_Nr", 11337 + offset, 1, 100),
            RangeDependency(root + "_Sensor1_Nr", 11290 + offset, 1, 65),
            AnyDependencies(
                (
                    (Dependency(prefix + "_OLadepumpe", 11375 + offset, (0,)),),
                    (
                        Dependency(prefix + "_OLadepumpe", 11375 + offset, (1,)),
                        RangeDependency(root + "_Relais2_Nr", 11329 + offset, 1, 100),
                    ),
                )
            ),
        )
        on, off = prefix + "_TEin", prefix + "_TAus"
        oi, ai = 11379 + offset, 11382 + offset
        for unit, alias, low, high, step in (
            (0, "_C", 0, 950, 10),
            (1, "_F", 320, 2040, 20),
        ):
            yield Setting(
                mode,
                mi,
                f"Heating function {slot} DHW heating mode",
                "select",
                True,
                options=(("Thermostat", 0), ("Zone", 1)),
                installed_by=installed,
                dependencies=(
                    *base,
                    Dependency("TemperatureHysteresisSelector", 60, (unit,)),
                    _raw_steps(on, oi, low, high - step, step),
                    _raw_steps(off, ai, low + step, high, step),
                    AnyDependencies(((PeerComparison(off, ai, on, oi, "gt"),),)),
                ),
                compound_mirrors=(
                    (on, oi, on + alias, oi + unit + 1),
                    (off, ai, off + alias, ai + unit + 1),
                ),
                requested_conditions=(RequestedCondition(1, (secondary,)),),
            )
        for name, index, label, input_name, input_index in (
            (
                root + "_OBetriebsartenschalterAus",
                11287,
                "operating mode switch off option",
                root + "_SensorBetriebsartenschalter1_Nr",
                11307,
            ),
            (
                prefix + "_OManuelleAufheizung",
                11387,
                "manual heating input option",
                root + "_SensorSchalter1_Nr",
                11311,
            ),
        ):
            yield Setting(
                name,
                index + offset,
                f"Heating function {slot} {label}",
                "switch",
                True,
                installed_by=installed,
                dependencies=(
                    *base,
                    AnyDependencies(
                        (
                            (Dependency(mode, mi, (0,)),),
                            (Dependency(mode, mi, (1,)), secondary),
                        )
                    ),
                    RangeDependency(input_name, input_index + offset, 1, 65),
                ),
            )

    for circuit in range(1, 8):
        offset = (circuit - 1) * 495
        root = f"Heizung_Heizkreis{circuit}"
        installed = Dependency(root + "_Type", 13684 + offset, (1,))
        active = _installed_activity(root, 13689 + offset, 13691 + offset)
        flow = RangeDependency(root + "_Sensor1_Nr", 13796 + offset, 1, 65)
        outdoor = RangeDependency(root + "_Sensor2_Nr", 13800 + offset, 1, 65)
        pump = RangeDependency(root + "_Relais1_Nr", 13764 + offset, 1, 100)
        reference = WeeklyProgram(root + "_SchaltuhrNr", 13817 + offset)
        correction = root + "_dTNachtkorrektur"
        ci = 13730 + offset
        coupling = (
            (Dependency(root + "_Kopplung", 14039 + offset, (0,)),)
            if circuit != 1
            else ()
        )
        for unit, alias, factor in ((0, "_K", 1), (1, "_R", 2)):
            yield Setting(
                root + "_ModusSchaltuhr",
                13704 + offset,
                f"Heating circuit {circuit} weekly timer mode",
                "select",
                True,
                options=(("Day/Night", 0), ("Day/Off", 1)),
                installed_by=installed,
                dependencies=(
                    active,
                    flow,
                    pump,
                    *coupling,
                    Dependency(root + "_System", 13697 + offset, (0, 2)),
                    Dependency(root + "_Heizsystem", 13978 + offset, (1, 2, 3, 5)),
                    Dependency(root + "_OSchaltuhr", 13703 + offset, (1,)),
                    Dependency("TemperatureHysteresisSelector", 60, (unit,)),
                    reference,
                ),
                requested_conditions=(
                    RequestedCondition(0, (replace(reference, require_days=True),)),
                    RequestedCondition(1, (replace(reference, require_days=True),)),
                    RequestedCondition(
                        0,
                        (
                            _raw_steps(
                                correction, ci, -200 * factor, 300 * factor, 10 * factor
                            ),
                        ),
                    ),
                    RequestedCondition(
                        0,
                        (
                            PeerComparison(
                                correction, ci, correction + alias, ci + unit + 1, "eq"
                            ),
                        ),
                    ),
                ),
            )
        cooling = root + "_Kuehlung"
        threshold = cooling + "_Aussen_TAussen"
        ti = 14072 + offset
        outside = AnyDependencies(
            tuple(
                (
                    outdoor,
                    Dependency("TemperatureHysteresisSelector", 60, (unit,)),
                    _raw_steps(threshold, ti, lo, hi, step),
                    PeerComparison(
                        threshold, ti, threshold + alias, ti + unit + 1, "eq"
                    ),
                )
                for unit, alias, lo, hi, step in (
                    (0, "_C", 200, 400, 10),
                    (1, "_F", 680, 1040, 20),
                )
            )
        )
        external = RangeDependency(
            cooling + "_ExtSchalter_Sensor1_Nr", 14068 + offset, 1, 65
        )
        yield Setting(
            cooling + "_Modus",
            14066 + offset,
            f"Heating circuit {circuit} cooling trigger",
            "select",
            True,
            options=(("Outdoor", 0), ("External switch", 1), ("Both", 2)),
            installed_by=installed,
            dependencies=(
                active,
                flow,
                Dependency(root + "_System", 13697 + offset, (1, 2)),
                RangeDependency(cooling + "_Relais1_Nr", 14059 + offset, 1, 100),
            ),
            requested_conditions=(
                RequestedCondition(0, (outside,)),
                RequestedCondition(1, (external,)),
                RequestedCondition(2, (outside,)),
                RequestedCondition(2, (external,)),
            ),
        )

    for slot in range(1, 4):
        offset = (slot - 1) * 14
        root = f"Solar_Kol{slot}"
        target, ti = root + "_Tmin", 3539 + offset
        systems = tuple(sorted(s for s in BX_PLUS_SOLAR_SYSTEMS if s // 10000 >= slot))
        for unit, alias, lo, hi, step in (
            (0, "_C", 100, 900, 10),
            (1, "_F", 500, 1940, 20),
        ):
            yield Setting(
                root + "_Omin",
                3533 + offset,
                f"Solar collector {slot} minimum temperature enabled",
                "switch",
                True,
                installed_by=RangeDependency(
                    root + "_Sensor1_Nr", 3536 + offset, 1, 65
                ),
                dependencies=(
                    Dependency("Solar_SystemId", 3416, systems),
                    RangeDependency("Solar_Relais1_Nr", 3452, 1, 100),
                    Dependency("TemperatureHysteresisSelector", 60, (unit,)),
                ),
                compound_mirrors=((target, ti, target + alias, ti + unit + 1),),
                requested_conditions=(
                    RequestedCondition(1, (_raw_steps(target, ti, lo, hi, step),)),
                ),
            )


BX_PLUS_OPERATING_MODE_OPTIONS = tuple(_operating_mode_options())


def _unit_value(name, index, selector, alias, low, high, step):
    """Selected-unit preference evidence without changing its native value."""
    return (
        _raw_steps(name, index, low, high, step),
        PeerComparison(name, index, name + alias, index + selector + 1, "eq"),
    )


def _room_sensor_groups(root, base):
    """Complete sensor-type room setup, including its optional weekly program."""
    groups = []
    for unit, ta, da, low, high, step, factor in (
        (0, "_C", "_K", 100, 300, 10, 1),
        (1, "_F", "_R", 500, 860, 20, 2),
    ):
        sensor = (
            Dependency(root + "_Typ", base + 4, (0,)),
            RangeDependency(root + "_Sensor1_Nr", base + 15, 1, 65),
            Dependency("TemperatureHysteresisSelector", 60, (unit,)),
            *_unit_value(root + "_TRaum", base + 5, unit, ta, low, high, step),
            *_unit_value(
                root + "_dTRaum", base + 8, unit, da, 5 * factor, 200 * factor, factor
            ),
        )
        groups.extend(
            (
                (*sensor, Dependency(root + "_OSchaltuhr", base + 2, (0,))),
                (
                    *sensor,
                    Dependency(root + "_OSchaltuhr", base + 2, (1,)),
                    WeeklyProgram(root + "_SchaltuhrNr", base + 30, True),
                    *_unit_value(
                        root + "_dTAbsenkung",
                        base + 11,
                        unit,
                        da,
                        10 * factor,
                        200 * factor,
                        10 * factor,
                    ),
                ),
            )
        )
    return tuple(groups)


def _room_thermal_controls():
    """Documented RTH, pump and thermal regulation controls of installed functions."""
    for circuit in range(1, 8):
        offset = (circuit - 1) * 495
        root = f"Heizung_Heizkreis{circuit}"
        installed = Dependency(root + "_Type", 13684 + offset, (1,))
        common = (
            _installed_activity(root, 13689 + offset, 13691 + offset),
            Dependency(root + "_System", 13697 + offset, (0, 2)),
            RangeDependency(root + "_Relais1_Nr", 13764 + offset, 1, 100),
        )
        rooms = []
        for room in range(1, 6):
            prefix = f"{root}_Raumthermostat{room}"
            base = 13819 + offset + (room - 1) * 32
            sensor = _room_sensor_groups(prefix, base)
            switch = (
                Dependency(prefix + "_Typ", base + 4, (1,)),
                RangeDependency(prefix + "_SensorSchalter1_Nr", base + 19, 1, 65),
            )
            rooms.append((prefix, base, sensor, switch))
        for prefix, base, sensor, switch in rooms:
            mode = prefix + "_Aktiviert"
            # RTH's two documented operating states are distinct from the
            # generic ActivityMode storage enumeration. No input assignment
            # exists for a separate activity switch in the room structure.
            remaining_sensor = AnyDependencies(
                tuple(
                    (
                        Dependency(other + "_Option", oi, (1,)),
                        Dependency(other + "_Aktiviert", oi + 1, (1,)),
                        *group,
                    )
                    for other, oi, other_sensor, _ in rooms
                    if other != prefix
                    for group in other_sensor
                )
            )
            yield Setting(
                mode,
                base + 1,
                f"{prefix.replace('_', ' ')} temporary activation",
                "select",
                True,
                options=(("Deactivated", 0), ("Activated", 1)),
                native_options=(0, 1, 2),
                installed_by=installed,
                dependencies=(
                    *common,
                    Dependency(prefix + "_Option", base, (1,)),
                    AnyDependencies(
                        (
                            (
                                Dependency(prefix + "_Typ", base + 4, (0,)),
                                RangeDependency(
                                    prefix + "_Sensor1_Nr", base + 15, 1, 65
                                ),
                            ),
                            switch,
                        )
                    ),
                ),
                requested_conditions=(
                    RequestedCondition(1, (AnyDependencies((*sensor, switch)),)),
                    RequestedCondition(
                        0,
                        (
                            Dependency(mode, base + 1, (0,)),
                            Dependency(root + "_Heizsystem", 13978 + offset, (1, 2, 3)),
                            Dependency(prefix + "_Typ", base + 4, (1,)),
                            remaining_sensor,
                        ),
                    ),
                ),
            )
        active_rooms = AnyDependencies(
            tuple(
                (
                    Dependency(prefix + "_Option", base, (1,)),
                    Dependency(prefix + "_Aktiviert", base + 1, (1,)),
                    *group,
                )
                for prefix, base, sensor, switch in rooms
                for group in (*sensor, switch)
            )
        )
        yield Setting(
            root + "_RTH_HKaus",
            14041 + offset,
            f"Heating circuit {circuit} room thermostat heating-off option",
            "switch",
            True,
            installed_by=installed,
            dependencies=common,
            requested_conditions=(RequestedCondition(1, (active_rooms,)),),
        )
        prefix = root + "_Nachheizung"
        mi = 13706 + offset
        on, off = prefix + "_dTEin", prefix + "_dTAus"
        oi, ai = 13747 + offset, 13750 + offset
        thermal = AnyDependencies(
            tuple(
                (
                    RangeDependency(root + "_Sensor4_Nr", 13804 + offset, 1, 65),
                    Dependency("TemperatureHysteresisSelector", 60, (unit,)),
                    *_unit_value(
                        on, oi, unit, alias, -150 * factor, 445 * factor, 5 * factor
                    ),
                    *_unit_value(
                        off, ai, unit, alias, -145 * factor, 450 * factor, 5 * factor
                    ),
                    PeerComparison(off, ai, on, oi, "gt"),
                )
                for unit, alias, factor in ((0, "_K", 1), (1, "_R", 2))
            )
        )
        yield Setting(
            prefix + "_Modus",
            mi,
            f"Heating circuit {circuit} supplementary heating mode",
            "select",
            True,
            options=(("Thermostat", 0), ("Zone", 1), ("On/Off", 2)),
            installed_by=installed,
            dependencies=(
                *common,
                Dependency(root + "_ONachheizung", 13705 + offset, (1,)),
                _installed_activity(prefix, 13708 + offset, 13710 + offset),
                RangeDependency(root + "_Relais6_Nr", 14003 + offset, 1, 100),
            ),
            requested_conditions=(
                RequestedCondition(0, (thermal,)),
                RequestedCondition(1, (thermal,)),
                RequestedCondition(
                    1, (RangeDependency(root + "_Sensor5_Nr", 13808 + offset, 1, 65),)
                ),
            ),
        )

    for slot in (1, 2):
        offset = (slot - 1) * 29
        root = f"Heizung_RelaisPumpe{slot}"
        installed = Dependency(root + "_Type", 11166 + offset, (2,))
        for phase, mode, ti, duration in (
            ("Anlauf", 11178, 11179, 11182),
            ("Nachlauf", 11183, 11184, 11187),
        ):
            target = f"{root}_{phase}_T{phase}"
            thermal = AnyDependencies(
                tuple(
                    (
                        Dependency("TemperatureHysteresisSelector", 60, (unit,)),
                        *_unit_value(target, ti + offset, unit, alias, lo, hi, step),
                        RangeDependency(
                            root + "_AnlaufNachlauf_Sensor1_Nr", 11189 + offset, 1, 65
                        ),
                    )
                    for unit, alias, lo, hi, step in (
                        (0, "_C", 100, 900, 10),
                        (1, "_F", 500, 1940, 20),
                    )
                )
            )
            yield Setting(
                f"{root}_{phase}_Modus",
                mode + offset,
                f"Heating pump {slot} {phase} mode",
                "select",
                True,
                options=(("None", 0), ("Temperature", 1), ("Time", 2)),
                installed_by=installed,
                dependencies=(
                    Dependency(root + "_Aktiviert_Modus", 11169 + offset, (1,)),
                    RangeDependency(root + "_Relais1_Nr", 11171 + offset, 1, 100),
                ),
                requested_conditions=(
                    RequestedCondition(1, (thermal,)),
                    RequestedCondition(
                        2,
                        (
                            RangeDependency(
                                f"{root}_{phase}_dt{phase}", duration + offset, 0, 300
                            ),
                        ),
                    ),
                ),
            )

    for slot in range(1, 17):
        offset = (slot - 1) * 151
        root = f"Heizung_Wf{slot}"
        prefix = root + "_BwVorerwaermung"
        common = (
            _installed_activity(root, 11273 + offset, 11275 + offset),
            RangeDependency(root + "_Relais1_Nr", 11321 + offset, 1, 100),
            RangeDependency(root + "_Sensor1_Nr", 11290 + offset, 1, 65),
            RangeDependency(root + "_SensorDurchfluss1_Nr", 11315 + offset, 1, 65),
            AnyDependencies(
                (
                    (Dependency(prefix + "_OVentil", 11388 + offset, (0,)),),
                    (
                        Dependency(prefix + "_OVentil", 11388 + offset, (1,)),
                        RangeDependency(root + "_Relais2_Nr", 11329 + offset, 1, 100),
                    ),
                )
            ),
            AnyDependencies(
                (
                    (Dependency(prefix + "_ODifferenz", 11398 + offset, (0,)),),
                    (
                        Dependency(prefix + "_ODifferenz", 11398 + offset, (1,)),
                        RangeDependency(root + "_Sensor2_Nr", 11294 + offset, 1, 65),
                        RangeDependency(root + "_Sensor3_Nr", 11298 + offset, 1, 65),
                    ),
                )
            ),
        )
        for suffix, index, label, variants in (
            (
                "TMax",
                11389,
                "temperature target",
                (
                    (0, "_C", "°C", ("20", "90", "1", "0.1")),
                    (1, "_F", "°F", ("68", "194", "2", "0.1")),
                ),
            ),
            (
                "dTMax",
                11394,
                "speed-regulation hysteresis",
                (
                    (0, "_K", "K", ("0.5", "10", "0.5", "0.1")),
                    (1, "_R", "°R", ("1", "20", "1", "0.1")),
                ),
            ),
        ):
            name = prefix + "_" + suffix
            for selector, alias, unit, limits in variants:
                yield Setting(
                    name,
                    index + offset,
                    f"Heating function {slot} DHW preheat {label}",
                    "number",
                    True,
                    limits=limits,
                    unit=unit,
                    installed_by=Dependency(root + "_Type", 11266 + offset, (3,)),
                    dependencies=(
                        *common,
                        Dependency("TemperatureHysteresisSelector", 60, (selector,)),
                    ),
                    compound_mirrors=(
                        (
                            name,
                            index + offset,
                            name + alias,
                            index + offset + selector + 1,
                        ),
                    ),
                )


BX_PLUS_ROOM_THERMAL_CONTROLS = tuple(_room_thermal_controls())


def _activity_setting(root, index, installed, requirements):
    """Ordinary native activity, never allocation or direct actor control."""
    ready = AnyDependencies((requirements,))
    return Setting(
        root + "_Aktiviert_Modus",
        index,
        root.replace("_", " ") + " activity",
        "select",
        True,
        options=(("Deactivated", 0), ("Activated", 1), ("External switch", 2)),
        installed_by=installed,
        requested_conditions=(
            RequestedCondition(1, (ready,)),
            RequestedCondition(2, (ready,)),
            RequestedCondition(
                2,
                (
                    RangeDependency(
                        root + "_Aktiviert_SensorSchalter_Nr", index + 2, 1, 65
                    ),
                ),
            ),
        ),
    )


def _configured_timer(root, flag, reference):
    return AnyDependencies(
        (
            (Dependency(root + "_OSchaltuhr", flag, (0,)),),
            (
                Dependency(root + "_OSchaltuhr", flag, (1,)),
                WeeklyProgram(root + "_SchaltuhrNr", reference, True),
            ),
        )
    )


def _activity_thermal(root, rows, ordered=()):
    """Validate the already configured native selected-unit companions."""
    return AnyDependencies(
        tuple(
            (
                Dependency("TemperatureHysteresisSelector", 60, (unit,)),
                *(
                    dep
                    for suffix, index, c, f, delta in rows
                    for dep in _unit_value(
                        root + "_" + suffix,
                        index,
                        unit,
                        ("_K", "_R")[unit] if delta else ("_C", "_F")[unit],
                        *(c if unit == 0 else f),
                    )
                ),
                *(
                    PeerComparison(root + "_" + high, hi, root + "_" + low, lo, "gt")
                    for high, hi, low, lo in ordered
                ),
            )
            for unit in (0, 1)
        )
    )


def _installed_function_activity():
    """Expand the shared native activity evaluator across evidenced contexts."""
    for slot in range(1, 17):
        ao = (slot - 1) * 251
        root = f"Anlage_Wf{slot}"
        sensor = RangeDependency(root + "_Sensor1_Nr", 6576 + ao, 1, 65)
        secondary = RangeDependency(root + "_Sensor2_Nr", 6580 + ao, 1, 65)
        output = RangeDependency(root + "_Relais1_Nr", 6592 + ao, 1, 100)
        timer = _configured_timer(root, 6574 + ao, 6620 + ao)
        for kind, extra in (
            (
                2,
                (
                    RangeDependency(root + "_Relais2_Nr", 6600 + ao, 1, 100),
                    PeerComparison(
                        root + "_Relais1_Nr",
                        6592 + ao,
                        root + "_Relais2_Nr",
                        6600 + ao,
                        "ne",
                    ),
                    RangeDependency(root + "_Mischer_dtIntervall", 6625 + ao, 1, 20),
                    _activity_thermal(
                        root + "_Mischer",
                        (("TZiel", 6629 + ao, (0, 1300, 10), (320, 2660, 20), False),),
                    ),
                ),
            ),
            (
                3,
                (
                    secondary,
                    timer,
                    _activity_thermal(
                        root + "_Boilerladung",
                        (
                            (
                                "TBoilerEin",
                                6632 + ao,
                                (0, 940, 10),
                                (320, 2020, 20),
                                False,
                            ),
                            (
                                "TBoilerAus",
                                6635 + ao,
                                (10, 950, 10),
                                (340, 2040, 20),
                                False,
                            ),
                        ),
                        (("TBoilerAus", 6635 + ao, "TBoilerEin", 6632 + ao),),
                    ),
                ),
            ),
            (
                5,
                (
                    secondary,
                    timer,
                    _activity_thermal(
                        root + "_Waermeaustausch",
                        (
                            ("dTEin", 6639 + ao, (10, 300, 5), (20, 600, 10), True),
                            ("dTAus", 6642 + ao, (5, 295, 5), (10, 590, 10), True),
                            ("dTSoll", 6645 + ao, (15, 400, 5), (30, 800, 10), True),
                            ("TMax", 6651 + ao, (100, 950, 10), (500, 2040, 20), False),
                            ("TMin", 6654 + ao, (100, 950, 10), (500, 2040, 20), False),
                            ("HMinMax", 6657 + ao, (5, 100, 5), (10, 200, 10), True),
                        ),
                        (
                            ("dTEin", 6639 + ao, "dTAus", 6642 + ao),
                            ("dTSoll", 6645 + ao, "dTEin", 6639 + ao),
                        ),
                    ),
                ),
            ),
        ):
            yield _activity_setting(
                root,
                6566 + ao,
                Dependency(root + "_Type", 6559 + ao, (kind,)),
                (sensor, output, *extra),
            )

        ho = (slot - 1) * 151
        root = f"Heizung_Wf{slot}"
        sensor = RangeDependency(root + "_Sensor1_Nr", 11290 + ho, 1, 65)
        secondary = RangeDependency(root + "_Sensor2_Nr", 11294 + ho, 1, 65)
        output = RangeDependency(root + "_Relais1_Nr", 11321 + ho, 1, 100)
        optional_output = RangeDependency(root + "_Relais2_Nr", 11329 + ho, 1, 100)
        prefix = root + "_ThBwErwaermung"
        yield _activity_setting(
            root,
            11273 + ho,
            Dependency(root + "_Type", 11266 + ho, (2,)),
            (
                sensor,
                RangeDependency(root + "_NhRelais1_Nr", 11337 + ho, 1, 100),
                AnyDependencies(
                    (
                        (Dependency(prefix + "_Modus", 11378 + ho, (0,)),),
                        (Dependency(prefix + "_Modus", 11378 + ho, (1,)), secondary),
                    )
                ),
                AnyDependencies(
                    (
                        (Dependency(prefix + "_OLadepumpe", 11375 + ho, (0,)),),
                        (
                            Dependency(prefix + "_OLadepumpe", 11375 + ho, (1,)),
                            optional_output,
                        ),
                    )
                ),
                _configured_timer(root, 11288 + ho, 11346 + ho),
                _activity_thermal(
                    prefix,
                    (
                        ("TEin", 11379 + ho, (0, 940, 10), (320, 2020, 20), False),
                        ("TAus", 11382 + ho, (10, 950, 10), (340, 2040, 20), False),
                    ),
                    (("TAus", 11382 + ho, "TEin", 11379 + ho),),
                ),
            ),
        )
        prefix = root + "_BwVorerwaermung"
        yield _activity_setting(
            root,
            11273 + ho,
            Dependency(root + "_Type", 11266 + ho, (3,)),
            (
                sensor,
                output,
                RangeDependency(root + "_SensorDurchfluss1_Nr", 11315 + ho, 1, 65),
                AnyDependencies(
                    (
                        (Dependency(prefix + "_OVentil", 11388 + ho, (0,)),),
                        (
                            Dependency(prefix + "_OVentil", 11388 + ho, (1,)),
                            optional_output,
                        ),
                    )
                ),
                AnyDependencies(
                    (
                        (Dependency(prefix + "_ODifferenz", 11398 + ho, (0,)),),
                        (
                            Dependency(prefix + "_ODifferenz", 11398 + ho, (1,)),
                            secondary,
                            RangeDependency(root + "_Sensor3_Nr", 11298 + ho, 1, 65),
                            _activity_thermal(
                                prefix,
                                (
                                    (
                                        "dTEin",
                                        11399 + ho,
                                        (10, 500, 5),
                                        (20, 1000, 10),
                                        True,
                                    ),
                                    (
                                        "dTAus",
                                        11402 + ho,
                                        (5, 495, 5),
                                        (10, 990, 10),
                                        True,
                                    ),
                                ),
                                (("dTEin", 11399 + ho, "dTAus", 11402 + ho),),
                            ),
                        ),
                    )
                ),
                _activity_thermal(
                    prefix,
                    (
                        ("TMax", 11389 + ho, (200, 900, 10), (680, 1940, 20), False),
                        ("dTMax", 11394 + ho, (5, 100, 5), (10, 200, 10), True),
                    ),
                ),
            ),
        )
        prefix = root + "_Zirkulation"
        variant = prefix + "_Variante"
        thermal = (
            sensor,
            _activity_thermal(
                prefix,
                (
                    ("TEin", 11408 + ho, (100, 590, 10), (500, 1380, 20), False),
                    ("TAus", 11411 + ho, (110, 600, 10), (520, 1400, 20), False),
                ),
                (("TAus", 11411 + ho, "TEin", 11408 + ho),),
            ),
        )
        timer = (WeeklyProgram(root + "_SchaltuhrNr", 11346 + ho, True),)
        demand = (
            RangeDependency(root + "_SensorSchalter1_Nr", 11311 + ho, 1, 65),
            _raw_steps(prefix + "_dtLaufzeit", 11406 + ho, 60, 900, 15),
            _raw_steps(prefix + "_dtPausenzeit", 11407 + ho, 10, 60, 1),
            _raw_steps(prefix + "_dtVerzoegerung", 11414 + ho, 0, 3, 1),
        )
        # Native Type 4 derives the applicable predicates from its variant,
        # not from the separate DHW timer flag. Preserve the installed variant.
        circulation = _activity_setting(
            root,
            11273 + ho,
            Dependency(root + "_Type", 11266 + ho, (4,)),
            (
                output,
                AnyDependencies(
                    tuple(
                        (Dependency(variant, 11405 + ho, (mode,)), *requirements)
                        for mode, requirements in enumerate(
                            (thermal, timer, thermal + timer, demand, demand + timer)
                        )
                    )
                ),
            ),
        )
        yield replace(
            circulation,
            dependencies=(Dependency(variant, 11405 + ho, (0, 1, 2, 3, 4)),),
        )

        so = (slot - 1) * 120
        root = f"Solar_Wf{slot}"
        for kind, name, index in (
            (6, "NhUnterdrueckung", 4461),
            (7, "Parallelrelais", 4464),
        ):
            yield _activity_setting(
                root,
                4415 + so,
                Dependency(root + "_Type", 4409 + so, (kind,)),
                (
                    RangeDependency(root + "_Relais1_Nr", 4436 + so, 1, 100),
                    RangeDependency("Solar_Relais1_Nr", 3452, 1, 100),
                    RangeDependency("Solar_Kol1_Sensor1_Nr", 3536, 1, 65),
                    SolarSelection(
                        root + "_" + name + "_SpeicherMaske", index + so, stores=True
                    ),
                ),
            )

    for slot in range(1, 8):
        offset = (slot - 1) * 133
        root = f"Wmz{slot}"
        # The controller owns activity even when the telemetry meter vanishes.
        measured = _activity_setting(
            root,
            17263 + offset,
            Dependency(root + "_Type", 17260 + offset, (1,)),
            (
                RangeDependency(root + "_Sensor1_Nr", 17359 + offset, 1, 65),
                RangeDependency(root + "_Sensor2_Nr", 17363 + offset, 1, 65),
                RangeDependency(root + "_Sensor3_Nr", 17367 + offset, 1, 65),
            ),
        )
        yield replace(
            measured,
            dependencies=(
                Dependency(root + "_OVolumenstromSensor", 17270 + offset, (1,)),
                Dependency(root + "_OBezugsrelais1", 17371 + offset, (0,)),
            ),
        )
        yield _activity_setting(
            root,
            17263 + offset,
            Dependency(root + "_Type", 17260 + offset, (2,)),
            (
                RangeDependency(
                    root + "_Impulszaehler_Sensor1_Nr", 17379 + offset, 1, 65
                ),
            ),
        )


def _dedicated_solar_activity():
    """Activity of assigned native solar functions, never install or reassign."""
    topology = Dependency("Solar_SystemId", 3416, tuple(sorted(BX_PLUS_SOLAR_SYSTEMS)))
    output = RangeDependency("Solar_Relais1_Nr", 3452, 1, 100)
    for slot in range(1, 4):
        offset = (slot - 1) * 18
        root = f"Solar_Wf{slot}_Roehrenkollektor"
        mask = root + "_KollektorMaske"
        setting = _activity_setting(
            root,
            3834 + offset,
            RangeDependency(mask, 3839 + offset, 1, 7),
            (
                output,
                _raw_steps(root + "_tBeginn", 3840 + offset, 0, 1410, 30),
                _raw_steps(root + "_tEnde", 3841 + offset, 0, 1410, 30),
                PeerComparison(
                    root + "_tEnde",
                    3841 + offset,
                    root + "_tBeginn",
                    3840 + offset,
                    "gt",
                ),
                _raw_steps(root + "_Laufzeit", 3842 + offset, 5, 600, 5),
                RangeDependency(root + "_Pausezeit", 3843 + offset, 1, 60),
            ),
        )
        yield replace(setting, dependencies=(SolarSelection(mask, 3839 + offset),))

        offset = (slot - 1) * 31
        root = f"Solar_Wf{slot}_Bypass"
        setting = _activity_setting(
            root,
            3888 + offset,
            RangeDependency(root + "_Sensor1_Nr", 3896 + offset, 1, 65),
            (
                output,
                RangeDependency(root + "_Relais1_Nr", 3900 + offset, 1, 100),
                Dependency(root + "_Variante", 3894 + offset, (0, 1)),
                _activity_thermal(
                    root,
                    (
                        ("dTein", 3908 + offset, (10, 200, 5), (20, 400, 10), True),
                        ("dTaus", 3911 + offset, (5, 195, 5), (10, 390, 10), True),
                    ),
                    (("dTein", 3908 + offset, "dTaus", 3911 + offset),),
                ),
            ),
        )
        yield replace(
            setting,
            dependencies=(SolarSelection(root + "_KollektorMaske", 3893 + offset),),
        )

    root = "Solar_Wf1_CsBypass"
    setting = _activity_setting(
        root,
        3981,
        RangeDependency(root + "_SensorEinstrahlung_Nr", 3988, 1, 65),
        (
            output,
            _raw_steps(root + "_Einstrahlung", 3991, 100, 500, 50),
            RangeDependency(root + "_Verzoegerung", 3992, 10, 300),
        ),
    )
    yield replace(
        setting, dependencies=(SolarSelection(root + "_KollektorMaske", 3986),)
    )

    root = "Solar_Wf1_Zieltemperatur"
    setting = _activity_setting(
        root,
        4335,
        RangeDependency(root + "_Sensor1_Nr", 4341, 1, 65),
        (
            output,
            RangeDependency("Solar_Kol1_Sensor1_Nr", 3536, 1, 65),
            RangeDependency("Solar_Sp1_Sensor1_Nr", 3576, 1, 65),
            _installed_activity("Solar_Sp1", 3604, 3606),
            _activity_thermal(
                root,
                (
                    ("Zieltemperatur", 4344, (200, 1100, 10), (680, 2300, 20), False),
                    ("Anstieg", 4347, (10, 200, 5), (20, 400, 10), True),
                ),
            ),
        ),
    )
    yield replace(setting, dependencies=(topology,))


BX_PLUS_ACTIVITY_SETTINGS = (
    *_installed_function_activity(),
    *_dedicated_solar_activity(),
)


def _native_coordination_settings():
    """Existing function interlocks and flow thresholds, without registration changes."""
    for slot in range(1, 17):
        ho = (slot - 1) * 151
        root = f"Heizung_Wf{slot}"
        mode = root + "_ThBwErwaermung_Modus"
        yield Setting(
            root + "_OFeststoffkesselAus",
            11285 + ho,
            f"Heating function {slot} solid fuel suppression",
            "switch",
            True,
            installed_by=Dependency(root + "_Type", 11266 + ho, (2,)),
            dependencies=(
                _installed_activity(root, 11273 + ho, 11275 + ho),
                RangeDependency(root + "_NhRelais1_Nr", 11337 + ho, 1, 100),
                RangeDependency(root + "_Sensor1_Nr", 11290 + ho, 1, 65),
                AnyDependencies(
                    (
                        (Dependency(mode, 11378 + ho, (0,)),),
                        (
                            Dependency(mode, 11378 + ho, (1,)),
                            RangeDependency(root + "_Sensor2_Nr", 11294 + ho, 1, 65),
                        ),
                    )
                ),
            ),
            requested_conditions=(
                RequestedCondition(
                    1, (FunctionSelection(root + "_FeststoffkesselMaske", 11286 + ho),)
                ),
            ),
        )
        ao = (slot - 1) * 251
        root = f"Anlage_Wf{slot}"
        prefix = root + "_RLBeimischung"
        selection = FunctionSelection(
            prefix + "_HeizkreisInternMaske", 6783 + ao, heating=True
        )
        mixer = (
            _installed_activity(root, 6566 + ao, 6568 + ao),
            *(
                RangeDependency(root + f"_Sensor{n}_Nr", 6576 + ao + 4 * (n - 1), 1, 65)
                for n in range(1, 4)
            ),
            RangeDependency(root + "_Relais1_Nr", 6592 + ao, 1, 100),
            RangeDependency(root + "_Relais2_Nr", 6600 + ao, 1, 100),
            AnyDependencies(
                (
                    (
                        PeerComparison(
                            root + "_Relais1_Nr",
                            6592 + ao,
                            root + "_Relais2_Nr",
                            6600 + ao,
                            "ne",
                        ),
                    ),
                )
            ),
            RangeDependency(prefix + "_Mischerstellzeit", 6772 + ao, 1, 20),
            _activity_thermal(
                prefix,
                (
                    ("dTEin", 6759 + ao, (10, 250, 5), (20, 500, 10), True),
                    ("dTAus", 6762 + ao, (5, 240, 5), (10, 480, 10), True),
                    ("dTSoll", 6765 + ao, (-200, 250, 10), (-400, 500, 20), True),
                    (
                        "TMax_Kesselruecklauf",
                        6768 + ao,
                        (100, 800, 10),
                        (500, 1760, 20),
                        False,
                    ),
                ),
                (("dTEin", 6759 + ao, "dTAus", 6762 + ao),),
            ),
        )
        yield Setting(
            prefix + "_OHeizkreisIntern",
            6782 + ao,
            f"Arrangement function {slot} internal heating circuit return mixing",
            "switch",
            True,
            installed_by=Dependency(root + "_Type", 6559 + ao, (11,)),
            dependencies=mixer,
            requested_conditions=(
                RequestedCondition(1, (selection,)),
                RequestedCondition(
                    0,
                    (
                        AnyDependencies(
                            (
                                (
                                    RangeDependency(
                                        prefix + "_Mischerlaufzeit", 6771 + ao, 10, 600
                                    ),
                                    _raw_steps(
                                        prefix + "_Justierzeit", 6773 + ao, 0, 1380, 60
                                    ),
                                ),
                            )
                        ),
                    ),
                ),
            ),
        )
        prefix = root + "_Funktionsblock"
        yield from _operating_pair(
            prefix + "_VolumenstromEin",
            6748 + ao,
            prefix + "_VolumenstromAus",
            6751 + ao,
            f"Arrangement function {slot} flow predicate",
            Dependency(root + "_Type", 6559 + ao, (9,)),
            (
                _installed_activity(root, 6566 + ao, 6568 + ao),
                Dependency(prefix + "_OVolumenstrom", 6747 + ao, (1,)),
                RangeDependency(root + "_SensorVolumenstrom1_Nr", 6804 + ao, 1, 65),
                RangeDependency(root + "_Relais1_Nr", 6592 + ao, 1, 100),
            ),
            (
                (
                    0,
                    "_LpMin",
                    "L/min",
                    ("1.0", "999.0", "0.1", "0.1"),
                    ("0.5", "998.5", "0.1", "0.1"),
                ),
                (
                    1,
                    "_GpMin",
                    "gal/min",
                    ("0.3", "263.9", "0.1", "0.1"),
                    ("0.1", "263.8", "0.1", "0.1"),
                ),
            ),
            rising=True,
            unit_selector=("FlowSelector", 61),
        )
    for circuit in range(1, 8):
        off = (circuit - 1) * 495
        root = f"Heizung_Heizkreis{circuit}"
        prefix = root + "_Nachheizung"
        yield Setting(
            prefix + "_OFeststoffkesselAus",
            14037 + off,
            f"Heating circuit {circuit} solid fuel suppression",
            "switch",
            True,
            installed_by=Dependency(root + "_Type", 13684 + off, (1,)),
            dependencies=(
                _installed_activity(root, 13689 + off, 13691 + off),
                Dependency(root + "_System", 13697 + off, (0, 2)),
                Dependency(root + "_ONachheizung", 13705 + off, (1,)),
                _installed_activity(prefix, 13708 + off, 13710 + off),
                RangeDependency(root + "_Relais6_Nr", 14003 + off, 1, 100),
                AnyDependencies(
                    (
                        (
                            Dependency(prefix + "_Modus", 13706 + off, (0,)),
                            RangeDependency(root + "_Sensor4_Nr", 13804 + off, 1, 65),
                        ),
                        (
                            Dependency(prefix + "_Modus", 13706 + off, (1,)),
                            RangeDependency(root + "_Sensor4_Nr", 13804 + off, 1, 65),
                            RangeDependency(root + "_Sensor5_Nr", 13808 + off, 1, 65),
                        ),
                        (Dependency(prefix + "_Modus", 13706 + off, (2,)),),
                    )
                ),
            ),
            requested_conditions=(
                RequestedCondition(
                    1,
                    (FunctionSelection(prefix + "_FeststoffkesselMaske", 14038 + off),),
                ),
            ),
        )


BX_PLUS_COORDINATION_OPTIONS += tuple(_native_coordination_settings())


def _heating_operating_settings():
    """Documented operating controls; existing flow protection is read-only.

    BX Plus manual from firmware 2.07: constant flow setpoint is bounded
    by configured flow limits; energy saving needs an assigned return
    sensor. Exact 2.08 catalogue validates every slot and selected unit.
    """
    for slot in range(1, 3):
        offset = (slot - 1) * 221
        root = f"Heizung_RelaisAnforderung{slot}"
        common = (
            Dependency(f"{root}_Aktiviert_Modus", 10727 + offset, (1,)),
            Dependency(f"{root}_OMindestlaufzeit", 10798 + offset, (1,)),
        )
        # Mutually exclusive variants: relay demand or analogue-only
        # demand. Never create two entities for the same parameter.
        for output in (
            (
                Dependency(f"{root}_ORelais1", 10734 + offset, (1,)),
                RangeDependency(f"{root}_Relais1_Nr", 10735 + offset, 1, 45),
            ),
            (
                Dependency(f"{root}_ORelais1", 10734 + offset, (0,)),
                Dependency(f"{root}_OAusgang1", 10744 + offset, (1,)),
                RangeDependency(f"{root}_Ausgang1_Nr", 10745 + offset, 1, 4),
            ),
        ):
            yield Setting(
                f"{root}_Mindestlaufzeit_tMin",
                10799 + offset,
                f"Heating demand {slot} minimum runtime",
                "number",
                True,
                limits=("0", "120", "5", "1"),
                unit="min",
                installed_by=Dependency(f"{root}_Type", 10724 + offset, (1,)),
                dependencies=(*common, *output),
            )
        # Ordinary flow correction of an already installed analogue
        # heating demand. Leave the device voltage/temperature calibration,
        # fixed voltage and protection limits to commissioning.
        installed = Dependency(f"{root}_Type", 10724 + offset, (1,))
        output = (
            Dependency(f"{root}_Aktiviert_Modus", 10727 + offset, (1,)),
            Dependency(f"{root}_OAusgang1", 10744 + offset, (1,)),
            RangeDependency(f"{root}_Ausgang1_Nr", 10745 + offset, 1, 4),
        )
        feedback = (
            *output,
            Dependency(f"{root}_Ausgang_OVorlauf", 10773 + offset, (1,)),
            RangeDependency(
                f"{root}_Ausgang_Vorlauf_Sensor1_Nr", 10775 + offset, 1, 65
            ),
        )
        for suffix, index, label, k, r, deps in (
            (
                "dTSollVorlauf",
                10770,
                "flow target offset",
                ("0", "20", "1", "0.1"),
                ("0", "40", "2", "0.1"),
                output,
            ),
            (
                "Vorlauf_dTHysterese",
                10778,
                "flow correction hysteresis",
                ("0.5", "20", "0.5", "0.1"),
                ("1", "40", "1", "0.1"),
                feedback,
            ),
        ):
            name = f"{root}_Ausgang_{suffix}"
            index += offset
            for selector, alias, unit, limits in (
                (0, "_K", "K", k),
                (1, "_R", "°R", r),
            ):
                yield Setting(
                    name,
                    index,
                    f"Heating demand {slot} {label}",
                    "number",
                    True,
                    limits=limits,
                    unit=unit,
                    installed_by=installed,
                    dependencies=(
                        *deps,
                        Dependency("TemperatureHysteresisSelector", 60, (selector,)),
                    ),
                    compound_mirrors=(
                        (name, index, name + alias, index + selector + 1),
                    ),
                )
        for suffix, index, label, limits, unit in (
            ("dVKorrektur", 10781, "voltage correction", ("0", "1", "0.1", "0.1"), "V"),
            ("dtIntervall", 10782, "correction interval", ("10", "600", "5", "1"), "s"),
        ):
            yield Setting(
                f"{root}_Ausgang_Vorlauf_{suffix}",
                index + offset,
                f"Heating demand {slot} {label}",
                "number",
                True,
                limits=limits,
                unit=unit,
                installed_by=installed,
                dependencies=feedback,
            )
    for slot in range(1, 3):
        offset = (slot - 1) * 29
        root = f"Heizung_RelaisPumpe{slot}"
        installed = Dependency(f"{root}_Type", 11166 + offset, (2,))
        common = (
            Dependency(f"{root}_Aktiviert_Modus", 11169 + offset, (1,)),
            RangeDependency(f"{root}_Relais1_Nr", 11171 + offset, 1, 100),
        )
        for phase, mode, temperature, duration, label in (
            ("Anlauf", 11178, 11179, 11182, "start"),
            ("Nachlauf", 11183, 11184, 11187, "overrun"),
        ):
            yield Setting(
                f"{root}_{phase}_dt{phase}",
                duration + offset,
                f"Heating pump {slot} {label} duration",
                "number",
                True,
                limits=("0", "300", "1", "1"),
                unit="s",
                installed_by=installed,
                dependencies=(
                    *common,
                    Dependency(f"{root}_{phase}_Modus", mode + offset, (2,)),
                ),
            )
            name = f"{root}_{phase}_T{phase}"
            index = temperature + offset
            for selector, alias, unit, limits in (
                (0, "_C", "°C", ("10", "90", "1", "0.1")),
                (1, "_F", "°F", ("50", "194", "2", "0.1")),
            ):
                yield Setting(
                    name,
                    index,
                    f"Heating pump {slot} {label} temperature",
                    "number",
                    True,
                    limits=limits,
                    unit=unit,
                    installed_by=installed,
                    dependencies=(
                        *common,
                        Dependency(f"{root}_{phase}_Modus", mode + offset, (1,)),
                        RangeDependency(
                            f"{root}_AnlaufNachlauf_Sensor1_Nr", 11189 + offset, 1, 65
                        ),
                        Dependency("TemperatureHysteresisSelector", 60, (selector,)),
                    ),
                    compound_mirrors=(
                        (name, index, name + alias, index + selector + 1),
                    ),
                )
    for circuit in range(1, 8):
        offset = (circuit - 1) * 495
        root = f"Heizung_Heizkreis{circuit}"
        installed = Dependency(f"{root}_Type", 13684 + offset, (1,))
        active = Dependency(f"{root}_Aktiviert_Modus", 13689 + offset, (1, 2))
        heating = Dependency(f"{root}_System", 13697 + offset, (0, 2))
        constant = Dependency(f"{root}_Heizsystem", 13978 + offset, (2,))
        flow = RangeDependency(f"{root}_Sensor1_Nr", 13796 + offset, 1, 65)
        outdoor = RangeDependency(f"{root}_Sensor2_Nr", 13800 + offset, 1, 65)
        mode_name = f"{root}_Heizsystem"
        mode_index = 13978 + offset
        rooms = AnyDependencies(
            tuple(
                (
                    Dependency(f"{room}_Option", 13819 + delta, (1,)),
                    Dependency(f"{room}_Aktiviert", 13820 + delta, (1,)),
                    Dependency(f"{room}_Typ", 13823 + delta, (0,)),
                    RangeDependency(f"{room}_Sensor1_Nr", 13834 + delta, 1, 65),
                )
                for n in range(1, 6)
                for room, delta in (
                    (f"{root}_Raumthermostat{n}", offset + 32 * (n - 1)),
                )
            )
        )
        # The native interpolator handles either endpoint order, including
        # identical outdoor points (first flow target fallback).
        for suffix, index, label, celsius, fahrenheit, dependencies in (
            (
                "Gerade_TAussen1",
                13979,
                "linear outdoor point 1",
                ("-20", "20"),
                ("-4", "68"),
                (Dependency(mode_name, mode_index, (3,)), outdoor),
            ),
            (
                "Gerade_TVorlauf1",
                13982,
                "linear flow point 1",
                ("20", "90"),
                ("68", "194"),
                (Dependency(mode_name, mode_index, (3,)), outdoor),
            ),
            (
                "Gerade_TAussen2",
                13985,
                "linear outdoor point 2",
                ("-20", "20"),
                ("-4", "68"),
                (Dependency(mode_name, mode_index, (3,)), outdoor),
            ),
            (
                "Gerade_TVorlauf2",
                13988,
                "linear flow point 2",
                ("20", "90"),
                ("68", "194"),
                (Dependency(mode_name, mode_index, (3,)), outdoor),
            ),
            (
                "Raum_TStart",
                13991,
                "room control initial flow temperature",
                ("20", "60"),
                ("68", "140"),
                (Dependency(mode_name, mode_index, (4,)), rooms),
            ),
        ):
            name = f"{root}_Heizsystem_{suffix}"
            index += offset
            for selector, alias, unit, bounds, step in (
                (0, "_C", "°C", celsius, "1"),
                (1, "_F", "°F", fahrenheit, "2"),
            ):
                yield Setting(
                    name,
                    index,
                    f"Heating circuit {circuit} {label}",
                    "number",
                    True,
                    limits=(*bounds, step, "0.1"),
                    unit=unit,
                    installed_by=installed,
                    dependencies=(
                        active,
                        heating,
                        flow,
                        *dependencies,
                        Dependency("TemperatureHysteresisSelector", 60, (selector,)),
                    ),
                    compound_mirrors=(
                        (name, index, name + alias, index + selector + 1),
                    ),
                )
        yield Setting(
            f"{root}_Heizsystem_Raumeinfluss_Faktor",
            14174 + offset,
            f"Heating circuit {circuit} room influence factor",
            "number",
            True,
            limits=("1", "9", "1", "1"),
            installed_by=installed,
            dependencies=(
                active,
                heating,
                flow,
                outdoor,
                rooms,
                Dependency(mode_name, mode_index, (5,)),
            ),
        )
        yield Setting(
            f"{root}_Heizsystem_Raumeinfluss_Abweichung",
            14175 + offset,
            f"Heating circuit {circuit} room deviation aggregation",
            "select",
            True,
            options=(("Minimum", 1), ("Average", 2), ("Maximum", 3)),
            limits=("1", "3", "1", "1"),
            installed_by=installed,
            dependencies=(
                active,
                heating,
                flow,
                rooms,
                AnyDependencies(
                    (
                        (Dependency(mode_name, mode_index, (4,)),),
                        (Dependency(mode_name, mode_index, (5,)), outdoor),
                    )
                ),
            ),
        )
        mixer = (
            active,
            heating,
            RangeDependency(f"{root}_Sensor1_Nr", 13796 + offset, 1, 65),
            RangeDependency(f"{root}_Relais2_Nr", 13772 + offset, 1, 100),
            RangeDependency(f"{root}_Relais3_Nr", 13780 + offset, 1, 100),
        )
        yield Setting(
            f"{root}_MischerIntervall",
            13719 + offset,
            f"Heating circuit {circuit} mixer interval",
            "number",
            True,
            limits=("1", "20", "1", "1"),
            unit="s",
            installed_by=installed,
            dependencies=mixer,
        )
        # Native pair callbacks require a larger separation than the
        # selected-unit editing increment (0.5 K or 1 degree Rankine).
        for setting in _operating_pair(
            f"{root}_Mischer_dTEin",
            14168 + offset,
            f"{root}_Mischer_dTAus",
            14171 + offset,
            f"Heating circuit {circuit} mixer hysteresis",
            installed,
            mixer,
            (
                (
                    0,
                    "_K",
                    "K",
                    ("0.5", "5", "0.1", "0.1"),
                    ("0", "4.5", "0.1", "0.1"),
                ),
                (
                    1,
                    "_R",
                    "°R",
                    ("1", "10", "0.2", "0.1"),
                    ("0", "9", "0.2", "0.1"),
                ),
            ),
            rising=True,
        ):
            peer = setting.relative_constraints[0]
            yield replace(
                setting,
                relative_constraints=(
                    replace(
                        peer,
                        operation="ge" if peer.operation == "gt" else "le",
                        margin=5 if setting.unit == "K" else 10,
                    ),
                ),
            )
        prefix = f"{root}_Nachheizung"
        # The zone mode uses two sensors; the thermostat mode uses only
        # sensor 4. Mutually exclusive variants preserve the same native ID.
        for mode in (0, 1):
            supplementary = (
                active,
                heating,
                Dependency(f"{root}_ONachheizung", 13705 + offset, (1,)),
                Dependency(f"{prefix}_Modus", 13706 + offset, (mode,)),
                Dependency(f"{prefix}_Aktiviert_Modus", 13708 + offset, (1, 2)),
                RangeDependency(f"{root}_Relais6_Nr", 14003 + offset, 1, 100),
                RangeDependency(f"{root}_Sensor4_Nr", 13804 + offset, 1, 65),
                *(
                    (RangeDependency(f"{root}_Sensor5_Nr", 13808 + offset, 1, 65),)
                    if mode == 1
                    else ()
                ),
            )
            yield from _operating_pair(
                f"{prefix}_dTEin",
                13747 + offset,
                f"{prefix}_dTAus",
                13750 + offset,
                f"Heating circuit {circuit} supplementary heating differential",
                installed,
                supplementary,
                (
                    (
                        0,
                        "_K",
                        "K",
                        ("-15", "44.5", "0.5", "0.1"),
                        ("-14.5", "45", "0.5", "0.1"),
                    ),
                    (
                        1,
                        "_R",
                        "°R",
                        ("-30", "89", "1", "0.1"),
                        ("-29", "90", "1", "0.1"),
                    ),
                ),
                rising=False,
            )
            for suffix, index, flag, flag_index, label in (
                ("tFruehstart", 13715, "OFruehstart", 13714, "early start"),
                ("tBlockierung", 13717, "OBlockierung", 13716, "blocking"),
            ):
                yield Setting(
                    f"{prefix}_{suffix}",
                    index + offset,
                    f"Heating circuit {circuit} supplementary heating {label}",
                    "number",
                    True,
                    limits=("0", "300", "1", "1"),
                    unit="min",
                    installed_by=installed,
                    dependencies=(
                        *supplementary,
                        Dependency(f"{prefix}_{flag}", flag_index + offset, (1,)),
                    ),
                )
        cooling = f"{root}_Kuehlung"
        cooling_common = (
            active,
            Dependency(f"{root}_System", 13697 + offset, (1, 2)),
            RangeDependency(f"{cooling}_Relais1_Nr", 14059 + offset, 1, 100),
        )
        # The native menu distinguishes the cooling demand trigger from
        # the selected constant/linear flow-temperature representation.
        # Preserve the configured protective limits and dew-point controls.
        for suffix, index, label, celsius, fahrenheit, extra in (
            (
                "Aussen_TAussen",
                14072,
                "outdoor cooling threshold",
                ("20", "40"),
                ("68", "104"),
                (
                    outdoor,
                    Dependency(f"{cooling}_Modus", 14066 + offset, (0, 2)),
                ),
            ),
            (
                "Kuehlsystem_TVorlauf",
                14076,
                "constant cooling flow target",
                ("5", "25"),
                ("40", "76"),
                (flow, Dependency(f"{cooling}_Kuehlsystem", 14075 + offset, (0,))),
            ),
            (
                "Kuehlsystem_TAussen1",
                14080,
                "linear cooling outdoor point 1",
                ("15", "45"),
                ("60", "114"),
                (
                    flow,
                    outdoor,
                    Dependency(f"{cooling}_Kuehlsystem", 14075 + offset, (1,)),
                ),
            ),
            (
                "Kuehlsystem_TVorlauf1",
                14083,
                "linear cooling flow point 1",
                ("5", "25"),
                ("40", "76"),
                (
                    flow,
                    outdoor,
                    Dependency(f"{cooling}_Kuehlsystem", 14075 + offset, (1,)),
                ),
            ),
            (
                "Kuehlsystem_TAussen2",
                14086,
                "linear cooling outdoor point 2",
                ("15", "45"),
                ("60", "114"),
                (
                    flow,
                    outdoor,
                    Dependency(f"{cooling}_Kuehlsystem", 14075 + offset, (1,)),
                ),
            ),
            (
                "Kuehlsystem_TVorlauf2",
                14089,
                "linear cooling flow point 2",
                ("5", "25"),
                ("40", "76"),
                (
                    flow,
                    outdoor,
                    Dependency(f"{cooling}_Kuehlsystem", 14075 + offset, (1,)),
                ),
            ),
        ):
            name = f"{cooling}_{suffix}"
            index += offset
            for selector, alias, unit, bounds, step in (
                (0, "_C", "°C", celsius, "1"),
                (1, "_F", "°F", fahrenheit, "2"),
            ):
                yield Setting(
                    name,
                    index,
                    f"Heating circuit {circuit} {label}",
                    "number",
                    True,
                    limits=(*bounds, step, "0.1"),
                    unit=unit,
                    installed_by=installed,
                    dependencies=(
                        *cooling_common,
                        *extra,
                        Dependency("TemperatureHysteresisSelector", 60, (selector,)),
                    ),
                    compound_mirrors=(
                        (name, index, name + alias, index + selector + 1),
                    ),
                )
        for suffix, index, label in (
            ("tEin", 14099, "start"),
            ("tAus", 14100, "end"),
        ):
            yield Setting(
                f"{root}_Kuehlung_Timer_{suffix}",
                index + offset,
                f"Heating circuit {circuit} cooling timer {label}",
                "number",
                True,
                limits=("0", "1425", "15", "1"),
                unit="min",
                installed_by=installed,
                dependencies=(
                    active,
                    Dependency(f"{root}_System", 13697 + offset, (1, 2)),
                    Dependency(f"{root}_Kuehlung_OTimer", 14098 + offset, (1,)),
                    RangeDependency(
                        f"{root}_Kuehlung_Relais1_Nr", 14059 + offset, 1, 100
                    ),
                ),
            )
        minimum = f"{root}_TVorlaufMin"
        maximum = f"{root}_TVorlaufMax"
        target = f"{root}_TVorlaufVorgabe"
        indices = (13814 + offset, 13733 + offset, 14022 + offset)
        for selector, alias, unit, limits, low, high in (
            (
                0,
                "_C",
                "°C",
                ("10", "90", "1", "0.1"),
                ("20", "89", "1", "0.1"),
                ("21", "90", "1", "0.1"),
            ),
            (
                1,
                "_F",
                "°F",
                ("50", "194", "2", "0.1"),
                ("68", "192", "2", "0.1"),
                ("70", "194", "2", "0.1"),
            ),
        ):
            yield Setting(
                target,
                indices[2],
                f"Heating circuit {circuit} constant flow setpoint",
                "number",
                True,
                limits=limits,
                unit=unit,
                installed_by=installed,
                dependencies=(
                    active,
                    heating,
                    constant,
                    Dependency("TemperatureHysteresisSelector", 60, (selector,)),
                ),
                relative_constraints=(
                    RelativeConstraint(minimum, indices[0], "ge"),
                    RelativeConstraint(maximum, indices[1], "le"),
                ),
                compound_mirrors=tuple(
                    (name, index, name + alias, index + selector + 1)
                    for name, index in zip(
                        (minimum, maximum, target), indices, strict=True
                    )
                ),
                peer_limits=((minimum, low), (maximum, high)),
            )
        prefix = f"{root}_Energiesparbetrieb"
        deps = (
            active,
            heating,
            Dependency(f"{root}_OEnergiesparbetrieb", 14158 + offset, (1,)),
            RangeDependency(f"{prefix}_Sensor1_Nr", 14160 + offset, 1, 65),
        )
        name = f"{prefix}_dTAus"
        index = 14163 + offset
        for selector, alias, unit, limits in (
            (0, "_K", "K", ("1", "49", "1", "0.1")),
            (1, "_R", "°R", ("2", "98", "2", "0.1")),
        ):
            yield Setting(
                name,
                index,
                f"Heating circuit {circuit} energy saving off differential",
                "number",
                True,
                limits=limits,
                unit=unit,
                installed_by=installed,
                dependencies=(
                    *deps,
                    Dependency("TemperatureHysteresisSelector", 60, (selector,)),
                ),
                compound_mirrors=((name, index, name + alias, index + selector + 1),),
            )
        for suffix, index, label in (
            ("dtPause", 14166, "pause"),
            ("dtLaufzeit", 14167, "runtime"),
        ):
            yield Setting(
                f"{prefix}_{suffix}",
                index + offset,
                f"Heating circuit {circuit} energy saving {label}",
                "number",
                True,
                limits=("0", "60", "1", "1"),
                unit="min",
                installed_by=installed,
                dependencies=deps,
            )


BX_PLUS_HEATING_OPERATING_SETTINGS = tuple(_heating_operating_settings())


def _solar_store_settings():
    """Native store adjustments, not a guessed SystemId-to-topology mapping.

    The exact firmware has a volatile assigned store sensor, first collector
    sensor and solar output reference. Require all three live, plus the
    native store's active mode. Defaults for uninstalled stores are readable
    but never establish applicability. Emergency limits remain read-only.
    """
    for slot in range(1, 6):
        offset = (slot - 1) * 36
        root = f"Solar_Sp{slot}"
        installed = RangeDependency(f"{root}_Sensor1_Nr", 3576 + offset, 1, 65)
        deps = (
            RangeDependency("Solar_Kol1_Sensor1_Nr", 3536, 1, 65),
            RangeDependency("Solar_Relais1_Nr", 3452, 1, 100),
            Dependency(f"{root}_Aktiviert_Modus", 3604 + offset, (1, 2)),
        )
        # Native priority maximum is the actual number of stores, not the
        # static five-slot catalogue limit. BX Plus has no five-store system.
        if slot <= 4:
            systems = tuple(
                sorted(
                    s for s in BX_PLUS_SOLAR_SYSTEMS if (s // 100) % 100 >= max(2, slot)
                )
            )
            yield Setting(
                f"{root}_Vorrang",
                3600 + offset,
                f"Solar store {slot} loading priority",
                "number",
                True,
                limits=("1", "5", "1", "1"),
                dependencies=(*deps, Dependency("Solar_SystemId", 3416, systems)),
                installed_by=installed,
                relative_constraints=(
                    RelativeConstraint("Solar_SystemId", 3416, "solar_store_priority"),
                ),
            )
        for selector, alias, unit, multiplier in (
            (0, "_K", "K", 1),
            (1, "_R", "°R", 2),
        ):
            entries = (
                ("dTein", 3579, ("1", "20", "0.5"), "on differential"),
                ("dTaus", 3582, ("0.5", "19.5", "0.5"), "off differential"),
                ("dTsoll", 3585, ("1.5", "30", "0.5"), "target differential"),
            )
            rows = tuple(
                (
                    f"{root}_{name}",
                    index + offset,
                    tuple(str(Decimal(value) * multiplier) for value in bounds)
                    + ("0.1",),
                    label,
                )
                for name, index, bounds, label in entries
            )
            mirrors = tuple(
                (name, index, name + alias, index + selector + 1)
                for name, index, _, _ in rows
            )
            rank = (1, 0, 2)
            for position, (name, index, limits, label) in enumerate(rows):
                others = tuple(
                    (other_position, other, other_index, bounds)
                    for other_position, (other, other_index, bounds, _) in enumerate(
                        rows
                    )
                    if other_position != position
                )
                yield Setting(
                    name,
                    index,
                    f"Solar store {slot} {label}",
                    "number",
                    True,
                    limits=limits,
                    unit=unit,
                    dependencies=(
                        *deps,
                        Dependency("TemperatureHysteresisSelector", 60, (selector,)),
                    ),
                    installed_by=installed,
                    relative_constraints=tuple(
                        RelativeConstraint(
                            other,
                            other_index,
                            "gt" if rank[position] > rank[i] else "lt",
                        )
                        for i, other, other_index, _ in others
                    ),
                    compound_mirrors=mirrors,
                    peer_limits=tuple(
                        (other, bounds) for _, other, _, bounds in others
                    ),
                )
            for suffix, index, bounds, label in (
                ("Anstieg", 3597, ("1", "20", "0.5"), "rise"),
                ("Hmax", 3594, ("0.1", "10", "0.1"), "store hysteresis"),
            ):
                name = f"{root}_{suffix}"
                index += offset
                yield Setting(
                    name,
                    index,
                    f"Solar store {slot} {label}",
                    "number",
                    True,
                    limits=tuple(str(Decimal(value) * multiplier) for value in bounds)
                    + ("0.1",),
                    unit=unit,
                    dependencies=(
                        *deps,
                        Dependency("TemperatureHysteresisSelector", 60, (selector,)),
                    ),
                    installed_by=installed,
                    compound_mirrors=(
                        (name, index, name + alias, index + selector + 1),
                    ),
                )
        for selector, alias, unit, limits in (
            (0, "_C", "°C", ("4", "95", "1", "0.1")),
            (1, "_F", "°F", ("40", "204", "2", "0.1")),
        ):
            target = f"{root}_Tsoll"
            maximum = f"{root}_Tmax"
            ti, mi = 3588 + offset, 3591 + offset
            mirrors = (
                (target, ti, target + alias, ti + selector + 1),
                (maximum, mi, maximum + alias, mi + selector + 1),
            )
            for name, index, other, other_index, direction, label in (
                (target, ti, maximum, mi, "le", "target temperature"),
                (maximum, mi, target, ti, "ge", "maximum temperature"),
            ):
                yield Setting(
                    name,
                    index,
                    f"Solar store {slot} {label}",
                    "number",
                    True,
                    limits=limits,
                    unit=unit,
                    dependencies=(
                        *deps,
                        Dependency("TemperatureHysteresisSelector", 60, (selector,)),
                    ),
                    installed_by=installed,
                    relative_constraints=(
                        RelativeConstraint(other, other_index, direction),
                    ),
                    compound_mirrors=mirrors,
                    peer_limits=((other, limits),),
                )
        for suffix, index, bounds, unit, label in (
            ("tMin", 3601, ("0", "300", "1", "1"), "s", "minimum runtime"),
            ("MinDrehzahl", 3602, ("20", "100", "1", "1"), "%", "minimum speed"),
        ):
            yield Setting(
                f"{root}_{suffix}",
                index + offset,
                f"Solar store {slot} {label}",
                "number",
                True,
                limits=bounds,
                unit=unit,
                dependencies=deps,
                installed_by=installed,
            )


BX_PLUS_SOLAR_STORE_SETTINGS = tuple(_solar_store_settings())


def _solar_collector_settings():
    """The adjustable operational collector minimum, not emergency shutdown."""
    for slot in range(1, 4):
        offset = (slot - 1) * 14
        root = f"Solar_Kol{slot}"
        installed = RangeDependency(f"{root}_Sensor1_Nr", 3536 + offset, 1, 65)
        name = f"{root}_Tmin"
        index = 3539 + offset
        for selector, alias, unit, limits in (
            (0, "_C", "°C", ("10", "90", "1", "0.1")),
            (1, "_F", "°F", ("50", "194", "2", "0.1")),
        ):
            yield Setting(
                name,
                index,
                f"Solar collector {slot} minimum temperature",
                "number",
                True,
                limits=limits,
                unit=unit,
                installed_by=installed,
                dependencies=(
                    RangeDependency("Solar_Relais1_Nr", 3452, 1, 100),
                    Dependency(f"{root}_Omin", 3533 + offset, (1,)),
                    Dependency("TemperatureHysteresisSelector", 60, (selector,)),
                ),
                compound_mirrors=((name, index, name + alias, index + selector + 1),),
            )


BX_PLUS_SOLAR_COLLECTOR_SETTINGS = tuple(_solar_collector_settings())


def _solar_loading_logic_settings():
    """Operational loading timing only; do not change the hydraulic scheme.

    The BX Plus manual describes sequence control for multi-store systems.
    Require native store/collector/output assignments and the selected loading
    mode, not the numeric digits of SystemId. Adjust optional values only
    while the corresponding native option is enabled.
    """
    installed = RangeDependency("Solar_Kol1_Sensor1_Nr", 3536, 1, 65)
    base = (
        RangeDependency("Solar_Relais1_Nr", 3452, 1, 100),
        RangeDependency("Solar_Sp1_Sensor1_Nr", 3576, 1, 65),
        Dependency("Solar_Sp1_Aktiviert_Modus", 3604, (1, 2)),
    )
    sequence = (
        *base,
        RangeDependency("Solar_Sp2_Sensor1_Nr", 3612, 1, 65),
        Dependency("Solar_Sp2_Aktiviert_Modus", 3640, (1, 2)),
        Dependency("Solar_Beladungstyp", 3417, (0,)),
    )
    for name, index, label, limits, unit, deps in (
        (
            "Pendelpause",
            3418,
            "loading break",
            ("1", "5", "1", "1"),
            "min",
            sequence,
        ),
        (
            "Umwaelzzeit",
            3419,
            "circulation time",
            ("1", "60", "1", "1"),
            "min",
            sequence,
        ),
        (
            "Pausendrehzahl",
            3421,
            "loading break speed",
            ("20", "100", "1", "1"),
            "%",
            (*sequence, Dependency("Solar_OPausendrehzahl", 3420, (1,))),
        ),
        (
            "Pumpenverzoegerung",
            3423,
            "pump delay",
            ("5", "600", "5", "1"),
            "s",
            (*base, Dependency("Solar_OPumpenverzoegerung", 3422, (1,))),
        ),
    ):
        yield Setting(
            f"Solar_{name}",
            index,
            f"Solar {label}",
            "number",
            True,
            limits=limits,
            unit=unit,
            installed_by=installed,
            dependencies=deps,
        )

    # Native option bits are ordinary persisted preferences, not assignments.
    # Reuse the corresponding Number's conditions and validate the stored
    # companion before enabling it; disabling leaves its configured value.
    for name, index, label, deps, companion in (
        (
            "Solar_OPausendrehzahl",
            3420,
            "Solar loading break speed enabled",
            sequence,
            RangeDependency("Solar_Pausendrehzahl", 3421, 20, 100),
        ),
        (
            "Solar_OPumpenverzoegerung",
            3422,
            "Solar pump delay enabled",
            base,
            Dependency("Solar_Pumpenverzoegerung", 3423, tuple(range(5, 601, 5))),
        ),
    ):
        systems = tuple(
            sorted(
                system
                for system in BX_PLUS_SOLAR_SYSTEMS
                if name != "Solar_OPausendrehzahl" or (system // 100) % 100 >= 2
            )
        )
        yield Setting(
            name,
            index,
            label,
            "switch",
            True,
            installed_by=installed,
            dependencies=(*deps, Dependency("Solar_SystemId", 3416, systems)),
            requested_conditions=(RequestedCondition(1, (companion,)),),
        )

    # Native volatile eligibility bit, not a guessed relay/topology mapping.
    # At least two actually active and assigned stores in the selected native
    # system must be present, including systems where store 1 is disabled.
    pairs = AnyDependencies(
        tuple(
            (
                Dependency(
                    "Solar_SystemId",
                    3416,
                    tuple(
                        sorted(
                            system
                            for system in BX_PLUS_SOLAR_SYSTEMS
                            if (system // 100) % 100 >= second
                        )
                    ),
                ),
                *(
                    dep
                    for slot in (first, second)
                    for dep in (
                        RangeDependency(
                            f"Solar_Sp{slot}_Sensor1_Nr", 3576 + 36 * (slot - 1), 1, 65
                        ),
                        Dependency(
                            f"Solar_Sp{slot}_Aktiviert_Modus",
                            3604 + 36 * (slot - 1),
                            (1, 2),
                        ),
                    )
                ),
            )
            for first in range(1, 4)
            for second in range(first + 1, 5)
        )
    )
    name = "Solar_Spreizladung_dTDifferenz"
    for selector, alias, unit, limits in (
        (0, "_K", "K", ("20", "90", "1", "0.1")),
        (1, "_R", "°R", ("40", "180", "2", "0.1")),
    ):
        yield Setting(
            name,
            3425,
            "Solar spread loading temperature differential",
            "number",
            True,
            limits=limits,
            unit=unit,
            installed_by=installed,
            dependencies=(
                RangeDependency("Solar_Relais1_Nr", 3452, 1, 100),
                Dependency("Solar_ParallelbeladungMoeglich", 3439, (1,)),
                Dependency("Solar_OSpreizladung", 3424, (1,)),
                pairs,
                Dependency("TemperatureHysteresisSelector", 60, (selector,)),
            ),
            compound_mirrors=((name, 3425, name + alias, 3426 + selector),),
        )

        # The option itself has stable identity across the selected unit.
        # The companion uses the same exact native parent/child contract.
        minimum, maximum, step = (200, 900, 10) if selector == 0 else (400, 1800, 20)
        yield Setting(
            "Solar_OSpreizladung",
            3424,
            "Solar spread loading enabled",
            "switch",
            True,
            installed_by=installed,
            dependencies=(
                RangeDependency("Solar_Relais1_Nr", 3452, 1, 100),
                Dependency("Solar_ParallelbeladungMoeglich", 3439, (1,)),
                pairs,
                Dependency("TemperatureHysteresisSelector", 60, (selector,)),
            ),
            compound_mirrors=((name, 3425, name + alias, 3426 + selector),),
            requested_conditions=(
                RequestedCondition(
                    1,
                    (Dependency(name, 3425, tuple(range(minimum, maximum + 1, step))),),
                ),
            ),
        )


BX_PLUS_SOLAR_LOADING_LOGIC_SETTINGS = tuple(_solar_loading_logic_settings())


def _solar_dedicated_settings():
    """Ordinary adjustments for independently assigned native solar functions."""
    output = RangeDependency("Solar_Relais1_Nr", 3452, 1, 100)

    def collector_conditions(name, index, mask):
        return (
            output,
            Dependency(name, index, (mask,)),
            *(
                RangeDependency(
                    f"Solar_Kol{slot}_Sensor1_Nr", 3536 + 14 * (slot - 1), 1, 65
                )
                for slot in range(1, 4)
                if mask & (1 << (slot - 1))
            ),
        )

    # The native mask is a read-only selection. Each mutually exclusive
    # contract requires every selected collector, not just a default bit.
    for slot in range(1, 4):
        offset = (slot - 1) * 31
        root = f"Solar_Wf{slot}_Bypass"
        installed = RangeDependency(f"{root}_Sensor1_Nr", 3896 + offset, 1, 65)
        for mask in range(1, 8):
            yield from _operating_pair(
                f"{root}_dTein",
                3908 + offset,
                f"{root}_dTaus",
                3911 + offset,
                f"Solar bypass {slot} differential",
                installed,
                (
                    Dependency(f"{root}_Aktiviert_Modus", 3888 + offset, (1, 2)),
                    Dependency(f"{root}_Variante", 3894 + offset, (0, 1)),
                    RangeDependency(f"{root}_Relais1_Nr", 3900 + offset, 1, 100),
                    *collector_conditions(
                        f"{root}_KollektorMaske", 3893 + offset, mask
                    ),
                ),
                (
                    (
                        0,
                        "_K",
                        "K",
                        ("1", "20", "0.5", "0.1"),
                        ("0.5", "19.5", "0.5", "0.1"),
                    ),
                    (1, "_R", "°R", ("2", "40", "1", "0.1"), ("1", "39", "1", "0.1")),
                ),
                rising=True,
            )
    root = "Solar_Wf1_CsBypass"
    installed = RangeDependency(f"{root}_SensorEinstrahlung_Nr", 3988, 1, 65)
    for mask in range(1, 8):
        for suffix, index, limits, unit, label in (
            ("Einstrahlung", 3991, ("100", "500", "50", "1"), "W/m²", "irradiation"),
            ("Verzoegerung", 3992, ("10", "300", "1", "1"), "s", "delay"),
        ):
            yield Setting(
                f"{root}_{suffix}",
                index,
                f"Solar CS bypass {label}",
                "number",
                True,
                limits=limits,
                unit=unit,
                installed_by=installed,
                dependencies=(
                    Dependency(f"{root}_Aktiviert_Modus", 3981, (1, 2)),
                    *collector_conditions(f"{root}_KollektorMaske", 3986, mask),
                ),
            )
    root = "Solar_Wf1_Zieltemperatur"
    installed = RangeDependency(f"{root}_Sensor1_Nr", 4341, 1, 65)
    deps = (
        output,
        RangeDependency("Solar_Kol1_Sensor1_Nr", 3536, 1, 65),
        RangeDependency("Solar_Sp1_Sensor1_Nr", 3576, 1, 65),
        Dependency("Solar_Sp1_Aktiviert_Modus", 3604, (1, 2)),
        Dependency(f"{root}_Aktiviert_Modus", 4335, (1, 2)),
    )
    for suffix, index, label, variants in (
        (
            "Zieltemperatur",
            4344,
            "target temperature",
            (
                (0, "_C", "°C", ("20", "110", "1", "0.1")),
                (1, "_F", "°F", ("68", "230", "2", "0.1")),
            ),
        ),
        (
            "Anstieg",
            4347,
            "rise",
            (
                (0, "_K", "K", ("1", "20", "0.5", "0.1")),
                (1, "_R", "°R", ("2", "40", "1", "0.1")),
            ),
        ),
    ):
        name = f"{root}_{suffix}"
        for selector, alias, unit, limits in variants:
            yield Setting(
                name,
                index,
                f"Solar {label}",
                "number",
                True,
                limits=limits,
                unit=unit,
                installed_by=installed,
                dependencies=(
                    *deps,
                    Dependency("TemperatureHysteresisSelector", 60, (selector,)),
                ),
                compound_mirrors=((name, index, name + alias, index + selector + 1),),
            )


def _solar_tube_exchanger_settings():
    """Native operational parameters, with topology and assignment checks."""
    output = RangeDependency("Solar_Relais1_Nr", 3452, 1, 100)
    clock = ("0", "1410", "30", "1")
    for slot in range(1, 4):
        offset = (slot - 1) * 18
        root = f"Solar_Wf{slot}_Roehrenkollektor"
        mask = root + "_KollektorMaske"
        installed = RangeDependency(mask, 3839 + offset, 1, 7)
        deps = (
            output,
            SolarSelection(mask, 3839 + offset),
            Dependency(root + "_Aktiviert_Modus", 3834 + offset, (1, 2)),
        )
        for suffix, index, other, other_index, relation in (
            ("tBeginn", 3840, "tEnde", 3841, "le"),
            ("tEnde", 3841, "tBeginn", 3840, "ge"),
        ):
            yield Setting(
                root + "_" + suffix,
                index + offset,
                f"Solar tube collector {slot} {'start' if suffix == 'tBeginn' else 'end'}",
                "number",
                True,
                limits=clock,
                unit="min",
                installed_by=installed,
                dependencies=deps,
                relative_constraints=(
                    RelativeConstraint(
                        root + "_" + other, other_index + offset, relation, 30
                    ),
                ),
                peer_limits=((root + "_" + other, clock),),
            )
        for suffix, index, bounds, unit, label in (
            ("Laufzeit", 3842, ("5", "600", "5", "1"), "s", "runtime"),
            ("Pausezeit", 3843, ("1", "60", "1", "1"), "min", "pause"),
        ):
            yield Setting(
                root + "_" + suffix,
                index + offset,
                f"Solar tube collector {slot} {label}",
                "number",
                True,
                limits=bounds,
                unit=unit,
                installed_by=installed,
                dependencies=deps,
            )
    for slot in range(1, 6):
        offset = (slot - 1) * 49
        root = f"Solar_Wf{slot}_ExtWT"
        installed = RangeDependency(root + "_Sensor1_Nr", 4094 + offset, 1, 65)
        deps = (
            output,
            SolarSelection(root + "_SpeicherMaske", 4089 + offset, stores=True),
            RangeDependency(root + "_Relais1_Nr", 4102 + offset, 1, 100),
        )
        for suffix, index, bounds, label in (
            ("Nachlaufzeit", 4090, ("0", "15", "1", "1"), "run-on"),
            ("Mindestlaufzeit", 4092, ("1", "15", "1", "1"), "minimum runtime"),
        ):
            yield Setting(
                root + "_" + suffix,
                index + offset,
                f"Solar exchanger {slot} {label}",
                "number",
                True,
                limits=bounds,
                unit="min",
                installed_by=installed,
                dependencies=deps,
            )
        yield from _operating_pair(
            root + "_dTein",
            4112 + offset,
            root + "_dTaus",
            4115 + offset,
            f"Solar exchanger {slot} differential",
            installed,
            deps,
            (
                (
                    0,
                    "_K",
                    "K",
                    ("1", "20", "0.5", "0.1"),
                    ("0.5", "19.5", "0.5", "0.1"),
                ),
                (1, "_R", "°R", ("2", "40", "1", "0.1"), ("1", "39", "1", "0.1")),
            ),
            rising=True,
        )
        for suffix, index, label, variants in (
            (
                "ZieltemperaturSekundaer",
                4109,
                "target",
                (
                    (0, "_C", "°C", ("15", "95", "1", "0.1")),
                    (1, "_F", "°F", ("60", "204", "2", "0.1")),
                ),
            ),
            (
                "dTAnstieg",
                4121,
                "rise",
                (
                    (0, "_K", "K", ("1", "30", "1", "0.1")),
                    (1, "_R", "°R", ("2", "60", "2", "0.1")),
                ),
            ),
        ):
            name = root + "_" + suffix
            for selector, alias, unit, limits in variants:
                yield Setting(
                    name,
                    index + offset,
                    f"Solar exchanger {slot} {label}",
                    "number",
                    True,
                    limits=limits,
                    unit=unit,
                    installed_by=installed,
                    dependencies=(
                        *deps,
                        Dependency(root + "_OZieltemperatur", 4091 + offset, (1,)),
                        RangeDependency(root + "_Sensor2_Nr", 4098 + offset, 1, 65),
                        Dependency("TemperatureHysteresisSelector", 60, (selector,)),
                    ),
                    compound_mirrors=(
                        (
                            name,
                            index + offset,
                            name + alias,
                            index + offset + selector + 1,
                        ),
                    ),
                )


BX_PLUS_SOLAR_DEDICATED_SETTINGS = (
    *tuple(_solar_dedicated_settings()),
    *tuple(_solar_tube_exchanger_settings()),
)


def _heating_comfort_settings():
    """Native heating comfort corrections and sensor-based room thermostats.

    BX Plus manual heating circuit adjustment channels: day/night correction,
    room setpoint and hysteresis. Keep flow safety limits and topology intact.
    All indices, selector options and bounds are checked against exact RPT.
    """
    # The native holiday shortcut is available after commissioning and
    # edits a controller-wide day counter, not an individual circuit.
    yield Setting(
        "Urlaubsfunktion_Tage",
        19063,
        "Controller-wide holiday remaining days",
        "number",
        True,
        limits=("0", "200", "1", "1"),
        unit="d",
        dependencies=(Dependency("InbetriebnahmeAbgeschlossen", 68, (1,)),),
    )
    for circuit in range(1, 8):
        offset = (circuit - 1) * 495
        root = f"Heizung_Heizkreis{circuit}"
        installed = Dependency(f"{root}_Type", 13684 + offset, (1,))
        active = Dependency(f"{root}_Aktiviert_Modus", 13689 + offset, (1, 2))
        heating = Dependency(f"{root}_System", 13697 + offset, (0, 2))
        regulation = Dependency(f"{root}_Heizsystem", 13978 + offset, (1, 2, 3))
        # Native status menus reference circuit 1 for coupled followers.
        # This is the configured preference, never a forced runtime status.
        coupling = (
            (Dependency(f"{root}_Kopplung", 14039 + offset, (0,)),)
            if circuit != 1
            else ()
        )
        yield Setting(
            f"{root}_Betriebsart",
            14014 + offset,
            (
                "Heating circuit 1 configured mode (including coupled circuits)"
                if circuit == 1
                else f"Heating circuit {circuit} configured mode"
            ),
            "select",
            True,
            options=(
                ("Auto", 0),
                ("Day", 1),
                ("Night", 2),
                ("Summer", 3),
                ("Holiday", 4),
                ("Off", 5),
            ),
            installed_by=installed,
            dependencies=(active, *coupling),
            requested_conditions=(
                RequestedCondition(
                    4, (RangeDependency("Urlaubsfunktion_Tage", 19063, 1, 200),)
                ),
                RequestedCondition(
                    0,
                    (
                        Dependency(f"{root}_OUrlaub", 14176 + offset, (0,)),
                        Dependency("Urlaubsfunktion_Tage", 19063, (0,)),
                    ),
                ),
            ),
        )
        for suffix, index, low, high, label, extra in (
            ("dTTageskorrektur", 13727, -5, 45, "day correction", ()),
            (
                "dTNachtkorrektur",
                13730,
                -20,
                30,
                "night correction",
                (
                    Dependency(f"{root}_OSchaltuhr", 13703 + offset, (1,)),
                    Dependency(f"{root}_ModusSchaltuhr", 13704 + offset, (0,)),
                ),
            ),
        ):
            name = f"{root}_{suffix}"
            index += offset
            for selector, alias, unit, factor in (
                (0, "_K", "K", 1),
                (1, "_R", "°R", 2),
            ):
                yield Setting(
                    name,
                    index,
                    f"Heating circuit {circuit} {label}",
                    "number",
                    True,
                    limits=(str(low * factor), str(high * factor), str(factor), "0.1"),
                    unit=unit,
                    dependencies=(
                        active,
                        heating,
                        regulation,
                        *extra,
                        Dependency("TemperatureHysteresisSelector", 60, (selector,)),
                    ),
                    installed_by=installed,
                    compound_mirrors=(
                        (name, index, name + alias, index + selector + 1),
                    ),
                )
        limit_conditions = (
            active,
            heating,
            Dependency(f"{root}_OHeizgrenze", 13739 + offset, (1,)),
            RangeDependency(f"{root}_Sensor2_Nr", 13800 + offset, 1, 65),
        )
        for suffix, index, label, modes in (
            ("TSommerTag", 13741, "day heating cut-off", (0, 1)),
            ("TSommerNacht", 13744, "night heating cut-off", (1,)),
        ):
            name = f"{root}_{suffix}"
            index += offset
            for selector, alias, unit, limits in (
                (0, "_C", "°C", ("0", "40", "1", "0.1")),
                (1, "_F", "°F", ("32", "104", "2", "0.1")),
            ):
                yield Setting(
                    name,
                    index,
                    f"Heating circuit {circuit} {label}",
                    "number",
                    True,
                    limits=limits,
                    unit=unit,
                    installed_by=installed,
                    dependencies=(
                        *limit_conditions,
                        Dependency(f"{root}_Heizgrenze", 13740 + offset, modes),
                        Dependency("TemperatureHysteresisSelector", 60, (selector,)),
                    ),
                    compound_mirrors=(
                        (name, index, name + alias, index + selector + 1),
                    ),
                )
        for suffix, index, label in (
            ("tSommerTagEin", 13700, "heating limit day period start"),
            ("tSommerTagAus", 13701, "heating limit day period end"),
        ):
            yield Setting(
                f"{root}_{suffix}",
                index + offset,
                f"Heating circuit {circuit} {label}",
                "number",
                True,
                limits=("0", "1425", "15", "1"),
                unit="min",
                installed_by=installed,
                dependencies=(
                    *limit_conditions,
                    Dependency(f"{root}_Heizgrenze", 13740 + offset, (1,)),
                ),
            )
        for thermostat in range(1, 6):
            prefix = f"{root}_Raumthermostat{thermostat}"
            base = 13819 + offset + (thermostat - 1) * 32
            conditions = (
                active,
                heating,
                Dependency(f"{prefix}_Option", base, (1,)),
                Dependency(f"{prefix}_Aktiviert", base + 1, (1, 2)),
                Dependency(f"{prefix}_Typ", base + 4, (0,)),
                RangeDependency(f"{prefix}_Sensor1_Nr", base + 15, 1, 65),
            )
            for suffix, delta, label, variants in (
                (
                    "TRaum",
                    5,
                    "room setpoint",
                    (
                        ("_C", "°C", ("10", "30", "1", "0.1")),
                        ("_F", "°F", ("50", "86", "2", "0.1")),
                    ),
                ),
                (
                    "dTRaum",
                    8,
                    "room hysteresis",
                    (
                        ("_K", "K", ("0.5", "20", "0.1", "0.1")),
                        ("_R", "°R", ("1", "40", "0.2", "0.1")),
                    ),
                ),
                (
                    "dTAbsenkung",
                    11,
                    "room setback",
                    (
                        ("_K", "K", ("1", "20", "1", "0.1")),
                        ("_R", "°R", ("2", "40", "2", "0.1")),
                    ),
                ),
            ):
                name = f"{prefix}_{suffix}"
                index = base + delta
                for selector, (alias, unit, limits) in enumerate(variants):
                    yield Setting(
                        name,
                        index,
                        f"Heating circuit {circuit} thermostat {thermostat} {label}",
                        "number",
                        True,
                        limits=limits,
                        unit=unit,
                        dependencies=(
                            *conditions,
                            *(
                                (Dependency(f"{prefix}_OSchaltuhr", base + 2, (1,)),)
                                if suffix == "dTAbsenkung"
                                else ()
                            ),
                            Dependency(
                                "TemperatureHysteresisSelector", 60, (selector,)
                            ),
                        ),
                        installed_by=installed,
                        compound_mirrors=(
                            (name, index, name + alias, index + selector + 1),
                        ),
                    )


BX_PLUS_HEATING_COMFORT_SETTINGS = tuple(_heating_comfort_settings())


def encode_action(setting, value):
    """Return the validated wire value or a fixed, non-sensitive error."""
    try:
        return setting.encode(value)
    except CatalogError as err:
        raise VBusError("Invalid configuration value") from err
