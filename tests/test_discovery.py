"""Tests for catalogue parsing, read coverage and snapshot comparison."""

import importlib
import os
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

PACKAGE = "_deltasol_discovery_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [
    str(Path(__file__).resolve().parents[1] / "custom_components/deltasol")
]
sys.modules[PACKAGE] = package
catalog = importlib.import_module(f"{PACKAGE}.catalog")
discovery = importlib.import_module(f"{PACKAGE}.discovery")
vbus = importlib.import_module(f"{PACKAGE}.vbus")


def menu():
    name = "TemperatureLimit"
    return f"""
    <root>
      <type id="Number" isBuiltIn="true"/>
      <type id="Temperature" base="Number" unit="°C">
        <storeFactor>0.1</storeFactor><minimum>0</minimum>
      </type>
      <value id="{name}" index="17" idHash="{vbus.parameter_hash(name)}"
             storage="persistent">
        <type id="Limit" base="Temperature"><maximum>100</maximum></type>
      </value>
      <value id="Password" index="18" idHash="{vbus.parameter_hash("Password")}"/>
      <value id="Struct" storage="struct"/>
      <value id="UnknownType" index="19"
             idHash="{vbus.parameter_hash("UnknownType")}"/>
    </root>""".encode()


class DiscoveryTests(unittest.TestCase):
    def test_metadata_inheritance_and_redaction(self):
        parsed = catalog.parse_menu(menu(), "test")
        definitions = {d.name: d for d in parsed.definitions}
        limit = definitions["TemperatureLimit"]
        self.assertEqual(limit.metadata["maximum"], "100")
        self.assertEqual(limit.metadata["minimum"], "0")
        self.assertEqual(catalog.decoded_value(limit, 215), 21.5)
        self.assertEqual(definitions["Password"].excluded, "sensitive")
        self.assertEqual(definitions["Struct"].excluded, "non_value_definition")
        self.assertIsNone(catalog.decoded_value(definitions["UnknownType"], 5))

    def test_capture_and_diff(self):
        parsed = catalog.parse_menu(menu(), "test")
        controller = SimpleNamespace(address=0x7112)
        capture = discovery.ConfigurationCapture(parsed, controller)
        self.assertEqual(len(capture.next_batch()), 2)
        capture.apply(
            capture.next_batch(),
            {
                "TemperatureLimit": vbus.ParameterObservation(17, 215, "read"),
                "UnknownType": vbus.ParameterObservation(None, None, "not_resolved"),
            },
        )
        self.assertEqual(capture.status, "complete")
        exported = capture.export(True)
        self.assertEqual(exported["scanned_count"], 2)
        self.assertNotIn("Password", exported["parameters"])
        self.assertIsNone(
            capture.export(False)["parameters"]["TemperatureLimit"]["raw_value"]
        )
        new = discovery.ConfigurationCapture(parsed, controller)
        new.apply(
            new.next_batch(),
            {
                "TemperatureLimit": vbus.ParameterObservation(17, 225, "read"),
                "UnknownType": vbus.ParameterObservation(None, None, "not_resolved"),
            },
        )
        diff = discovery.compare_captures(capture, new)
        self.assertEqual(diff["changes"]["TemperatureLimit"]["after_raw"], 225)
        self.assertNotIn("UnknownType", diff["changes"])

    def test_invalid_xml_and_archive_rejected(self):
        with self.assertRaises(catalog.CatalogError):
            catalog.parse_menu(b"<!DOCTYPE x><x/>", "test")
        with self.assertRaises(catalog.CatalogError):
            catalog.load_catalog("/nonexistent/archive.zip")

    @unittest.skipUnless(os.environ.get("RPT_ARCHIVE"), "real archive not provided")
    def test_exact_vendor_archive(self):
        parsed = catalog.load_catalog(os.environ["RPT_ARCHIVE"])
        self.assertEqual(parsed.digest, catalog.BX_PLUS_SHA256)
        definitions = {d.name: d for d in parsed.definitions}
        self.assertGreater(len(definitions), 7000)
        self.assertEqual(definitions["Schema"].index, 86)
        self.assertEqual(definitions["Relais_Regler_R4_Handbetrieb"].index, 2019)
        self.assertEqual(definitions["Modul1_Vorhanden"].storage, "volatile")
        self.assertGreater(sum(d.excluded is None for d in definitions.values()), 1000)
        print("Exact vendor archive: definitions", len(definitions))
