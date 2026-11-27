"""Regression tests for profile, device, mode and parameter write gates."""

import asyncio
import importlib
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

PACKAGE = "_deltasol_parameter_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [
    str(Path(__file__).resolve().parents[1] / "custom_components/deltasol")
]
sys.modules[PACKAGE] = package
parameters = importlib.import_module(f"{PACKAGE}.parameters")
parameters_configuration = importlib.import_module(f"{PACKAGE}.configuration")

NAME = "Relais_Regler_R4_Handbetrieb"
SPEED = "Relais_Regler_R4_ODrehzahl"
UID = "gateway_7112_R4_mode"
M1_NAME = "Relais_Modul1_R1_Handbetrieb"
M1_SPEED = "Relais_Modul1_R1_ODrehzahl"
M1_UID = "gateway_7112_M1_R1_mode"
M2_NAME = "Relais_Modul2_R5_Handbetrieb"
M2_SPEED = "Relais_Modul2_R5_ODrehzahl"
M2_UID = "gateway_7112_M2_R5_mode"


def telemetry(channel="00", address="7112", source="DeltaSol BX Plus [Regler]"):
    endpoint = Mock(bus_src=source)
    endpoint.product_details = {"serial": "gateway"}
    return {f"{channel}_0010_{address}_0100__000_2_0": endpoint}


def module(number):
    return telemetry(address=f"665{number}", source=f"EM #{number}")


