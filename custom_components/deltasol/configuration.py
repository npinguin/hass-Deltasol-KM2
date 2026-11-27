"""Typed configuration capabilities; the existing parameter owner performs I/O.

Evidence for the first supported profile: BX Plus RPT 2.08, exact digest
in catalog.py, live changeset 2734DABC, RESOL BX Plus manual Basic settings
and MIT resol-vbus BaseConfigurationOptimizer / ConnectionCustomizer.
The latter confirms enumeration ordinals and save transactions, not current
indices. No site-specific settings or proprietary catalogue are bundled.
"""

import re
from dataclasses import dataclass, replace
from decimal import Decimal
from operator import ge, gt, le, lt, ne

from .catalog import CatalogError, Definition, encode_numeric_value
from .vbus import VBusError


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


@dataclass(frozen=True)
class RelativeConstraint:
    """Verified relation between values with the same raw encoding."""

    name: str
    index: int
    operation: str

    def matches(self, raw, values, current=None):
        other = values.get(self.name)
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
            and compare(raw, other)
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
    dependencies: tuple[Dependency | RangeDependency, ...] = ()
    owner_address: int | None = None
    relative_constraints: tuple[RelativeConstraint, ...] = ()
    # Installed program selector: may be used as a Discovery hint, never as
    # authorization. The same dependency is always re-read live for an action.
    installed_by: Dependency | None = None
    owner_relay: int | None = None
    # Each tuple describes a parent and its selected child. Read both live
    # before accepting any adjustment; never issue a write to a child alias.
    compound_mirrors: tuple[tuple[str, int, str, int], ...] = ()
    # Alternative pump ownership: positively read native output assignment
    # and the relay's own reference. Never infer an address from its ordinal.
    output_assignment: tuple[str, int, str, int] | None = None
    unresolved_role: Dependency | None = None
    # Exact encoding bounds for sibling settings that participate in a
    # compound transaction. Their current values must also be valid.
    peer_limits: tuple[tuple[str, tuple[str, str, str, str]], ...] = ()

    @property
    def native_slot(self):
        """The native function owning the setting, not the connected equipment."""
        if not self.installed_by or self.owner_relay is not None:
            return None
        match = re.fullmatch(
            r"(Anlage_Wf|Solar_Wf|Heizung_Wf|Heizung_Heizkreis|Wmz)([1-9][0-9]*)_Type",
            self.installed_by.name,
        )
        if not match:
            return None
        slot = f"{match[1]}{match[2]}"
        if not self.name.startswith(slot + "_"):
            return None
        return slot, match[1], int(match[2])

    def numeric_definition(self):
        if self.kind != "number" or self.limits is None:
            raise CatalogError("Numeric setting bounds are not established")
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
            result[dependency.name] = dependency.index
        for parent, index, child, child_index in self.compound_mirrors:
            result[parent] = index
            result[child] = child_index
        if self.output_assignment:
            assignment, index, relay, relay_index = self.output_assignment
            result[assignment] = index
            result[relay] = relay_index
        if self.unresolved_role:
            result[self.unresolved_role.name] = self.unresolved_role.index
        return result

    def accepts(self, values, requested=None):
        raw = values.get(self.name) if requested is None else requested
        if self.output_assignment:
            assignment, _, relay, _ = self.output_assignment
            reference = values.get(relay)
            if (
                type(reference) is not int
                or reference <= 0
                or values.get(assignment) != reference
            ):
                return False
        return (
            self.decode(raw) is not None
            and all(
                type(values.get(parent)) is int and values[parent] == values.get(child)
                for parent, _, child, _ in self.compound_mirrors
            )
            and all(
                replace(self, limits=limits).decode(values.get(name)) is not None
                for name, limits in self.peer_limits
            )
            and (self.installed_by is None or self.installed_by.matches(values))
            and all(dep.matches(values) for dep in self.dependencies)
            and all(
                rule.matches(raw, values, values.get(self.name))
                for rule in self.relative_constraints
            )
        )


@dataclass(frozen=True)
class ConfigurationCapability:
    """A live, device-bound setting with verified semantic write evidence."""

    setting: Setting
    controller: object
    device: object
    changeset: int
    raw: int

    @property
    def unique_id(self):
        return (
            f"{self.controller.serial}_{self.controller.address:04X}_"
            f"{self.setting.name}_configuration"
        )

    @property
    def value(self):
        return self.setting.decode(self.raw)


# All currently independently corroborated basic-menu settings that resolve
# on the reference controller. Expand the profile from evidence, not by
# treating every readable parameter as writable. Schema, topology, counters,
# clocks/date, safety and dependent thermal parameters are not granted by
# these contracts. The generic writer below is not limited to this group.
BX_PLUS_SETTINGS = (
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
# 2.08 exact RPT establishes names, indices and actuator roles. A speed
# flag alone does not distinguish a pump from a mixing valve or a relay.
# Permit only positively identified pump roles, keep output assignment,
# signal type, safety limits and manual override unchanged.
PUMP_ROLES = (
    (2, "Kollektorpumpe"),
    (4, "Primaerpumpe"),
    (5, "Sekundaerpumpe"),
    (6, "Speicherpumpe"),
    (9, "BypassPumpe"),
    (11, "ExtWTPrimaerpumpe"),
    (12, "ExtWTSekundaerpumpe"),
    (16, "Zwillingspumpe"),
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
                installed_by=installed,
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
# Keep safety min/max temperature limits and topology read-only.
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
)


def _differential_settings():
    for slot in range(1, 17):
        offset = (slot - 1) * 251
        root = f"Anlage_Wf{slot}"
        for kind, ordinal, start, on, off, target in DIFFERENTIAL_FAMILIES:
            prefix = f"{root}_{kind}"
            installed = Dependency(f"{root}_Type", 6559 + offset, (ordinal,))
            active = Dependency(f"{root}_Aktiviert_Modus", 6566 + offset, (1,))
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


def _pump_assignment_settings():
    """Use native free-relay assignment as alternate positive pump evidence.

    The vendor manual specifies exclusive allocation of a free relay and
    identifies heat exchange and solid fuel functions as pump-controlled.
    Only their primary output, with its own independently resolved relay
    reference, is eligible. A positive role or any explicit contrary role
    result takes precedence; the fallback is only for unresolved lookups.
    """
    for setting in BX_PLUS_PUMP_SETTINGS:
        role = setting.installed_by
        relay_name = role.name.removesuffix("_AktorFunktion") + "_Nr"
        for slot in range(1, 17):
            offset = (slot - 1) * 251
            root = f"Anlage_Wf{slot}"
            yield replace(
                setting,
                installed_by=Dependency(f"{root}_Type", 6559 + offset, (5, 6)),
                dependencies=(
                    *setting.dependencies,
                    Dependency(f"{root}_Aktiviert_Modus", 6566 + offset, (1,)),
                ),
                output_assignment=(
                    f"{root}_Relais1_Nr",
                    6592 + offset,
                    relay_name,
                    role.index - 2,
                ),
                unresolved_role=role,
            )


BX_PLUS_PUMP_ASSIGNMENT_SETTINGS = tuple(_pump_assignment_settings())


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
                ),
                installed_by=Dependency(f"{arrangement}_Type", 6559 + ao, (1,)),
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


def encode_action(setting, value):
    """Return the validated wire value or a fixed, non-sensitive error."""
    try:
        return setting.encode(value)
    except CatalogError as err:
        raise VBusError("Invalid configuration value") from err
