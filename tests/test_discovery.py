"""Tests for catalogue parsing, read coverage and snapshot comparison."""

import asyncio
import hashlib
import importlib
import io
import os
import sys
import types
import unittest
import xml.etree.ElementTree as ET
import zipfile
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
        ):
            self.assertEqual(
                discovery.encode_native_day(discovery.decode_native_day(payload)),
                payload,
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

    def test_weekly_program_candidate_requires_complete_verified_source(self):
        import struct

        source = catalog.source_for(0x7112, catalog.BX_PLUS_CHANGESET)
        definitions = tuple(
            catalog.Definition(name, index, "unspecified", kind, {"type_chain": [kind]})
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
        self.assertFalse(result["payload_verified"])
        self.assertEqual(result["candidate_windows"][0]["start_minute"], 480)
        self.assertEqual(result["candidate_status"], "wire_word_mapping_unverified")
        self.assertEqual(
            exported["references"][reference.name]["candidate_program"], "Wsu1"
        )
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
            catalog.Definition(name, index, "unspecified", None, {"type_chain": [kind]})
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
    def test_exact_vendor_archive(self):
        parsed = catalog.load_catalog(os.environ["RPT_ARCHIVE"])
        self.assertEqual(parsed.digest, catalog.BX_PLUS_SHA256)
        definitions = {d.name: d for d in parsed.definitions}
        self.assertGreater(len(definitions), 7000)
        self.assertEqual(definitions["Schema"].index, 86)
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
                self.assertIsNone(row.excluded)
        # Corroborate the generic Solar function instance selector separately
        # from its type-specific storage. No ordinal-to-instance assumption.
        for slot in range(1, 17):
            prefix = f"Solar_Wf{slot}"
            self.assertEqual(
                definitions[prefix + "_Type"].index, 4409 + (slot - 1) * 120
            )
            self.assertEqual(definitions[prefix + "_Nr"].index, 4410 + (slot - 1) * 120)
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
        self.assertEqual(len(bound), len(profile.settings))
        self.assertEqual(
            catalog.ordinal_options(definitions["Solar_Beladungstyp"]),
            {0: "Pendelladung", 1: "SukzessiveLadung"},
        )
        for setting in configuration.BX_PLUS_SOLAR_LOADING_LOGIC_SETTINGS:
            target = definitions[setting.name]
            self.assertTrue(target.metadata["menu_refs"])
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
        self.assertEqual(len(configuration.BX_PLUS_HEATING_COMFORT_SETTINGS), 168)
        for setting in configuration.BX_PLUS_HEATING_COMFORT_SETTINGS:
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
        self.assertEqual(len(configuration.BX_PLUS_TIMING_SETTINGS), 112)
        for setting in configuration.BX_PLUS_TIMING_SETTINGS:
            target = definitions[setting.name]
            self.assertEqual(target.index, setting.index)
            self.assertIsNone(target.excluded)
            self.assertEqual(target.storage, "unspecified")
            for name, index in setting.expected.items():
                self.assertEqual(definitions[name].index, index)
            self.assertTrue(target.metadata["menu_refs"])
            if "Time" in target.metadata["type_chain"]:
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
        self.assertEqual(len(configuration.BX_PLUS_ADDITIONAL_OPERATING_SETTINGS), 272)
        self.assertEqual(len(configuration.BX_PLUS_HEATING_OPERATING_SETTINGS), 42)
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
        self.assertEqual(len(configuration.BX_PLUS_SOLAR_STORE_SETTINGS), 80)
        self.assertEqual(len(configuration.BX_PLUS_SOLAR_COLLECTOR_SETTINGS), 6)
        for setting in configuration.BX_PLUS_ADDITIONAL_OPERATING_SETTINGS:
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
        self.assertEqual(len(configuration.BX_PLUS_TRANSFER_SETTINGS), 224)
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
            # Static research only: identify whether the shipped RPT assembly
            # contains relevant named symbols. No execution, decompilation
            # or vendor binary contents in the release or CI artifacts.
            binary = archive.read("BXPLUS_2.08/DeltaSol_BXPlus_208.dll")
            import re

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
