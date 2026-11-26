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
        source for source in CATALOG_SOURCES
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
        excluded = None
        if is_sensitive(Definition(name, index, storage, type_id, metadata)):
            excluded = "sensitive"
        elif storage in ("abstract", "struct"):
            excluded = "non_value_definition"
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


def decoded_value(definition, raw):
    """Decode verified numeric scaling, booleans and enum mappings."""
    chain = definition.metadata.get("type_chain", [])
    if raw is None:
        return None
    if "Boolean" in chain:
        return bool(raw) if raw in (0, 1) else None
    if "Handbetrieb" in chain:
        return {0: "Off", 1: "Min", 2: "Auto", 3: "Max", 4: "On"}.get(raw)
    if "Number" not in chain:
        return explicit_options(definition).get(raw)
    factor = _number(definition.metadata.get("storeFactor", "1"))
    if factor is None or factor <= 0:
        return None
    value = Decimal(raw) * factor
    return int(value) if value == value.to_integral_value() else float(value)


def encode_numeric_value(definition, value):
    """Validate exact static bounds, step and signed wire encoding.

    This is conversion only, not permission to write. Conditional constraints,
    active function, current mapping and storage still require separate proof.
    """
    metadata = definition.metadata
    chain = metadata.get("type_chain", [])
    if (
        is_sensitive(definition)
        or definition.excluded
        or "Number" not in chain
        or "Boolean" in chain
        or "Handbetrieb" in chain
        or metadata.get("value_text")
        or metadata.get("unresolved_base")
        or isinstance(value, bool)
    ):
        raise CatalogError("Numeric encoding not established")
    factor = _number(metadata.get("storeFactor", "1"))
    minimum = _number(metadata.get("minimum"))
    maximum = _number(metadata.get("maximum"))
    step = _number(metadata.get("step"))
    number = _number(str(value))
    if (
        number is None
        or factor is None
        or factor <= 0
        or minimum is None
        or maximum is None
        or step is None
        or step <= 0
        or minimum > maximum
    ):
        raise CatalogError("Numeric bounds or step not established")
    if not minimum <= number <= maximum:
        raise CatalogError("Value outside parameter range")
    if (number - minimum) % step:
        raise CatalogError("Value does not match parameter step")
    raw = number / factor
    if raw != raw.to_integral_value() or not -(2**31) <= raw < 2**31:
        raise CatalogError("Value not exactly representable on VBus")
    return int(raw)
