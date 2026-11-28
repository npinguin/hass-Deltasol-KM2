"""Tests for catalogue parsing, read coverage and snapshot comparison."""

import asyncio
import hashlib
import importlib
import io
import os
import re
import shutil
import struct
import subprocess
import sys
import types
import unittest
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections import Counter
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
        self.assertEqual(limit.metadata["rpt_storage"], "persistent")
        unknown = definitions["UnknownType"]
        self.assertEqual(unknown.storage, "unspecified")
        self.assertEqual(unknown.metadata["rpt_storage"], "preferences")
        evidence = catalog.configuration_evidence(unknown, definitions)
        self.assertEqual(evidence["rpt_storage"], "preferences")
        self.assertEqual(evidence["write_access"], "not_verified")
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
        summary = exported["summary"]
        self.assertEqual(summary["progress_percent"], 100)
        self.assertEqual(summary["read_count"], 1)
        self.assertEqual(summary["not_resolved_count"], 1)
        self.assertEqual(summary["other_unread_count"], 0)
        self.assertEqual(summary["remaining_count"], 0)
        self.assertTrue(summary["started_at"])
        self.assertTrue(summary["finished_at"])
        self.assertNotIn("Password", exported["parameters"])
        self.assertNotIn(
            "raw_value", capture.export(False)["parameters"]["TemperatureLimit"]
        )
        self.assertIn(
            "last_observation", capture.export(False)["parameters"]["TemperatureLimit"]
        )
        self.assertNotIn("metadata", exported["parameters"]["UnknownType"])
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

    def test_bounded_batches_and_layout_mismatch(self):
        definitions = tuple(
            catalog.Definition(f"Test{n}", n + 1, "unspecified", None, {})
            for n in range(35)
        )
        parsed = catalog.Catalog("test", definitions)
        capture = discovery.ConfigurationCapture(
            parsed, SimpleNamespace(address=0x7112)
        )
        batch = capture.next_batch()
        self.assertEqual(len(batch), discovery.BATCH_SIZE)
        self.assertEqual(capture.progress_percent, 0)
        capture.apply(
            batch,
            {
                d.name: vbus.ParameterObservation(d.index + 1, 123, "read")
                for d in batch
            },
        )
        self.assertEqual(capture.offset, discovery.BATCH_SIZE)
        self.assertEqual(capture.progress_percent, 45.71)
        for status in ("recovering", "paused", "incomplete"):
            capture.status = status
            self.assertEqual(capture.progress_percent, 45.71)
        capture.status = "running"
        self.assertEqual(capture.status, "running")
        self.assertEqual(capture.next_batch()[0].name, "Test16")
        self.assertEqual(capture.results["Test0"].status, "layout_mismatch")
        self.assertIsNone(capture.results["Test0"].value)
        while capture.next_batch():
            batch = capture.next_batch()
            capture.apply(
                batch,
                {d.name: vbus.ParameterObservation(d.index, 0, "read") for d in batch},
            )
        self.assertEqual(capture.status, "complete")
        self.assertEqual(capture.offset, 35)
        self.assertEqual(capture.progress_percent, 100)
        self.assertEqual(capture.export(True)["parameters"]["Test34"]["raw_value"], 0)

    def test_checkpoint_roundtrip_and_rejection(self):
        parsed = catalog.parse_menu(menu(), "test")
        controller = SimpleNamespace(
            channel=0, address=0x7112, source="BX", serial="test"
        )
        capture = discovery.ConfigurationCapture(parsed, controller)
        first = capture.next_batch()[:1]
        capture.apply(
            first, {first[0].name: vbus.ParameterObservation(17, 215, "read")}
        )
        capture.retry_count = 3
        capture.consecutive_failures = 2
        capture.error = "VBus bus acquisition failed"
        stored = capture.checkpoint()
        restored = discovery.ConfigurationCapture.restore(
            stored, parsed, controller, capture.changeset
        )
        self.assertEqual(restored.status, "paused")
        self.assertEqual(restored.offset, 1)
        self.assertEqual(restored.retry_count, 3)
        self.assertEqual(restored.consecutive_failures, 2)
        self.assertEqual(restored.error, "VBus bus acquisition failed")
        self.assertEqual(restored.results, capture.results)
        self.assertEqual(restored.next_batch(), capture.next_batch())
        restored.apply(
            restored.next_batch(),
            {"UnknownType": vbus.ParameterObservation(None, None, "not_resolved")},
        )
        self.assertEqual(
            discovery.ConfigurationCapture.restore(
                restored.checkpoint(), parsed, controller, capture.changeset
            ).status,
            "complete",
        )
        for changed in (
            {**stored, "offset": 2},
            {**stored, "changeset": 1},
            {**stored, "error": "untrusted credential or payload"},
            {**stored, "observations": [["Password", 18, 1234, "read"]]},
            {**stored, "observations": [[first[0].name, 18, 1234, "read"]]},
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                discovery.ConfigurationCapture.restore(
                    changed, parsed, controller, capture.changeset
                )

    def test_sensitive_filter_and_priority(self):
        definitions = (
            catalog.Definition("WsuMenu_Test", 3, "volatile", None, {}),
            catalog.Definition("Bedienercode", 55, "unspecified", None, {}),
            catalog.Definition("Alias", 55, "persistent", None, {}),
            catalog.Definition(
                "MesseModus_BedienercodeReset", 18225, "persistent", None, {}
            ),
            catalog.Definition("Schema", 86, "unspecified", None, {}),
            catalog.Definition("Modul1_Status", 194, "volatile", None, {}),
        )
        capture = discovery.ConfigurationCapture(
            catalog.Catalog("test", definitions), SimpleNamespace(address=0x7112)
        )
        self.assertEqual(
            [d.name for d in capture.candidates],
            ["Schema", "Modul1_Status", "WsuMenu_Test"],
        )
        capture.results["Bedienercode"] = vbus.ParameterObservation(55, 1234, "read")
        exported = capture.export(True)
        self.assertEqual(exported["counts"]["sensitive_redacted"], 3)
        self.assertNotIn("Bedienercode", exported["parameters"])
        self.assertNotIn("Alias", exported["parameters"])
        self.assertNotIn("metadata", exported["parameters"]["Schema"])

    def test_parameterization_evidence_does_not_grant_write_access(self):
        for storage, metadata, expected in (
            ("persistent", {}, "storage_only"),
            ("volatile", {}, "storage_only"),
            ("unspecified", {}, "optimizer_candidate"),
            (
                "volatile",
                {"allow_parameterization": "true"},
                "explicit_parameterization_hint",
            ),
        ):
            definition = catalog.Definition("Setting", 1, storage, None, metadata)
            self.assertEqual(catalog.parameterization_evidence(definition), expected)

    def test_compound_parent_reads_extend_legacy_checkpoint(self):
        name = "Setpoint"
        child = "Setpoint_K"
        selector = "TemperatureHysteresisSelector"
        xml = f"""
        <root>
          <type id="HysteresisProxy" selectorValueRef="{selector}"/>
          <value id="{selector}" index="60" idHash="{vbus.parameter_hash(selector)}"/>
          <value id="{name}" index="100" storage="abstract"
                 idHash="{vbus.parameter_hash(name)}">
            <type id="Proxy" base="HysteresisProxy"/>
          </value>
          <value id="{child}" index="101" compoundValueRef="{name}"
                 idHash="{vbus.parameter_hash(child)}"/>
          <value id="Unverified" index="200" storage="abstract" idHash="0">
            <type id="UnverifiedProxy" base="HysteresisProxy"/>
          </value>
          <value id="Unverified_K" index="201" compoundValueRef="Unverified"
                 idHash="{vbus.parameter_hash("Unverified_K")}"/>
        </root>""".encode()
        parsed = catalog.parse_menu(xml, "test")
        controller = SimpleNamespace(
            address=0x7112, channel=0, source="BX", serial="test"
        )
        capture = discovery.ConfigurationCapture(parsed, controller)
        self.assertEqual(capture.candidates[-1].name, name)
        self.assertNotIn("Unverified", [d.name for d in capture.candidates])
        legacy = capture.base_candidates
        capture.apply(
            legacy,
            {d.name: vbus.ParameterObservation(d.index, 1, "read") for d in legacy},
        )
        stored = capture.checkpoint()
        stored["status"] = "complete"
        stored["finished_at"] = stored["started_at"]
        restored = discovery.ConfigurationCapture.restore(
            stored, parsed, controller, capture.changeset
        )
        self.assertEqual(restored.status, "paused")
        self.assertEqual(restored.offset, len(legacy))
        self.assertTrue(restored.extension_pending)
        self.assertEqual(restored.results, capture.results)
        self.assertEqual([d.name for d in restored.next_batch()], [name])
        restored.apply(
            restored.next_batch(), {name: vbus.ParameterObservation(100, 65, "read")}
        )
        self.assertEqual(restored.status, "complete")
        compatible = restored.storage_checkpoint()
        self.assertEqual(compatible["status"], "complete")
        self.assertEqual(compatible["offset"], len(legacy))
        self.assertEqual(len(compatible["observations"]), len(legacy))
        self.assertEqual(
            compatible["extension"]["observations"], [[name, 100, 65, "read"]]
        )
        roundtrip = discovery.ConfigurationCapture.restore(
            compatible, parsed, controller, capture.changeset
        )
        self.assertEqual(roundtrip.status, "complete")
        self.assertEqual(roundtrip.results, restored.results)
        # The legacy prefix is independently valid and resumable after rollback.
        legacy_only = {k: v for k, v in compatible.items() if k != "extension"}
        resumed = discovery.ConfigurationCapture.restore(
            legacy_only, parsed, controller, capture.changeset
        )
        self.assertEqual(resumed.status, "paused")
        self.assertEqual(resumed.offset, len(legacy))
        self.assertTrue(resumed.extension_pending)
        # Explicit pause after expansion starts must not be mistaken for
        # the legacy completed prefix, even at exactly the same offset.
        resumed.status = "paused"
        paused = discovery.ConfigurationCapture.restore(
            resumed.storage_checkpoint(), parsed, controller, capture.changeset
        )
        self.assertFalse(paused.extension_pending)
        self.assertEqual(paused.status, "paused")
        row = restored.export(True)["parameters"][name]
        self.assertEqual(row["status"], "read")
        self.assertEqual(row["raw_value"], 65)
        self.assertEqual(row["write_access"], "not_verified")
        self.assertEqual(row["write_access_reason"], "compound_parent_write_unverified")

    def test_configuration_evidence_preserves_compound_and_selector_boundary(self):
        selector = catalog.Definition("Unit", 60, "unspecified", "Enum", {})
        parent = catalog.Definition(
            "Setpoint",
            100,
            "abstract",
            "Temperature",
            {"selector_ref": "Unit"},
            "non_value_definition",
        )
        child = catalog.Definition(
            "Setpoint_C",
            101,
            "unspecified",
            "Number",
            {"compound_ref": "Setpoint", "type_chain": ["Number"]},
        )
        definitions = {d.name: d for d in (selector, parent, child)}
        evidence = catalog.configuration_evidence(child, definitions)
        self.assertEqual(evidence["write_access"], "not_verified")
        self.assertEqual(evidence["write_access_reason"], "compound_target_unverified")
        self.assertEqual(evidence["compound_target"], "Setpoint")
        self.assertEqual(evidence["selector_dependencies"], ("Unit",))
        self.assertEqual(evidence["storage_action"], "not_verified")
        capture = discovery.ConfigurationCapture(
            catalog.Catalog("test", tuple(definitions.values())),
            SimpleNamespace(address=0x7112),
        )
        row = capture.export(True)["parameters"]["Setpoint_C"]
        self.assertEqual(row["write_access_reason"], "compound_target_unverified")
        self.assertEqual(row["status"], "not_scanned")

    def test_rpt_decimal_comma_quant_and_numeric_sentinels(self):
        definition = catalog.Definition(
            "Differential_K",
            123,
            "unspecified",
            "Temperature",
            {
                "type_chain": ["Temperature", "Number"],
                "minimum": "1,0",
                "maximum": "20,0",
                "quant": "0,5",
                "storeFactor": "0.1",
                "displayFactor": "0.1",
                "value_text": [{"value": "999.9", "label": "-"}],
            },
        )
        self.assertEqual(catalog.encode_numeric_value(definition, 6.5), 65)
        for value in (6.1, 999.9, 0.5, 20.5, True):
            with self.subTest(value=value), self.assertRaises(catalog.CatalogError):
                catalog.encode_numeric_value(definition, value)
        definition.metadata["quant_step"] = "alternate"
        with self.assertRaises(catalog.CatalogError):
            catalog.encode_numeric_value(definition, 6.5)

    def test_native_function_inventory_and_known_wire_decoding(self):
        def definition(name, index, base, labels=()):
            return catalog.Definition(
                name,
                index,
                "unspecified",
                base,
                {
                    "type_chain": [base],
                    "value_text": [
                        {"value": None, "id": label, "label": label} for label in labels
                    ],
                },
            )

        rows = (
            definition("Anlage_Wf1_Type", 10, "Anlage_WfX_Type", ("Frei", "Mixer")),
            definition("Anlage_Wf1_Aktiviert_Modus", 11, "ActivityMode"),
            definition("Anlage_Wf1_Relais1_Nr", 12, "AktorNr"),
            definition("Anlage_Wf2_Type", 13, "Anlage_WfX_Type", ("Frei", "Mixer")),
            definition("Anlage_Wf2_Relais1_Nr", 14, "AktorNr"),
            definition("Anlage_Wf3_Type", 15, "Anlage_WfX_Type", ("Frei", "Mixer")),
            definition(
                "Relais_R1_AktorFunktion", 16, "AktorFunktion", ("Free", "Pump")
            ),
        )
        rows[2].metadata["menu_refs"] = [{"menu": "Anlage_Wf1_Mixer"}]
        capture = discovery.ConfigurationCapture(
            catalog.Catalog("test", rows), SimpleNamespace(address=0x7112)
        )
        capture.results = {
            d.name: vbus.ParameterObservation(d.index, raw, "read")
            for d, raw in zip(rows, (1, 2, 4, 0, 5, 99, 1), strict=True)
        }
        inventory = capture.native_functions()["functions"]
        self.assertEqual(inventory["Anlage_Wf1"]["type"], "Mixer")
        self.assertEqual(inventory["Anlage_Wf1"]["activity_mode"], "switch_controlled")
        self.assertEqual(
            inventory["Anlage_Wf1"]["assignments"]["Anlage_Wf1_Relais1_Nr"],
            {
                "reference": 4,
                "reference_type": "AktorNr",
                "applicability": "selected_function_reference",
            },
        )
        self.assertEqual(inventory["Anlage_Wf2"]["status"], "unused")
        self.assertEqual(inventory["Anlage_Wf2"]["assignments"], {})
        self.assertEqual(inventory["Anlage_Wf3"]["status"], "unknown")
        export = capture.export(True)["parameters"]
        self.assertEqual(
            export["Anlage_Wf1_Relais1_Nr"]["applicability"],
            "selected_function_reference",
        )
        self.assertEqual(
            export["Anlage_Wf2_Relais1_Nr"]["applicability"], "function_unused"
        )
        self.assertEqual(
            capture.export(False)["parameters"]["Anlage_Wf1_Relais1_Nr"][
                "applicability"
            ],
            "stale",
        )
        self.assertIsNone(catalog.decoded_value(rows[-1], 1))
        self.assertEqual(catalog.decoded_value(rows[0], 1), "Mixer")
        temp = catalog.Definition(
            "Target_C",
            20,
            "unspecified",
            "TemperatureCelsius",
            {
                "type_chain": ["TemperatureCelsius", "Temperature", "Number"],
                "storeFactor": "0.1",
                "displayFactor": "1.0",
                "unit": "°C",
            },
        )
        self.assertEqual(catalog.decoded_value(temp, 600), 60)
        # Read semantics do not enable writing an unverified parent/child.
        with self.assertRaises(catalog.CatalogError):
            catalog.encode_numeric_value(temp, 60)

    def test_native_set_day_codec(self):
        # Observed native RPT 2.08 offline export, 2026-11-27, issue #10.
        monday = bytes.fromhex("20284850ffffffffffffffff")
        tuesday = bytes.fromhex("3a43ffffffffffffffffffff")
        self.assertEqual(
            discovery.decode_native_day(monday),
            [
                {"start_minute": 480, "end_minute": 600},
                {"start_minute": 1080, "end_minute": 1200},
            ],
        )
        self.assertEqual(
            discovery.decode_native_day(tuesday),
            [{"start_minute": 870, "end_minute": 1005}],
        )
        self.assertEqual(discovery.decode_native_day(bytes([255] * 12)), [])
        for malformed in (
            bytes(12),
            bytes(11),
            bytes.fromhex("2020ffffffffffffffffffff"),
            bytes.fromhex("20283027ffffffffffffffff"),
            bytes.fromhex("ffff2028ffffffffffffffff"),
            bytes.fromhex("205fffffffffffffffffffff")[:11],
            bytes.fromhex("205f5f60ffffffffffffffff"),
            bytes.fromhex("20ffffffffffffffffffffff"),
        ):
            self.assertIsNone(discovery.decode_native_day(malformed))

    def test_native_set_day_encoder_roundtrip_and_rejection(self):
        for payload in (
            bytes.fromhex("20284850ffffffffffffffff"),
            bytes.fromhex("3a43ffffffffffffffffffff"),
            bytes([255] * 12),
            bytes(range(12)),
            bytes.fromhex("005fffffffffffffffffffff"),
            bytes.fromhex("5e5fffffffffffffffffffff"),
        ):
            self.assertEqual(
                discovery.encode_native_day(discovery.decode_native_day(payload)),
                payload,
            )
        self.assertEqual(
            discovery.decode_native_day(bytes.fromhex("5e5fffffffffffffffffffff")),
            [{"start_minute": 1410, "end_minute": 1425}],
        )
        for windows in (
            [{"start_minute": 0, "end_minute": 1440}],
            [{"start_minute": 1, "end_minute": 60}],
            [{"start_minute": 0, "end_minute": 0}],
            [{"start_minute": True, "end_minute": 60}],
            [{"start_minute": 0, "end_minute": 60}] * 7,
            [
                {"start_minute": 0, "end_minute": 60},
                {"start_minute": 45, "end_minute": 90},
            ],
            [{"start_minute": 0}],
        ):
            with self.subTest(windows=windows), self.assertRaises(ValueError):
                discovery.encode_native_day(windows)

    def test_blob_size_inherits_maximum_and_rejects_invalid_evidence(self):
        for declaration, expected in (
            ('size="8"/>', 12),
            ('size="16"/>', 16),
            ('size="invalid"/>', None),
            ('size=""/>', None),
            ('size="12"><size>12</size></type>', 12),
            ('size="12"><size>16</size></type>', None),
            ("><size>16</size></type>", 16),
            ("><size>12</size><size>16</size></type>", None),
        ):
            with self.subTest(declaration=declaration):
                xml = (
                    '<root><type id="Blob" isBuiltIn="true"/>'
                    '<type id="BaseBlob" base="Blob" size="12"/>'
                    f'<value id="Day" index="10" idHash="{vbus.parameter_hash("Day")}">'
                    f'<type id="DayBlob" base="BaseBlob" {declaration}'
                    "</value></root>"
                ).encode()
                definition = catalog.parse_menu(xml, "test").by_name["Day"]
                self.assertEqual(definition.metadata.get("size_bytes"), expected)
                self.assertEqual(
                    definition.metadata.get("size_unverified", False),
                    expected is None,
                )

    def test_weekly_program_candidate_requires_complete_verified_source(self):
        import struct

        source = catalog.source_for(0x7112, catalog.BX_PLUS_CHANGESET)
        definitions = tuple(
            catalog.Definition(
                name,
                index,
                "unspecified",
                kind,
                {
                    "type_chain": [kind],
                    **({"size_bytes": 12} if kind == "Blob" else {}),
                },
            )
            for name, index, kind in (
                ("Wsu1_Belegt", 18414, "Boolean"),
                ("Wsu1_Schaltuhr_Woche_Initialisiert", 18418, "Boolean"),
                ("Wsu1_Schaltuhr_Woche_TagMo_Daten", 18419, "Blob"),
                ("Anlage_Wf1_SchaltuhrNr", 6620, "WochenschaltuhrNr"),
            )
        )
        controller = SimpleNamespace(address=source.address)
        words = struct.unpack("<iii", bytes.fromhex("20284850ffffffffffffffff"))
        capture = discovery.ConfigurationCapture(
            catalog.Catalog(source.digest, definitions, source), controller
        )
        flag, initialized, day, reference = definitions
        capture.results = {
            flag.name: vbus.ParameterObservation(flag.index, 1, "read"),
            initialized.name: vbus.ParameterObservation(initialized.index, 1, "read"),
            day.name: vbus.ParameterObservation(day.index, words[0], "read", words),
            reference.name: vbus.ParameterObservation(reference.index, 1, "read"),
        }
        exported = capture.weekly_program_evidence()
        result = exported["programs"]["Wsu1"]["days"]["Mo"]
        self.assertTrue(result["payload_verified"])
        self.assertEqual(
            exported["programs"]["Wsu1"]["schedule_status"], "partial_payload"
        )
        self.assertEqual(result["size_bytes"], 12)
        self.assertEqual(result["candidate_windows"][0]["start_minute"], 480)
        for bad_size in (None, 8, 16):
            day.metadata["size_bytes"] = bad_size
            self.assertNotIn(
                "candidate_windows",
                capture.weekly_program_evidence()["programs"]["Wsu1"]["days"]["Mo"],
            )
        del day.metadata["size_bytes"]
        fallback = capture.weekly_program_evidence()["programs"]["Wsu1"]["days"]["Mo"]
        self.assertIsNone(fallback["size_bytes"])
        self.assertIsNone(fallback["size_evidence"])
        self.assertNotIn("candidate_windows", fallback)
        self.assertEqual(result["candidate_status"], "wire_word_mapping_verified")
        self.assertEqual(
            exported["references"][reference.name]["candidate_program"], "Wsu1"
        )
        day.metadata["size_bytes"] = 12
        capture.results[day.name] = vbus.ParameterObservation(
            day.index, words[0], "read", (words[0], None, words[2])
        )
        self.assertNotIn(
            "candidate_windows",
            capture.weekly_program_evidence()["programs"]["Wsu1"]["days"]["Mo"],
        )
        capture.results[day.name] = vbus.ParameterObservation(
            day.index, words[0], "read", words
        )
        capture.catalog = catalog.Catalog(source.digest, definitions)
        self.assertNotIn(
            "candidate_windows",
            capture.weekly_program_evidence()["programs"]["Wsu1"]["days"]["Mo"],
        )
        self.assertNotIn(
            "candidate_program",
            capture.weekly_program_evidence()["references"][reference.name],
        )
        capture.catalog = catalog.Catalog(source.digest, definitions, source)
        capture.results[flag.name] = vbus.ParameterObservation(flag.index, 0, "read")
        self.assertNotIn(
            "candidate_program",
            capture.weekly_program_evidence()["references"][reference.name],
        )
        self.assertFalse(
            capture.weekly_program_evidence()["programs"]["Wsu1"]["days"]["Mo"][
                "payload_verified"
            ]
        )

    def test_weekly_program_evidence_never_invents_schedule_payload(self):
        blob = catalog.Definition(
            "Wsu23_Schaltuhr_Woche_TagMo_Daten",
            10,
            "unspecified",
            "Blob",
            {"type_chain": ["Blob"]},
        )
        reference = catalog.Definition(
            "Anlage_Wf1_SchaltuhrNr",
            11,
            "unspecified",
            "WochenschaltuhrNr",
            {"type_chain": ["WochenschaltuhrNr"]},
        )
        capture = discovery.ConfigurationCapture(
            catalog.Catalog("test", (blob, reference)),
            SimpleNamespace(address=0x7112),
        )
        capture.results = {
            blob.name: vbus.ParameterObservation(10, 0, "read"),
            reference.name: vbus.ParameterObservation(11, 23, "read"),
        }
        result = capture.export(True)["weekly_programs"]
        week = result["programs"]["Wsu23"]
        self.assertEqual(week["schedule_status"], "unknown")
        day = week["days"]["Mo"]
        self.assertTrue(day["scalar_observed"])
        self.assertFalse(day["payload_verified"])
        self.assertEqual(result["references"][reference.name]["reference"], 23)
        self.assertEqual(result["write_access"], "not_verified")
        self.assertEqual(capture.export(False)["weekly_programs"]["status"], "stale")

    def test_weekly_program_allocation_is_not_payload_evidence(self):
        rows = tuple(
            catalog.Definition(name, index, "unspecified", None, metadata)
            for name, index, metadata in (
                ("Wsu2_Belegt", 10, {"type_chain": ["Boolean"]}),
                (
                    "Wsu2_Schaltuhr_Woche_Initialisiert",
                    11,
                    {"type_chain": ["Boolean"]},
                ),
                ("Wsu2_Schaltuhr_Woche_TagMo_Daten", 12, {"type_chain": ["Blob"]}),
            )
        )
        capture = discovery.ConfigurationCapture(
            catalog.Catalog("test", rows), SimpleNamespace(address=0x7112)
        )
        for allocated, initialized, status in (
            (0, 0, "unallocated"),
            (1, 0, "uninitialized"),
            (1, 1, "payload_unverified"),
            (2, 1, "unknown"),
        ):
            capture.results = {
                d.name: vbus.ParameterObservation(d.index, value, "read")
                for d, value in zip(rows, (allocated, initialized, 0), strict=True)
            }
            week = capture.weekly_program_evidence()["programs"]["Wsu2"]
            self.assertEqual(week["schedule_status"], status)
            self.assertFalse(week["days"]["Mo"]["payload_verified"])

    def test_packed_day_checkpoint_preserves_scalar_compatibility(self):
        rows = tuple(
            catalog.Definition(
                name,
                index,
                "unspecified",
                None,
                {
                    "type_chain": [kind],
                    **({"size_bytes": 12} if kind == "Blob" else {}),
                },
            )
            for name, index, kind in (
                ("Wsu2_Belegt", 10, "Boolean"),
                ("Wsu2_Schaltuhr_Woche_Initialisiert", 11, "Boolean"),
                ("Wsu2_Schaltuhr_Woche_TagMo_Daten", 12, "Blob"),
            )
        )
        controller = SimpleNamespace(
            channel=0, address=0x7112, source="test", serial="test"
        )
        parsed = catalog.Catalog("test", rows)
        capture = discovery.ConfigurationCapture(parsed, controller)
        self.assertEqual(len(capture.next_batch()), 2)
        self.assertEqual(len(capture.packed_days), 1)
        capture.apply(
            rows[:2],
            {d.name: vbus.ParameterObservation(d.index, 1, "read") for d in rows[:2]},
        )
        day = rows[2]
        capture.apply(
            (day,),
            {day.name: vbus.ParameterObservation(12, 1, "read", (1, -1, 2))},
        )
        stored = capture.storage_checkpoint()
        self.assertEqual(stored["observations"][-1], [day.name, 12, 1, "read"])
        restored = discovery.ConfigurationCapture.restore(
            stored, parsed, controller, capture.changeset
        )
        self.assertEqual(restored.results[day.name].packed_words, (1, -1, 2))
        item = restored.weekly_program_evidence()["programs"]["Wsu2"]["days"]["Mo"]
        self.assertEqual(item["observed_words"], [1, -1, 2])
        self.assertFalse(item["payload_verified"])
        restored.results[day.name] = vbus.ParameterObservation(
            12, 1, "read", (1, -1, 3)
        )
        changes = discovery.compare_captures(capture, restored)["changes"]
        self.assertEqual(
            changes[day.name]["packed_word_evidence"]["status"],
            "observed_words_changed",
        )
        self.assertEqual(changes[day.name]["before_raw"], 1)
        self.assertEqual(changes[day.name]["after_raw"], 1)
        restored.results[day.name] = vbus.ParameterObservation(12, 1, "read")
        changes = discovery.compare_captures(capture, restored)["changes"]
        self.assertEqual(
            changes[day.name]["packed_word_evidence"]["status"], "coverage_changed"
        )
        restored.results[day.name] = vbus.ParameterObservation(12, 2, "read")
        changes = discovery.compare_captures(capture, restored)["changes"]
        self.assertEqual(
            changes[day.name]["packed_word_evidence"]["status"],
            "observed_words_changed",
        )
        stored["packed_words"][day.name] = [1, None, 0]
        restored = discovery.ConfigurationCapture.restore(
            stored, parsed, controller, capture.changeset
        )
        self.assertEqual(restored.results[day.name].packed_words, (1, None, 0))
        stored["packed_words"][day.name][0] = 2
        with self.assertRaises(ValueError):
            discovery.ConfigurationCapture.restore(
                stored, parsed, controller, capture.changeset
            )

    def test_function_context_is_catalogue_driven_not_fixed_slot_count(self):
        rows = (
            catalog.Definition(
                "Anlage_Wf23_Type",
                10,
                "unspecified",
                "Anlage_WfX_Type",
                {
                    "type_chain": ["Anlage_WfX_Type"],
                    "value_text": [
                        {"id": name, "value": None}
                        for name in ("Frei", "Alpha", "Beta")
                    ],
                },
            ),
            catalog.Definition(
                "Anlage_Wf23_Beta_Target",
                11,
                "unspecified",
                "Number",
                {"menu_refs": [{"menu": "Anlage_Wf23_Beta"}]},
            ),
            catalog.Definition(
                "Anlage_Wf23_Alpha_Target",
                12,
                "unspecified",
                "Number",
                {"menu_refs": [{"menu": "Anlage_Wf23_Alpha", "mask": "Enabled"}]},
            ),
            catalog.Definition(
                "Anlage_Wf230_Alpha_Target", 13, "unspecified", "Number", {}
            ),
        )
        capture = discovery.ConfigurationCapture(
            catalog.Catalog("test", rows), SimpleNamespace(address=0x7112)
        )
        capture.results[rows[0].name] = vbus.ParameterObservation(10, 1, "read")
        functions, contexts = capture.function_context()
        self.assertEqual(functions["Anlage_Wf23"]["slot"], 23)
        self.assertEqual(contexts[rows[1].name]["applicability"], "other_function_type")
        self.assertEqual(
            contexts[rows[2].name]["applicability"], "conditional_unverified"
        )
        self.assertNotIn(rows[3].name, contexts)

    def test_dedicated_solar_context_is_not_generic_type_or_write_permission(self):
        rows = (
            catalog.Definition("Solar_SystemId", 3416, "unspecified", "Number", {}),
            catalog.Definition(
                "Solar_Wf1_Type",
                4409,
                "unspecified",
                "Solar_WfX_Type",
                {
                    "type_chain": ["Solar_WfX_Type"],
                    "value_text": [
                        {"id": name, "value": None} for name in ("Frei", "Alpha")
                    ],
                },
            ),
            catalog.Definition(
                "Solar_Wf1_ExtWT_Relais1_Nr",
                4102,
                "unspecified",
                "AktorNr",
                {"type_chain": ["AktorNr"]},
            ),
            catalog.Definition(
                "Solar_Wf1_Roehrenkollektor_Aktiviert_Modus",
                3834,
                "unspecified",
                "ActivityMode",
                {},
            ),
            catalog.Definition(
                "Solar_Wf1_Alpha_Target", 4417, "unspecified", "Number", {}
            ),
        )
        source = catalog.CATALOG_SOURCES[0]
        capture = discovery.ConfigurationCapture(
            catalog.Catalog(source.digest, rows, source),
            SimpleNamespace(address=source.address),
        )
        for system_id in (None, 0, 1):
            for generic_type in (0, 1):
                with self.subTest(system_id=system_id, generic_type=generic_type):
                    capture.results.clear()
                    for row, raw in zip(
                        rows, (system_id, generic_type, 2, 1, 10), strict=True
                    ):
                        if raw is not None:
                            capture.results[row.name] = vbus.ParameterObservation(
                                row.index, raw, "read"
                            )
                    functions, contexts = capture.function_context()
                    external = functions["Solar_Wf1_ExtWT"]
                    self.assertEqual(external["status"], "topology_unverified")
                    self.assertEqual(external["system_id_raw"], system_id)
                    self.assertEqual(
                        external["assignments"][rows[2].name]["reference"], 2
                    )
                    self.assertEqual(
                        functions["Solar_Wf1_Roehrenkollektor"]["activity_mode"],
                        "enabled",
                    )
                    self.assertEqual(
                        contexts[rows[2].name]["applicability"],
                        "solar_topology_unverified",
                    )
                    self.assertEqual(
                        contexts[rows[4].name]["function_slot"], "Solar_Wf1"
                    )
                    self.assertEqual(
                        functions["Solar_Wf1"]["status"],
                        "configured" if generic_type else "unused",
                    )
                    self.assertEqual(
                        catalog.configuration_evidence(
                            rows[2], capture.catalog.by_name
                        )["write_access"],
                        "not_verified",
                    )

    def test_compound_projection_requires_exact_selector_and_parent_evidence(self):
        parent = catalog.Definition(
            "Target",
            100,
            "abstract",
            "TemperatureProxy",
            {"compound_parent_read_candidate": True},
            "non_value_definition",
        )
        selector = catalog.Definition(
            "TemperatureHysteresisSelector", 60, "unspecified", None, {}
        )
        children = tuple(
            catalog.Definition(
                f"Target_{suffix}",
                index,
                "unspecified",
                base,
                {
                    "compound_ref": parent.name,
                    "type_chain": [base, "Number"],
                    "storeFactor": "0.1",
                    "displayFactor": "1",
                },
            )
            for suffix, index, base in (
                ("C", 101, "TemperatureCelsius"),
                ("F", 102, "TemperatureFahrenheit"),
            )
        )
        capture = discovery.ConfigurationCapture(
            catalog.Catalog("test", (selector, parent, *children)),
            SimpleNamespace(address=0x7112),
        )
        capture.results = {
            selector.name: vbus.ParameterObservation(60, 0, "read"),
            children[0].name: vbus.ParameterObservation(101, 600, "read"),
            children[1].name: vbus.ParameterObservation(102, 1400, "read"),
        }
        row = capture.compound_parameters()["Target"]
        self.assertEqual(row["value"], 60)
        self.assertEqual(row["unit"], "°C")
        self.assertEqual(row["evidence"], "parent_not_observed")
        capture.results[parent.name] = vbus.ParameterObservation(100, 600, "read")
        self.assertEqual(
            capture.compound_parameters()["Target"]["evidence"],
            "parent_matches_selected_child",
        )
        capture.results[selector.name] = vbus.ParameterObservation(60, 1, "read")
        row = capture.compound_parameters()["Target"]
        self.assertEqual(row["value"], 140)
        self.assertEqual(row["unit"], "°F")
        self.assertEqual(row["evidence"], "parent_encoding_unverified")
        self.assertEqual(row["write_access"], "not_verified")
        capture.results[selector.name] = vbus.ParameterObservation(60, 2, "read")
        row = capture.compound_parameters()["Target"]
        self.assertIsNone(row["value"])
        self.assertEqual(row["evidence"], "selector_unknown")

    def test_numeric_sentinel_and_unverified_display_units(self):
        metadata = {
            "type_chain": ["Number"],
            "storeFactor": "0.1",
            "displayFactor": "0.1",
            "value_text": [{"value": "-999.9", "label": "-"}],
        }
        definition = catalog.Definition("Reading", 1, "persistent", None, metadata)
        self.assertIsNone(catalog.decoded_value(definition, -9999))
        self.assertEqual(catalog.decoded_value(definition, 215), 21.5)
        metadata["displayFactor"] = "86400"
        self.assertIsNone(catalog.decoded_value(definition, 215))

    def test_exact_numeric_encoding_and_rejection(self):
        definition = catalog.Definition(
            "Setpoint",
            123,
            "persistent",
            "Number",
            {
                "type_chain": ["Number"],
                "minimum": "-10",
                "maximum": "80",
                "step": "0.5",
                "storeFactor": "0.1",
            },
        )
        self.assertEqual(catalog.encode_numeric_value(definition, -2.5), -25)
        self.assertEqual(catalog.encode_numeric_value(definition, 21.5), 215)
        self.assertEqual(catalog.decoded_value(definition, -25), -2.5)
        for value in (81, 21.1, "NaN", "Infinity", True):
            with self.subTest(value=value), self.assertRaises(catalog.CatalogError):
                catalog.encode_numeric_value(definition, value)
        metadata = dict(definition.metadata)
        del metadata["step"]
        incomplete = catalog.Definition(
            "Setpoint", 123, "persistent", "Number", metadata
        )
        with self.assertRaises(catalog.CatalogError):
            catalog.encode_numeric_value(incomplete, 21.5)

    def test_preserve_menu_and_quant_evidence(self):
        name = "Setpoint"
        xml = f"""
        <root>
          <type id="Number"/>
          <value id="{name}" idHash="{vbus.parameter_hash(name)}" index="23"
                 allowParameterization="true" compoundValueRef="Parent">
            <type id="SetpointType" base="Number">
              <quant step="2">0,5</quant><displayFactor>0.1</displayFactor>
            </type>
          </value>
          <menu id="Example" type="Conditional">
            <line valueRef="{name}" mask="1" maskVal="1" extraRef="Dependency"/>
          </menu>
        </root>""".encode()
        definition = catalog.parse_menu(xml, "test").definitions[0]
        self.assertEqual(definition.metadata["quant"], "0,5")
        self.assertEqual(definition.metadata["quant_step"], "2")
        self.assertEqual(definition.metadata["allow_parameterization"], "true")
        self.assertEqual(definition.metadata["compound_ref"], "Parent")
        self.assertEqual(
            definition.metadata["menu_refs"],
            [
                {
                    "mask": "1",
                    "maskVal": "1",
                    "extraRef": "Dependency",
                    "menu": "Example",
                    "menu_type": "Conditional",
                }
            ],
        )
        with self.assertRaises(catalog.CatalogError):
            catalog.encode_numeric_value(definition, 20)

    def test_explicit_enum_only(self):
        definition = catalog.Definition(
            "Mode",
            123,
            "persistent",
            "Mode",
            {
                "value_text": [
                    {"value": "2", "label": "Normal"},
                    {"value": "7", "label": "Economy"},
                ]
            },
        )
        self.assertEqual(
            catalog.explicit_options(definition), {2: "Normal", 7: "Economy"}
        )
        self.assertEqual(catalog.decoded_value(definition, 7), "Economy")
        self.assertIsNone(catalog.decoded_value(definition, 0))
        definition.metadata["value_text"].append({"value": None, "label": "Unknown"})
        self.assertEqual(catalog.explicit_options(definition), {})

    def test_catalog_source_and_verified_download(self):
        source = catalog.source_for(0x7112, 0x2734DABC)
        with self.assertRaises(catalog.CatalogError):
            catalog.source_for(0x7E11, 0x2734DABC)
        with self.assertRaises(catalog.CatalogError):
            catalog.source_for(0x7112, 123)
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr(source.menu_member, menu())
        raw = data.getvalue()
        from dataclasses import replace

        source = replace(source, digest=hashlib.sha256(raw).hexdigest())
        response = MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = source.url
        response.read.return_value = raw
        with TemporaryDirectory() as directory:
            path = Path(directory) / source.filename
            with (
                patch.object(catalog, "CATALOG_SOURCES", (source,)),
                patch.object(catalog, "urlopen", return_value=response),
            ):
                with self.assertRaises(catalog.CatalogMissing):
                    catalog.load_catalog(path, source)
                downloaded = catalog.download_catalog(path, source)
                self.assertEqual(downloaded, catalog.load_catalog(path, source))
                self.assertTrue(downloaded.matches(source.address, source.changeset))
                self.assertFalse(downloaded.matches(0x7E11, source.changeset))
                self.assertFalse(downloaded.matches(source.address, 123))
                self.assertFalse(
                    catalog.parse_menu(menu(), source.digest).matches(
                        source.address, source.changeset
                    )
                )
                self.assertEqual(path.read_bytes(), raw)
                response.read.return_value = b"wrong digest"
                with self.assertRaises(catalog.CatalogError):
                    catalog.download_catalog(path, source)
                self.assertEqual(path.read_bytes(), raw)

    def test_normalized_catalogue_rejects_conflicting_definitions(self):
        first = catalog.Definition("Limit", 17, "unspecified", "Number", {})
        second = catalog.Definition("Limit", 18, "unspecified", "Number", {})
        with self.assertRaises(catalog.CatalogError):
            _ = catalog.Catalog("test", (first, second)).by_name

    def test_invalid_xml_and_archive_rejected(self):
        with self.assertRaises(catalog.CatalogError):
            catalog.parse_menu(b"<!DOCTYPE x><x/>", "test")
        with self.assertRaises(catalog.CatalogError):
            catalog.load_catalog("/nonexistent/archive.zip")

    def test_catalogue_binding_rejects_unapproved_metadata_changes(self):
        configuration = importlib.import_module(f"{PACKAGE}.configuration")
        setting = configuration.Setting(
            "Limit", 17, "Limit", "number", True, limits=("0", "100", "1", "1")
        )
        definition = catalog.Definition(
            "Limit",
            17,
            "unspecified",
            "Number",
            {"type_chain": ["Number"], "minimum": "0", "maximum": "100"},
        )
        bound = configuration.bind_setting(setting, {"Limit": definition})
        self.assertEqual(bound.encode(42), 42)
        self.assertEqual(bound.decode(42), 42)
        self.assertEqual(bound.value_definition.metadata["step"], "1")
        for altered in (
            catalog.Definition(
                "Limit", 18, "unspecified", "Number", definition.metadata
            ),
            catalog.Definition("Limit", 17, "volatile", "Number", definition.metadata),
            catalog.Definition(
                "Limit",
                17,
                "unspecified",
                "Number",
                {**definition.metadata, "maximum": "101"},
            ),
            catalog.Definition(
                "Limit",
                17,
                "unspecified",
                "Number",
                {**definition.metadata, "quant": "2"},
            ),
        ):
            with self.assertRaises(catalog.CatalogError):
                configuration.bind_setting(setting, {"Limit": altered})
        self.assertIsNone(setting.value_definition)

    @unittest.skipUnless(os.environ.get("RPT_ARCHIVE"), "real archive not provided")
    def test_registered_native_firmware_contracts(self):
        """Verify native tables directly in the registered build."""
        archive_path = Path(os.environ["RPT_ARCHIVE"])
        self.assertEqual(
            hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            catalog.BX_PLUS_SHA256,
        )
        # Registered and owner-supplied builds have identical XML/image but
        # different DLL hashes, image bases and section sizes. Do not assume
        # that a supplied-build callback address applies to the registered one.
        expected = {
            "BXPLUS_2.08/DeltaSol_BXPlus_208.dll": (
                "a5b6ecc469c65fdd45b49a1c3f1543973677720a8fcf40f1ea363c483982a71c"
            ),
            "BXPLUS_2.08/bxplus_208.xml": (
                "dcd82d53056e78b8a26209b9b4012f3b3787d12e2f835bf270cc7b616968ccd3"
            ),
            "BXPLUS_2.08/BXPlus_208-Menu.xml": (
                "5311e1a5c2da8fe606c8dc49093202e6607d93e1b51430e84a93d4a56ec6e60e"
            ),
            "BXPLUS_2.08/Resol_BXPlus.png": (
                "e70397a53ff8f09d79af19cb431e9f1b65e83af6f5cd4aafe35441e6bf3ee9c2"
            ),
        }
        with zipfile.ZipFile(archive_path) as archive:
            actual = {
                name: hashlib.sha256(archive.read(name)).hexdigest()
                for name in expected
            }
            self.assertEqual(
                sorted(name for name in archive.namelist() if not name.endswith("/")),
                sorted(expected),
            )
            binary = archive.read("BXPLUS_2.08/DeltaSol_BXPlus_208.dll")
            root = ET.fromstring(archive.read(catalog.BX_PLUS_MENU))
        self.assertEqual(actual, expected)

        pe = struct.unpack_from("<I", binary, 60)[0]
        self.assertEqual(binary[pe : pe + 4], b"PE\x00\x00")
        self.assertEqual(struct.unpack_from("<H", binary, pe + 4)[0], 0x14C)
        count = struct.unpack_from("<H", binary, pe + 6)[0]
        optional_size = struct.unpack_from("<H", binary, pe + 20)[0]
        optional = pe + 24
        base = struct.unpack_from("<I", binary, optional + 28)[0]
        self.assertEqual(base, 0x6A880000)
        sections = {}
        for i in range(count):
            offset = optional + optional_size + i * 40
            name, size, address, raw_size, raw_offset = struct.unpack_from(
                "<8sIIII", binary, offset
            )
            sections[name.rstrip(b"\x00").decode("ascii")] = (
                size,
                address,
                raw_size,
                raw_offset,
            )
        self.assertEqual(sections[".text"][0], 1125220)

        def locate(absolute, length, section):
            _, address, raw_size, raw_offset = sections[section]
            rva = absolute - base
            self.assertGreaterEqual(rva, address)
            self.assertLessEqual(rva + length, address + raw_size)
            return raw_offset + rva - address

        values = {node.get("id"): int(node.get("index")) for node in root.iter("value")}
        self.assertEqual(len(values), 15680)
        ids = sorted(set(values.values()) | {54, 57, 58})
        self.assertEqual(len(ids), 15683)
        table = struct.pack(f"<{len(ids)}H", *ids)
        self.assertEqual(binary.count(table), 1)
        index_offset = binary.index(table)
        pointer_offset = (index_offset + len(table) + 31) & ~31
        pointers = struct.unpack_from(f"<{len(ids)}I", binary, pointer_offset)
        descriptors = {
            index: struct.unpack_from("<4I", binary, locate(ptr, 16, ".rdata"))
            for index, ptr in zip(ids, pointers, strict=True)
        }
        for index in values.values():
            for callback in descriptors[index][2:]:
                locate(callback, 1, ".text")

        def descriptor(name):
            return descriptors[values[name]]

        for circuit in range(1, 8):
            name = f"Heizung_Heizkreis{circuit}_Betriebsart"
            self.assertEqual(values[name], 14014 + 495 * (circuit - 1))
            self.assertEqual(descriptor(name)[1], 0x10007)
        self.assertEqual(descriptor("Urlaubsfunktion_Tage")[1], 0x8)

        allocation_table = bytes(8)
        for slot in range(51):
            prefix = f"Wsu{slot}"
            week = f"{prefix}_Schaltuhr_Woche"
            monday = descriptor(f"{week}_TagMo_Daten")[0]
            for day, label in enumerate(("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")):
                pointer, flags, _, _ = descriptor(f"{week}_Tag{label}_Daten")
                self.assertEqual(flags, 0x400C)
                self.assertEqual(pointer, monday + 12 * day)
            self.assertEqual(descriptor(f"{week}_Initialisiert")[0], monday + 85)
            self.assertEqual(descriptor(f"{prefix}_Belegt")[0], monday + 87)
            if slot:
                root_pointer = descriptor(prefix)[0]
                allocation_table += struct.pack(
                    "<IHH", root_pointer, values[prefix], values[week]
                )
                native_monday, native_ttl = struct.unpack_from(
                    "<II", binary, locate(root_pointer, 8, ".rdata")
                )
                self.assertEqual(native_monday, monday)
                self.assertEqual(native_ttl, descriptor(f"{prefix}_Ttl")[0])
        self.assertEqual(binary.count(allocation_table), 1)
        print(
            "Registered native evidence: 15683 IDs, 357 day descriptors, "
            "50 nonzero allocation records; index/allocator file offsets:",
            hex(index_offset),
            hex(binary.index(allocation_table)),
        )
        # Fingerprints of complete address-independent instruction blocks
        # inspected in the supplied DLL. Compare actual instructions, not just
        # the descriptor layout, without copying vendor bytecode to the repo.
        _, text_rva, text_size, text_offset = sections[".text"]
        code = memoryview(binary)[text_offset : text_offset + text_size]
        # Reuse the same native descriptor join for the shared activity
        # contract. The independent build must agree on both the storage
        # relationship and the actual normalized evaluator instructions.
        configuration = importlib.import_module(f"{PACKAGE}.configuration")
        activity = {setting.name for setting in configuration.BX_PLUS_ACTIVITY_SETTINGS}
        pairs = set()
        for name in activity:
            pointer, flags, first, second = descriptor(name)
            switch = name.removesuffix("_Modus") + "_SensorSchalter_Nr"
            self.assertEqual(pointer, descriptor(switch)[0] + 1)
            self.assertEqual(flags, 7)
            pairs.add((first, second))
        self.assertEqual(len(pairs), 1)
        # The six shared-heating activity editors install an actual change
        # callback (unlike the generic registration flags below).
        for family in ("Anforderung", "Pumpe", "Ventil"):
            for slot in (1, 2):
                name = f"Heizung_Relais{family}{slot}_Aktiviert_Modus"
                _, flags, editor, _ = descriptor(name)
                self.assertEqual(flags, 0x10007)
                offset = locate(editor, 52, ".text")
                wrapper = bytearray(binary[offset : offset + 52])
                self.assertEqual(wrapper[34], 0xE8)
                self.assertEqual(wrapper[42:45], bytes.fromhex("c7405c"))
                callback = struct.unpack_from("<I", wrapper, 45)[0]
                locate(callback, 1, ".text")
                wrapper[35:39] = bytes(4)
                wrapper[45:49] = bytes(4)
                self.assertEqual(
                    hashlib.sha256(wrapper).hexdigest(),
                    "5d5f9fc28f3b333a17f0b8f675e830870e80ac6a92a2e4743dd2a968bb44d533",
                )
        registration_pairs = set()
        registration_flags = {
            f"Anlage_Wf{slot}_{suffix}": flags
            for slot in range(1, 17)
            for suffix, flags in (
                ("Feststoffkessel_OMischer", 0xFFFA),
                ("Funktionsblock_OVolumenstrom", 0xFFFB),
            )
        }
        registration_flags.update(
            {
                f"Heizung_Wf{slot}_{suffix}": flags
                for slot in range(1, 17)
                for suffix, flags in (
                    ("ThBwErwaermung_OLadepumpe", 0xFFFD),
                    ("BwVorerwaermung_OVentil", 0xFFF9),
                )
            }
        )
        registration_flags.update(
            {
                f"Heizung_Heizkreis{circuit}_Raumthermostat{room}_Option": 0xFFFF
                for circuit in range(1, 8)
                for room in range(1, 6)
            }
        )
        self.assertEqual(len(registration_flags), 99)
        for name, flags in registration_flags.items():
            native = descriptor(name)
            # Upper word is the change-callback flag, not the menu editor.
            self.assertEqual(native[1], flags)
            registration_pairs.add(native[2:])
        self.assertEqual(len(registration_pairs), 1)
        for slot in range(1, 3):
            name = f"Heizung_RelaisAnforderung{slot}_OMindestlaufzeit"
            self.assertEqual(descriptor(name)[1], 0xFFF9)
            self.assertIn(descriptor(name)[2:], registration_pairs)
        # Fixed native evidence inventory from the remaining V1 review.
        # This is not a write allowlist: a generic editor and a value-change
        # callback are distinct, and neither proves registration preservation.
        review_families = {
            "Anlage_WTUmwaelzpumpe_Aktiviert_Modus": (1, 0x7),
            "Anlage_WfX_Feststoffkessel_OMischer": (16, 0xFFFA),
            "Anlage_WfX_Funktionsblock_OVolumenstrom": (16, 0xFFFB),
            "Heizung_HeizkreisX_Fernzugriff_Modus": (7, 0x7),
            "Heizung_HeizkreisX_OFernzugriff": (7, 0xFFFF),
            "Heizung_HeizkreisX_ONachheizung": (7, 0xFFFB),
            "Heizung_HeizkreisX_RaumthermostatX_Option": (35, 0xFFFF),
            "Heizung_RelaisAnforderungX_Aktiviert_Modus": (2, 0x10007),
            "Heizung_RelaisAnforderungX_OAusgangX": (2, 0xFFFE),
            "Heizung_RelaisAnforderungX_ORelaisX": (2, 0xFFFF),
            "Heizung_RelaisPumpeX_Aktiviert_Modus": (2, 0x10007),
            "Heizung_RelaisVentilX_Aktiviert_Modus": (2, 0x10007),
            "Heizung_WfX_BwVorerwaermung_OVentil": (16, 0xFFF9),
            "Heizung_WfX_ThBwErwaermung_OLadepumpe": (16, 0xFFFD),
            "Solar_Losreissimpuls": (1, 0xF),
            "Solar_ORuecklaufSensor": (1, 0xFFFA),
            "Solar_OVorlaufSensor": (1, 0xFFFB),
            "Solar_WfX_DrainBack_OBooster": (1, 0xFFFF),
        }
        review = {family: [] for family in review_families}
        for row in catalog.load_catalog(archive_path).definitions:
            family = re.sub(r"(?<=\D)\d+", "X", row.name)
            if (
                family in review
                and row.metadata.get("menu_refs")
                and not row.excluded
                and not row.metadata.get("compound_ref")
                and row.metadata.get("rpt_storage") in ("preferences", "persistent")
            ):
                review[family].append(row.name)
        for family, names in review.items():
            with self.subTest(native_review_family=family):
                count, expected_flag = review_families[family]
                self.assertEqual(len(names), count)
                flags = Counter()
                editor_pairs = set()
                for name in names:
                    pointer, flag, editor, reader = descriptor(name)
                    flags[hex(flag)] += 1
                    editor_pairs.add((hex(editor - base), hex(reader - base)))
                    self.assertNotEqual(pointer, 0)
                self.assertEqual(flags, {hex(expected_flag): count})
                print(
                    "Native remaining family:",
                    family,
                    sorted(flags.items()),
                    sorted(editor_pairs),
                )
        delay_pairs = set()
        for slot in range(1, 17):
            root = f"Anlage_Wf{slot}_Funktionsblock"
            flag = descriptor(root + "_OMindestausschaltzeit")
            duration = descriptor(root + "_dtMindestausschaltzeit")
            self.assertEqual(flag[1], 0xFFFC)
            self.assertEqual(flag[0] - duration[0], 42)
            delay_pairs.add(flag[2:])
        self.assertEqual(len(delay_pairs), 1)
        for actor in range(1, 101):
            root = f"Aktor{actor}"
            role = descriptor(root + "_Funktion")[0]
            self.assertEqual(descriptor(root + "_RelaisNr")[0], role + 2)
            self.assertEqual(descriptor(root + "_AusgangNr")[0], role + 3)
            for name, index in configuration.ActorReferences.actor_layout(
                actor
            ).items():
                self.assertEqual(values[name], index)
        relay_base = descriptor("Relais_Regler_R1_AktorFunktion")[0]
        for number in range(1, 46):
            layout = configuration.ActorReferences.output_layout(("relay", number))
            role = next(iter(layout))
            self.assertEqual(descriptor(role)[0], relay_base + 4 * (number - 1))
            for name, index in layout.items():
                self.assertEqual(values[name], index)
        for number in range(1, 5):
            layout = configuration.ActorReferences.output_layout(("analog", number))
            role = next(iter(layout))
            self.assertEqual(descriptor(role)[0], relay_base + 4 * (44 + number))
            for name, index in layout.items():
                self.assertEqual(values[name], index)
        decoder_prefix = bytes.fromhex("5589e583ec108b4508c1f808")
        decoder_matches = []
        position = text_offset
        while (
            position := binary.find(decoder_prefix, position, text_offset + text_size)
        ) >= 0:
            if hashlib.sha256(binary[position : position + 115]).hexdigest() == (
                "8eff49107718eba0bd1cbef241e3220dd4e081f46e8ba52262acac0e6f5657ad"
            ):
                decoder_matches.append(position)
            position += 1
        self.assertEqual(len(decoder_matches), 1)
        prefix = bytes.fromhex("5589e583ec388b450c8845e4807de400747a8b45")
        matches = []
        position = text_offset
        while (position := binary.find(prefix, position, text_offset + text_size)) >= 0:
            body = bytearray(binary[position : position + 150])
            if body[0x5B] == 0xE8:
                body[0x5C:0x60] = bytes(4)
                if hashlib.sha256(body).hexdigest() == (
                    "7abb4b99cbd75f3c497e17444b38044bfffa1bbd43399e0207b464c54ee43795"
                ):
                    matches.append(text_rva + position - text_offset)
            position += 1
        print("Native shared activity evaluator:", [hex(x) for x in matches])
        self.assertEqual(len(matches), 1)
        fingerprints = (
            (
                "VBus read Blob",
                157,
                0x07E4416A,
                "cd077ffb2dd1e1c9fc4550c548850e87109fadf987039835beeed5ff1d300757",
            ),
            (
                "VBus write Blob",
                166,
                0x61CE4F13,
                "e742b2836e8580be998f7dd5f3df00382876ff393f061b5dcbc4db49051f13ff",
            ),
            (
                "VBus bulk Blob",
                189,
                0x15E35D69,
                "f682fd4cc40bf542bde79f4608042b2093de3e23c80dc5a1174db22fa3e2b300",
            ),
            (
                "VBus bulk command",
                23,
                0x6EB10750,
                "af86a19f2d78fc815e96c3419102e693c1e7fa0a63e334ec35dbf5f4b89bb067",
            ),
        )
        locations = {}
        for label, length, checksum, digest in fingerprints:
            matches = []
            for i in range(len(code) - length + 1):
                candidate = code[i : i + length]
                if (
                    zlib.adler32(candidate) == checksum
                    and hashlib.sha256(candidate).hexdigest() == digest
                ):
                    matches.append(text_rva + i)
            print("Native instruction fingerprint:", label, [hex(x) for x in matches])
            with self.subTest(label=label):
                self.assertEqual(len(matches), 1)
            if len(matches) == 1:
                locations[label] = matches[0]
        if len(locations) == len(fingerprints):
            self.assertEqual(
                locations["VBus bulk Blob"] - locations["VBus bulk command"],
                0x6C,
            )
        # Compare native branch control flow, retaining all jumps and
        # constants. Only PE-declared address relocations and external CALL
        # displacements are normalized. The verified bulk branch locates it.
        reloc_rva, reloc_length = struct.unpack_from("<II", binary, optional + 136)
        reloc_offset = locate(base + reloc_rva, reloc_length, ".reloc")
        reloc_end = reloc_offset + reloc_length
        relocations = set()
        while reloc_offset < reloc_end:
            page, length = struct.unpack_from("<II", binary, reloc_offset)
            self.assertGreaterEqual(length, 8)
            self.assertEqual(length % 2, 0)
            self.assertLessEqual(reloc_offset + length, reloc_end)
            for offset in range(reloc_offset + 8, reloc_offset + length, 2):
                item = struct.unpack_from("<H", binary, offset)[0]
                kind = item >> 12
                if kind:
                    self.assertEqual(kind, 3)
                    relocations.add(page + (item & 0xFFF))
            reloc_offset += length

        # Direct relocated storage references are useful positive evidence;
        # absence is not proof because native code also accesses record offsets.
        references = {}
        for rva in sorted(relocations):
            if text_rva <= rva < text_rva + text_size - 3:
                offset = text_offset + rva - text_rva
                address = struct.unpack_from("<I", binary, offset)[0]
                references.setdefault(address, []).append(rva)
        parsed = catalog.load_catalog(archive_path).by_name
        for family, names in review.items():
            representative = min(names, key=lambda name: values[name])
            pointer, flags, editor, reader = descriptor(representative)
            row = parsed[representative]
            direct = references.get(pointer, [])
            print(
                "Native lifecycle evidence:",
                representative,
                row.index,
                row.metadata.get("type_chain"),
                "options",
                catalog.ordinal_options(row),
                "menu",
                row.metadata.get("menu_refs"),
                "storage references",
                len(direct),
                [hex(rva) for rva in direct[:8]],
            )
            self.assertEqual(flags, review_families[family][1])
            self.assertEqual(values[representative], row.index)
            for rva in direct:
                self.assertIn(rva, relocations)
            if flags & 0x10000:
                offset = locate(editor, 52, ".text")
                callback = struct.unpack_from("<I", binary, offset + 45)[0]
                self.assertIn(
                    editor + 45 - base,
                    relocations,
                )
                print(
                    "Native change callback RVA:", representative, hex(callback - base)
                )

        # Static disassembly of the exact registered archive is an optional
        # developer evidence probe, not a runtime dependency or write gate.
        # Log only semantic operands/call edges, never vendor bytes.
        if shutil.which("objdump"):
            with TemporaryDirectory() as directory:
                library = Path(directory) / "reference.dll"
                library.write_bytes(binary)

                def inspect_region(label, start, end):
                    output = subprocess.run(
                        [
                            "objdump",
                            "-d",
                            "-Mintel",
                            "--no-show-raw-insn",
                            f"--start-address={base + start}",
                            f"--stop-address={base + end}",
                            str(library),
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    ).stdout
                    edges = []
                    memory = []
                    immediates = []
                    for line in output.splitlines():
                        match = re.match(r"\s*([0-9a-f]+):\s+(\w+)\s*(.*)", line)
                        if not match:
                            continue
                        address, mnemonic, operands = match.groups()
                        rva = int(address, 16) - base
                        if mnemonic in ("call", "jmp") or mnemonic.startswith("j"):
                            target = re.match(r"([0-9a-f]+)(?:\s|$)", operands)
                            edges.append(
                                (
                                    hex(rva),
                                    mnemonic,
                                    hex(int(target[1], 16) - base)
                                    if target
                                    else "indirect",
                                )
                            )
                        if (
                            mnemonic
                            in ("mov", "movzx", "movsx", "and", "or", "test", "cmp")
                            and "PTR [" in operands
                        ):
                            memory.append((hex(rva), mnemonic, operands))
                        if mnemonic in ("mov", "push", "cmp", "test", "and", "or") and (
                            re.search(r"(?:^|,)0x[0-9a-f]+$", operands)
                        ):
                            immediates.append((hex(rva), mnemonic, operands))
                    self.assertTrue(edges or memory or immediates, label)
                    print("Native region edges:", label, edges)
                    print("Native region memory:", label, memory)
                    print("Native region constants:", label, immediates)

                # Continue from the proven callback edges in research 4.40.
                # Each helper is shared by both native instances.
                for label, start, end in (
                    ("registry add", 0xF9E38, 0xF9ECD),
                    ("registry remove", 0xF9ECD, 0xF9ECD + 420),
                    ("registry lookup", 0xF9563, 0xF9563 + 160),
                    ("setup and periodic dispatch", 0x2BFC, 0x2C45),
                ):
                    inspect_region(label, start, end)

        # Only compact exact metadata; large inherited role tables add no
        # transition evidence.
        for name in (
            "Solar_Losreissimpuls",
            "Heizung_Heizkreis1_Fernzugriff_Modus",
            "Heizung_Heizkreis1_Fernzugriff_SensorBAS_Nr",
            "Heizung_Heizkreis1_Fernzugriff_SensorFV_Nr",
        ):
            row = parsed[name]
            print(
                "Native selected metadata:",
                name,
                row.index,
                {
                    key: row.metadata.get(key)
                    for key in (
                        "type_chain",
                        "value_text",
                        "unit",
                        "minimum",
                        "maximum",
                        "quant",
                        "storeFactor",
                        "rpt_storage",
                        "menu_refs",
                    )
                },
            )

        def fingerprint(start, length, calls, relocation_count, expected):
            offset = locate(base + start, length, ".text")
            body = bytearray(binary[offset : offset + length])
            reloc = sorted(
                r - start for r in relocations if start <= r < start + length
            )
            self.assertEqual(len(reloc), relocation_count)
            for pos in reloc:
                self.assertLessEqual(pos + 4, length)
                body[pos : pos + 4] = bytes(4)
            targets = {}
            for pos in calls:
                self.assertEqual(body[pos], 0xE8)
                targets[pos] = (
                    start + pos + 5 + struct.unpack_from("<i", body, pos + 1)[0]
                )
                body[pos + 1 : pos + 5] = bytes(4)
            actual = hashlib.sha256(body).hexdigest()
            print("Native normalized fingerprint:", hex(start), actual)
            self.assertEqual(actual, expected)
            return targets

        # The full normalized handler differs across builds (46134041013).
        # Verify just the relevant bulk branches and their storage callees.
        # Addresses are anchored to the independently matched command block.
        stage = locations["VBus bulk command"]
        begin = fingerprint(
            stage - 414,
            242,
            (
                0x2B,
                0x3D,
                0x4D,
                0x58,
                0x5D,
                0x68,
                0x7A,
                0x86,
                0x92,
                0xD4,
                0xE8,
            ),
            6,
            "3a95ed342c3803e7a05f3ea91b24307e503d7cf5041ea1dd2b2f6a27ecaf9171",
        )
        commit = fingerprint(
            stage - 172,
            172,
            (0x46, 0x8E, 0xA2),
            7,
            "ed541bcef617e8bdda4dc11f45c63e8730523295ddf8ccc2b1d49ecec7c15e05",
        )
        writes = fingerprint(
            stage,
            495,
            (0x3D, 0x14C, 0x185, 0x1D1, 0x1E5),
            4,
            "a368b721434d098f8986c700aa605335ee5b93cf4b1ba310b1b852c31196ee75",
        )
        self.assertEqual(begin[0xD4], commit[0x8E])
        self.assertEqual(commit[0x8E], writes[0x1D1])
        fingerprint(
            commit[0x46],
            245,
            (0x12, 0x39, 0x63, 0xC1, 0xE9),
            9,
            "5e0867c3c52e06568928f10944d58ee15b875c6921987e89e0e56e8390dfa949",
        )
        dirty = fingerprint(
            writes[0x185],
            77,
            (0x23, 0x45),
            2,
            "f3f9f943ec77a2f881142737033a2d3a2f42dd53f7b2ba89af4d97751d852f1b",
        )
        self.assertEqual(dirty[0x23], dirty[0x45])
        print("Native bulk branch and storage helper contracts verified:", hex(stage))
        # The two JNI block comparisons in run 46133822341 did not match;
        # JNI is not the integration's transport. These four VBus blocks
        # establish byte order and immediate mutation, not
        # permission, commit durability or live physical recovery.

    @unittest.skipUnless(os.environ.get("RPT_ARCHIVE"), "real archive not provided")
    def test_exact_vendor_archive(self):
        parsed = catalog.load_catalog(os.environ["RPT_ARCHIVE"])
        self.assertEqual(parsed.digest, catalog.BX_PLUS_SHA256)
        definitions = {d.name: d for d in parsed.definitions}
        self.assertGreater(len(definitions), 7000)
        self.assertEqual(definitions["Schema"].index, 86)
        self.assertEqual(
            Counter(row.metadata["rpt_storage"] for row in definitions.values()),
            {
                "preferences": 7492,
                "volatile": 3123,
                "abstract": 2438,
                "struct": 1915,
                "persistent": 712,
            },
        )
        # Independent MX 2xx optimizer and BX Plus SET evidence agree on
        # the 51 x 9 timer layout. Verify the exact pinned RPT independently.
        for slot in range(51):
            prefix = f"Wsu{slot}"
            base = 18401 + 13 * slot
            for suffix, offset, kind in (
                ("_Belegt", 0, "Boolean"),
                ("_Schaltuhr_Woche_Initialisiert", 4, "Boolean"),
                *(
                    (f"_Schaltuhr_Woche_Tag{day}_Daten", 5 + i, "Blob")
                    for i, day in enumerate(("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"))
                ),
            ):
                row = definitions[prefix + suffix]
                self.assertEqual(row.index, base + offset)
                self.assertIn(kind, row.metadata["type_chain"])
                if kind == "Blob":
                    self.assertEqual(row.metadata["size_bytes"], 12)
                    self.assertEqual(row.metadata["rpt_storage"], "preferences")
                self.assertIsNone(row.excluded)
        # Corroborate the generic Solar function instance selector separately
        # from its type-specific storage. No ordinal-to-instance assumption.
        for slot in range(1, 17):
            prefix = f"Solar_Wf{slot}"
            self.assertEqual(
                definitions[prefix + "_Type"].index, 4409 + (slot - 1) * 120
            )
            self.assertEqual(definitions[prefix + "_Nr"].index, 4410 + (slot - 1) * 120)
        # Dedicated native families are separate from generic Solar_Wf Type.
        capture = discovery.ConfigurationCapture(
            parsed, SimpleNamespace(address=0x7112)
        )
        self.assertEqual(len(capture.packed_days), 357)
        for name in capture.packed_days:
            self.assertEqual(
                capture.weekly_day_shape(definitions[name]),
                (12, "rpt_declared"),
            )
        slots, members = capture.function_slots
        dedicated = {
            name
            for name, (_, family, _) in slots.items()
            if family == "solar_dedicated"
        }
        self.assertEqual(len(dedicated), 18)
        self.assertIn("Solar_Wf5_ExtWT", dedicated)
        self.assertIn("Solar_Wf1_DrainBack", dedicated)
        self.assertIn(
            definitions["Solar_Wf1_ExtWT_Relais1_Nr"], members["Solar_Wf1_ExtWT"]
        )
        self.assertNotIn(
            definitions["Solar_Wf1_ExtWT_Relais1_Nr"], members["Solar_Wf1"]
        )
        # Corroborate the production write contracts against the exact RPT,
        # not the earlier optimizer indices.
        configuration = importlib.import_module(f"{PACKAGE}.configuration")
        parameters = importlib.import_module(f"{PACKAGE}.parameters")
        profile = parameters.PROFILES[0]
        bound = profile.settings_for(catalog.BX_PLUS_CHANGESET, parsed)
        for setting in profile.settings:
            with self.subTest(contract=setting.name, unit=setting.unit):
                compiled = configuration.bind_setting(setting, parsed.by_name)
                self.assertIn(compiled, bound)
                self.assertEqual(compiled.name, setting.name)
                self.assertEqual(compiled.expected, setting.expected)
        options = configuration.BX_PLUS_PROGRAM_OPTIONS
        self.assertEqual(len({setting.name for setting in options}), 51)
        for setting in options:
            target = definitions[setting.name]
            self.assertEqual(target.metadata["rpt_storage"], "preferences")
            self.assertTrue(target.metadata["menu_refs"])
            self.assertEqual(target.index, setting.index)
            self.assertTrue(setting.save)
            if setting.kind == "switch":
                self.assertIn("Boolean", target.metadata["type_chain"])
                self.assertTrue(
                    any(
                        "BooleanNop" in ref.get("mask", "")
                        for ref in target.metadata["menu_refs"]
                    )
                )
            else:
                self.assertEqual(setting.kind, "select")
                self.assertEqual(
                    catalog.ordinal_options(target), {0: "Tag", 1: "TagNacht"}
                )
            for name, index in setting.expected.items():
                self.assertEqual(definitions[name].index, index)
            for rule in setting.requested_conditions:
                for dependency in rule.alternatives:
                    self.assertEqual(
                        definitions[dependency.name].index, dependency.index
                    )
        thermal = configuration.BX_PLUS_THERMAL_OPTIONS
        self.assertEqual(len({setting.name for setting in thermal}), 80)
        self.assertEqual(len(thermal), 160)
        for setting in thermal:
            target = definitions[setting.name]
            self.assertEqual(target.index, setting.index)
            self.assertEqual(target.metadata["rpt_storage"], "preferences")
            self.assertIn("Boolean", target.metadata["type_chain"])
            self.assertTrue(
                any(
                    "BooleanNop" in ref.get("mask", "")
                    for ref in target.metadata["menu_refs"]
                )
            )
            self.assertTrue(setting.save)
            self.assertEqual(setting.kind, "switch")
            for name, index in setting.expected.items():
                self.assertEqual(definitions[name].index, index)
        timers = configuration.BX_PLUS_WEEKLY_TIMER_OPTIONS
        self.assertEqual(len({setting.name for setting in timers}), 74)
        self.assertEqual(len(timers), 106)
        for setting in timers:
            target = definitions[setting.name]
            reference = setting.timer_reference
            self.assertEqual(target.index, setting.index)
            self.assertIn("Boolean", target.metadata["type_chain"])
            self.assertEqual(target.metadata["rpt_storage"], "preferences")
            self.assertIn(
                "WochenschaltuhrNr", definitions[reference.name].metadata["type_chain"]
            )
            self.assertTrue(
                any(
                    ref.get("extraRef") == reference.name
                    for ref in target.metadata["menu_refs"]
                )
            )
            for name, index in setting.expected.items():
                self.assertEqual(definitions[name].index, index)
        for slot in range(1, 51):
            allocation, days = configuration.WeeklyProgram.layout(slot)
            for name, index in {**allocation, **days}.items():
                self.assertEqual(definitions[name].index, index)
            for name in days:
                self.assertEqual(definitions[name].metadata["size_bytes"], 12)
                self.assertIn("Blob", definitions[name].metadata["type_chain"])
        coordination = configuration.BX_PLUS_COORDINATION_OPTIONS
        self.assertEqual(len({setting.name for setting in coordination}), 170)
        for setting in coordination:
            target = definitions[setting.name]
            self.assertEqual(target.index, setting.index)
            self.assertTrue(target.metadata["menu_refs"])
            self.assertTrue(setting.save)
            if setting.kind == "switch":
                self.assertEqual(target.metadata["rpt_storage"], "preferences")
                self.assertIn("Boolean", target.metadata["type_chain"])
            else:
                self.assertEqual(setting.kind, "number")
                self.assertTrue(target.metadata["compound_parent_read_candidate"])
                self.assertIn("FlowProxy", target.metadata["type_chain"])
            for name, index in setting.expected.items():
                self.assertEqual(definitions[name].index, index)
        modes = configuration.BX_PLUS_OPERATING_MODE_OPTIONS
        self.assertEqual(len({setting.name for setting in modes}), 65)
        self.assertEqual(len(modes), 91)
        for setting in modes:
            target = definitions[setting.name]
            self.assertEqual(target.index, setting.index)
            self.assertEqual(target.metadata["rpt_storage"], "preferences")
            self.assertTrue(target.metadata["menu_refs"])
            self.assertTrue(setting.save)
            if setting.kind == "select":
                self.assertEqual(
                    set(catalog.ordinal_options(target)),
                    {raw for _, raw in setting.options},
                )
            else:
                self.assertIn("Boolean", target.metadata["type_chain"])
            for name, index in setting.expected.items():
                self.assertEqual(definitions[name].index, index)
        room_controls = configuration.BX_PLUS_ROOM_THERMAL_CONTROLS
        self.assertEqual(len({setting.name for setting in room_controls}), 85)
        self.assertEqual(len(room_controls), 117)
        for setting in room_controls:
            target = definitions[setting.name]
            self.assertEqual(target.index, setting.index)
            self.assertTrue(target.metadata["menu_refs"])
            self.assertTrue(setting.save)
            if setting.kind == "select":
                self.assertEqual(
                    set(catalog.ordinal_options(target)),
                    set(setting.native_options or dict(setting.options).values()),
                )
            elif setting.kind == "switch":
                self.assertIn("Boolean", target.metadata["type_chain"])
            else:
                self.assertEqual(setting.kind, "number")
                self.assertTrue(target.metadata["compound_parent_read_candidate"])
            for name, index in setting.expected.items():
                self.assertEqual(definitions[name].index, index)
        activity = configuration.BX_PLUS_ACTIVITY_SETTINGS
        self.assertEqual(len({setting.name for setting in activity}), 83)
        self.assertEqual(len(activity), 218)
        for setting in activity:
            target = definitions[setting.name]
            self.assertEqual(target.index, setting.index)
            self.assertEqual(target.metadata["rpt_storage"], "preferences")
            self.assertIn("ActivityMode", target.metadata["type_chain"])
            self.assertEqual(set(catalog.ordinal_options(target)), {0, 1, 2})
            self.assertTrue(target.metadata["menu_refs"])
            for name, index in setting.expected.items():
                self.assertEqual(definitions[name].index, index)
        for slot in range(1, 17):
            root = f"Heizung_Wf{slot}"
            variant = definitions[root + "_Zirkulation_Variante"]
            self.assertEqual(
                [item.get("id") for item in variant.metadata["value_text"]],
                [
                    "Thermisch",
                    "Schaltuhr",
                    "ThermischUndUhr",
                    "Anforderung",
                    "AnforderungUndUhr",
                ],
            )
            self.assertTrue(
                all(
                    item.get("value") is None for item in variant.metadata["value_text"]
                )
            )
            self.assertEqual(variant.index, 11405 + (slot - 1) * 151)
            setting = next(
                s
                for s in activity
                if s.name == root + "_Aktiviert_Modus"
                and s.installed_by.allowed == (4,)
            )
            self.assertEqual(setting.dependencies[0].allowed, tuple(range(5)))
            self.assertEqual(
                {ref.name for ref in setting.weekly_references(1)},
                {root + "_SchaltuhrNr"},
            )
        self.assertEqual(len(bound), len(profile.settings) + 350)
        self.assertEqual(len([s for s in bound if s.weekly_slot is not None]), 350)
        # Report the remaining catalogue/menu review surface as part of the
        # existing exact-archive check. A menu reference does not grant write
        # permission; the inventory is evidence, not an automatic allowlist.
        bound_names = {setting.name for setting in bound}
        menu_preferences = {
            name
            for name, row in definitions.items()
            if row.metadata.get("menu_refs")
            and (not row.excluded or row.metadata.get("compound_parent_read_candidate"))
            and (
                row.metadata.get("rpt_storage") in ("preferences", "persistent")
                or row.metadata.get("compound_parent_read_candidate")
            )
            and not row.metadata.get("compound_ref")
        }
        self.assertIn("Heizung_Heizkreis1_Heizsystem_Gerade_TAussen1", menu_preferences)
        self.assertIn("Heizung_Heizkreis1_Mischer_dTEin", menu_preferences)
        flow = {
            f"Anlage_Wf{slot}_Funktionsblock_Volumenstrom{suffix}"
            for slot in range(1, 17)
            for suffix in ("Ein", "Aus")
        }
        self.assertTrue(flow <= menu_preferences)
        self.assertTrue(flow <= bound_names)
        self.assertEqual(
            len({s.name for s in bound if s.name.endswith("_OFeststoffkesselAus")}), 23
        )
        self.assertEqual(
            len(
                {
                    s.name
                    for s in bound
                    if s.name.endswith("_RLBeimischung_OHeizkreisIntern")
                }
            ),
            16,
        )
        unbound = menu_preferences - bound_names
        families = Counter(re.sub(r"(?<=\D)\d+", "X", name) for name in unbound)
        print(
            "Exact menu review surface:",
            len(menu_preferences),
            "menu preferences/proxies;",
            len(menu_preferences & bound_names),
            "bound;",
            len(unbound),
            "without typed contracts;",
            len(families),
            "normalized families",
        )
        print("Unbound native menu families:", sorted(families.items()))
        # Every remaining preference is explicitly accounted for, including
        # open reviews. A read-only scope reason is not a native access bit.
        reasons = Counter(
            configuration.native_menu_review_reason(name) for name in unbound
        )
        self.assertEqual(sum(reasons.values()), len(unbound))
        print("Native menu review categories:", sorted(reasons.items()))
        pending = Counter(
            re.sub(r"(?<=\D)\d+", "X", name)
            for name in unbound
            if configuration.native_menu_review_reason(name)
            in (
                "program_option_review_required",
                "native_registration_transition_unverified",
                "native_deallocation_preservation_unverified",
                "native_operating_contract_unverified",
                "hydraulic_applicability_review_required",
                "manual_override_contract_unverified",
            )
        )
        print("Pending semantic menu review:", sorted(pending.items()))
        for slot in range(1, 17):
            prefix = f"Anlage_Wf{slot}"
            self.assertEqual(
                catalog.ordinal_options(definitions[prefix + "_Type"])[11],
                "RLBeimischung",
            )
            for suffix in ("dTEin", "dTAus"):
                target = definitions[f"{prefix}_RLBeimischung_{suffix}"]
                self.assertTrue(
                    any(
                        ref.get("menu") == f"{prefix}_RLBeimischung"
                        for ref in target.metadata["menu_refs"]
                    )
                )
                self.assertEqual(target.storage, "abstract")
        self.assertEqual(
            catalog.ordinal_options(definitions["Solar_Beladungstyp"]),
            {0: "Pendelladung", 1: "SukzessiveLadung"},
        )
        for circuit in range(1, 8):
            root = f"Heizung_Heizkreis{circuit}"
            target = definitions[root + "_Betriebsart"]
            self.assertEqual(
                catalog.ordinal_options(target),
                {
                    0: "Automatik",
                    1: "Tag",
                    2: "Nacht",
                    3: "Sommer",
                    4: "Urlaub",
                    5: "Aus",
                },
            )
            self.assertEqual(target.storage, "unspecified")
            self.assertEqual(definitions[root + "_Betriebsstatus"].storage, "volatile")
            self.assertTrue(
                any(
                    ref.get("menu") == f"Status_{root}"
                    for ref in target.metadata["menu_refs"]
                )
            )
            self.assertFalse(any(s.name == root + "_Betriebsstatus" for s in bound))
        # All linked status menus refer to the same master preference.
        master = definitions["Heizung_Heizkreis1_Betriebsart"]
        self.assertTrue(
            {f"Status_Heizung_Heizkreis{n}" for n in range(1, 8)}.issubset(
                {ref.get("menu") for ref in master.metadata["menu_refs"]}
            )
        )
        solar_optional = configuration.BX_PLUS_SOLAR_DEDICATED_SETTINGS
        self.assertEqual(len({s.name for s in solar_optional}), 52)
        for setting in solar_optional:
            target = definitions[setting.name]
            self.assertTrue(target.metadata["menu_refs"])
            self.assertEqual(
                definitions[setting.installed_by.name].index,
                setting.installed_by.index,
            )
            self.assertTrue(
                {"SensorNr", "SensorEinstrahlungNr", "InstanzMaske"}.intersection(
                    definitions[setting.installed_by.name].metadata["type_chain"]
                )
            )
            for dependency in setting.dependencies:
                if dependency.name.endswith("_KollektorMaske"):
                    self.assertIn(
                        "InstanzMaske",
                        definitions[dependency.name].metadata["type_chain"],
                    )
                if dependency.name.startswith("Solar_Kol"):
                    self.assertEqual(definitions[dependency.name].storage, "volatile")
        pulse = next(
            setting for setting in bound if setting.name == "Solar_Losreissimpuls"
        )
        self.assertEqual(pulse.index, 3438)
        self.assertEqual(pulse.limits, ("10", "300", "1", "1"))
        self.assertEqual(pulse.unit, "s")
        self.assertEqual(pulse.encode(25), 25)
        self.assertEqual(pulse.decode(300), 300)
        self.assertEqual(definitions[pulse.name].metadata["rpt_storage"], "preferences")
        self.assertIn(
            {"mask": "UserLevel_EL2", "menu": "Solar_Experte", "menu_type": None},
            definitions[pulse.name].metadata["menu_refs"],
        )
        for setting in configuration.BX_PLUS_SOLAR_LOADING_LOGIC_SETTINGS:
            target = definitions[setting.name]
            self.assertTrue(target.metadata["menu_refs"])
            if setting.kind == "switch":
                self.assertEqual(target.storage, "unspecified")
                self.assertIn("Boolean", target.metadata["type_chain"])
                self.assertTrue(
                    any(
                        ref.get("menu") == "Solar_Grundeinstellungen_Ladelogik"
                        for ref in target.metadata["menu_refs"]
                    )
                )
            elif setting.name == "Solar_Spreizladung_dTDifferenz":
                self.assertEqual(target.storage, "abstract")
                capability = definitions["Solar_ParallelbeladungMoeglich"]
                self.assertEqual(capability.index, 3439)
                self.assertEqual(capability.storage, "volatile")
                self.assertIn("Boolean", capability.metadata["type_chain"])
                self.assertTrue(
                    any(
                        "Solar_ParallelbeladungMoeglich" in ref.get("mask", "")
                        for ref in definitions["Solar_OSpreizladung"].metadata[
                            "menu_refs"
                        ]
                    )
                )
            else:
                self.assertEqual(target.metadata["unit"].strip(), setting.unit)
                self.assertEqual(target.storage, "unspecified")
        for setting in configuration.BX_PLUS_SETTINGS:
            definition = definitions[setting.name]
            self.assertEqual(definition.index, setting.index)
            self.assertIsNone(definition.excluded)
            self.assertEqual(definition.storage, "unspecified")
            self.assertTrue(
                any(
                    ref.get("menu") == "Grundeinstellungen"
                    for ref in definition.metadata["menu_refs"]
                )
            )
            self.assertFalse(definition.metadata.get("compound_ref"))
            self.assertFalse(definition.metadata.get("selector_ref"))
            if setting.kind == "switch":
                self.assertIn("Boolean", definition.metadata["type_chain"])
            else:
                labels = [item["label"] for item in definition.metadata["value_text"]]
                self.assertEqual(len(labels), len(setting.options))
                self.assertEqual([raw for _, raw in setting.options], [0, 1])
        for setting in configuration.BX_PLUS_LOGGING_SETTINGS:
            target = definitions[setting.name]
            self.assertEqual(target.storage, "unspecified")
            self.assertTrue(
                any(
                    ref.get("menu") == "SDKarte"
                    and "SDKarte_Status_KarteVorhanden" in ref.get("mask", "")
                    and "SDKarte_Status_KarteNichtVoll" in ref.get("mask", "")
                    for ref in target.metadata["menu_refs"]
                )
            )
            if setting.kind == "number":
                self.assertEqual(target.metadata["minimum"], "1")
                self.assertEqual(target.metadata["maximum"], "1200")
                self.assertEqual(target.metadata["quant_step"], "1")
            else:
                self.assertEqual(
                    catalog.ordinal_options(target), {0: "Linear", 1: "Zyklisch"}
                )
        # Every generated mixer slot must match the exact firmware layout,
        # documented setting and ordinal semantics. The live Type and
        # ActivityMode checks still gate every native action.
        self.assertEqual(len(configuration.BX_PLUS_DIFFERENTIAL_SETTINGS), 352)
        for setting in configuration.BX_PLUS_DIFFERENTIAL_SETTINGS:
            parent = definitions[setting.name]
            self.assertEqual(parent.index, setting.index)
            self.assertEqual(parent.storage, "abstract")
            self.assertTrue(parent.metadata["compound_parent_read_candidate"])
            self.assertEqual(
                parent.metadata["selector_ref"], "TemperatureHysteresisSelector"
            )
            selector = definitions[setting.installed_by.name]
            self.assertEqual(selector.index, setting.installed_by.index)
            self.assertEqual(
                selector.metadata["value_text"][setting.installed_by.allowed[0]]["id"],
                setting.name.split("_")[2],
            )
            for name, index in setting.expected.items():
                self.assertEqual(definitions[name].index, index)
            for name, _, alias, _ in setting.compound_mirrors:
                child = definitions[alias]
                self.assertEqual(child.metadata["compound_ref"], name)
                if name == setting.name:
                    self.assertEqual(
                        catalog.numeric_encoding(child),
                        tuple(Decimal(v) for v in setting.limits),
                    )
        comfort = configuration.BX_PLUS_HEATING_COMFORT_SETTINGS
        self.assertEqual(len(comfort), 288)
        self.assertEqual(len([s for s in comfort if s.kind == "select"]), 7)
        self.assertEqual(
            len(
                {
                    s.name
                    for s in comfort
                    if "SommerTag" in s.name or "SommerNacht" in s.name
                }
            ),
            28,
        )
        self.assertEqual(
            len({s.name for s in comfort if s.name.endswith("_dTAbsenkung")}), 35
        )
        vacation = next(s for s in bound if s.name == "Urlaubsfunktion_Tage")
        self.assertEqual(vacation.limits, ("0", "200", "1", "1"))
        self.assertEqual(
            vacation.expected,
            {"Urlaubsfunktion_Tage": 19063, "InbetriebnahmeAbgeschlossen": 68},
        )
        for setting in (s for s in comfort if s.compound_mirrors):
            parent = definitions[setting.name]
            self.assertEqual(parent.index, setting.index)
            self.assertTrue(parent.metadata["compound_parent_read_candidate"])
            for name, index in setting.expected.items():
                self.assertEqual(definitions[name].index, index)
            for name, _, alias, _ in setting.compound_mirrors:
                child = definitions[alias]
                self.assertEqual(child.metadata["compound_ref"], name)
                self.assertEqual(
                    catalog.numeric_encoding(child),
                    tuple(Decimal(v) for v in setting.limits),
                )
        self.assertEqual(len(configuration.BX_PLUS_LOADING_SETTINGS), 128)
        for setting in configuration.BX_PLUS_LOADING_SETTINGS:
            parent = definitions[setting.name]
            self.assertEqual(parent.index, setting.index)
            self.assertTrue(parent.metadata["compound_parent_read_candidate"])
            selector = definitions[setting.installed_by.name]
            self.assertEqual(
                selector.metadata["value_text"][setting.installed_by.allowed[0]]["id"],
                setting.name.split("_")[2],
            )
            for name, index in setting.expected.items():
                self.assertEqual(definitions[name].index, index)
            for name, _, alias, _ in setting.compound_mirrors:
                child = definitions[alias]
                self.assertEqual(child.metadata["compound_ref"], name)
                limits = (
                    setting.limits
                    if name == setting.name
                    else dict(setting.peer_limits)[name]
                )
                self.assertEqual(
                    catalog.numeric_encoding(child),
                    tuple(Decimal(v) for v in limits),
                )
        self.assertEqual(len(configuration.BX_PLUS_THERMOSTAT_SETTINGS), 128)
        for setting in configuration.BX_PLUS_THERMOSTAT_SETTINGS:
            parent = definitions[setting.name]
            self.assertEqual(parent.index, setting.index)
            self.assertTrue(parent.metadata["compound_parent_read_candidate"])
            for name, index in setting.expected.items():
                self.assertEqual(definitions[name].index, index)
            for name, _, alias, _ in setting.compound_mirrors:
                self.assertEqual(definitions[alias].metadata["compound_ref"], name)
                if name == setting.name:
                    self.assertEqual(
                        catalog.numeric_encoding(definitions[alias]),
                        tuple(Decimal(v) for v in setting.limits),
                    )
        self.assertEqual(len(configuration.BX_PLUS_REFERENCE_SETTINGS), 64)
        for setting in configuration.BX_PLUS_REFERENCE_SETTINGS:
            self.assertIn(setting.name, bound_names)
            target = definitions[setting.name]
            self.assertEqual(target.index, setting.index)
            self.assertTrue(target.metadata["menu_refs"])
            self.assertEqual(target.metadata["rpt_storage"], "preferences")
            if setting.kind == "select":
                self.assertEqual(set(catalog.ordinal_options(target)), {0, 1, 2, 3})
            else:
                self.assertIn("Boolean", target.metadata["type_chain"])
            for name, index in setting.expected.items():
                self.assertEqual(definitions[name].index, index)
        self.assertEqual(len(configuration.BX_PLUS_TIMING_SETTINGS), 160)
        for setting in configuration.BX_PLUS_TIMING_SETTINGS:
            target = definitions[setting.name]
            self.assertEqual(target.index, setting.index)
            self.assertIsNone(target.excluded)
            self.assertEqual(target.storage, "unspecified")
            for name, index in setting.expected.items():
                self.assertEqual(definitions[name].index, index)
            self.assertTrue(target.metadata["menu_refs"])
            if setting.kind == "switch":
                self.assertIn("Boolean", target.metadata["type_chain"])
                self.assertTrue(
                    any(
                        ref.get("menu") == setting.name.rsplit("_", 1)[0]
                        and "BooleanNop" in ref.get("mask", "")
                        for ref in target.metadata["menu_refs"]
                    )
                )
            elif setting.name.endswith("_Funktionsblock_dtMindestausschaltzeit"):
                self.assertEqual(target.metadata["minimum"], "1")
                self.assertEqual(target.metadata["maximum"], "1439")
                self.assertEqual(target.metadata["quant"], "1")
                self.assertEqual(target.metadata["quant_step"], "1")
            elif "Time" in target.metadata["type_chain"]:
                self.assertEqual(target.metadata["minimum"], "1:00")
                self.assertEqual(target.metadata["maximum"], "15:00")
                self.assertEqual(target.metadata["quant"], "00:15")
            else:
                self.assertEqual(
                    Decimal(target.metadata["minimum"]), Decimal(setting.limits[0])
                )
                self.assertEqual(
                    Decimal(target.metadata["maximum"]), Decimal(setting.limits[1])
                )
                self.assertEqual(target.metadata["unit"].strip(), setting.unit)
        self.assertEqual(len(configuration.BX_PLUS_MIXER_SETTINGS), 16)
        for setting in configuration.BX_PLUS_MIXER_SETTINGS:
            target = definitions[setting.name]
            self.assertEqual(target.index, setting.index)
            self.assertIsNone(target.excluded)
            self.assertEqual(target.storage, "unspecified")
            self.assertIn("Number", target.metadata["type_chain"])
            self.assertEqual(target.metadata["minimum"], "1")
            self.assertEqual(target.metadata["maximum"], "20")
            self.assertEqual(target.metadata["unit"].strip(), "s")
            self.assertFalse(target.metadata.get("compound_ref"))
            self.assertFalse(target.metadata.get("selector_ref"))
            self.assertTrue(
                any(
                    ref["menu"] == setting.name.removesuffix("_dtIntervall")
                    and not ref.get("mask")
                    for ref in target.metadata["menu_refs"]
                )
            )
            selector = definitions[setting.installed_by.name]
            self.assertEqual(selector.index, setting.installed_by.index)
            self.assertEqual(selector.metadata["value_text"][2]["id"], "Mischer")
            activity = definitions[setting.dependencies[0].name]
            self.assertEqual(activity.index, setting.dependencies[0].index)
            self.assertEqual(activity.metadata["value_text"][1]["label"], "Aktiviert")
        self.assertEqual(len(configuration.BX_PLUS_HQM_SETTINGS), 21)
        for setting in configuration.BX_PLUS_HQM_SETTINGS:
            self.assertEqual(
                setting.owner_address, 0x7130 + int(setting.name.split("_")[0][3:])
            )
            target = definitions[setting.name]
            self.assertEqual(target.index, setting.index)
            self.assertIsNone(target.excluded)
            self.assertEqual(target.storage, "unspecified")
            self.assertTrue(target.metadata["menu_refs"])
            self.assertFalse(target.metadata.get("compound_ref"))
            self.assertFalse(target.metadata.get("selector_ref"))
            selector = definitions[setting.installed_by.name]
            self.assertEqual(selector.index, setting.installed_by.index)
            self.assertEqual(selector.metadata["value_text"][1]["id"], "Wmz")
            for dependency in setting.dependencies:
                self.assertEqual(definitions[dependency.name].index, dependency.index)
            if setting.kind == "switch":
                self.assertIn("Boolean", target.metadata["type_chain"])
            elif setting.kind == "select":
                self.assertEqual(
                    [item["id"] for item in target.metadata["value_text"]],
                    ["CO2", "Oel", "Gas", "Kohle"],
                )
            else:
                self.assertEqual(target.metadata["minimum"], "0.0000001")
                self.assertEqual(target.metadata["maximum"], "100")
                self.assertEqual(target.metadata["storeFactor"], "0.0000001")
                self.assertEqual(target.metadata["displayFactor"], "0.0000001")
                self.assertEqual(target.metadata["quant"], "1")
                self.assertEqual(setting.encode(0.5000001), 5000001)
        self.assertEqual(len(configuration.BX_PLUS_HEATING_SETTINGS), 7)
        for setting in configuration.BX_PLUS_HEATING_SETTINGS:
            target = definitions[setting.name]
            self.assertEqual(target.index, setting.index)
            self.assertIsNone(target.excluded)
            self.assertEqual(target.storage, "unspecified")
            self.assertIn("Number", target.metadata["type_chain"])
            self.assertEqual(target.metadata["minimum"], "0.3")
            self.assertEqual(target.metadata["maximum"], "3")
            self.assertEqual(target.metadata["quant"], "0.1")
            self.assertEqual(target.metadata["storeFactor"], "0.1")
            self.assertEqual(target.metadata["displayFactor"], "0.1")
            self.assertEqual(
                {ref["mask"] for ref in target.metadata["menu_refs"]},
                {
                    setting.name.replace("Heizkurve", "Heizsystem_Kennlinie"),
                    setting.name.replace("Heizkurve", "Heizsystem_Raumeinfluss"),
                },
            )
            selector = definitions[setting.installed_by.name]
            self.assertEqual(selector.index, setting.installed_by.index)
            self.assertEqual(selector.metadata["value_text"][1]["id"], "Intern")
            for dependency in setting.dependencies:
                self.assertEqual(definitions[dependency.name].index, dependency.index)
            modes = definitions[setting.dependencies[1].name].metadata["value_text"]
            self.assertEqual(
                [modes[i]["id"] for i in (1, 5)], ["Kennlinie", "Raumeinfluss"]
            )
        self.assertEqual(len(configuration.BX_PLUS_ADDITIONAL_OPERATING_SETTINGS), 435)
        self.assertEqual(len(configuration.BX_PLUS_HEATING_OPERATING_SETTINGS), 373)
        for setting in configuration.BX_PLUS_HEATING_OPERATING_SETTINGS:
            for name, index in setting.expected.items():
                self.assertEqual(definitions[name].index, index)
            if setting.name.endswith("TVorlaufVorgabe"):
                self.assertTrue(
                    definitions[setting.name].metadata["compound_parent_read_candidate"]
                )
                self.assertEqual(
                    {peer.operation for peer in setting.relative_constraints},
                    {"ge", "le"},
                )
                self.assertTrue(
                    any(
                        ref.get("mask", "").endswith("_Heizsystem_Konstant")
                        for ref in definitions[setting.name].metadata["menu_refs"]
                    )
                )
        self.assertEqual(len(configuration.BX_PLUS_SOLAR_STORE_SETTINGS), 84)
        self.assertEqual(len(configuration.BX_PLUS_SOLAR_COLLECTOR_SETTINGS), 6)
        for setting in configuration.BX_PLUS_ADDITIONAL_OPERATING_SETTINGS:
            if setting.name.startswith("Anlage_WTUmwaelzpumpe_"):
                self.assertEqual(
                    setting.installed_by.name, "Anlage_WTUmwaelzpumpe_Relais_Nr"
                )
                continue
            selector = definitions[setting.installed_by.name]
            self.assertEqual(
                selector.metadata["value_text"][setting.installed_by.allowed[0]]["id"],
                setting.name.split("_")[2],
            )
        for setting in configuration.BX_PLUS_SOLAR_STORE_SETTINGS:
            # Topology evidence must be volatile native references, not
            # persistent configuration defaults or a guessed SystemId.
            for ref in (setting.installed_by, *setting.dependencies[:2]):
                definition = definitions[ref.name]
                self.assertEqual(definition.index, ref.index)
                self.assertEqual(definition.storage, "volatile")
                self.assertTrue(
                    {"SensorNr", "AktorNr", "RelaisNr", "AusgangNr"}.intersection(
                        definition.metadata["type_chain"]
                    )
                )
        self.assertEqual(len(configuration.BX_PLUS_TRANSFER_SETTINGS), 256)
        self.assertEqual(len(configuration.BX_PLUS_SOLID_FUEL_MIXER_SETTINGS), 80)
        for setting in (
            *configuration.BX_PLUS_TRANSFER_SETTINGS,
            *configuration.BX_PLUS_SOLID_FUEL_MIXER_SETTINGS,
        ):
            parent = definitions[setting.name]
            self.assertEqual(parent.index, setting.index)
            selector = definitions[setting.installed_by.name]
            self.assertEqual(
                selector.metadata["value_text"][setting.installed_by.allowed[0]]["id"],
                setting.name.split("_")[2],
            )
            for name, index in setting.expected.items():
                self.assertEqual(definitions[name].index, index)
            if not setting.compound_mirrors:
                self.assertEqual(parent.storage, "unspecified")
                self.assertEqual(
                    catalog.numeric_encoding(parent),
                    tuple(Decimal(v) for v in setting.limits),
                )
                continue
            self.assertEqual(parent.storage, "abstract")
            self.assertTrue(parent.metadata["compound_parent_read_candidate"])
            self.assertEqual(
                parent.metadata["selector_ref"], "TemperatureHysteresisSelector"
            )
            for name, _, alias, _ in setting.compound_mirrors:
                child = definitions[alias]
                self.assertEqual(child.metadata["compound_ref"], name)
                self.assertEqual(
                    catalog.numeric_encoding(child),
                    tuple(Decimal(v) for v in setting.limits),
                )
        self.assertEqual(len(configuration.BX_PLUS_PUMP_SETTINGS), 30)
        for setting in configuration.BX_PLUS_PUMP_SETTINGS:
            target = definitions[setting.name]
            self.assertEqual(target.index, setting.index)
            self.assertIsNone(target.excluded)
            self.assertEqual(target.storage, "unspecified")
            self.assertIn("Number", target.metadata["type_chain"])
            self.assertEqual(target.metadata["minimum"], "20")
            self.assertEqual(target.metadata["maximum"], "100")
            self.assertEqual(target.metadata["unit"], "%")
            self.assertFalse(target.metadata.get("compound_ref"))
            self.assertFalse(target.metadata.get("selector_ref"))
            role = definitions[setting.optional_role.name]
            self.assertEqual(role.index, setting.optional_role.index)
            for raw, semantic_id in configuration.PUMP_ROLES:
                self.assertEqual(role.metadata["value_text"][raw]["id"], semantic_id)
            for name, index in setting.expected.items():
                self.assertEqual(definitions[name].index, index)
                self.assertIsNone(definitions[name].excluded)
        self.assertEqual(definitions["Bedienercode"].excluded, "sensitive")
        self.assertEqual(
            definitions["MesseModus_BedienercodeReset"].excluded, "sensitive"
        )
        capture = discovery.ConfigurationCapture(
            parsed, SimpleNamespace(address=0x7112)
        )
        self.assertEqual(capture.next_batch()[0].name, "Schema")
        self.assertNotIn("Bedienercode", capture.export(True)["parameters"])
        self.assertEqual(len(capture.base_candidates), 11325)
        self.assertEqual(len(capture.packed_days), 357)
        self.assertEqual(len({name.split("_")[0] for name in capture.packed_days}), 51)
        self.assertTrue(
            all(
                definitions[name].index is not None
                and all(
                    definitions[flag].index == index for flag, index in flags.items()
                )
                for name, flags in capture.packed_days.items()
            )
        )
        parents = capture.candidates[len(capture.base_candidates) :]
        self.assertIn("Solar_Sp1_dTein", {d.name for d in parents})
        self.assertIn("Anlage_Wf1_Feststoffkessel_dTEin", {d.name for d in parents})
        self.assertTrue(all(not catalog.is_sensitive(d) for d in parents))
        print("RPT read-only compound parent extension:", len(parents))
        self.assertEqual(definitions["Relais_Regler_R4_Handbetrieb"].index, 2019)
        self.assertEqual(definitions["Modul1_Vorhanden"].storage, "volatile")
        self.assertEqual(
            catalog.decoded_value(definitions["Relais_Regler_R4_Handbetrieb"], 2),
            "Auto",
        )
        self.assertGreater(sum(d.excluded is None for d in definitions.values()), 1000)
        counts = {}
        for definition in definitions.values():
            status = definition.excluded or "candidate"
            counts[status] = counts.get(status, 0) + 1
        print("Exact vendor archive: definitions", len(definitions), counts)
        self.assertEqual(
            catalog.encode_numeric_value(definitions["Solar_Sp1_dTein_K"], 6.5),
            65,
        )
        self.assertEqual(
            catalog.encode_numeric_value(
                definitions["Anlage_Wf1_Feststoffkessel_dTEin_K"], 6
            ),
            60,
        )
        # Targeted firmware evidence, not live values or redistributed XML.
        for name in (
            "Relais_Regler_R1_MinDrehzahl",
            "Relais_Regler_R1_MaxDrehzahl",
            "Relais_Regler_R1_ODrehzahl",
            "Relais_Regler_R1_OAdapter",
            "Relais_Modul1_R1_MinDrehzahl",
            "Solar_SystemId",
            "Solar_Sp1_dTein",
            "Solar_Sp1_dTein_K",
            "Solar_Sp1_dTein_R",
            "Anlage_Wf1_Type",
            "Anlage_Wf1_Aktiviert_Modus",
            "Anlage_Wf1_Feststoffkessel_dTEin",
            "Anlage_Wf1_Feststoffkessel_dTEin_K",
            "Anlage_Wf1_Feststoffkessel_dTEin_R",
            "Heizung_Heizkreis1_Betriebsstatus",
            "Wmz1_Type",
            "Wmz1_FrostschutzGehalt",
            "Wmz1_OAlternativAnzeige",
            "Wmz1_AlternativAnzeigeEinheit",
            "Anlage_Wf1_Mischer_dtIntervall",
            "Heizung_Heizkreis1_Type",
            "Heizung_Heizkreis1_Aktiviert_Modus",
            "Heizung_Heizkreis1_Heizkurve",
            "Heizung_Heizkreis1_System",
            "Heizung_Heizkreis1_Heizsystem",
            "Wmz1_AlternativAnzeigeFaktor",
            "Heizung_Heizkreis1_dTTageskorrektur_K",
            "Heizung_Heizkreis1_dTNachtkorrektur_K",
        ):
            d = definitions.get(name)
            if d is None:
                print("RPT exact contract absent:", name)
                continue
            relevant = {
                k: d.metadata[k]
                for k in (
                    "type_chain",
                    "selector_ref",
                    "selector_refs",
                    "compound_ref",
                    "minimum",
                    "maximum",
                    "quant",
                    "quant_step",
                    "storeFactor",
                    "displayFactor",
                    "unit",
                    "value_text",
                    "menu_refs",
                    "allow_parameterization",
                    "force_use",
                )
                if k in d.metadata
            }
            print("RPT exact contract:", name, d.index, d.storage, relevant)

        # Aggregate schema evidence only: no vendor data or site values bundled.
        with zipfile.ZipFile(os.environ["RPT_ARCHIVE"]) as archive:
            # Identify additional machine-readable firmware artifacts in the
            # exact pinned vendor package (e.g. optimizer definitions).
            # Report names and sizes only; do not redistribute vendor content.
            print(
                "RPT archive inventory:",
                [
                    (item.filename, item.file_size)
                    for item in archive.infolist()
                    if not item.is_dir()
                ],
            )
            # RPT 2.02 DeviceProperties selects the actual parameter filter
            # from the device descriptor. The app contains several filters,
            # so class presence alone does not establish device behavior.
            descriptor = ET.fromstring(archive.read("BXPLUS_2.08/bxplus_208.xml"))
            filters = [
                node.get("classname", "").rsplit(".", 1)[-1]
                for node in descriptor.iter()
                if node.tag.rsplit("}", 1)[-1] == "parameterfilter"
            ]
            self.assertEqual(filters, ["MX2Filter"])
            # Static research only: identify whether the shipped RPT assembly
            # contains relevant named symbols. No execution, decompilation
            # or vendor binary contents in the release or CI artifacts.
            binary = archive.read("BXPLUS_2.08/DeltaSol_BXPlus_208.dll")

            identifiers = set(re.findall(rb"[A-Za-z_][A-Za-z0-9_]{3,100}", binary))
            keywords = (
                b"wochenschalt",
                b"schaltuhr",
                b"zeitfenster",
                b"blob",
                b"systemid",
            )
            print(
                "RPT assembly symbol evidence:",
                [
                    (
                        keyword.decode(),
                        sum(keyword in symbol.lower() for symbol in identifiers),
                    )
                    for keyword in keywords
                ],
            )
            root = ET.fromstring(archive.read(catalog.BX_PLUS_MENU))
        # Targeted structural evidence for the remaining protocol blockers.
        # Output tag/attribute names and counts only, never proprietary XML.
        for type_id in ("Blob", "Solar_SystemId", "WochenschaltuhrTag"):
            nodes = [n for n in root.iter("type") if n.get("id") == type_id]
            print(
                "RPT type structure:",
                type_id,
                [(sorted(n.attrib), sorted({child.tag for child in n})) for n in nodes],
            )
        print(
            "RPT solar presence definition tags:",
            sorted(
                {
                    n.tag
                    for n in root.iter()
                    if n.get("id") in ("Solar_Sp1_Vorhanden", "Solar_Mehrspeicher")
                }
            ),
        )
        value_attributes = Counter()
        value_children = Counter()
        tags = Counter(node.tag for node in root.iter())
        for node in root.iter("value"):
            value_attributes.update(node.attrib.keys())
            value_children.update(child.tag for child in node)
        print("RPT value attribute coverage:", sorted(value_attributes.items()))
        print("RPT value child coverage:", sorted(value_children.items()))
        print("RPT XML element coverage:", sorted(tags.items()))
        print(
            "RPT allowParameterization flags:",
            sorted(
                Counter(
                    node.get("allowParameterization")
                    for node in root.iter("value")
                    if node.get("allowParameterization") is not None
                ).items()
            ),
        )
        print(
            "RPT explicit parameterization safe candidates:",
            [
                (d.name, d.storage, d.type_id)
                for d in capture.candidates
                if d.metadata.get("allow_parameterization") == "true"
            ],
        )
        print(
            "RPT menu type frequencies:",
            Counter(n.get("type") for n in root.iter("menu")).most_common(12),
        )
        print(
            "RPT line attribute coverage:",
            sorted(Counter(k for n in root.iter("line") for k in n.attrib).items()),
        )
        print(
            "RPT menu attribute coverage:",
            sorted(Counter(k for n in root.iter("menu") for k in n.attrib).items()),
        )
        print(
            "RPT quant attribute coverage:",
            sorted(Counter(k for n in root.iter("quant") for k in n.attrib).items()),
        )
        print(
            "RPT quant value frequencies:",
            Counter((n.text or "").strip() for n in root.iter("quant")).most_common(12),
        )
        print(
            "RPT typed metadata coverage:",
            dict(
                Counter(
                    key
                    for definition in capture.candidates
                    for key in definition.metadata
                )
            ),
        )


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def runtime(self, operation):
        definitions = tuple(
            catalog.Definition(f"Test{n}", n + 1, "unspecified", None, {})
            for n in range(35)
        )
        capture = discovery.ConfigurationCapture(
            catalog.Catalog("test", definitions), SimpleNamespace(address=0x7112)
        )

        telemetry = SimpleNamespace(last_update_success=True, data={})

        async def read_batch(capture, names):
            if not telemetry.last_update_success:
                raise vbus.VBusTransientError(
                    "Controller telemetry is unavailable", "telemetry"
                )
            return operation(
                telemetry.data, capture.controller, names, capture.changeset
            )

        return SimpleNamespace(
            capture=capture,
            stop_capture=False,
            resume_on_load=False,
            capture_wakeup=asyncio.Event(),
            notify_capture=lambda: None,
            coordinator=telemetry,
            relay_coordinator=SimpleNamespace(
                async_read_configuration_batch=read_batch
            ),
        )

    def observations_for(self, names):
        return {
            name: vbus.ParameterObservation(index, 0, "read")
            for name, index in names.items()
        }

    async def test_retries_same_batch_without_losing_checkpoint(self):
        attempts = []

        def operation(data, controller, names, changeset):
            attempts.append(tuple(names))
            if len(attempts) in (2, 4):
                raise vbus.VBusTransientError(
                    "VBus parameter response failed",
                    "response",
                    command=0x0300,
                    index=17,
                )
            return self.observations_for(names)

        runtime = self.runtime(operation)
        with patch.object(discovery, "RETRY_DELAYS", (0,)):
            await discovery.capture_batches(runtime)
        self.assertEqual(runtime.capture.status, "complete")
        self.assertEqual(runtime.capture.offset, 35)
        self.assertEqual(runtime.capture.retry_count, 2)
        self.assertEqual(attempts[1], attempts[2])
        self.assertEqual(attempts[3], attempts[4])
        self.assertEqual(runtime.capture.consecutive_failures, 0)
        self.assertIsNone(runtime.capture.error)

    async def test_exhausted_retries_keep_offset_and_failure(self):
        def operation(*args):
            raise vbus.VBusTransientError("VBus gateway connection failed", "connect")

        runtime = self.runtime(operation)
        with patch.object(discovery, "RETRY_DELAYS", (0, 0)):
            await discovery.capture_batches(runtime)
        capture = runtime.capture
        self.assertEqual(capture.status, "incomplete")
        self.assertEqual(capture.offset, 0)
        self.assertEqual(capture.retry_count, 2)
        self.assertEqual(capture.consecutive_failures, 3)
        self.assertEqual(capture.last_failure["phase"], "connect")

    async def test_pause_interrupts_backoff(self):
        def operation(*args):
            raise vbus.VBusTransientError("VBus gateway connection failed", "connect")

        runtime = self.runtime(operation)
        recovering = asyncio.Event()

        def notify():
            if runtime.capture.status == "recovering":
                recovering.set()

        runtime.notify_capture = notify
        with patch.object(discovery, "RETRY_DELAYS", (60,)):
            task = asyncio.create_task(discovery.capture_batches(runtime))
            await asyncio.wait_for(recovering.wait(), timeout=1)
            runtime.stop_capture = True
            runtime.capture_wakeup.set()
            await asyncio.wait_for(task, timeout=1)
        self.assertEqual(runtime.capture.status, "paused")
        self.assertEqual(runtime.capture.offset, 0)
        self.assertIsNone(runtime.capture.retry_in_seconds)

    async def test_hard_failures_are_not_retried(self):
        for message in (
            "VBus gateway rejected connection or credentials",
            "Controller identity changed during discovery",
            "Controller changeset changed during discovery",
            "Unrecognized controller parameter layout",
        ):
            with self.subTest(message=message):

                def operation(*args, message=message):
                    raise vbus.VBusError(message)

                runtime = self.runtime(operation)
                await discovery.capture_batches(runtime)
                self.assertEqual(runtime.capture.status, "incomplete")
                self.assertEqual(runtime.capture.retry_count, 0)
                self.assertEqual(runtime.capture.error, message)

    async def test_temporary_telemetry_loss_recovers(self):
        runtime = self.runtime(
            lambda data, controller, names, changeset: self.observations_for(names)
        )
        runtime.coordinator.last_update_success = False

        def notify():
            if runtime.capture.status == "recovering":
                runtime.coordinator.last_update_success = True

        runtime.notify_capture = notify
        with patch.object(discovery, "RETRY_DELAYS", (0,)):
            await discovery.capture_batches(runtime)
        self.assertEqual(runtime.capture.status, "complete")
        self.assertEqual(runtime.capture.retry_count, 1)
        self.assertEqual(runtime.capture.last_failure["phase"], "telemetry")

    async def test_active_cancellation_retains_committed_progress_and_resume(self):
        runtime = self.runtime(
            lambda data, controller, names, changeset: self.observations_for(names)
        )
        entered = asyncio.Event()
        original = runtime.relay_coordinator.async_read_configuration_batch

        async def read(capture, names):
            if capture.offset:
                entered.set()
                await asyncio.Event().wait()
            return await original(capture, names)

        runtime.relay_coordinator.async_read_configuration_batch = read
        task = asyncio.create_task(discovery.capture_batches(runtime))
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(runtime.capture.status, "paused")
        self.assertEqual(runtime.capture.offset, 16)
        self.assertTrue(runtime.resume_on_load)
        self.assertEqual(runtime.capture.error, "capture_cancelled")