class ParameterTests(unittest.TestCase):
    def setUp(self):
        self.api = parameters.RelayParameters("gateway", "secret")
        self.session = MagicMock()
        self.session.bus.return_value.__enter__.return_value = self.session
        self.session.read.return_value = 12345
        self.values = {"Schema": (86, 0), NAME: (2019, 2), SPEED: (2015, 0)}
        self.session.read_parameters.side_effect = self.read_parameters
        self.session.read_parameter_observations.side_effect = self.observe_parameters
        self.ctx = MagicMock()
        self.ctx.__enter__.return_value = self.session
        self.patcher = patch.object(parameters, "open_vbus", return_value=self.ctx)
        self.connect = self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.clock = patch.object(parameters, "monotonic", return_value=100.0)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.wait = patch.object(parameters, "sleep")
        self.sleep = self.wait.start()
        self.addCleanup(self.wait.stop)

    def read_parameters(self, address, names, changeset):
        self.assertEqual(address, 0x7112)
        self.assertEqual(changeset, self.session.read.return_value)
        return {name: self.values[name] for name in names if name in self.values}

    def observe_parameters(self, address, names, changeset, **kwargs):
        found = self.read_parameters(address, names, changeset)
        return {
            name: parameters.ParameterObservation(*found[name], "read")
            if name in found
            else parameters.ParameterObservation(None, None, "not_resolved")
            for name in names
        }

    def activate_modules(self):
        self.values.update(
            {
                "Modul1_Aktiviert": (191, 1),
                "Modul1_Vorhanden": (193, 1),
                M1_NAME: (2173, 2),
                M1_SPEED: (2169, 1),
                "Modul2_Aktiviert": (197, 1),
                "Modul2_Vorhanden": (199, 1),
                M2_NAME: (2299, 2),
                M2_SPEED: (2295, 0),
            }
        )

    def discover(self, data=None):
        return self.api.discover(telemetry() if data is None else data).capabilities

    def test_compound_differential_requires_live_parent_child_and_unit(self):
        self.session.read.return_value = 0x2734DABC
        variants = [
            s
            for s in parameters_configuration.BX_PLUS_DIFFERENTIAL_SETTINGS
            if s.name == "Anlage_Wf1_Feststoffkessel_dTEin"
        ]
        celsius, fahrenheit = variants
        self.values.update(
            {
                "TemperatureHysteresisSelector": (60, 0),
                "Anlage_Wf1_Type": (6559, 6),
                "Anlage_Wf1_Aktiviert_Modus": (6566, 1),
            }
        )
        for parent, index, child, child_index in celsius.compound_mirrors:
            raw = {"dTEin": 50, "dTAus": 30, "dTSoll": 100}[parent.rsplit("_", 1)[1]]
            self.values[parent] = (index, raw)
            self.values[child] = (child_index, raw)
            self.values[child[:-2] + "_R"] = (child_index + 1, raw * 2)
        hints = {"Anlage_Wf1_Type": parameters.ParameterObservation(6559, 6, "read")}

        def discover():
            return {
                uid: cap
                for uid, cap in self.api.discover(
                    telemetry(), hints
                ).configuration.items()
                if cap.setting.compound_mirrors
            }

        def capability():
            return next(
                c for c in discover().values() if c.setting.name == celsius.name
            )

        initial = capability()
        self.assertEqual(initial.value, 5)
        self.assertEqual(initial.setting.unit, "K")
        self.assertEqual(len(discover()), 3)

        def write(address, index, value, changeset, *, save):
            self.assertEqual(
                (address, index, changeset, save), (0x7112, 6663, 0x2734DABC, True)
            )
            self.values[celsius.name] = (index, value)
            self.values[celsius.name + "_K"] = (index + 1, value)
            return value

        self.session.write_verified.side_effect = write
        self.assertEqual(self.api.set_configuration(telemetry(), initial, 6).value, 6)
        self.session.write_verified.assert_called_once()
        self.assertEqual(
            self.api.set_configuration(telemetry(), capability(), 6).value, 6
        )
        self.session.write_verified.assert_called_once()
        for invalid in (3, 10, 6.5):
            with self.assertRaises(parameters.VBusError):
                self.api.set_configuration(telemetry(), capability(), invalid)
        # Mismatched parent/child and changed unit must not generate writes.
        self.values[celsius.name + "_K"] = (6664, 50)
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(telemetry(), initial, 7)
        self.assertFalse(discover())
        self.values["TemperatureHysteresisSelector"] = (60, 1)
        for parent, index, child, child_index in fahrenheit.compound_mirrors:
            self.values[parent] = (index, self.values[child][1])
        replacement = capability()
        self.assertEqual(replacement.unique_id, initial.unique_id)
        self.assertEqual(replacement.setting.unit, "°R")
        self.assertEqual(replacement.value, 10)
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(telemetry(), initial, 7)
        self.session.write_verified.assert_called_once()

    def test_mixer_family_requires_live_installed_type_and_activity(self):
        self.session.read.return_value = 0x2734DABC
        setting = parameters_configuration.BX_PLUS_MIXER_SETTINGS[0]
        self.values.update(
            {
                setting.name: (setting.index, 4),
                setting.installed_by.name: (setting.installed_by.index, 2),
                setting.dependencies[0].name: (setting.dependencies[0].index, 1),
            }
        )
        self.assertFalse(self.api.discover(telemetry()).configuration)
        hints = {
            setting.installed_by.name: parameters.ParameterObservation(
                setting.installed_by.index, 2, "read"
            )
        }
        observed = self.api.discover(telemetry(), hints).configuration
        cap = next(iter(observed.values()))
        self.assertEqual(cap.value, 4)
        self.assertEqual(cap.setting.kind, "number")
        self.assertEqual(cap.setting.unit, "s")
        self.values[setting.installed_by.name] = (setting.installed_by.index, 0)
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(telemetry(), cap, 5)
        self.session.write_verified.assert_not_called()
        self.assertFalse(self.api.discover(telemetry(), hints).configuration)
        self.values[setting.installed_by.name] = (setting.installed_by.index, 2)
        self.values[setting.dependencies[0].name] = (setting.dependencies[0].index, 0)
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(telemetry(), cap, 5)
        self.session.write_verified.assert_not_called()
        self.values[setting.dependencies[0].name] = (setting.dependencies[0].index, 1)

        def write(address, index, value, changeset, *, save):
            self.assertEqual(
                (address, index, value, changeset, save),
                (0x7112, setting.index, 5, 0x2734DABC, True),
            )
            self.values[setting.name] = (index, value)
            return value

        self.session.write_verified.side_effect = write
        self.assertEqual(self.api.set_configuration(telemetry(), cap, 5).value, 5)
        self.session.write_verified.assert_called_once()
        for invalid in (0, 21, 4.5):
            with self.assertRaises(parameters.VBusError):
                self.api.set_configuration(telemetry(), cap, invalid)
        self.session.write_verified.assert_called_once()

    def test_hqm_display_family_checks_activation_and_typed_factor(self):
        self.session.read.return_value = 0x2734DABC
        switch, unit, factor = parameters_configuration.BX_PLUS_HQM_SETTINGS[:3]
        installed = switch.installed_by
        active = switch.dependencies[0]
        self.values.update(
            {
                installed.name: (installed.index, 1),
                active.name: (active.index, 1),
                switch.name: (switch.index, 0),
                unit.name: (unit.index, 0),
                factor.name: (factor.index, 5000000),
            }
        )
        hints = {
            installed.name: parameters.ParameterObservation(installed.index, 1, "read")
        }

        def controls():
            return {
                cap.setting.name: cap
                for cap in self.api.discover(telemetry(), hints).configuration.values()
            }

        self.assertEqual(set(controls()), {switch.name})

        def write(address, index, value, changeset, *, save):
            self.assertTrue(save)
            name = next(name for name, pair in self.values.items() if pair[0] == index)
            self.values[name] = (index, value)
            return value

        self.session.write_verified.side_effect = write
        self.api.set_configuration(telemetry(), controls()[switch.name], True)
        caps = controls()
        self.assertEqual(set(caps), {switch.name, unit.name, factor.name})
        self.api.set_configuration(telemetry(), caps[unit.name], "Gas")
        self.api.set_configuration(telemetry(), caps[factor.name], 0.5000001)
        self.assertEqual(self.values[factor.name][1], 5000001)
        self.assertEqual(self.session.write_verified.call_count, 3)
        self.values[switch.name] = (switch.index, 0)
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(telemetry(), caps[unit.name], "Coal")
        self.assertEqual(self.session.write_verified.call_count, 3)
        self.assertEqual(set(controls()), {switch.name})

    def test_heating_curve_checks_live_mode_and_quantization(self):
        self.session.read.return_value = 0x2734DABC
        setting = parameters_configuration.BX_PLUS_HEATING_SETTINGS[0]
        installed = setting.installed_by
        active, mode = setting.dependencies
        self.values.update(
            {
                installed.name: (installed.index, 1),
                active.name: (active.index, 1),
                mode.name: (mode.index, 1),
                setting.name: (setting.index, 12),
            }
        )
        hints = {
            installed.name: parameters.ParameterObservation(installed.index, 1, "read")
        }
        cap = next(iter(self.api.discover(telemetry(), hints).configuration.values()))
        self.assertEqual(cap.value, 1.2)

        def write(address, index, value, changeset, *, save):
            self.assertEqual((index, value, save), (setting.index, 15, True))
            self.values[setting.name] = (index, value)
            return value

        self.session.write_verified.side_effect = write
        self.assertEqual(self.api.set_configuration(telemetry(), cap, 1.5).value, 1.5)
        self.values[mode.name] = (mode.index, 2)
        self.assertFalse(self.api.discover(telemetry(), hints).configuration)
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(telemetry(), cap, 1.6)
        self.session.write_verified.assert_called_once()

    def test_pump_speed_requires_registered_speed_controlled_pump_and_pair(self):
        self.session.read.return_value = 0x2734DABC
        self.activate_modules()
        minimum, maximum = parameters_configuration.BX_PLUS_PUMP_SETTINGS[10:12]
        role = minimum.installed_by
        self.values.update(
            {
                role.name: (role.index, 2),
                minimum.name: (minimum.index, 30),
                maximum.name: (maximum.index, 80),
            }
        )
        hints = {role.name: parameters.ParameterObservation(role.index, 2, "read")}
        data = {**telemetry(), **module(1)}

        def controls():
            return {
                cap.setting.name: cap
                for cap in self.api.discover(data, hints).configuration.values()
            }

        caps = controls()
        self.assertEqual(set(caps), {minimum.name, maximum.name})
        self.assertEqual(caps[minimum.name].device.address, 0x6651)

        def write(address, index, value, changeset, *, save):
            self.assertTrue(save)
            self.values[minimum.name] = (index, value)
            return value

        self.session.write_verified.side_effect = write
        self.assertEqual(
            self.api.set_configuration(data, caps[minimum.name], 40).value, 40
        )
        for value in (19, 81, 40.5):
            with self.assertRaises(parameters.VBusError):
                self.api.set_configuration(data, caps[minimum.name], value)
        for name, index, invalid, valid in (
            (role.name, role.index, 3, 2),
            (M1_SPEED, 2169, 0, 1),
            (M1_NAME, 2173, 1, 2),
            ("Modul1_Aktiviert", 191, 0, 1),
        ):
            self.values[name] = (index, invalid)
            self.assertFalse(controls())
            with self.assertRaises(parameters.VBusError):
                self.api.set_configuration(data, caps[minimum.name], 50)
            self.values[name] = (index, valid)
        self.session.write_verified.assert_called_once()

    def test_pump_assignment_requires_own_reference_and_unresolved_role(self):
        self.session.read.return_value = 0x2734DABC
        self.values.update(
            {
                "Relais_Regler_R1_Handbetrieb": (1977, 2),
                "Relais_Regler_R1_ODrehzahl": (1973, 1),
                "Relais_Regler_R1_MinDrehzahl": (1974, 30),
                "Relais_Regler_R1_MaxDrehzahl": (1975, 80),
                "Relais_Regler_R1_Nr": (1969, 1),
                "Anlage_Wf1_Type": (6559, 6),
                "Anlage_Wf1_Aktiviert_Modus": (6566, 1),
                "Anlage_Wf1_Relais1_Nr": (6592, 1),
            }
        )
        hints = {"Anlage_Wf1_Type": parameters.ParameterObservation(6559, 6, "read")}

        def pumps():
            return {
                cap.setting.name: cap
                for cap in self.api.discover(telemetry(), hints).configuration.values()
                if cap.setting.owner_relay is not None
            }

        name = "Relais_Regler_R1_MinDrehzahl"
        caps = pumps()
        self.assertEqual(len(caps), 2)
        cap = caps[name]
        self.assertIsNotNone(cap.setting.output_assignment)

        def write(address, index, value, changeset, *, save):
            self.assertEqual((index, value, save), (1974, 40, True))
            self.values[name] = (index, value)
            return value

        self.session.write_verified.side_effect = write
        self.assertEqual(self.api.set_configuration(telemetry(), cap, 40).value, 40)
        # A second installed native function may conflict with a previously
        # registered control; the direct action must not use stale ownership.
        self.values.update(
            {
                "Anlage_Wf2_Type": (6810, 6),
                "Anlage_Wf2_Aktiviert_Modus": (6817, 1),
                "Anlage_Wf2_Relais1_Nr": (6843, 1),
            }
        )
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(telemetry(), cap, 50)
        self.values["Anlage_Wf2_Type"] = (6810, 0)
        self.values["Anlage_Wf1_Relais1_Nr"] = (6592, 2)
        self.assertFalse(pumps())
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(telemetry(), cap, 50)
        self.values["Anlage_Wf1_Relais1_Nr"] = (6592, 1)
        self.values["Relais_Regler_R1_AktorFunktion"] = (1971, 3)
        self.assertFalse(pumps())
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(telemetry(), cap, 50)
        del self.values["Relais_Regler_R1_AktorFunktion"]
        del self.values["Relais_Regler_R1_Nr"]
        self.assertFalse(pumps())
        self.session.write_verified.assert_called_once()

    def test_loading_pair_rejects_bad_sibling_and_keeps_topology_unchanged(self):
        setting = next(
            candidate
            for candidate in parameters_configuration.BX_PLUS_LOADING_SETTINGS
            if candidate.name == "Heizung_Wf1_ThBwErwaermung_TEin"
            and candidate.unit == "°C"
        )
        values = {
            "Heizung_Wf1_Type": 2,
            "Heizung_Wf1_Aktiviert_Modus": 2,
            "Heizung_Wf1_ThBwErwaermung_Modus": 0,
            "TemperatureHysteresisSelector": 0,
            "Heizung_Wf1_ThBwErwaermung_TEin": 450,
            "Heizung_Wf1_ThBwErwaermung_TEin_C": 450,
            "Heizung_Wf1_ThBwErwaermung_TAus": 550,
            "Heizung_Wf1_ThBwErwaermung_TAus_C": 550,
        }
        self.assertTrue(setting.accepts(values, setting.encode(50)))
        self.assertFalse(setting.accepts(values, setting.encode(55)))
        values["Heizung_Wf1_ThBwErwaermung_Modus"] = 1
        self.assertTrue(setting.accepts(values, setting.encode(50)))
        values["Heizung_Wf1_ThBwErwaermung_TAus"] = 9999
        values["Heizung_Wf1_ThBwErwaermung_TAus_C"] = 9999
        self.assertFalse(setting.accepts(values, setting.encode(50)))

    def test_thermostat_preserves_orientation_and_switch_controlled_mode(self):
        setting = next(
            setting
            for setting in parameters_configuration.BX_PLUS_THERMOSTAT_SETTINGS
            if setting.name == "Anlage_Wf1_Funktionsblock_TTh1Ein"
            and setting.unit == "°C"
        )
        values = {
            "Anlage_Wf1_Type": 9,
            "Anlage_Wf1_Aktiviert_Modus": 2,
            "TemperatureHysteresisSelector": 0,
            "Anlage_Wf1_Funktionsblock_OThermostat1": 1,
            "Anlage_Wf1_Funktionsblock_TTh1Ein": 500,
            "Anlage_Wf1_Funktionsblock_TTh1Ein_C": 500,
            "Anlage_Wf1_Funktionsblock_TTh1Aus": 600,
            "Anlage_Wf1_Funktionsblock_TTh1Aus_C": 600,
        }
        self.assertTrue(setting.accepts(values))
        self.assertTrue(setting.accepts(values, setting.encode(55)))
        self.assertFalse(setting.accepts(values, setting.encode(60)))
        self.assertFalse(setting.accepts(values, setting.encode(65)))
        values["Anlage_Wf1_Funktionsblock_OThermostat1"] = 0
        self.assertFalse(setting.accepts(values))

    def test_circulation_runtime_requires_demand_mode_and_exact_seconds(self):
        self.session.read.return_value = 0x2734DABC
        setting = next(
            s
            for s in parameters_configuration.BX_PLUS_TIMING_SETTINGS
            if s.name == "Heizung_Wf1_Zirkulation_dtLaufzeit"
        )
        self.values.update(
            {
                "Heizung_Wf1_Type": (11266, 4),
                "Heizung_Wf1_Aktiviert_Modus": (11273, 2),
                "Heizung_Wf1_Zirkulation_Variante": (11405, 3),
                setting.name: (setting.index, 180),
            }
        )
        hints = {"Heizung_Wf1_Type": parameters.ParameterObservation(11266, 4, "read")}
        cap = next(iter(self.api.discover(telemetry(), hints).configuration.values()))
        self.assertEqual(cap.value, 180)
        self.assertEqual(cap.setting.unit, "s")
        self.assertEqual(cap.setting.encode(195), 195)
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(telemetry(), cap, 191)
        self.values["Heizung_Wf1_Zirkulation_Variante"] = (11405, 1)
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(telemetry(), cap, 195)
        self.session.write_verified.assert_not_called()

    def test_verified_configuration_save_and_reconnect(self):
        self.session.read.return_value = 0x2734DABC
        self.values["AutoSommerWinterzeit"] = (56, 1)
        self.values["TemperatureHysteresisSelector"] = (60, 0)
        observed = self.api.discover(telemetry()).configuration
        self.assertEqual(len(observed), 2)
        capability = next(
            cap
            for cap in observed.values()
            if cap.setting.name == "TemperatureHysteresisSelector"
        )
        self.assertEqual(capability.value, "°C")
        self.session.write_verified.assert_not_called()
        before = self.connect.call_count

        def write(address, index, value, changeset, *, save):
            self.assertEqual(
                (address, index, value, changeset, save),
                (0x7112, 60, 1, 0x2734DABC, True),
            )
            self.values["TemperatureHysteresisSelector"] = (60, 1)
            return value

        self.session.write_verified.side_effect = write
        result = self.api.set_configuration(telemetry(), capability, "°F")
        self.assertEqual(result.value, "°F")
        self.assertEqual(self.connect.call_count, before + 2)
        self.session.write_verified.assert_called_once()
        self.api.set_configuration(telemetry(), result, "°F")
        self.session.write_verified.assert_called_once()
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(telemetry(), result, "invalid")
        self.values["TemperatureHysteresisSelector"] = (61, 1)
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(telemetry(), result, "°C")
        self.session.write_verified.assert_called_once()

    def test_configuration_reconnect_mismatch_never_resaves(self):
        self.session.read.return_value = 0x2734DABC
        self.values["AutoSommerWinterzeit"] = (56, 1)
        cap = next(iter(self.api.discover(telemetry()).configuration.values()))
        self.session.write_verified.return_value = 0
        with self.assertRaisesRegex(parameters.VBusError, "reconnect readback"):
            self.api.set_configuration(telemetry(), cap, False)
        self.session.write_verified.assert_called_once()
        self.session.read.return_value = 12345
        self.assertEqual(self.api.discover(telemetry()).configuration, {})

    def test_configuration_numeric_and_dependency_contract(self):
        setting = parameters.Setting(
            "Setting",
            200,
            "Setting",
            "number",
            True,
            limits=("0", "10", "0.5", "0.1"),
            dependencies=(parameters_configuration.Dependency("Active", 201, (1,)),),
        )
        self.assertEqual(setting.encode(2.5), 25)
        self.assertEqual(setting.decode(25), 2.5)
        self.assertTrue(setting.accepts({"Setting": 25, "Active": 1}))
        self.assertFalse(setting.accepts({"Setting": 25, "Active": 0}))
        for invalid in (2.3, 11, True):
            with self.assertRaises(parameters.VBusError):
                parameters.encode_action(setting, invalid)

    def test_relative_constraint_before_write(self):
        from dataclasses import replace

        setting = parameters.Setting(
            "Threshold",
            200,
            "Threshold",
            "number",
            True,
            limits=("0", "10", "0.5", "0.1"),
            dependencies=(parameters_configuration.Dependency("Active", 201, (1,)),),
            relative_constraints=(
                parameters_configuration.RelativeConstraint("Maximum", 202, "lt"),
            ),
        )
        self.session.read.return_value = 0x2734DABC
        self.values.update(
            {"Threshold": (200, 25), "Active": (201, 1), "Maximum": (202, 60)}
        )
        profile = replace(parameters.PROFILES[0], settings=(setting,))
        with patch.object(parameters, "PROFILES", (profile,)):
            cap = next(iter(self.api.discover(telemetry()).configuration.values()))
            with self.assertRaisesRegex(parameters.VBusError, "conditional constraint"):
                self.api.set_configuration(telemetry(), cap, 6)
            self.session.write_verified.assert_not_called()
            self.values["Active"] = (201, 0)
            with self.assertRaises(parameters.VBusError):
                self.api.set_configuration(telemetry(), cap, 3)
            self.session.write_verified.assert_not_called()
            self.values["Active"] = (201, 1)

            def write(address, index, value, changeset, *, save):
                self.assertTrue(save)
                self.values["Threshold"] = (index, value)
                return value

            self.session.write_verified.side_effect = write
            result = self.api.set_configuration(telemetry(), cap, 3)
            self.assertEqual(result.value, 3)
            self.session.write_verified.assert_called_once()

    def test_positive_discovery_only(self):
        result = self.discover()
        self.assertEqual(list(result), [UID])
        self.assertEqual(result[UID].current_option, "Auto")
        self.assertEqual(result[UID].index, 2019)
        self.session.write_verified.assert_not_called()

    def test_unknown_and_ambiguous_controller(self):
        self.assertEqual(self.discover(telemetry(address="1234")), {})
        self.assertEqual(self.discover(telemetry(channel="01")), {})
        self.assertEqual(self.discover({**telemetry(), **telemetry(channel="01")}), {})
        self.connect.assert_not_called()

    def test_unknown_layout_is_rejected(self):
        self.values[NAME] = (1321, 2)
        with self.assertRaises(parameters.VBusError):
            self.discover()
        self.session.write_verified.assert_not_called()

    def test_unknown_mode_and_missing_speed_not_enabled(self):
        self.values[NAME] = (2019, 42)
        self.assertEqual(self.discover(), {})
        self.values[NAME] = (2019, 2)
        del self.values[SPEED]
        self.assertEqual(self.discover(), {})

    def test_fixed_relay_on_is_four_and_readback(self):
        cap = self.discover()[UID]
        self.assertEqual(cap.options, ("Auto", "Off", "On"))
        self.session.write_verified.return_value = 4
        updated = self.api.set_mode(telemetry(), cap, "On")
        self.session.write_verified.assert_called_once_with(0x7112, 2019, 4, 12345)
        self.assertEqual(updated.current_option, "On")
        self.assertEqual(updated.unique_id, UID)

    def test_speed_control_has_distinct_min_and_max(self):
        self.values[SPEED] = (2015, 1)
        self.values[NAME] = (2019, 3)
        cap = self.discover()[UID]
        self.assertEqual(cap.current_option, "Max")
        self.assertEqual(cap.options, ("Auto", "Off", "Min", "Max"))
        with self.assertRaises(parameters.VBusError):
            self.api.set_mode(telemetry(), cap, "On")
        self.session.write_verified.return_value = 1
        self.assertEqual(
            self.api.set_mode(telemetry(), cap, "Min").current_option, "Min"
        )
        self.session.write_verified.assert_called_once_with(0x7112, 2019, 1, 12345)

    def test_actual_ein_mode_is_on(self):
        self.values[NAME] = (2019, 4)
        self.assertEqual(self.discover()[UID].current_option, "On")

    def test_write_rejects_unknown_option_and_stale_identity(self):
        cap = self.discover()[UID]
        with self.assertRaises(parameters.VBusError):
            self.api.set_mode(telemetry(), cap, "invalid")
        with self.assertRaises(parameters.VBusError):
            self.api.set_mode(telemetry(channel="01"), cap, "Off")
        self.session.write_verified.assert_not_called()

    def test_write_rejects_changed_changeset(self):
        cap = self.discover()[UID]
        self.session.read.return_value = 12346
        with self.assertRaises(parameters.VBusError):
            self.api.set_mode(telemetry(), cap, "Off")
        self.session.write_verified.assert_not_called()

    def test_write_rejects_disappeared_parameter(self):
        cap = self.discover()[UID]
        del self.values[NAME]
        with self.assertRaises(parameters.VBusError):
            self.api.set_mode(telemetry(), cap, "Off")
        self.session.write_verified.assert_not_called()

    def test_write_rejects_unknown_layout(self):
        cap = self.discover()[UID]
        self.values["Schema"] = (1, 0)
        with self.assertRaises(parameters.VBusError):
            self.api.set_mode(telemetry(), cap, "Off")
        self.session.write_verified.assert_not_called()

    def test_auto_noop_uses_observed_value(self):
        cap = self.discover()[UID]
        self.assertEqual(self.api.set_mode(telemetry(), cap, "Auto").value, 2)
        self.session.write_verified.assert_not_called()

    def test_out_of_scope_mode_can_be_restored_to_auto(self):
        self.values[NAME] = (2019, 1)
        cap = self.discover()[UID]
        self.assertIsNone(cap.current_option)
        self.session.write_verified.return_value = 2
        self.assertEqual(
            self.api.set_mode(telemetry(), cap, "Auto").current_option, "Auto"
        )

    def test_write_rechecks_speed_setting(self):
        cap = self.discover()[UID]
        self.values[SPEED] = (2015, 1)
        with self.assertRaises(parameters.VBusError):
            self.api.set_mode(telemetry(), cap, "On")
        self.session.write_verified.assert_not_called()

    def test_no_module_or_ambiguous_module_never_probed(self):
        self.activate_modules()
        self.assertEqual(set(self.discover()), {UID})
        calls = self.session.read_parameters.call_args_list
        self.assertFalse(any("Modul1_Aktiviert" in call.args[1] for call in calls))
        data = {**telemetry(), **module(1), **telemetry("01", "6651", "EM #1")}
        self.assertEqual(set(self.discover(data)), {UID})

    def test_one_and_two_modules_use_existing_physical_devices(self):
        self.activate_modules()
        one = {**telemetry(), **module(1)}
        self.assertEqual(set(self.discover(one)), {UID, M1_UID})
        data = {**one, **module(2), **telemetry(address="7113", source="Module #1")}
        result = self.discover(data)
        self.assertEqual(set(result), {UID, M1_UID, M2_UID})
        self.assertEqual(result[M1_UID].device.source, "EM #1")
        self.assertEqual(result[M2_UID].device.address, 0x6652)
        self.assertEqual(result[M1_UID].options, ("Auto", "Off", "Min", "Max"))
        self.assertEqual(result[M2_UID].options, ("Auto", "Off", "On"))
        self.session.write_verified.return_value = 3
        self.assertEqual(self.api.set_mode(data, result[M1_UID], "Max").value, 3)
        self.session.write_verified.assert_called_once_with(0x7112, 2173, 3, 12345)

    def test_unregistered_or_missing_module_never_writable(self):
        self.activate_modules()
        data = {**telemetry(), **module(1), **module(2)}
        cap = self.discover(data)[M1_UID]
        self.values["Modul1_Aktiviert"] = (191, 0)
        self.values["Modul2_Vorhanden"] = (199, 0)
        result = self.api.discover(data)
        self.assertEqual(set(result.capabilities), {UID})
        self.assertEqual(result.group_status["M1"], "not_registered_or_present")
        self.assertEqual(result.group_status["M2"], "not_registered_or_present")
        with self.assertRaises(parameters.VBusError):
            self.api.set_mode(data, cap, "Off")
        self.session.write_verified.assert_not_called()
        self.values["Modul1_Aktiviert"] = (191, 1)
        with self.assertRaises(parameters.VBusError):
            self.api.set_mode(telemetry(), cap, "Off")
        self.assertEqual(self.discover(data)[M1_UID].unique_id, cap.unique_id)

    def test_unknown_device_is_inventory_only(self):
        data = {**telemetry(), **telemetry(address="7777", source="Other")}
        self.assertEqual(len(parameters.device_inventory(data)), 2)
        self.assertEqual(set(self.discover(data)), {UID})

    def test_session_gap_applies_to_reads_and_writes(self):
        cap = self.discover()[UID]
        self.sleep.assert_not_called()
        self.api.set_mode(telemetry(), cap, "Auto")
        self.sleep.assert_called_once_with(parameters.SESSION_GAP_SECONDS)

    def test_session_gap_applies_after_failed_session(self):
        self.ctx.__enter__.side_effect = parameters.VBusError("connection failed")
        with self.assertRaises(parameters.VBusError):
            self.discover()
        self.ctx.__enter__.side_effect = None
        self.discover()
        self.sleep.assert_called_once_with(parameters.SESSION_GAP_SECONDS)

    def test_adaptive_transport_cooldown_and_health(self):
        failure = parameters.VBusTransientError(
            "VBus bus acquisition failed", "acquire"
        )
        self.ctx.__enter__.side_effect = failure
        for _ in range(2):
            with self.assertRaises(parameters.VBusTransientError):
                self.discover()
        self.assertEqual(
            self.api.transport_health()["failure_counts"], {"bus_acquisition": 2}
        )
        self.assertEqual(self.api.transport_health()["cooldown_seconds"], 5)
        self.ctx.__enter__.side_effect = None
        self.discover()
        self.assertEqual(self.api.transport_health()["cooldown_seconds"], 1.5)
        self.assertEqual(self.api.transport_health()["successful_sessions"], 1)

    def test_profile_specific_unresolved_presence_uses_positive_live_evidence(self):
        self.activate_modules()
        self.session.read.return_value = 0x2734DABC
        del self.values["Modul1_Vorhanden"]
        data = {**telemetry(), **module(1)}
        cap = self.discover(data)[M1_UID]
        self.assertEqual(cap.registration_source, "activation_and_observed_module")
        self.session.write_verified.return_value = 3
        self.assertEqual(self.api.set_mode(data, cap, "Max").value, 3)
        self.session.write_verified.assert_called_once_with(0x7112, 2173, 3, 0x2734DABC)
        self.session.write_verified.reset_mock()
        self.values["Modul1_Vorhanden"] = (193, 0)
        self.assertNotIn(M1_UID, self.discover(data))
        with self.assertRaises(parameters.VBusError):
            self.api.set_mode(data, cap, "Off")
        del self.values["Modul1_Vorhanden"]
        self.values["Modul1_Aktiviert"] = (191, 0)
        self.assertNotIn(M1_UID, self.discover(data))
        self.values["Modul1_Aktiviert"] = (191, 1)
        self.assertNotIn(M1_UID, self.discover(telemetry()))
        with self.assertRaises(parameters.VBusError):
            self.api.set_mode(telemetry(), cap, "Off")
        self.session.write_verified.assert_not_called()

    def test_module_flag_evidence_preserves_controller_controls(self):
        self.activate_modules()
        del self.values["Modul1_Vorhanden"]
        data = {**telemetry(), **module(1)}
        result = self.api.discover(data)
        self.assertEqual(set(result.capabilities), {UID})
        self.assertEqual(result.group_status["M1"], "registration_unknown")
        flags = result.module_flags["M1"]
        self.assertEqual(flags["Modul1_Aktiviert"].value, 1)
        self.assertEqual(flags["Modul1_Vorhanden"].status, "not_resolved")
        self.values["Modul1_Vorhanden"] = (193, 3)
        result = self.api.discover(data)
        self.assertEqual(
            result.module_flags["M1"]["Modul1_Vorhanden"].status, "invalid_value"
        )
        self.values["Modul1_Vorhanden"] = (192, 1)
        result = self.api.discover(data)
        self.assertEqual(
            result.module_flags["M1"]["Modul1_Vorhanden"].status, "layout_mismatch"
        )
        self.assertEqual(set(result.capabilities), {UID})
        self.session.write_verified.assert_not_called()


class RelayRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def coordinator(self, operation):
        async def execute(function, *args):
            return function(*args)

        return SimpleNamespace(
            last_update_success=True,
            data=telemetry(),
            hass=SimpleNamespace(async_add_executor_job=execute),
            api=SimpleNamespace(fetch_relay_modes=operation),
        )

    async def test_cancelled_executor_drains_before_releasing_ownership(self):
        started = asyncio.Event()
        finish = asyncio.Event()

        async def execute(*args):
            started.set()
            await finish.wait()
            return "read"

        hass = SimpleNamespace(async_add_executor_job=execute)
        task = asyncio.create_task(parameters.run_parameter_job(hass, Mock()))
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        finish.set()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_initial_refresh_attempts_only_once(self):
        operation = Mock(
            side_effect=parameters.VBusTransientError(
                "VBus bus acquisition failed", "acquire"
            )
        )
        with self.assertRaises(parameters.VBusTransientError):
            await parameters.refresh_relay_modes(
                self.coordinator(operation), initial=True
            )
        operation.assert_called_once()

    async def test_relay_read_recovers_before_update_failure(self):
        failure = parameters.VBusTransientError(
            "VBus parameter response failed", "response"
        )
        operation = Mock(side_effect=[failure, failure, "verified"])
        source = self.coordinator(operation)
        with patch.object(parameters, "RELAY_RETRY_DELAYS", (0, 0)):
            result = await parameters.refresh_relay_modes(source)
        self.assertEqual(result, "verified")
        self.assertEqual(operation.call_count, 3)

    async def test_relay_outage_remains_a_failure(self):
        failure = parameters.VBusTransientError(
            "VBus gateway connection failed", "connect"
        )
        operation = Mock(side_effect=failure)
        with (
            patch.object(parameters, "RELAY_RETRY_DELAYS", (0, 0)),
            self.assertRaises(parameters.VBusTransientError),
        ):
            await parameters.refresh_relay_modes(self.coordinator(operation))
        self.assertEqual(operation.call_count, 3)

    async def test_relay_authoritative_negative_is_not_retried(self):
        operation = Mock(return_value={})
        result = await parameters.refresh_relay_modes(self.coordinator(operation))
        self.assertEqual(result, {})
        operation.assert_called_once()

    async def test_relay_authentication_failure_is_not_retried(self):
        operation = Mock(
            side_effect=parameters.VBusError(
                "VBus gateway rejected connection or credentials"
            )
        )
        with self.assertRaises(parameters.VBusError):
            await parameters.refresh_relay_modes(self.coordinator(operation))
        operation.assert_called_once()
