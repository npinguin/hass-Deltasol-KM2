"""Tests for catalogue parsing, read coverage and snapshot comparison."""

import asyncio
import importlib
import os
import sys
import types
import unittest
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
        self.assertNotIn(
            "raw_value", capture.export(False)["parameters"]["TemperatureLimit"]
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
        capture.apply(
            batch,
            {
                d.name: vbus.ParameterObservation(d.index + 1, 123, "read")
                for d in batch
            },
        )
        self.assertEqual(capture.offset, discovery.BATCH_SIZE)
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
        self.assertEqual(capture.export(True)["parameters"]["Test34"]["raw_value"], 0)

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
        self.assertEqual(definitions["Bedienercode"].excluded, "sensitive")
        self.assertEqual(
            definitions["MesseModus_BedienercodeReset"].excluded, "sensitive"
        )
        capture = discovery.ConfigurationCapture(
            parsed, SimpleNamespace(address=0x7112)
        )
        self.assertEqual(capture.next_batch()[0].name, "Schema")
        self.assertNotIn("Bedienercode", capture.export(True)["parameters"])
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

        # Aggregate schema evidence only: no vendor data or site values bundled.
        with zipfile.ZipFile(os.environ["RPT_ARCHIVE"]) as archive:
            root = ET.fromstring(archive.read(catalog.BX_PLUS_MENU))
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

        async def execute(function, *args):
            return function(*args)

        return SimpleNamespace(
            capture=capture,
            stop_capture=False,
            capture_wakeup=asyncio.Event(),
            notify_capture=lambda: None,
            coordinator=SimpleNamespace(
                last_update_success=True,
                data={},
                hass=SimpleNamespace(async_add_executor_job=execute),
                api=SimpleNamespace(read_configuration_batch=operation),
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
                    "VBus parameter response failed", "response", command=0x0300, index=17
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
                def operation(*args):
                    raise vbus.VBusError(message)

                runtime = self.runtime(operation)
                await discovery.capture_batches(runtime)
                self.assertEqual(runtime.capture.status, "incomplete")
                self.assertEqual(runtime.capture.retry_count, 0)
                self.assertEqual(runtime.capture.error, message)

    async def test_temporary_telemetry_loss_recovers(self):
        runtime = self.runtime(lambda data, controller, names, changeset: self.observations_for(names))
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
