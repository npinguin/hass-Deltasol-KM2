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

    def test_weekly_schedule_uses_existing_owner_and_reconciles_partial_write(self):
        config = parameters_configuration
        self.session.read.return_value = 0x2734DABC
        definitions = {}
        for owner in config.WEEKLY_OWNERS:
            self.values[owner.name] = (owner.index, 0)
            for name, index in owner.expected.items():
                kind = "WochenschaltuhrNr" if name == owner.name else "Boolean"
                definitions[name] = config.Definition(
                    name, index, "unspecified", kind, {"type_chain": [kind]}
                )
        owner = next(
            o for o in config.WEEKLY_OWNERS if o.name == "Anlage_Wf1_SchaltuhrNr"
        )
        self.values[owner.name] = (owner.index, 1)
        for condition in owner.conditions:
            self.values[condition.name] = (condition.index, condition.allowed[0])
        day = "Wsu1_Schaltuhr_Woche_TagMo_Daten"
        flags = {"Wsu1_Belegt": 18414, "Wsu1_Schaltuhr_Woche_Initialisiert": 18418}
        for name, index in flags.items():
            definitions[name] = config.Definition(
                name, index, "unspecified", "Boolean", {"type_chain": ["Boolean"]}
            )
            self.values[name] = (index, 1)
        definitions[day] = config.Definition(
            day,
            18419,
            "unspecified",
            "Blob",
            {"type_chain": ["Blob"], "size_bytes": 12, "rpt_storage": "preferences"},
        )
        catalogue = SimpleNamespace(matches=lambda *_: True, by_name=definitions)
        words = config.parse_weekly_day("08:00-10:00, 18:00-20:00")
        state = list(words)

        def observe(address, names, changeset, **kwargs):
            self.values[day] = (18419, state[0])
            result = self.observe_parameters(address, names, changeset, **kwargs)
            if day in (kwargs.get("packed") or {}):
                result[day] = parameters.ParameterObservation(
                    18419, state[0], "read", tuple(state)
                )
            return result

        self.session.read_parameter_observations.side_effect = observe
        hints = {
            name: parameters.ParameterObservation(index, value, "read")
            for name, (index, value) in self.values.items()
        }

        def capability():
            return next(
                cap
                for cap in self.api.discover(
                    telemetry(), hints, catalogue
                ).configuration.values()
                if cap.setting.name == day
            )

        # No historical allocation hint is needed for a newly linked program.
        for name in flags:
            hints.pop(name)
        cap = capability()
        self.assertEqual(cap.value, "08:00-10:00, 18:00-20:00")
        self.assertEqual(cap.owners, (owner.name,))
        for invalid in (
            "24:00-24:00",
            "08:01-09:00",
            "09:00-08:00",
            "08:00-10:00, 09:00-11:00",
        ):
            with self.assertRaises(parameters.VBusError):
                self.api.set_configuration(telemetry(), cap, invalid, catalogue)
        self.session.write_packed_verified.assert_not_called()

        def write(address, index, before, wanted, changeset):
            self.assertEqual((address, index, changeset), (0x7112, 18419, 0x2734DABC))
            self.assertEqual(before, tuple(state))
            state[:] = wanted

        self.session.write_packed_verified.side_effect = write
        updated = self.api.set_configuration(
            telemetry(), cap, "08:30-10:00, 18:00-20:00", catalogue
        )
        self.assertEqual(updated.value, "08:30-10:00, 18:00-20:00")
        self.assertEqual(updated.unique_id, cap.unique_id)
        self.api.set_configuration(telemetry(), updated, updated.value, catalogue)
        self.session.write_packed_verified.assert_called_once()
        other = next(o for o in config.WEEKLY_OWNERS if o.name != owner.name)
        self.values[other.name] = (other.index, 1)
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(telemetry(), updated, "", catalogue)
        # The shared reference was observed, but none of its conditions
        # were resolved. Even with an unchanged displayed owner set, an
        # unknown shared function must not be treated as definitely inactive.
        hints[other.name] = parameters.ParameterObservation(other.index, 1, "read")
        shared = capability()
        self.assertIn(other.name, shared.owners)
        self.assertNotIn(other.name, shared.active_owners)
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(telemetry(), shared, "", catalogue)
        self.session.write_packed_verified.assert_called_once()
        # A positive reference with a definite disabled timer is different
        # from an unknown function: the unchanged day is a valid no-op.
        timer = other.conditions[-1]
        self.values[timer.name] = (timer.index, 0)
        verified = self.api.set_configuration(
            telemetry(), shared, shared.value, catalogue
        )
        self.assertEqual(verified.owners, shared.owners)
        self.assertEqual(verified.active_owners, shared.active_owners)
        self.values[other.name] = (other.index, 0)
        hints.pop(other.name)
        self.session.write_packed_verified.assert_called_once()

        def partial(address, index, before, wanted, changeset):
            state[0] = wanted[0]
            raise parameters.VBusError("VBus write outcome uncertain")

        self.session.write_packed_verified.side_effect = partial
        with self.assertRaisesRegex(parameters.VBusError, "outcome uncertain: partial"):
            self.api.set_configuration(
                telemetry(), updated, "06:00-07:00, 09:00-10:00, 11:00-12:00", catalogue
            )
        self.assertEqual(self.session.write_packed_verified.call_count, 2)
        self.session.write_verified.assert_not_called()

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

        data = {
            **telemetry(),
            **telemetry(address="7131", source="DeltaSol BX Plus [HQM #1]"),
        }

        def controls(inventory=data):
            return {
                cap.setting.name: cap
                for cap in self.api.discover(inventory, hints).configuration.values()
            }

        self.assertEqual(set(controls()), {switch.name})
        self.assertEqual(controls()[switch.name].device.address, 0x7131)
        self.assertEqual(controls()[switch.name].controller.address, 0x7112)
        self.assertFalse(controls(telemetry()))
        self.assertFalse(controls({**telemetry(), **telemetry(address="7132")}))
        ambiguous = dict(data)
        ambiguous["00_0010_7131_0100__002_2_0"] = Mock(
            bus_src="Other meter", product_details={"serial": "gateway"}
        )
        self.assertFalse(controls(ambiguous))

        def write(address, index, value, changeset, *, save):
            self.assertTrue(save)
            self.assertEqual(address, 0x7112)
            name = next(name for name, pair in self.values.items() if pair[0] == index)
            self.values[name] = (index, value)
            return value

        self.session.write_verified.side_effect = write
        self.api.set_configuration(data, controls()[switch.name], True)
        caps = controls()
        self.assertEqual(set(caps), {switch.name, unit.name, factor.name})
        self.api.set_configuration(data, caps[unit.name], "Gas")
        self.api.set_configuration(data, caps[factor.name], 0.5000001)
        self.assertEqual(self.values[factor.name][1], 5000001)
        self.assertEqual(self.session.write_verified.call_count, 3)
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(telemetry(), caps[unit.name], "Coal")
        self.assertEqual(self.session.write_verified.call_count, 3)
        self.values[switch.name] = (switch.index, 0)
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(data, caps[unit.name], "Coal")
        self.assertEqual(self.session.write_verified.call_count, 3)
        self.assertEqual(set(controls()), {switch.name})

    def test_global_holiday_duration_requires_commissioning(self):
        self.session.read.return_value = 0x2734DABC
        self.values.update(
            {
                "Urlaubsfunktion_Tage": (19063, 0),
                "InbetriebnahmeAbgeschlossen": (68, 0),
            }
        )
        self.assertFalse(self.api.discover(telemetry()).configuration)
        self.values["InbetriebnahmeAbgeschlossen"] = (68, 1)
        cap = next(
            c
            for c in self.api.discover(telemetry()).configuration.values()
            if c.setting.name == "Urlaubsfunktion_Tage"
        )
        self.assertEqual(cap.value, 0)
        for invalid in (-1, 201, 0.5):
            with self.assertRaises(parameters.VBusError):
                self.api.set_configuration(telemetry(), cap, invalid)
        self.session.write_verified.assert_not_called()

        def write(address, index, value, changeset, *, save):
            self.assertEqual(
                (address, index, changeset, save), (0x7112, 19063, 0x2734DABC, True)
            )
            self.values["Urlaubsfunktion_Tage"] = (index, value)

        self.session.write_verified.side_effect = write
        changed = self.api.set_configuration(telemetry(), cap, 7)
        self.assertEqual(changed.value, 7)
        self.assertEqual(self.api.set_configuration(telemetry(), changed, 0).value, 0)
        self.values["InbetriebnahmeAbgeschlossen"] = (68, 0)
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(telemetry(), changed, 1)
        self.assertEqual(self.session.write_verified.call_count, 2)

    def test_native_heating_mode_respects_coupling_and_holiday(self):
        self.session.read.return_value = 0x2734DABC
        master = "Heizung_Heizkreis1"
        follower = "Heizung_Heizkreis2"
        self.values.update(
            {
                master + "_Type": (13684, 1),
                master + "_Aktiviert_Modus": (13689, 1),
                master + "_Betriebsart": (14014, 0),
                master + "_OUrlaub": (14176, 0),
                follower + "_Type": (14179, 1),
                follower + "_Aktiviert_Modus": (14184, 1),
                follower + "_Betriebsart": (14509, 0),
                follower + "_Kopplung": (14534, 1),
                follower + "_OUrlaub": (14671, 0),
                "Urlaubsfunktion_Tage": (19063, 0),
            }
        )
        hints = {
            name: parameters.ParameterObservation(index, raw, "read")
            for name, (index, raw) in self.values.items()
            if name.endswith("_Type")
        }

        def controls():
            return {
                cap.setting.name: cap
                for cap in self.api.discover(telemetry(), hints).configuration.values()
            }

        cap = controls()[master + "_Betriebsart"]
        self.assertEqual(cap.value, "Auto")
        self.assertNotIn(follower + "_Betriebsart", controls())
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(telemetry(), cap, "Holiday")
        self.session.write_verified.assert_not_called()

        def write(address, index, value, changeset, *, save):
            self.assertEqual((address, changeset, save), (0x7112, 0x2734DABC, True))
            name = {14014: master, 14509: follower}[index] + "_Betriebsart"
            self.values[name] = (index, value)

        self.session.write_verified.side_effect = write
        day = self.api.set_configuration(telemetry(), cap, "Day")
        self.assertEqual(day.value, "Day")
        self.assertEqual(day.unique_id, cap.unique_id)
        self.api.set_configuration(telemetry(), day, "Day")
        self.session.write_verified.assert_called_once()
        self.values["Urlaubsfunktion_Tage"] = (19063, 7)
        holiday = self.api.set_configuration(telemetry(), day, "Holiday")
        self.assertEqual(holiday.value, "Holiday")
        self.values[master + "_OUrlaub"] = (14176, 1)
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(telemetry(), holiday, "Auto")
        self.assertEqual(self.session.write_verified.call_count, 2)
        self.values["Urlaubsfunktion_Tage"] = (19063, 0)
        self.assertEqual(
            self.api.set_configuration(telemetry(), holiday, "Auto").value, "Auto"
        )
        self.values[follower + "_Kopplung"] = (14534, 0)
        independent = controls()[follower + "_Betriebsart"]
        self.assertEqual(
            self.api.set_configuration(telemetry(), independent, "Night").value,
            "Night",
        )
        self.values[follower + "_Kopplung"] = (14534, 1)
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(telemetry(), independent, "Off")
        self.assertEqual(self.session.write_verified.call_count, 4)

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
        role = minimum.optional_role
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

    def test_pump_native_speed_control_without_volatile_role(self):
        self.session.read.return_value = 0x2734DABC
        self.values.update(
            {
                "Relais_Regler_R1_Handbetrieb": (1977, 2),
                "Relais_Regler_R1_ODrehzahl": (1973, 1),
                "Relais_Regler_R1_MinDrehzahl": (1974, 30),
                "Relais_Regler_R1_MaxDrehzahl": (1975, 80),
            }
        )

        def pumps():
            return {
                cap.setting.name: cap
                for cap in self.api.discover(telemetry()).configuration.values()
                if cap.setting.owner_relay is not None
            }

        name = "Relais_Regler_R1_MinDrehzahl"
        caps = pumps()
        self.assertEqual(len(caps), 2)
        cap = caps[name]

        def write(address, index, value, changeset, *, save):
            self.assertEqual((address, index, value, save), (0x7112, 1974, 40, True))
            self.values[name] = (index, value)
            return value

        self.session.write_verified.side_effect = write
        self.assertEqual(self.api.set_configuration(telemetry(), cap, 40).value, 40)
        # Explicit non-pump role and wrong resolved index remain disqualifying.
        role = "Relais_Regler_R1_AktorFunktion"
        for row in ((1971, 3), (1972, 2)):
            self.values[role] = row
            self.assertFalse(pumps())
            with self.assertRaises(parameters.VBusError):
                self.api.set_configuration(telemetry(), cap, 50)
        del self.values[role]
        self.values["Relais_Regler_R1_ODrehzahl"] = (1973, 0)
        self.assertFalse(pumps())
        self.session.write_verified.assert_called_once()

    def test_transfer_thresholds_use_independent_sensors_and_selected_alias(self):
        self.session.read.return_value = 0x2734DABC
        name = "Anlage_Wf1_Feststoffkessel_TMin"
        target = next(
            setting
            for setting in parameters_configuration.BX_PLUS_TRANSFER_SETTINGS
            if setting.name == name and setting.unit == "°C"
        )
        self.values.update(
            {
                "Anlage_Wf1_Type": (6559, 6),
                "Anlage_Wf1_Aktiviert_Modus": (6566, 1),
                "TemperatureHysteresisSelector": (60, 0),
                name: (6675, 600),
                name + "_C": (6676, 600),
                "Anlage_Wf1_Feststoffkessel_TMax": (6672, 600),
                "Anlage_Wf1_Feststoffkessel_TMax_C": (6673, 600),
            }
        )
        hints = {"Anlage_Wf1_Type": parameters.ParameterObservation(6559, 6, "read")}

        def controls():
            return {
                cap.setting.name: cap
                for cap in self.api.discover(telemetry(), hints).configuration.values()
            }

        cap = controls()[name]
        self.assertEqual(cap.setting, target)
        self.assertEqual(cap.value, 60)

        def write(address, index, value, changeset, *, save):
            self.assertEqual((address, index, value, save), (0x7112, 6675, 650, True))
            self.values[name] = (index, value)
            self.values[name + "_C"] = (index + 1, value)
            return value

        self.session.write_verified.side_effect = write
        # Different sensors: a source minimum above the sink maximum is valid.
        self.assertEqual(self.api.set_configuration(telemetry(), cap, 65).value, 65)
        for invalid in (96, 3, 65.5):
            with self.assertRaises(parameters.VBusError):
                self.api.set_configuration(telemetry(), cap, invalid)
        self.values[name + "_C"] = (6676, 600)
        self.assertNotIn(name, controls())
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(telemetry(), cap, 70)
        self.session.write_verified.assert_called_once()

    def test_optional_transfer_controls_follow_existing_function_options(self):
        block = next(
            s
            for s in parameters_configuration.BX_PLUS_DIFFERENTIAL_SETTINGS
            if s.name == "Anlage_Wf1_Funktionsblock_dTEin" and s.unit == "K"
        )
        values = {
            "Anlage_Wf1_Type": 9,
            "Anlage_Wf1_Aktiviert_Modus": 2,
            "TemperatureHysteresisSelector": 0,
            "Anlage_Wf1_Funktionsblock_ODifferenz": 1,
        }
        for parent, _, child, _ in block.compound_mirrors:
            raw = {"dTEin": 50, "dTAus": 30, "dTSoll": 100}[parent.rsplit("_", 1)[1]]
            values[parent] = values[child] = raw
        self.assertTrue(block.accepts(values, block.encode(6)))
        self.assertFalse(block.accepts(values, block.encode(3)))
        values["Anlage_Wf1_Funktionsblock_ODifferenz"] = 0
        self.assertFalse(block.accepts(values))

        mixer = next(
            s
            for s in parameters_configuration.BX_PLUS_SOLID_FUEL_MIXER_SETTINGS
            if s.name == "Anlage_Wf1_Feststoffkessel_Mischer_dtIntervall"
        )
        values.update(
            {
                "Anlage_Wf1_Type": 6,
                "Anlage_Wf1_Feststoffkessel_OMischer": 1,
                mixer.name: 4,
            }
        )
        self.assertTrue(mixer.accepts(values, mixer.encode(5)))
        values["Anlage_Wf1_Feststoffkessel_OMischer"] = 0
        self.assertFalse(mixer.accepts(values))
        target = next(
            s
            for s in parameters_configuration.BX_PLUS_TRANSFER_SETTINGS
            if s.name == "Anlage_Wf1_Feststoffkessel_Zieltemperatur_TZiel"
            and s.unit == "°C"
        )
        values.update(
            {
                "Anlage_Wf1_Feststoffkessel_OZieltemperatur": 1,
                target.name: 650,
                target.name + "_C": 650,
            }
        )
        self.assertTrue(target.accepts(values, target.encode(70)))
        values["Anlage_Wf1_Feststoffkessel_OZieltemperatur"] = 0
        self.assertFalse(target.accepts(values))

    def test_heating_comfort_requires_applicable_native_configuration(self):
        settings = parameters_configuration.BX_PLUS_HEATING_COMFORT_SETTINGS
        night = next(
            s
            for s in settings
            if s.name == "Heizung_Heizkreis1_dTNachtkorrektur" and s.unit == "K"
        )
        values = {
            "Heizung_Heizkreis1_Type": 1,
            "Heizung_Heizkreis1_Aktiviert_Modus": 1,
            "Heizung_Heizkreis1_System": 0,
            "Heizung_Heizkreis1_Heizsystem": 1,
            "Heizung_Heizkreis1_OSchaltuhr": 1,
            "Heizung_Heizkreis1_ModusSchaltuhr": 0,
            "TemperatureHysteresisSelector": 0,
            night.name: -50,
            night.name + "_K": -50,
        }
        self.assertTrue(night.accepts(values, night.encode(-6)))
        self.assertFalse(night.accepts(values, -201))
        values["Heizung_Heizkreis1_ModusSchaltuhr"] = 1
        self.assertFalse(night.accepts(values))
        room = next(
            s
            for s in settings
            if s.name == "Heizung_Heizkreis1_Raumthermostat1_TRaum" and s.unit == "°C"
        )
        values.update(
            {
                "Heizung_Heizkreis1_Raumthermostat1_Option": 1,
                "Heizung_Heizkreis1_Raumthermostat1_Aktiviert": 1,
                "Heizung_Heizkreis1_Raumthermostat1_Typ": 0,
                "Heizung_Heizkreis1_Raumthermostat1_Sensor1_Nr": 2,
                room.name: 200,
                room.name + "_C": 200,
            }
        )
        self.assertTrue(room.accepts(values, room.encode(21)))
        setback = next(
            s
            for s in settings
            if s.name == "Heizung_Heizkreis1_Raumthermostat1_dTAbsenkung"
            and s.unit == "K"
        )
        values.update({setback.name: 50, setback.name + "_K": 50})
        self.assertFalse(setback.accepts(values))
        values["Heizung_Heizkreis1_Raumthermostat1_OSchaltuhr"] = 1
        self.assertTrue(setback.accepts(values, setback.encode(6)))
        values["Heizung_Heizkreis1_Raumthermostat1_Sensor1_Nr"] = 0
        self.assertFalse(setback.accepts(values))
        self.assertFalse(room.accepts(values))
        values["Heizung_Heizkreis1_Raumthermostat1_Sensor1_Nr"] = 2
        cutoff = next(
            s
            for s in settings
            if s.name == "Heizung_Heizkreis1_TSommerNacht" and s.unit == "°C"
        )
        clock = next(
            s for s in settings if s.name == "Heizung_Heizkreis1_tSommerTagEin"
        )
        values.update(
            {
                "Heizung_Heizkreis1_OHeizgrenze": 1,
                "Heizung_Heizkreis1_Sensor2_Nr": 3,
                "Heizung_Heizkreis1_Heizgrenze": 1,
                cutoff.name: 140,
                cutoff.name + "_C": 140,
                clock.name: 540,
            }
        )
        self.assertTrue(cutoff.accepts(values, cutoff.encode(15)))
        self.assertTrue(clock.accepts(values, clock.encode(555)))
        self.assertFalse(clock.accepts(values, 556))
        values["Heizung_Heizkreis1_Heizgrenze"] = 0
        self.assertFalse(cutoff.accepts(values))
        self.assertFalse(clock.accepts(values))
        values["Heizung_Heizkreis1_Raumthermostat1_Typ"] = 1
        self.assertFalse(room.accepts(values))
        values["Heizung_Heizkreis1_Raumthermostat1_Typ"] = 0
        values["Heizung_Heizkreis1_System"] = 1
        self.assertFalse(room.accepts(values))

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
        self.assertEqual(setting.native_slot, ("Heizung_Wf1", "Heizung_Wf", 1))
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

    def test_native_function_changes_without_rescanning_capture(self):
        from dataclasses import replace

        catalogue = importlib.import_module(f"{PACKAGE}.catalog")
        settings = tuple(
            parameters.Setting(
                f"Value{i}",
                200 + i,
                f"Value {i}",
                "number",
                True,
                limits=("0", "10", "1", "1"),
                installed_by=parameters_configuration.Dependency(
                    f"Type{i}", 300 + i, (1,)
                ),
            )
            for i in range(5)
        )
        definitions = tuple(
            catalogue.Definition(
                setting.name,
                setting.index,
                "unspecified",
                "Number",
                {"type_chain": ["Number"], "minimum": "0", "maximum": "10"},
            )
            for setting in settings
        ) + tuple(
            catalogue.Definition(
                setting.installed_by.name,
                setting.installed_by.index,
                "unspecified",
                "Selector",
                {},
            )
            for setting in settings
        )
        source = catalogue.source_for(0x7112, 0x2734DABC)
        vendor = catalogue.Catalog(source.digest, definitions, source)
        profile = replace(parameters.PROFILES[0], settings=settings)
        self.session.read.return_value = 0x2734DABC
        for i in range(5):
            self.values[f"Type{i}"] = (300 + i, 0)
            self.values[f"Value{i}"] = (200 + i, 5)
        with patch.object(parameters, "PROFILES", (profile,)):
            first = self.api.discover(telemetry(), {}, vendor)
            self.assertEqual(len(first.selector_hints), 4)
            self.assertFalse(first.configuration)
            self.values["Type4"] = (304, 1)
            second = self.api.discover(telemetry(), {}, vendor, first)
            self.assertEqual(len(second.selector_hints), 5)
            capability = next(iter(second.configuration.values()))
            self.assertEqual(capability.setting.name, "Value4")
            self.assertEqual(capability.value, 5)
            self.values["Type4"] = (304, 0)
            third = self.api.discover(telemetry(), {}, vendor, second)
            self.assertFalse(third.configuration)
            with self.assertRaises(parameters.VBusError):
                self.api.set_configuration(telemetry(), capability, 6, vendor)
        self.session.write_verified.assert_not_called()

    def test_catalogue_provenance_never_changes_identity_or_authorizes_write(self):
        catalogue = importlib.import_module(f"{PACKAGE}.catalog")
        source = catalogue.source_for(0x7112, 0x2734DABC)
        self.session.read.return_value = source.changeset
        self.values["AutoSommerWinterzeit"] = (56, 1)
        definition = catalogue.Definition(
            "AutoSommerWinterzeit",
            56,
            "unspecified",
            "Boolean",
            {"type_chain": ["Boolean"]},
        )
        vendor = catalogue.Catalog(source.digest, (definition,), source)
        unverified = catalogue.Catalog(source.digest, (definition,))
        data = telemetry()
        before = self.api.discover(data).configuration
        after = self.api.discover(data, {}, vendor).configuration
        self.assertEqual(set(before), set(after))
        uid = next(iter(before))
        self.assertEqual(before[uid].value, after[uid].value)
        self.assertEqual(self.api.discover(data, {}, unverified).configuration, {})
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(data, before[uid], False, unverified)
        self.session.write_verified.assert_not_called()

    def test_constant_flow_setpoint_respects_existing_protection(self):
        setting = next(
            s
            for s in parameters_configuration.BX_PLUS_HEATING_OPERATING_SETTINGS
            if s.name == "Heizung_Heizkreis1_TVorlaufVorgabe" and s.unit == "°C"
        )
        values = {
            "Heizung_Heizkreis1_Type": 1,
            "Heizung_Heizkreis1_Aktiviert_Modus": 1,
            "Heizung_Heizkreis1_System": 0,
            "Heizung_Heizkreis1_Heizsystem": 2,
            "TemperatureHysteresisSelector": 0,
        }
        for parent, _, child, _ in setting.compound_mirrors:
            raw = {
                "TVorlaufMin": 200,
                "TVorlaufMax": 500,
                "TVorlaufVorgabe": 450,
            }[parent.rsplit("_", 1)[1]]
            values[parent] = values[child] = raw
        self.assertTrue(setting.accepts(values, setting.encode(48)))
        self.assertFalse(setting.accepts(values, setting.encode(51)))
        self.assertFalse(setting.accepts(values, setting.encode(19)))
        values["Heizung_Heizkreis1_TVorlaufMax_C"] = 600
        self.assertFalse(setting.accepts(values, setting.encode(48)))

    def test_supplementary_heating_cooling_and_circulation_operating_constraints(self):
        settings = parameters_configuration.BX_PLUS_HEATING_OPERATING_SETTINGS
        root = "Heizung_Heizkreis1"
        prefix = root + "_Nachheizung"
        on = next(s for s in settings if s.name == prefix + "_dTEin" and s.unit == "K")
        early = next(s for s in settings if s.name == prefix + "_tFruehstart")
        values = {
            root + "_Type": 1,
            root + "_Aktiviert_Modus": 1,
            root + "_System": 0,
            root + "_ONachheizung": 1,
            prefix + "_Modus": 0,
            prefix + "_Aktiviert_Modus": 1,
            root + "_Relais6_Nr": 2,
            root + "_Sensor4_Nr": 4,
            prefix + "_dTEin": -50,
            prefix + "_dTEin_K": -50,
            prefix + "_dTAus": 50,
            prefix + "_dTAus_K": 50,
            prefix + "_tFruehstart": 60,
            "TemperatureHysteresisSelector": 0,
        }
        self.assertTrue(on.accepts(values, on.encode(-4.5)))
        self.assertFalse(on.accepts(values, on.encode(5)))
        self.assertFalse(early.accepts(values))
        values[prefix + "_OFruehstart"] = 1
        self.assertTrue(early.accepts(values, early.encode(90)))
        values[prefix + "_Modus"] = 1
        zone = next(
            s for s in settings if s.name == on.name and s.unit == "K" and s != on
        )
        self.assertFalse(on.accepts(values))
        self.assertFalse(zone.accepts(values))
        values[root + "_Sensor5_Nr"] = 5
        self.assertTrue(zone.accepts(values, zone.encode(-4.5)))
        clock = next(s for s in settings if s.name == root + "_Kuehlung_Timer_tEin")
        values.update(
            {
                clock.name: 540,
                root + "_Kuehlung_OTimer": 1,
                root + "_Kuehlung_Relais1_Nr": 3,
            }
        )
        self.assertFalse(clock.accepts(values))
        values[root + "_System"] = 2
        self.assertTrue(clock.accepts(values, clock.encode(555)))
        values[root + "_Kuehlung_OTimer"] = 0
        self.assertFalse(clock.accepts(values))
        pump = "Anlage_WTUmwaelzpumpe"
        duration = next(
            s
            for s in parameters_configuration.BX_PLUS_ADDITIONAL_OPERATING_SETTINGS
            if s.name == pump + "_RuhemodusDauer"
        )
        values = {
            pump + "_Relais_Nr": 2,
            pump + "_Aktiviert_Modus": 1,
            pump + "_ORuhemodus": 1,
            duration.name: 120,
        }
        self.assertTrue(duration.accepts(values, duration.encode(12.5)))
        self.assertFalse(duration.accepts(values, 126))
        values[pump + "_ORuhemodus"] = 0
        self.assertFalse(duration.accepts(values))

    def test_native_analogue_heating_flow_adjustment_requires_installed_feedback(self):
        root = "Heizung_RelaisAnforderung1"
        settings = parameters_configuration.BX_PLUS_HEATING_OPERATING_SETTINGS
        names = (
            root + "_Ausgang_dTSollVorlauf",
            root + "_Ausgang_Vorlauf_dTHysterese",
            root + "_Ausgang_Vorlauf_dVKorrektur",
            root + "_Ausgang_Vorlauf_dtIntervall",
        )
        offset, hysteresis, voltage, interval = (
            next(s for s in settings if s.name == name and s.unit in ("K", "V", "s"))
            for name in names
        )
        values = {
            root + "_Type": 1,
            root + "_Aktiviert_Modus": 1,
            root + "_OAusgang1": 1,
            root + "_Ausgang1_Nr": 1,
            root + "_Ausgang_OVorlauf": 1,
            root + "_Ausgang_Vorlauf_Sensor1_Nr": 2,
            "TemperatureHysteresisSelector": 0,
            offset.name: 50,
            offset.name + "_K": 50,
            hysteresis.name: 10,
            hysteresis.name + "_K": 10,
            voltage.name: 1,
            interval.name: 30,
        }
        for setting, value in (
            (offset, 7),
            (hysteresis, 1.5),
            (voltage, 0.2),
            (interval, 35),
        ):
            self.assertTrue(setting.accepts(values, setting.encode(value)))
        self.assertFalse(hysteresis.accepts(values, 11))
        self.assertFalse(voltage.accepts(values, 11))
        self.assertFalse(interval.accepts(values, 31))
        values[root + "_Ausgang_Vorlauf_Sensor1_Nr"] = 0
        self.assertTrue(offset.accepts(values))
        for setting in (hysteresis, voltage, interval):
            self.assertFalse(setting.accepts(values))
        values[root + "_Ausgang1_Nr"] = 0
        self.assertFalse(offset.accepts(values))

    def test_native_menu_review_reasons_do_not_grant_access(self):
        reason = parameters_configuration.native_menu_review_reason
        for name, expected in (
            ("Relais_Modul1_R1_Handbetrieb", "existing_relay_owner"),
            ("Wmz1_WertJahr_Wh", "runtime_or_history_preserved"),
            ("Heizung_Heizkreis1_Relais2_Nr", "hardware_or_topology_assignment"),
            (
                "Heizung_Heizkreis1_Kuehlung_Taupunkt_dTErhoehung",
                "protection_or_safety_configuration",
            ),
            (
                "Heizung_RelaisAnforderung1_Ausgang_Spannung1",
                "commissioning_or_control_scheme",
            ),
            ("Solar_Sp5_Vorrang", "unsupported_native_topology_slot"),
            (
                "Solar_Wf1_DrainBack_Befuellzeit",
                "hydraulic_applicability_review_required",
            ),
            ("Heizung_Heizkreis1_OSchaltuhr", "program_option_review_required"),
            ("Solar_Spreizladung_dTDifferenz", "native_operating_contract_unverified"),
        ):
            self.assertEqual(reason(name), expected)

    def test_native_mixer_requires_assignments_and_full_hysteresis_margin(self):
        settings = parameters_configuration.BX_PLUS_HEATING_OPERATING_SETTINGS
        root = "Heizung_Heizkreis1"
        name = root + "_Mischer_dTEin"
        on = next(s for s in settings if s.name == name and s.unit == "K")
        off = next(
            s for s in settings if s.name == root + "_Mischer_dTAus" and s.unit == "K"
        )
        interval = next(s for s in settings if s.name == root + "_MischerIntervall")
        values = {
            root + "_Type": 1,
            root + "_Aktiviert_Modus": 1,
            root + "_System": 0,
            root + "_Sensor1_Nr": 1,
            root + "_Relais2_Nr": 2,
            root + "_Relais3_Nr": 3,
            interval.name: 4,
            on.name: 15,
            on.name + "_K": 15,
            off.name: 10,
            off.name + "_K": 10,
            "TemperatureHysteresisSelector": 0,
        }
        self.assertTrue(on.accepts(values, on.encode(1.5)))
        self.assertFalse(on.accepts(values, on.encode(1.4)))
        self.assertFalse(off.accepts(values, off.encode(1.1)))
        self.assertTrue(interval.accepts(values, interval.encode(6)))
        values[root + "_Relais3_Nr"] = 0
        self.assertFalse(interval.accepts(values))
        self.assertFalse(on.accepts(values))

    def test_native_cooling_settings_and_mixer_target_follow_assignments(self):
        root = "Heizung_Heizkreis1"
        prefix = root + "_Kuehlung"
        settings = parameters_configuration.BX_PLUS_HEATING_OPERATING_SETTINGS

        def find(suffix):
            return next(
                s for s in settings if s.name == prefix + suffix and s.unit == "°C"
            )

        outside = find("_Aussen_TAussen")
        constant = find("_Kuehlsystem_TVorlauf")
        linear = find("_Kuehlsystem_TVorlauf1")
        values = {
            root + "_Type": 1,
            root + "_Aktiviert_Modus": 1,
            root + "_System": 2,
            root + "_Sensor1_Nr": 1,
            root + "_Sensor2_Nr": 2,
            prefix + "_Relais1_Nr": 3,
            prefix + "_Modus": 0,
            prefix + "_Kuehlsystem": 0,
            outside.name: 240,
            outside.name + "_C": 240,
            constant.name: 200,
            constant.name + "_C": 200,
            linear.name: 200,
            linear.name + "_C": 200,
            "TemperatureHysteresisSelector": 0,
        }
        self.assertTrue(outside.accepts(values, outside.encode(25)))
        self.assertTrue(constant.accepts(values, constant.encode(21)))
        self.assertFalse(linear.accepts(values))
        values[prefix + "_Kuehlsystem"] = 1
        self.assertFalse(constant.accepts(values))
        self.assertTrue(linear.accepts(values, linear.encode(19)))
        values[prefix + "_Modus"] = 1
        self.assertFalse(outside.accepts(values))
        values[root + "_Sensor2_Nr"] = 0
        self.assertFalse(linear.accepts(values))
        values[prefix + "_Relais1_Nr"] = 0
        self.assertFalse(linear.accepts(values))

        root = "Anlage_Wf1"
        target = next(
            s
            for s in parameters_configuration.BX_PLUS_ADDITIONAL_OPERATING_SETTINGS
            if s.name == root + "_Mischer_TZiel" and s.unit == "°C"
        )
        values = {
            root + "_Type": 2,
            root + "_Aktiviert_Modus": 1,
            root + "_Sensor1_Nr": 1,
            root + "_Relais1_Nr": 2,
            root + "_Relais2_Nr": 3,
            target.name: 600,
            target.name + "_C": 600,
            "TemperatureHysteresisSelector": 0,
        }
        self.assertTrue(target.accepts(values, target.encode(65)))
        values[root + "_Relais2_Nr"] = 0
        self.assertFalse(target.accepts(values))

    def test_native_sd_logging_requires_usable_card(self):
        interval, method = parameters_configuration.BX_PLUS_LOGGING_SETTINGS
        values = {
            "SDKarte_Status_KarteVorhanden": 1,
            "SDKarte_Status_Schreibfehler": 0,
            "SDKarte_Status_KarteUnbekannt": 0,
            "SDKarte_Status_KarteUnformatiert": 0,
            "SDKarte_Status_KarteVoll": 0,
            "SDKarte_Status_KarteSchreibgeschuetzt": 0,
            interval.name: 60,
            method.name: 0,
        }
        self.assertTrue(interval.accepts(values, interval.encode(1200)))
        self.assertFalse(interval.accepts(values, 1201))
        self.assertTrue(
            method.accepts(values, method.encode("Cyclic (overwrite oldest)"))
        )
        for status in ("KarteVoll", "KarteSchreibgeschuetzt", "Schreibfehler"):
            name = "SDKarte_Status_" + status
            values[name] = 1
            self.assertFalse(interval.accepts(values))
            self.assertFalse(method.accepts(values))
            values[name] = 0
        del values["SDKarte_Status_KarteVorhanden"]
        self.assertFalse(method.accepts(values))

    def test_native_linear_and_room_control_follow_installed_mode(self):
        root = "Heizung_Heizkreis1"
        settings = parameters_configuration.BX_PLUS_HEATING_OPERATING_SETTINGS

        def find(suffix, unit=None):
            return next(
                s
                for s in settings
                if s.name == root + "_Heizsystem_" + suffix and s.unit == unit
            )

        linear = find("Gerade_TAussen1", "°C")
        initial = find("Raum_TStart", "°C")
        factor = find("Raumeinfluss_Faktor")
        aggregation = find("Raumeinfluss_Abweichung")
        values = {
            root + "_Type": 1,
            root + "_Aktiviert_Modus": 1,
            root + "_System": 0,
            root + "_Sensor1_Nr": 1,
            root + "_Sensor2_Nr": 2,
            root + "_Heizsystem": 3,
            linear.name: 100,
            linear.name + "_C": 100,
            root + "_Heizsystem_Gerade_TAussen2": 0,
            initial.name: 400,
            initial.name + "_C": 400,
            factor.name: 4,
            aggregation.name: 2,
            "TemperatureHysteresisSelector": 0,
        }
        self.assertTrue(linear.accepts(values, linear.encode(0)))
        self.assertFalse(initial.accepts(values))
        values[root + "_Heizsystem"] = 4
        self.assertFalse(linear.accepts(values))
        self.assertFalse(initial.accepts(values))
        for n in (2, 5):
            room = f"{root}_Raumthermostat{n}"
            values.update(
                {
                    room + "_Option": 1,
                    room + "_Aktiviert": 1,
                    room + "_Typ": 0,
                    room + "_Sensor1_Nr": n,
                }
            )
        self.assertTrue(initial.accepts(values, initial.encode(50)))
        self.assertTrue(aggregation.accepts(values, aggregation.encode("Maximum")))
        self.assertEqual(aggregation.decode(1), "Minimum")
        self.assertFalse(factor.accepts(values))
        values[root + "_Heizsystem"] = 5
        self.assertTrue(factor.accepts(values, factor.encode(9)))
        self.assertFalse(factor.accepts(values, 10))
        self.assertTrue(aggregation.accepts(values))
        values[root + "_Raumthermostat2_Aktiviert"] = 0
        self.assertTrue(factor.accepts(values))
        values[root + "_Raumthermostat5_Sensor1_Nr"] = 0
        self.assertFalse(factor.accepts(values))
        self.assertFalse(aggregation.accepts(values))

    def test_native_demand_runtime_and_function_block_restart_delay(self):
        settings = parameters_configuration.BX_PLUS_HEATING_OPERATING_SETTINGS
        root = "Heizung_RelaisAnforderung1"
        name = root + "_Mindestlaufzeit_tMin"
        relay, analogue = (s for s in settings if s.name == name)
        values = {
            root + "_Type": 1,
            root + "_Aktiviert_Modus": 1,
            root + "_OMindestlaufzeit": 1,
            root + "_ORelais1": 1,
            root + "_Relais1_Nr": 2,
            root + "_OAusgang1": 1,
            root + "_Ausgang1_Nr": 1,
            name: 10,
        }
        self.assertTrue(relay.accepts(values, relay.encode(15)))
        self.assertFalse(relay.accepts(values, 16))
        self.assertFalse(analogue.accepts(values))
        values[root + "_ORelais1"] = 0
        self.assertFalse(relay.accepts(values))
        self.assertTrue(analogue.accepts(values, analogue.encode(20)))
        values[root + "_OMindestlaufzeit"] = 0
        self.assertFalse(analogue.accepts(values))

        root = "Anlage_Wf1"
        setting = next(
            s
            for s in parameters_configuration.BX_PLUS_TIMING_SETTINGS
            if s.name == root + "_Funktionsblock_dtMindestausschaltzeit"
        )
        values = {
            root + "_Type": 9,
            root + "_Aktiviert_Modus": 1,
            root + "_Funktionsblock_OMindestausschaltzeit": 1,
            root + "_Relais1_Nr": 1,
            setting.name: 60,
        }
        self.assertTrue(setting.accepts(values, setting.encode(1439)))
        self.assertFalse(setting.accepts(values, 1440))
        values[root + "_Relais1_Nr"] = 0
        self.assertFalse(setting.accepts(values))

    def test_heating_pump_start_and_overrun_follow_native_mode(self):
        settings = parameters_configuration.BX_PLUS_HEATING_OPERATING_SETTINGS
        root = "Heizung_RelaisPumpe2"
        timed = next(s for s in settings if s.name == root + "_Anlauf_dtAnlauf")
        thermal = next(
            s for s in settings if s.name == root + "_Anlauf_TAnlauf" and s.unit == "°C"
        )
        values = {
            root + "_Type": 2,
            root + "_Aktiviert_Modus": 1,
            root + "_Relais1_Nr": 4,
            root + "_Anlauf_Modus": 2,
            timed.name: 60,
            thermal.name: 500,
            thermal.name + "_C": 500,
            "TemperatureHysteresisSelector": 0,
        }
        self.assertTrue(timed.accepts(values, timed.encode(75)))
        self.assertFalse(thermal.accepts(values))
        values[root + "_Anlauf_Modus"] = 1
        self.assertFalse(timed.accepts(values))
        self.assertFalse(thermal.accepts(values))
        values[root + "_AnlaufNachlauf_Sensor1_Nr"] = 6
        self.assertTrue(thermal.accepts(values, thermal.encode(55)))
        values[root + "_Relais1_Nr"] = 0
        self.assertFalse(thermal.accepts(values))

    def test_energy_saving_requires_enabled_option_and_assigned_sensor(self):
        setting = next(
            s
            for s in parameters_configuration.BX_PLUS_HEATING_OPERATING_SETTINGS
            if s.name == "Heizung_Heizkreis1_Energiesparbetrieb_dtPause"
        )
        values = {
            "Heizung_Heizkreis1_Type": 1,
            "Heizung_Heizkreis1_Aktiviert_Modus": 1,
            "Heizung_Heizkreis1_System": 0,
            "Heizung_Heizkreis1_OEnergiesparbetrieb": 1,
            "Heizung_Heizkreis1_Energiesparbetrieb_Sensor1_Nr": 2,
            setting.name: 15,
        }
        self.assertTrue(setting.accepts(values, setting.encode(20)))
        values["Heizung_Heizkreis1_Energiesparbetrieb_Sensor1_Nr"] = 0
        self.assertFalse(setting.accepts(values))
        values["Heizung_Heizkreis1_Energiesparbetrieb_Sensor1_Nr"] = 2
        values["Heizung_Heizkreis1_OEnergiesparbetrieb"] = 0
        self.assertFalse(setting.accepts(values))

    def test_solar_defaults_are_not_installed_store_evidence(self):
        self.session.read.return_value = 0x2734DABC
        name = "Solar_Sp1_dTein"
        setting = next(
            s
            for s in parameters_configuration.BX_PLUS_SOLAR_STORE_SETTINGS
            if s.name == name and s.unit == "K"
        )
        self.values.update(
            {
                "TemperatureHysteresisSelector": (60, 0),
                "Solar_Sp1_Aktiviert_Modus": (3604, 1),
            }
        )
        for parent, index, child, child_index in setting.compound_mirrors:
            raw = {"dTein": 60, "dTaus": 40, "dTsoll": 100}[parent.rsplit("_", 1)[1]]
            self.values[parent] = (index, raw)
            self.values[child] = (child_index, raw)
        hints = {
            "Solar_Sp1_Sensor1_Nr": parameters.ParameterObservation(3576, 2, "read")
        }

        def controls():
            return {
                cap.setting.name: cap
                for cap in self.api.discover(telemetry(), hints).configuration.values()
            }

        self.assertNotIn(name, controls())
        self.values.update(
            {
                "Solar_Sp1_Sensor1_Nr": (3576, 2),
                "Solar_Kol1_Sensor1_Nr": (3536, 1),
                "Solar_Relais1_Nr": (3452, 1),
            }
        )
        cap = controls()[name]
        self.assertEqual(cap.value, 6)
        self.assertEqual(cap.setting.native_slot, ("Solar_Sp1", "Solar_Sp", 1))

        def write(address, index, value, changeset, *, save):
            self.assertEqual((address, index, value, save), (0x7112, 3579, 70, True))
            self.values[name] = (index, value)
            self.values[name + "_K"] = (index + 1, value)
            return value

        self.session.write_verified.side_effect = write
        self.assertEqual(self.api.set_configuration(telemetry(), cap, 7).value, 7)
        for invalid in (4, 10, 7.25):
            with self.assertRaises(parameters.VBusError):
                self.api.set_configuration(telemetry(), cap, invalid)
        del self.values["Solar_Relais1_Nr"]
        self.assertNotIn(name, controls())
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(telemetry(), cap, 8)
        self.session.write_verified.assert_called_once()

    def test_solar_priority_uses_native_store_count(self):
        settings = {
            setting.name: setting
            for setting in parameters_configuration.BX_PLUS_SOLAR_STORE_SETTINGS
            if setting.name.endswith("_Vorrang")
        }
        self.assertEqual(len(settings), 4)
        setting = settings["Solar_Sp1_Vorrang"]
        values = {
            "Solar_SystemId": 20201,
            "Solar_Sp1_Sensor1_Nr": 2,
            "Solar_Sp1_Aktiviert_Modus": 1,
            "Solar_Kol1_Sensor1_Nr": 1,
            "Solar_Relais1_Nr": 1,
            setting.name: 1,
        }
        self.assertTrue(setting.accepts(values, 2))
        self.assertFalse(setting.accepts(values, 3))
        values["Solar_SystemId"] = 10401
        self.assertTrue(setting.accepts(values, 4))
        self.assertFalse(setting.accepts(values, 5))
        for system in (10101, 10501, 0):
            values["Solar_SystemId"] = system
            self.assertFalse(setting.accepts(values, 1))
        values["Solar_SystemId"] = 20201
        values["Solar_Sp1_Sensor1_Nr"] = 0
        self.assertFalse(setting.accepts(values, 1))

    def test_heat_exchange_hysteresis_requires_selected_unit_and_assignments(self):
        setting = next(
            setting
            for setting in parameters_configuration.BX_PLUS_TRANSFER_SETTINGS
            if setting.name == "Anlage_Wf1_Waermeaustausch_HMinMax"
            and setting.unit == "K"
        )
        values = {
            setting.name: 20,
            setting.name + "_K": 20,
            "Anlage_Wf1_Type": 5,
            "Anlage_Wf1_Aktiviert_Modus": 1,
            "Anlage_Wf1_Sensor1_Nr": 1,
            "Anlage_Wf1_Sensor2_Nr": 2,
            "Anlage_Wf1_Relais1_Nr": 1,
            "TemperatureHysteresisSelector": 0,
        }
        self.assertTrue(setting.accepts(values, 25))
        self.assertFalse(setting.accepts(values, 26))
        values["Anlage_Wf1_Sensor2_Nr"] = 0
        self.assertFalse(setting.accepts(values, 25))
        values["Anlage_Wf1_Sensor2_Nr"] = 2
        values["TemperatureHysteresisSelector"] = 1
        self.assertFalse(setting.accepts(values, 25))

    def test_solar_loading_logic_requires_live_sequence_and_enabled_options(self):
        self.session.read.return_value = 0x2734DABC
        self.values.update(
            {
                "Solar_Kol1_Sensor1_Nr": (3536, 1),
                "Solar_Relais1_Nr": (3452, 1),
                "Solar_Sp1_Sensor1_Nr": (3576, 2),
                "Solar_Sp1_Aktiviert_Modus": (3604, 1),
                "Solar_Sp2_Sensor1_Nr": (3612, 3),
                "Solar_Sp2_Aktiviert_Modus": (3640, 1),
                "Solar_Beladungstyp": (3417, 0),
                "Solar_OPausendrehzahl": (3420, 1),
                "Solar_OPumpenverzoegerung": (3422, 1),
                "Solar_Pendelpause": (3418, 2),
                "Solar_Umwaelzzeit": (3419, 15),
                "Solar_Pausendrehzahl": (3421, 30),
                "Solar_Pumpenverzoegerung": (3423, 15),
            }
        )
        hints = {
            "Solar_Kol1_Sensor1_Nr": parameters.ParameterObservation(3536, 1, "read")
        }

        def controls():
            return {
                cap.setting.name: cap
                for cap in self.api.discover(telemetry(), hints).configuration.values()
            }

        names = {
            s.name
            for s in parameters_configuration.BX_PLUS_SOLAR_LOADING_LOGIC_SETTINGS
            if s.name != "Solar_Spreizladung_dTDifferenz"
        }
        self.assertTrue(names <= controls().keys())
        cap = controls()["Solar_Pendelpause"]

        def write(address, index, value, changeset, *, save):
            self.assertEqual((address, index, value, save), (0x7112, 3418, 3, True))
            self.values[cap.setting.name] = (index, value)
            return value

        self.session.write_verified.side_effect = write
        self.assertEqual(self.api.set_configuration(telemetry(), cap, 3).value, 3)
        self.values["Solar_Beladungstyp"] = (3417, 1)
        self.assertNotIn(cap.setting.name, controls())
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(telemetry(), cap, 4)
        self.values["Solar_Beladungstyp"] = (3417, 0)
        self.values["Solar_Sp2_Sensor1_Nr"] = (3612, 0)
        self.assertEqual(names & controls().keys(), {"Solar_Pumpenverzoegerung"})
        self.values["Solar_OPumpenverzoegerung"] = (3422, 0)
        self.assertFalse(names & controls().keys())
        self.session.write_verified.assert_called_once()

    def test_native_spread_loading_uses_live_eligibility_and_actual_store_pair(self):
        name = "Solar_Spreizladung_dTDifferenz"
        setting = next(
            s for s in parameters_configuration.BX_PLUS_SOLAR_LOADING_LOGIC_SETTINGS
            if s.name == name and s.unit == "K"
        )
        values = {
            name: 300,
            name + "_K": 300,
            "Solar_SystemId": 10301,
            "Solar_Relais1_Nr": 1,
            "Solar_Kol1_Sensor1_Nr": 1,
            "Solar_OSpreizladung": 1,
            "Solar_ParallelbeladungMoeglich": 1,
            "Solar_Sp2_Sensor1_Nr": 2,
            "Solar_Sp2_Aktiviert_Modus": 1,
            "Solar_Sp3_Sensor1_Nr": 3,
            "Solar_Sp3_Aktiviert_Modus": 1,
            "TemperatureHysteresisSelector": 0,
        }
        self.assertTrue(setting.accepts(values, setting.encode(35)))
        self.assertFalse(setting.accepts(values, 199))
        values["Solar_SystemId"] = 10201
        self.assertFalse(setting.accepts(values))
        values["Solar_SystemId"] = 10301
        values["Solar_ParallelbeladungMoeglich"] = 0
        self.assertFalse(setting.accepts(values))
        values["Solar_ParallelbeladungMoeglich"] = 1
        values["Solar_OSpreizladung"] = 0
        self.assertFalse(setting.accepts(values))
        values["Solar_OSpreizladung"] = 1
        del values["Solar_ParallelbeladungMoeglich"]
        self.assertFalse(setting.accepts(values))

    def test_dedicated_solar_bypass_requires_selected_collectors_and_assignment(self):
        self.session.read.return_value = 0x2734DABC
        root = "Solar_Wf1_Bypass"
        name = root + "_dTein"
        self.values.update(
            {
                root + "_Sensor1_Nr": (3896, 3),
                root + "_Relais1_Nr": (3900, 2),
                root + "_Aktiviert_Modus": (3888, 1),
                root + "_Variante": (3894, 0),
                root + "_KollektorMaske": (3893, 3),
                "Solar_Kol1_Sensor1_Nr": (3536, 1),
                "Solar_Kol2_Sensor1_Nr": (3550, 2),
                "Solar_Relais1_Nr": (3452, 1),
                "TemperatureHysteresisSelector": (60, 0),
                name: (3908, 60),
                name + "_K": (3909, 60),
                root + "_dTaus": (3911, 40),
                root + "_dTaus_K": (3912, 40),
            }
        )
        hints = {root + "_Sensor1_Nr": parameters.ParameterObservation(3896, 3, "read")}

        def controls():
            return {
                cap.setting.name: cap
                for cap in self.api.discover(telemetry(), hints).configuration.values()
            }

        cap = controls()[name]
        self.assertEqual(cap.setting.native_slot, (root, "Solar_Bypass", 1))
        self.assertEqual(cap.value, 6)

        def write(address, index, value, changeset, *, save):
            self.assertEqual((address, index, value, save), (0x7112, 3908, 70, True))
            self.values[name] = (index, value)
            self.values[name + "_K"] = (index + 1, value)
            return value

        self.session.write_verified.side_effect = write
        self.assertEqual(self.api.set_configuration(telemetry(), cap, 7).value, 7)
        self.values["Solar_Kol2_Sensor1_Nr"] = (3550, 0)
        self.assertNotIn(name, controls())
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(telemetry(), cap, 8)
        self.values[root + "_KollektorMaske"] = (3893, 1)
        replacement = controls()[name]
        self.assertEqual(replacement.unique_id, cap.unique_id)
        with self.assertRaises(parameters.VBusError):
            self.api.set_configuration(telemetry(), cap, 8)
        self.values[root + "_Relais1_Nr"] = (3900, 0)
        self.assertNotIn(name, controls())
        self.session.write_verified.assert_called_once()

    def test_dedicated_solar_target_and_cs_bypass_require_live_references(self):
        settings = parameters_configuration.BX_PLUS_SOLAR_DEDICATED_SETTINGS
        target = next(
            s
            for s in settings
            if s.name == "Solar_Wf1_Zieltemperatur_Zieltemperatur" and s.unit == "°C"
        )
        values = {
            target.name: 650,
            target.name + "_C": 650,
            "Solar_Wf1_Zieltemperatur_Sensor1_Nr": 3,
            "Solar_Wf1_Zieltemperatur_Aktiviert_Modus": 1,
            "Solar_Kol1_Sensor1_Nr": 1,
            "Solar_Sp1_Sensor1_Nr": 2,
            "Solar_Sp1_Aktiviert_Modus": 1,
            "Solar_Relais1_Nr": 1,
            "TemperatureHysteresisSelector": 0,
        }
        self.assertTrue(target.accepts(values, target.encode(70)))
        values["Solar_Wf1_Zieltemperatur_Sensor1_Nr"] = 0
        self.assertFalse(target.accepts(values))
        cs = next(s for s in settings if s.name == "Solar_Wf1_CsBypass_Einstrahlung")
        values.update(
            {
                cs.name: 200,
                "Solar_Wf1_CsBypass_SensorEinstrahlung_Nr": 4,
                "Solar_Wf1_CsBypass_Aktiviert_Modus": 1,
                "Solar_Wf1_CsBypass_KollektorMaske": 1,
            }
        )
        self.assertTrue(cs.accepts(values, cs.encode(250)))
        values["Solar_Wf1_CsBypass_SensorEinstrahlung_Nr"] = 0
        self.assertFalse(cs.accepts(values))

    def test_native_solar_tube_exchanger_dynamic_constraints(self):
        settings = parameters_configuration.BX_PLUS_SOLAR_DEDICATED_SETTINGS
        tube = "Solar_Wf1_Roehrenkollektor"
        start = next(s for s in settings if s.name == tube + "_tBeginn")
        values = {
            "Solar_SystemId": 20101,
            "Solar_Relais1_Nr": 1,
            "Solar_Kol1_Sensor1_Nr": 1,
            "Solar_Kol2_Sensor1_Nr": 2,
            tube + "_Aktiviert_Modus": 1,
            tube + "_KollektorMaske": 3,
            tube + "_tBeginn": 360,
            tube + "_tEnde": 420,
        }
        self.assertTrue(start.accepts(values, start.encode(390)))
        self.assertFalse(start.accepts(values, start.encode(420)))
        values["Solar_SystemId"] = 10101
        self.assertFalse(start.accepts(values))
        values["Solar_SystemId"] = 20101
        values["Solar_Kol2_Sensor1_Nr"] = 0
        self.assertFalse(start.accepts(values))
        root = "Solar_Wf1_ExtWT"
        on = next(s for s in settings if s.name == root + "_dTein" and s.unit == "K")
        values = {
            "Solar_SystemId": 10201,
            "Solar_Relais1_Nr": 1,
            "Solar_Sp1_Sensor1_Nr": 1,
            "Solar_Sp1_Aktiviert_Modus": 1,
            "Solar_Sp2_Sensor1_Nr": 2,
            "Solar_Sp2_Aktiviert_Modus": 1,
            root + "_SpeicherMaske": 3,
            root + "_Sensor1_Nr": 3,
            root + "_Relais1_Nr": 2,
            root + "_dTein": 60,
            root + "_dTein_K": 60,
            root + "_dTaus": 40,
            root + "_dTaus_K": 40,
            "TemperatureHysteresisSelector": 0,
        }
        self.assertTrue(on.accepts(values, on.encode(4.5)))
        self.assertFalse(on.accepts(values, on.encode(4)))
        values["Solar_Sp2_Aktiviert_Modus"] = 0
        self.assertFalse(on.accepts(values))

    def test_return_mixing_requires_installed_sensors_outputs_and_order(self):
        self.session.read.return_value = 0x2734DABC
        prefix = "Anlage_Wf1"
        name = prefix + "_RLBeimischung_dTEin"
        setting = next(
            s
            for s in parameters_configuration.BX_PLUS_ADDITIONAL_OPERATING_SETTINGS
            if s.name == name and s.unit == "K"
        )
        self.values.update(
            {
                prefix + "_Type": (6559, 11),
                prefix + "_Aktiviert_Modus": (6566, 1),
                prefix + "_Sensor1_Nr": (6576, 1),
                prefix + "_Sensor2_Nr": (6580, 2),
                prefix + "_Sensor3_Nr": (6584, 3),
                prefix + "_Relais1_Nr": (6592, 1),
                prefix + "_Relais2_Nr": (6600, 2),
                "TemperatureHysteresisSelector": (60, 0),
                name: (6759, 50),
                name + "_K": (6760, 50),
                prefix + "_RLBeimischung_dTAus": (6762, 30),
                prefix + "_RLBeimischung_dTAus_K": (6763, 30),
            }
        )
        hints = {prefix + "_Type": parameters.ParameterObservation(6559, 11, "read")}

        def controls():
            return {
                cap.setting.name: cap
                for cap in self.api.discover(telemetry(), hints).configuration.values()
            }

        cap = controls()[name]
        self.assertEqual(cap.value, 5)
        self.assertEqual(cap.setting.native_slot, (prefix, "Anlage_Wf", 1))

        def write(address, index, value, changeset, *, save):
            self.assertEqual((address, index, value, save), (0x7112, 6759, 55, True))
            self.values[name] = (index, value)
            self.values[name + "_K"] = (index + 1, value)
            return value

        self.session.write_verified.side_effect = write
        self.assertEqual(self.api.set_configuration(telemetry(), cap, 5.5).value, 5.5)
        for invalid in (3, 5.25, 26):
            with self.assertRaises(parameters.VBusError):
                self.api.set_configuration(telemetry(), cap, invalid)
        for dependency, index in (
            (prefix + "_Sensor3_Nr", 6584),
            (prefix + "_Relais2_Nr", 6600),
            (prefix + "_Aktiviert_Modus", 6566),
            (prefix + "_Type", 6559),
        ):
            old = self.values[dependency]
            self.values[dependency] = (index, 0)
            self.assertNotIn(name, controls())
            with self.assertRaises(parameters.VBusError):
                self.api.set_configuration(telemetry(), cap, 6)
            self.values[dependency] = old
        self.session.write_verified.assert_called_once()
        # The same semantic contract has the corresponding Fahrenheit variant.
        fahrenheit = next(
            s
            for s in parameters_configuration.BX_PLUS_ADDITIONAL_OPERATING_SETTINGS
            if s.name == name and s.unit == "°R"
        )
        values = {key: raw for key, (_, raw) in self.values.items()}
        values.update(
            {
                "TemperatureHysteresisSelector": 1,
                name: 110,
                name + "_R": 110,
                prefix + "_RLBeimischung_dTAus": 60,
                prefix + "_RLBeimischung_dTAus_R": 60,
            }
        )
        self.assertTrue(fahrenheit.accepts(values, fahrenheit.encode(12)))
        self.assertFalse(fahrenheit.accepts(values, fahrenheit.encode(6)))
        values[name + "_R"] = 100
        self.assertFalse(fahrenheit.accepts(values))
        self.assertEqual(setting.name, fahrenheit.name)

    def test_return_mixing_target_and_timing_require_live_assignment(self):
        prefix = "Anlage_Wf1_RLBeimischung"
        values = {
            "Anlage_Wf1_Type": 11,
            "Anlage_Wf1_Aktiviert_Modus": 1,
            "Anlage_Wf1_Sensor1_Nr": 1,
            "Anlage_Wf1_Sensor2_Nr": 2,
            "Anlage_Wf1_Sensor3_Nr": 3,
            "Anlage_Wf1_Relais1_Nr": 1,
            "Anlage_Wf1_Relais2_Nr": 2,
            "TemperatureHysteresisSelector": 0,
            prefix + "_dTSoll": 70,
            prefix + "_dTSoll_K": 70,
            prefix + "_Mischerlaufzeit": 105,
            prefix + "_Mischerstellzeit": 2,
        }
        settings = {
            (s.name, s.unit): s
            for s in parameters_configuration.BX_PLUS_ADDITIONAL_OPERATING_SETTINGS
        }
        for suffix, unit, wanted, invalid in (
            ("dTSoll", "K", -10, 26),
            ("Mischerlaufzeit", "s", 120, 601),
            ("Mischerstellzeit", "s", 5, 21),
        ):
            setting = settings[(prefix + "_" + suffix, unit)]
            self.assertTrue(setting.accepts(values, setting.encode(wanted)))
            with self.assertRaises(ValueError):
                setting.encode(invalid)
            values["Anlage_Wf1_Relais2_Nr"] = 0
            self.assertFalse(setting.accepts(values))
            values["Anlage_Wf1_Relais2_Nr"] = 2
        fahrenheit = settings[(prefix + "_dTSoll", "°R")]
        values["TemperatureHysteresisSelector"] = 1
        values[prefix + "_dTSoll"] = 140
        values[prefix + "_dTSoll_R"] = 140
        self.assertTrue(fahrenheit.accepts(values, fahrenheit.encode(-20)))
        values[prefix + "_dTSoll_R"] = 120
        self.assertFalse(fahrenheit.accepts(values))

    def test_circulation_temperature_requires_thermal_variant_and_order(self):
        setting = next(
            s
            for s in parameters_configuration.BX_PLUS_ADDITIONAL_OPERATING_SETTINGS
            if s.name == "Heizung_Wf1_Zirkulation_TEin" and s.unit == "°C"
        )
        values = {
            "Heizung_Wf1_Type": 4,
            "Heizung_Wf1_Aktiviert_Modus": 1,
            "Heizung_Wf1_Zirkulation_Variante": 0,
            "TemperatureHysteresisSelector": 0,
            setting.name: 400,
            setting.name + "_C": 400,
            "Heizung_Wf1_Zirkulation_TAus": 450,
            "Heizung_Wf1_Zirkulation_TAus_C": 450,
        }
        self.assertTrue(setting.accepts(values, setting.encode(42)))
        self.assertFalse(setting.accepts(values, setting.encode(45)))
        values["Heizung_Wf1_Zirkulation_Variante"] = 1
        self.assertFalse(setting.accepts(values))
        values["Heizung_Wf1_Zirkulation_Variante"] = 2
        self.assertTrue(setting.accepts(values))
        values[setting.name + "_C"] = 410
        self.assertFalse(setting.accepts(values))

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

    def test_read_only_resync_reacquires_on_the_same_authenticated_connection(self):
        transport = importlib.import_module(f"{PACKAGE}.vbus")
        connection = Mock()
        offer = transport.Datagram(0, 0x7112, 0x0500).encode()
        reply = transport.Datagram(0x20, 0x7112, 0x0100, 2019, 2).encode()
        connection.recv.side_effect = [
            offer,
            TimeoutError(),
            offer,
            offer,
            reply,
            offer,
        ]
        session = transport.VBusSession(connection)
        self.ctx.__enter__.return_value = session
        result = self.api._read_only_session(
            0x7112, lambda active: active.read(0x7112, 2019)
        )
        self.assertEqual(result, 2)
        self.connect.assert_called_once()
        commands = [
            transport.Datagram.decode(call.args[0]).command
            for call in connection.sendall.call_args_list
        ]
        self.assertEqual(commands, [0x0300, 0x0600, 0x0300, 0x0600])
        health = self.api.transport_health()
        self.assertEqual(health["sessions"], 1)
        self.assertEqual(health["read_resync_attempts"], 1)
        self.assertEqual(health["read_resync_successes"], 1)
        self.assertEqual(health["release_attempts"], 2)

    def test_read_only_resync_requires_confirmed_release_and_new_offer(self):
        transport = importlib.import_module(f"{PACKAGE}.vbus")
        offer = transport.Datagram(0, 0x7112, 0x0500).encode()
        for confirmed in (False, True):
            with self.subTest(confirmed=confirmed):
                connection = Mock()
                connection.recv.side_effect = [
                    offer,
                    TimeoutError(),
                    *(
                        [offer, TimeoutError()]
                        if confirmed
                        else [TimeoutError(), TimeoutError()]
                    ),
                ]
                session = transport.VBusSession(connection)
                self.ctx.__enter__.return_value = session
                api = parameters.RelayParameters("gateway", "secret")
                with self.assertRaises(parameters.VBusTransientError):
                    api._read_only_session(
                        0x7112, lambda active: active.read(0x7112, 2019)
                    )
                commands = [
                    transport.Datagram.decode(call.args[0]).command
                    for call in connection.sendall.call_args_list
                ]
                self.assertEqual(
                    commands,
                    [0x0300, 0x0600] if confirmed else [0x0300, 0x0600, 0x0600],
                )
                self.assertEqual(
                    api.transport_health()["read_resync_attempts"], int(confirmed)
                )
                self.assertEqual(api.transport_health()["read_resync_successes"], 0)

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
