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
    if "Time" in chain:
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
    if setting.kind == "switch":
        if "Boolean" not in target.metadata.get("type_chain", ()):
            raise CatalogError("Catalogue boolean contract differs")
    elif setting.kind == "select":
        options = explicit_options(target) or ordinal_options(target)
        if set(options) != {raw for _, raw in setting.options}:
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


def _operating_pair(
    on, on_index, off, off_index, label, installed, dependencies, variants, *, rising
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
                    Dependency("TemperatureHysteresisSelector", 60, (selector,)),
                ),
                relative_constraints=(
                    RelativeConstraint(other, other_index, "gt" if direction else "lt"),
                ),
                compound_mirrors=mirrors,
                peer_limits=((other, peer),),
            )


def _additional_operating_settings():
    """Adjust documented, installed functions; preserve output and safety options."""
    for slot in range(1, 17):
        ao = (slot - 1) * 251
        ho = (slot - 1) * 151
        arrangement = f"Anlage_Wf{slot}"
        heating = f"Heizung_Wf{slot}"
        active = Dependency(f"{arrangement}_Aktiviert_Modus", 6566 + ao, (1, 2))
        # BX Plus manual pp. 60-61: return mixing uses three temperature
        # sensors and two mixer outputs. The operating thresholds are
        # independent of automatic adjustment time and boiler maximum.
        prefix = f"{arrangement}_RLBeimischung"
        yield from _operating_pair(
            f"{prefix}_dTEin",
            6759 + ao,
            f"{prefix}_dTAus",
            6762 + ao,
            f"Arrangement function {slot} return mixing differential",
            Dependency(f"{arrangement}_Type", 6559 + ao, (11,)),
            (
                active,
                RangeDependency(f"{arrangement}_Sensor1_Nr", 6576 + ao, 1, 65),
                RangeDependency(f"{arrangement}_Sensor2_Nr", 6580 + ao, 1, 65),
                RangeDependency(f"{arrangement}_Sensor3_Nr", 6584 + ao, 1, 65),
                RangeDependency(f"{arrangement}_Relais1_Nr", 6592 + ao, 1, 100),
                RangeDependency(f"{arrangement}_Relais2_Nr", 6600 + ao, 1, 100),
            ),
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


def _heating_operating_settings():
    """Documented operating controls; existing flow protection is read-only.

    BX Plus manual from firmware 2.07: constant flow setpoint is bounded
    by configured flow limits; energy saving needs an assigned return
    sensor. Exact 2.08 catalogue validates every slot and selected unit.
    """
    for circuit in range(1, 8):
        offset = (circuit - 1) * 495
        root = f"Heizung_Heizkreis{circuit}"
        installed = Dependency(f"{root}_Type", 13684 + offset, (1,))
        active = Dependency(f"{root}_Aktiviert_Modus", 13689 + offset, (1, 2))
        heating = Dependency(f"{root}_System", 13697 + offset, (0, 2))
        constant = Dependency(f"{root}_Heizsystem", 13978 + offset, (2,))
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


BX_PLUS_SOLAR_LOADING_LOGIC_SETTINGS = tuple(_solar_loading_logic_settings())


def _heating_comfort_settings():
    """Native heating comfort corrections and sensor-based room thermostats.

    BX Plus manual heating circuit adjustment channels: day/night correction,
    room setpoint and hysteresis. Keep flow safety limits and topology intact.
    All indices, selector options and bounds are checked against exact RPT.
    """
    for circuit in range(1, 8):
        offset = (circuit - 1) * 495
        root = f"Heizung_Heizkreis{circuit}"
        installed = Dependency(f"{root}_Type", 13684 + offset, (1,))
        active = Dependency(f"{root}_Aktiviert_Modus", 13689 + offset, (1, 2))
        heating = Dependency(f"{root}_System", 13697 + offset, (0, 2))
        regulation = Dependency(f"{root}_Heizsystem", 13978 + offset, (1, 2, 3))
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
        for thermostat in range(1, 6):
            prefix = f"{root}_Raumthermostat{thermostat}"
            base = 13819 + offset + (thermostat - 1) * 32
            conditions = (
                active,
                heating,
                Dependency(f"{prefix}_Option", base, (1,)),
                Dependency(f"{prefix}_Aktiviert", base + 1, (1, 2)),
                Dependency(f"{prefix}_Typ", base + 4, (0,)),
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
