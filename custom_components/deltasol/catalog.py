"""Read the exact locally supplied RPT catalogue without executing vendor code."""

import hashlib
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

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


class CatalogError(ValueError):
    """A locally supplied definition is missing or cannot be trusted."""


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
    while type_node is not None:
        name = type_node.get("id")
        if name in seen:
            break
        seen.add(name)
        chain.append(type_node)
        type_node = types.get(type_node.get("base"))
    result = {}
    for node in reversed(chain):
        if node.get("unit") is not None:
            result["unit"] = node.get("unit")
        for tag in ("storeFactor", "minimum", "maximum", "step", "default"):
            child = node.find(tag)
            if child is not None:
                result[tag] = (child.text or "").strip() or None
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
        if node.get("structValueRef"):
            metadata["struct_ref"] = node.get("structValueRef")
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


def load_catalog(path):
    """Validate the exact known archive; read only the menu member, never DLLs."""
    try:
        with open(path, "rb") as stream:
            data = stream.read(MAX_ARCHIVE + 1)
        digest = hashlib.sha256(data).hexdigest()
        if len(data) > MAX_ARCHIVE or digest != BX_PLUS_SHA256:
            raise CatalogError("Unsupported RPT archive digest")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo(BX_PLUS_MENU)
            if info.file_size > MAX_XML:
                raise CatalogError("RPT menu exceeds size limit")
            return parse_menu(archive.read(info), digest)
    except (OSError, KeyError, zipfile.BadZipFile) as err:
        raise CatalogError("RPT archive missing or invalid") from err


def decoded_value(definition, raw):
    """Decode numeric scaling only when the catalogue establishes it."""
    chain = definition.metadata.get("type_chain", [])
    if raw is None:
        return None
    if "Boolean" in chain:
        return bool(raw) if raw in (0, 1) else None
    if "Handbetrieb" in chain:
        return {0: "Off", 1: "Min", 2: "Auto", 3: "Max", 4: "On"}.get(raw)
    if "Number" not in chain:
        return None
    factor = _number(definition.metadata.get("storeFactor", "1"))
    if factor is None:
        return None
    value = Decimal(raw) * factor
    return int(value) if value == value.to_integral_value() else float(value)
