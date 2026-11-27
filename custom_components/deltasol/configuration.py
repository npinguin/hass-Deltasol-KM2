"""Typed configuration capabilities; the existing parameter owner performs I/O.

Evidence for the first supported profile: BX Plus RPT 2.08, exact digest
in catalog.py, live changeset 2734DABC, RESOL BX Plus manual Basic settings
and MIT resol-vbus BaseConfigurationOptimizer / ConnectionCustomizer.
The latter confirms enumeration ordinals and save transactions, not current
indices. No site-specific settings or proprietary catalogue are bundled.
"""

from dataclasses import dataclass
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

    def matches(self, raw, values):
        other = values.get(self.name)
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
        return result

    def accepts(self, values, requested=None):
        raw = values.get(self.name) if requested is None else requested
        return (
            self.decode(raw) is not None
            and (self.installed_by is None or self.installed_by.matches(values))
            and all(dep.matches(values) for dep in self.dependencies)
            and all(rule.matches(raw, values) for rule in self.relative_constraints)
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
    unit = Dependency(f"{prefix}_AlternativAnzeigeEinheit", 17274 + offset, (0, 1, 2, 3))
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
    setting
    for number in range(1, 8)
    for setting in _hqm_display_settings(number)
)


def encode_action(setting, value):
    """Return the validated wire value or a fixed, non-sensitive error."""
    try:
        return setting.encode(value)
    except CatalogError as err:
        raise VBusError("Invalid configuration value") from err
