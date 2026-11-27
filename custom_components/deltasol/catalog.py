"""Read the exact locally supplied RPT catalogue without executing vendor code."""

import hashlib
import io
import os
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

from .vbus import parameter_hash

BX_PLUS_SHA256 = "1bcc59cac3d4176c68de255fced3b95840bada06db33b0bdae0cddb007a9f079"
BX_PLUS_MENU = "BXPLUS_2.08/BXPlus_208-Menu.xml"
BX_PLUS_CHANGESET = 0x2734DABC
MAX_ARCHIVE = 32 * 1024 * 1024
MAX_XML = 64 * 1024 * 1024
SENSITIVE = re.compile(
    r"passw|kennwort|pin(code)?|secret|credential|auth|token|api.?key|"
    r"zugangscode|zugriffscode|servicecode|installercode|access.?code|"
    r"bediener.?code|benutzer.?code|operator.?code|freigabe.?code|"
    r"installateur.?code|kundendienst.?code|sicherheits.?code|login|"
    r"serial|seriennummer|macaddr|ipaddr|hostname|email|e.mail|"
    r"telefon|phone|benutzer|username|user.?id|standort|location|"
    r"longitude|latitude|ssid|wlan|wifi|network|netzwerk|adresse|address",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CatalogSource:
    """Versioned catalogue source selected by controller identity and changeset."""

    address: int
    changeset: int
    filename: str
    menu_member: str
    digest: str
    url: str


CATALOG_SOURCES = (
    CatalogSource(
        0x7112,
        BX_PLUS_CHANGESET,
        "BXPLUS_2.08.zip",
        BX_PLUS_MENU,
        BX_PLUS_SHA256,
        "https://www.mastersolar.rs/wp-content/uploads/2021/02/BXPLUS_2.08.zip",
    ),
)


class CatalogError(ValueError):
    """A locally supplied definition is missing or cannot be trusted."""


class CatalogMissing(CatalogError):
    """The exact catalogue is not yet cached."""


def source_for(address, changeset):
    """Never select a catalogue by gateway firmware or changeset alone."""
    matches = [
        source
        for source in CATALOG_SOURCES
        if (source.address, source.changeset) == (address, changeset)
    ]
    if len(matches) != 1:
        raise CatalogError("Unsupported controller catalogue")
    return matches[0]


@dataclass(frozen=True)
class Definition:
    """Catalogue metadata, never proof of live availability or write access."""

    name: str
    index: int | None
    storage: str
    type_id: str | None
    metadata: dict
    excluded: str | None = None


def is_sensitive(definition):
    """Shared fail-closed classifier for discovery, export and comparison."""
    return definition.excluded == "sensitive" or any(
        SENSITIVE.search(str(value))
        for value in (
            definition.name,
            definition.type_id,
            definition.metadata.get("struct_ref"),
            definition.metadata.get("compound_ref"),
            *definition.metadata.get("type_chain", []),
        )
        if value
    )


@dataclass(frozen=True)
class Catalog:
    digest: str
    definitions: tuple[Definition, ...]


def _number(text):
    # RPT uses both decimal points and decimal commas, but not thousands
    # separators. Normalize only an unambiguous decimal literal.
    if isinstance(text, str) and re.fullmatch(r"[+-]?[0-9]+,[0-9]+", text):
        text = text.replace(",", ".")
    try:
        number = Decimal(text)
        return number if number.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _integer(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _metadata(type_node, types):
    """Resolve exact inheritance; preserve unsupported expressions as metadata."""
    chain = []
    seen = set()
    unresolved = None
    while type_node is not None:
        name = type_node.get("id")
        if name in seen:
            unresolved = name
            break
        seen.add(name)
        chain.append(type_node)
        base = type_node.get("base")
        type_node = types.get(base)
        if base and type_node is None:
            unresolved = base
    result = {}
    for node in reversed(chain):
        if node.get("unit") is not None:
            result["unit"] = node.get("unit")
        if node.get("selectorValueRef") is not None:
            selector = node.get("selectorValueRef")
            result["selector_ref"] = selector
            refs = result.setdefault("selector_refs", [])
            if selector not in refs:
                refs.append(selector)
        for tag in (
            "storeFactor",
            "displayFactor",
            "minimum",
            "maximum",
            "step",
            "quant",
            "default",
        ):
            child = node.find(tag)
            if child is not None:
                result[tag] = (child.text or "").strip() or None
                if tag == "quant":
                    result["quant_step"] = child.get("step")
        options = node.findall("valueText")
        if options:
            result["value_text"] = [
                {
                    "value": option.get("value"),
                    "id": option.get("id"),
                    "label": next(
                        (t.text for t in option.findall("text") if t.text), None
                    ),
                }
                for option in options
            ]
    result["type_chain"] = [node.get("id") for node in chain]
    if unresolved:
        result["unresolved_base"] = unresolved
    # Do not infer implicit enumeration ordinals or defaults.
    return result


def parse_menu(xml, digest):
    """Parse data-only RPT menu XML; retain non-readable definitions as evidence."""
    unsafe_xml = re.search(rb"<!\s*(DOCTYPE|ENTITY)", xml, re.IGNORECASE)
    if len(xml) > MAX_XML or unsafe_xml:
        raise CatalogError("Unsafe RPT XML")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as err:
        raise CatalogError("Invalid RPT XML") from err
    types = {node.get("id"): node for node in root.iter("type") if node.get("id")}
    # Preserve menu membership and masks as evidence, not evaluated permissions.
    # A line may be present in several conditional menus.
    menu_refs = {}
    for menu in root.iter("menu"):
        for line in menu.findall("line"):
            ref = line.get("valueRef")
            if not ref:
                continue
            evidence = {
                key: line.get(key)
                for key in ("mask", "maskVal", "extraRef")
                if line.get(key) is not None
            }
            evidence["menu"] = menu.get("id")
            evidence["menu_type"] = menu.get("type")
            records = menu_refs.setdefault(ref, [])
            if evidence not in records:
                records.append(evidence)
    result = {}
    for node in root.iter("value"):
        name = node.get("id")
        if not name:
            continue
        index = _integer(node.get("index"))
        storage = node.get("storage", "unspecified")
        type_node = node.find("type")
        type_id = type_node.get("id") if type_node is not None else None
        metadata = _metadata(type_node, types)
        metadata["applicability"] = "not_assessed"
        if name in menu_refs:
            metadata["menu_refs"] = menu_refs[name]
        if node.get("structValueRef"):
            metadata["struct_ref"] = node.get("structValueRef")
        if node.get("compoundValueRef"):
            metadata["compound_ref"] = node.get("compoundValueRef")
        if node.get("allowParameterization") is not None:
            metadata["allow_parameterization"] = node.get("allowParameterization")
        if node.get("priority"):
            metadata["priority"] = node.get("priority")
        if node.get("forceUse") is not None:
            metadata["force_use"] = node.get("forceUse")
        excluded = None
        if is_sensitive(Definition(name, index, storage, type_id, metadata)):
            excluded = "sensitive"
        elif storage in ("abstract", "struct"):
            excluded = "non_value_definition"
            # The upstream configuration optimizer resolves compound children
            # through their parent. Read only exact temperature proxies, not
            # arbitrary abstract structures, to establish actual wire evidence.
            if (
                storage == "abstract"
                and name.isascii()
                and _integer(node.get("idHash")) == parameter_hash(name)
                and index is not None
                and 0 < index <= 65535
                and metadata.get("selector_ref") == "TemperatureHysteresisSelector"
                and set(metadata.get("type_chain", ()))
                & {"TemperatureProxy", "HysteresisProxy"}
            ):
                metadata["compound_parent_read_candidate"] = True
        elif not name.isascii() or _integer(node.get("idHash")) != parameter_hash(name):
            excluded = "unverified_identifier"
        elif index is not None and not 0 < index <= 65535:
            excluded = "invalid_reference_index"
        definition = Definition(name, index, storage, type_id, metadata, excluded)
        if name in result and result[name] != definition:
            raise CatalogError("Conflicting RPT parameter definitions")
        result[name] = definition
    if not result:
        raise CatalogError("Empty RPT catalogue")
    # A name hash may collide; never read an ambiguous hash as a particular ID.
    by_hash = {}
    for definition in result.values():
        if definition.name.isascii():
            key = parameter_hash(definition.name)
            by_hash.setdefault(key, []).append(definition.name)
    for names in by_hash.values():
        if len(names) > 1:
            for name in names:
                definition = result[name]
                definition.metadata.pop("compound_parent_read_candidate", None)
                if definition.excluded is None:
                    result[name] = Definition(
                        name,
                        definition.index,
                        definition.storage,
                        definition.type_id,
                        definition.metadata,
                        "ambiguous_hash",
                    )
    return Catalog(digest, tuple(result.values()))


def _parse_archive(data, source):
    digest = hashlib.sha256(data).hexdigest()
    if len(data) > MAX_ARCHIVE or digest != source.digest:
        raise CatalogError("Unsupported RPT archive digest")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo(source.menu_member)
            if info.file_size > MAX_XML:
                raise CatalogError("RPT menu exceeds size limit")
            return parse_menu(archive.read(info), digest)
    except (KeyError, zipfile.BadZipFile) as err:
        raise CatalogError("Invalid RPT archive") from err


def load_catalog(path, source=CATALOG_SOURCES[0]):
    """Validate the exact cached archive, never execute vendor binaries."""
    try:
        with open(path, "rb") as stream:
            data = stream.read(MAX_ARCHIVE + 1)
    except FileNotFoundError as err:
        raise CatalogMissing("RPT catalogue not cached") from err
    except OSError as err:
        raise CatalogError("RPT archive unavailable") from err
    return _parse_archive(data, source)


def download_catalog(path, source):
    """Acquire a pinned public RPT archive locally; no site data is transmitted."""
    if source not in CATALOG_SOURCES:
        raise CatalogError("Untrusted RPT catalogue source")
    try:
        with urlopen(source.url, timeout=15) as response:
            target = urlsplit(response.geturl())
            if target.scheme != "https" or target.netloc != urlsplit(source.url).netloc:
                raise CatalogError("Unexpected RPT catalogue redirect")
            data = response.read(MAX_ARCHIVE + 1)
        catalog = _parse_archive(data, source)
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replacement only after checksum and XML validation.
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=".catalog-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            except OSError:
                temporary.unlink(missing_ok=True)
                raise
        try:
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return catalog
    except (OSError, URLError, TimeoutError) as err:
        raise CatalogError("RPT catalogue download failed") from err


def explicit_options(definition):
    """Return only unambiguous explicitly numbered enum options."""
    options = definition.metadata.get("value_text", [])
    result = {}
    for item in options:
        value = _integer(item.get("value"))
        label = item.get("label")
        if value is None or not label or value in result or label in result.values():
            return {}
        if not -(2**31) <= value < 2**31:
            return {}
        result[value] = label
    return result


def parameterization_evidence(definition):
    """Classify catalogue hints, not permission, active state or save semantics.

    The MIT resol-vbus base optimizer selects values without a storage class
    or with allowParameterization, then resolves compound selectors. Its older
    BX Plus layout must not be used as a write-address or permission source.
    """
    if definition.excluded or is_sensitive(definition):
        return "excluded"
    if definition.metadata.get("allow_parameterization") == "true":
        return "explicit_parameterization_hint"
    if definition.storage == "unspecified":
        return "optimizer_candidate"
    if definition.storage in ("volatile", "persistent"):
        return "storage_only"
    return "unclassified"


def configuration_evidence(definition, definitions):
    """Classify catalogue structure and proof gaps without granting write access."""
    metadata = definition.metadata
    compound = metadata.get("compound_ref")
    selectors = tuple(
        dict.fromkeys(
            (
                *metadata.get("selector_refs", ()),
                *((metadata["selector_ref"],) if metadata.get("selector_ref") else ()),
            )
        )
    )
    parent = definitions.get(compound) if compound else None
    if parent:
        selectors = tuple(
            dict.fromkeys(
                (
                    *selectors,
                    *parent.metadata.get("selector_refs", ()),
                    *(
                        (parent.metadata["selector_ref"],)
                        if parent.metadata.get("selector_ref")
                        else ()
                    ),
                )
            )
        )
    dependencies = tuple(
        name
        for name in selectors
        if name in definitions and not is_sensitive(definitions[name])
    )
    result = {
        "parameterization_evidence": parameterization_evidence(definition),
        "write_access": "not_verified",
        "applicability": "not_assessed",
        "storage_action": "not_verified",
    }
    if definition.excluded:
        result["write_access_reason"] = definition.excluded
    elif "Blob" in metadata.get("type_chain", ()):
        result["write_access_reason"] = "packed_storage_contract_unverified"
    elif compound:
        result["write_access_reason"] = "compound_target_unverified"
        if parent and not is_sensitive(parent):
            result["compound_target"] = compound
    elif selectors:
        result["write_access_reason"] = "selector_contract_unverified"
    elif metadata.get("allow_parameterization") == "true":
        result["write_access_reason"] = "explicit_hint_requires_contract"
    elif definition.storage in ("volatile", "persistent"):
        result["write_access_reason"] = "storage_not_write_permission"
    elif metadata.get("unresolved_base") or not metadata.get("type_chain"):
        result["write_access_reason"] = "type_unverified"
    else:
        result["write_access_reason"] = "no_verified_configuration_contract"
    if dependencies:
        result["selector_dependencies"] = dependencies
    if len(dependencies) != len(selectors):
        result["selector_evidence_incomplete"] = True
    if metadata.get("menu_refs"):
        result["menu_evidence"] = (
            "conditional"
            if any(
                any(key in ref for key in ("mask", "maskVal", "extraRef"))
                for ref in metadata["menu_refs"]
            )
            else "menu_reference"
        )
    return result


# Ordinal interpretation is corroborated for these controller selectors by
# the MIT BaseConfigurationOptimizer and the exact BX Plus 2.08 archive.
# In particular, AktorFunktion is NOT in this list: the live reference values
# may contain encoded function identities rather than plain enum ordinals.
ORDINAL_TYPES = frozenset(
    (
        "Anlage_WfX_Type",
        "Solar_WfX_Type",
        "Heizung_WfX_Type",
        "Heizung_HeizkreisX_Type",
        "WmzX_Type",
        "ActivityMode",
        "Heizung_HeizkreisX_Heizsystem",
        "VtTemperatureHysteresisSelector",
        "VtFlowSelector",
        "VtPressureSelector",
        "VtEnergySelector",
        "Wmz_WmzX_Struct_Vt{0}_AlternativAnzeigeEinheit",
    )
)


def ordinal_options(definition):
    """Exact reference ordinals for corroborated selector types only."""
    metadata = definition.metadata
    if not ORDINAL_TYPES.intersection(metadata.get("type_chain", ())):
        return {}
    options = metadata.get("value_text", ())
    if (
        metadata.get("unresolved_base")
        or not options
        or any(item.get("value") is not None for item in options)
    ):
        return {}
    labels = [item.get("id") or item.get("label") for item in options]
    if not all(labels) or len(set(labels)) != len(labels):
        return {}
    return dict(enumerate(labels))


def _known_display_scaling(chain, factor, display):
    return display == factor or (
        factor == Decimal("0.1")
        and display == 1
        and bool({"TemperatureCelsius", "TemperatureFahrenheit"}.intersection(chain))
    )


def decoded_value(definition, raw):
    """Decode verified numeric scaling, booleans and enum mappings."""
    chain = definition.metadata.get("type_chain", [])
    if type(raw) is not int:
        return None
    if "Boolean" in chain:
        return bool(raw) if raw in (0, 1) else None
    if "Handbetrieb" in chain:
        return {0: "Off", 1: "Min", 2: "Auto", 3: "Max", 4: "On"}.get(raw)
    if "Number" not in chain:
        explicit = explicit_options(definition)
        return (explicit or ordinal_options(definition)).get(raw)
    factor = _number(definition.metadata.get("storeFactor", "1"))
    if factor is None or factor <= 0:
        return None
    value = Decimal(raw) * factor
    # Numeric valueText may encode missing values, not enum options. Never
    # present a known sentinel as a physical measurement.
    for item in definition.metadata.get("value_text", []):
        if item.get("label") == "-" and _number(item.get("value")) == value:
            return None
    display = _number(definition.metadata.get("displayFactor", str(factor)))
    # For corroborated temperature-short types the storage factor is the
    # wire-to-engineering conversion; displayFactor only affects display.
    # Keep unrelated or unknown factor differences undecoded.
    if not _known_display_scaling(chain, factor, display):
        return None
    return int(value) if value == value.to_integral_value() else float(value)


def numeric_encoding(definition):
    """Return established static numeric bounds, increment and wire factor.

    The RPT quant field supplies a plain engineering-unit increment when
    no alternate quant step expression exists. A valueText sentinel is
    measurement absence, not an enumerated choice. This function establishes
    encoding only; applicability, permission, storage and dynamic bounds
    are separate requirements.
    """
    metadata = definition.metadata
    chain = metadata.get("type_chain", [])
    if (
        is_sensitive(definition)
        or definition.excluded
        or "Number" not in chain
        or "Boolean" in chain
        or "Handbetrieb" in chain
        or metadata.get("unresolved_base")
        or any(
            item.get("label") != "-" or _number(item.get("value")) is None
            for item in metadata.get("value_text", ())
        )
        or metadata.get("quant_step") is not None
    ):
        raise CatalogError("Numeric encoding not established")
    factor = _number(metadata.get("storeFactor", "1"))
    display = _number(metadata.get("displayFactor", str(factor)))
    minimum = _number(metadata.get("minimum"))
    maximum = _number(metadata.get("maximum"))
    step = _number(metadata.get("step", metadata.get("quant")))
    if (
        factor is None
        or factor <= 0
        or not _known_display_scaling(chain, factor, display)
        or minimum is None
        or maximum is None
        or step is None
        or step <= 0
        or minimum > maximum
        or any(number % factor for number in (minimum, maximum, step))
    ):
        raise CatalogError("Numeric bounds or step not established")
    return minimum, maximum, step, factor


def encode_numeric_value(definition, value):
    """Validate exact static bounds, step and signed wire encoding.

    This is conversion only, not permission to write. Conditional constraints,
    active function, current mapping and storage still require separate proof.
    """
    if isinstance(value, bool):
        raise CatalogError("Numeric encoding not established")
    minimum, maximum, step, factor = numeric_encoding(definition)
    number = _number(str(value))
    if number is None:
        raise CatalogError("Invalid numeric value")
    if any(
        _number(item.get("value")) == number
        for item in definition.metadata.get("value_text", ())
    ):
        raise CatalogError("Numeric sentinel is not an adjustable value")
    if not minimum <= number <= maximum:
        raise CatalogError("Value outside parameter range")
    if (number - minimum) % step:
        raise CatalogError("Value does not match parameter step")
    raw = number / factor
    if raw != raw.to_integral_value() or not -(2**31) <= raw < 2**31:
        raise CatalogError("Value not exactly representable on VBus")
    return int(raw)
