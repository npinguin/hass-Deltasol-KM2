"""Capability discovery and write gate regressions on production code."""

import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

PACKAGE = "_deltasol_parameter_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).resolve().parents[1] / "custom_components/deltasol")]
sys.modules[PACKAGE] = package
parameters = importlib.import_module(f"{PACKAGE}.parameters")

NAME = "Relais_Regler_R4_Handbetrieb"
UID = "gateway_7112_R4_mode"


def telemetry(channel="00", address="7112"):
    endpoint = Mock(bus_src="DeltaSol BX Plus [Regler]")
    endpoint.product_details = {"serial": "gateway"}
    return {f"{channel}_0010_{address}_0100__000_2_0": endpoint}


class ParameterTests(unittest.TestCase):
    def setUp(self):
        self.api = parameters.RelayParameters("gateway", "secret")
        self.session = MagicMock()
        self.session.bus.return_value.__enter__.return_value = self.session
        self.session.discover.return_value = (
            12345,
            {"Schema": (86, 0), NAME: (2019, 2)},
        )
        self.ctx = MagicMock()
        self.ctx.__enter__.return_value = self.session
        self.patcher = patch.object(parameters, "open_vbus", return_value=self.ctx)
        self.connect = self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_positive_discovery_only(self):
        result = self.api.discover(telemetry())
        self.assertEqual(list(result), [UID])
        self.assertEqual(result[UID].current_option, "Auto")
        self.assertEqual(result[UID].index, 2019)
        self.session.write_verified.assert_not_called()

    def test_unknown_and_ambiguous_controller(self):
        self.assertEqual(self.api.discover(telemetry(address="1234")), {})
        self.assertEqual(self.api.discover(telemetry(channel="01")), {})
        self.assertEqual(
            self.api.discover({**telemetry(), **telemetry(channel="01")}), {}
        )
        self.connect.assert_not_called()

    def test_unknown_layout_is_rejected(self):
        self.session.discover.return_value = (
            12345, {"Schema": (86, 0), NAME: (1321, 2)}
        )
        with self.assertRaises(parameters.VBusError):
            self.api.discover(telemetry())
        self.session.write_verified.assert_not_called()

    def test_unknown_mode_not_enabled(self):
        self.session.discover.return_value = (
            12345, {"Schema": (86, 0), NAME: (2019, 42)}
        )
        self.assertEqual(self.api.discover(telemetry()), {})

    def test_write_uses_revalidated_dynamic_index(self):
        cap = self.api.discover(telemetry())[UID]
        self.session.read.side_effect = [12345, 12345, 2]
        self.session.resolve.side_effect = [86, 2019]
        self.session.write_verified.return_value = 3
        updated = self.api.set_mode(telemetry(), cap, "On")
        self.session.write_verified.assert_called_once_with(
            0x7112, 2019, 3, 12345
        )
        self.assertEqual(updated.current_option, "On")
        self.assertEqual(updated.unique_id, UID)

    def test_write_rejects_unknown_option_and_stale_identity(self):
        cap = self.api.discover(telemetry())[UID]
        with self.assertRaises(parameters.VBusError):
            self.api.set_mode(telemetry(), cap, "invalid")
        with self.assertRaises(parameters.VBusError):
            self.api.set_mode(telemetry(channel="01"), cap, "Off")
        self.session.write_verified.assert_not_called()

    def test_write_rejects_changed_changeset(self):
        cap = self.api.discover(telemetry())[UID]
        self.session.read.return_value = 12346
        with self.assertRaises(parameters.VBusError):
            self.api.set_mode(telemetry(), cap, "Off")
        self.session.write_verified.assert_not_called()

    def test_write_rejects_disappeared_parameter(self):
        cap = self.api.discover(telemetry())[UID]
        self.session.read.return_value = 12345
        self.session.resolve.side_effect = [86, None]
        with self.assertRaises(parameters.VBusError):
            self.api.set_mode(telemetry(), cap, "Off")
        self.session.write_verified.assert_not_called()

    def test_write_rejects_unknown_layout(self):
        cap = self.api.discover(telemetry())[UID]
        self.session.read.return_value = 12345
        self.session.resolve.return_value = 1
        with self.assertRaises(parameters.VBusError):
            self.api.set_mode(telemetry(), cap, "Off")
        self.session.write_verified.assert_not_called()

    def test_auto_noop_uses_observed_value(self):
        cap = self.api.discover(telemetry())[UID]
        self.session.read.side_effect = [12345, 12345, 2]
        self.session.resolve.side_effect = [86, 2019]
        self.assertEqual(self.api.set_mode(telemetry(), cap, "Auto").value, 2)
        self.session.write_verified.assert_not_called()

    def test_out_of_scope_mode_can_be_restored_to_auto(self):
        self.session.discover.return_value = (
            12345, {"Schema": (86, 0), NAME: (2019, 1)}
        )
        cap = self.api.discover(telemetry())[UID]
        self.assertIsNone(cap.current_option)
        self.session.read.side_effect = [12345, 12345, 1]
        self.session.resolve.side_effect = [86, 2019]
        self.session.write_verified.return_value = 2
        self.assertEqual(self.api.set_mode(telemetry(), cap, "Auto").current_option, "Auto")
