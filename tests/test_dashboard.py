"""Source-level dashboard authorization and native action contract regression."""

import ast
import re
import unittest
from math import isclose
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "deltasol"
    / "dashboard.py"
)


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Execute the actual pure policy functions without loading HA.
        tree = ast.parse(PATH.read_text(encoding="utf-8"))
        functions = {
            "enabled",
            "engineering_enabled",
            "capability_access",
            "capability_section",
            "capability_role",
            "capability_availability",
            "inherit_object_section",
            "project_assignments",
            "engineering_catalogue",
            "object_kind",
            "observed_result",
            "scoped_control",
            "service_request",
        }
        cls.ns = {
            "ConfigEntryState": SimpleNamespace(LOADED="loaded"),
            "DOMAIN": "deltasol",
            "CONF_DASHBOARD": "enable_dashboard",
            "CONF_ENGINEERING": "show_engineering",
            "POLICY_CONTROL": "control",
            "HomeAssistantError": ValueError,
            "isclose": isclose,
            "re": re,
        }
        tree.body = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in functions
        ]
        exec(compile(tree, str(PATH), "exec"), cls.ns)  # noqa: S102

    def test_opt_in_and_admin_are_independent(self):
        entry = SimpleNamespace(
            domain="deltasol",
            state="loaded",
            options={"enable_dashboard": True, "show_engineering": True},
            runtime_data=SimpleNamespace(closing=False),
        )
        admin = SimpleNamespace(is_admin=True)
        resident = SimpleNamespace(is_admin=False)
        self.assertTrue(self.ns["engineering_enabled"](entry, admin))
        self.assertFalse(self.ns["engineering_enabled"](entry, resident))
        entry.options["enable_dashboard"] = False
        self.assertFalse(self.ns["enabled"](entry))
        self.assertFalse(self.ns["engineering_enabled"](entry, admin))

    def test_permitted_control_is_not_engineering_permission(self):
        check = MagicMock(return_value=True)
        user = SimpleNamespace(
            is_admin=False, permissions=SimpleNamespace(check_entity=check)
        )
        row = SimpleNamespace(entity_id="select.heating", entity_category=None)
        setting = SimpleNamespace(
            name="Heizung_Heizkreis1_Betriebsart", save=True, kind="select"
        )
        state = SimpleNamespace(state="Auto")
        access = self.ns["capability_access"]
        self.assertEqual(access(row, setting, state, user, False), ("controls", True))
        check.return_value = False
        self.assertEqual(access(row, setting, state, user, False), ("controls", False))
        self.assertIsNone(access(row, None, state, user, False, relay=True))
        state.state = "unavailable"
        check.return_value = True
        self.assertEqual(access(row, setting, state, user, False), ("controls", False))

    def test_discovery_button_unknown_is_not_unavailable(self):
        row = SimpleNamespace(
            entity_id="button.discovery", entity_category="diagnostic"
        )
        user = SimpleNamespace(
            is_admin=True,
            permissions=SimpleNamespace(check_entity=lambda *_: True),
        )
        state = SimpleNamespace(state="unknown")
        access = self.ns["capability_access"]
        self.assertEqual(access(row, None, state, user, True), ("engineering", True))
        self.assertIsNone(access(row, None, state, user, False))
        self.assertEqual(self.ns["observed_result"]("button", state, None), "submitted")

    def test_function_slot_is_not_a_new_device_or_inferred_topology(self):
        setting = SimpleNamespace(
            name="Anlage_Wf1_Feststoffkessel_dTEin",
            native_slot=("Anlage_Wf1", "Anlage_Wf", 1),
            weekly_slot=None,
        )
        self.assertEqual(self.ns["object_kind"](setting), ("solid_fuel", 1))
        self.assertEqual(self.ns["capability_section"](setting, None), "heating")
        setting.name = "Anlage_Wf1_Thermostat1_TEin"
        self.assertEqual(self.ns["object_kind"](setting), ("function", 1))

    def test_no_optimistic_readback(self):
        observed = self.ns["observed_result"]
        self.assertEqual(
            observed("number", SimpleNamespace(state="42"), 42), "observed"
        )
        self.assertEqual(
            observed("number", SimpleNamespace(state="41"), 42), "not_confirmed"
        )
        self.assertEqual(
            observed("number", SimpleNamespace(state="unavailable"), 42),
            "unavailable",
        )

    def test_action_scope_rejects_other_entry_or_denied_entity(self):
        allowed = {"entity_id": "number.allowed", "control": True}
        denied = {"entity_id": "number.denied", "control": False}
        self.ns["dashboard_objects"] = lambda *_: [
            {"id": "one", "objects": [{"capabilities": [allowed, denied]}]}
        ]
        scope = self.ns["scoped_control"]
        self.assertIs(scope(None, None, "one", "number.allowed"), allowed)
        for entry, entity in (
            ("two", "number.allowed"),
            ("one", "number.denied"),
            ("one", "light.other"),
        ):
            with self.assertRaises(ValueError):
                scope(None, None, entry, entity)

    def test_no_arbitrary_native_service_route(self):
        service = self.ns["service_request"]
        self.assertEqual(
            service({"kind": "number"}, 42), ("number", "set_value", {"value": 42})
        )
        with self.assertRaises(ValueError):
            service({"kind": "light"}, True)
        with self.assertRaises(ValueError):
            service({"kind": "number"}, True)

    def test_semantic_priority_distinguishes_target_from_measurement(self):
        role = self.ns["capability_role"]
        setting = SimpleNamespace(
            name="Heizung_Heizkreis1_Betriebsart", weekly_slot=None
        )
        temp = SimpleNamespace(attributes={"device_class": "temperature"})
        alarm = SimpleNamespace(attributes={"device_class": "problem"})
        self.assertEqual(role(setting, None, "controls"), ("mode", 100))
        self.assertEqual(role(None, temp, "status"), ("temperature", 95))
        self.assertGreater(role(None, alarm, "status")[1], 100)

    def test_domain_inheritance_does_not_guess_physical_sensor_assignment(self):
        inherit = self.ns["inherit_object_section"]
        asset = {
            "object_confirmed": True,
            "capabilities": [{"section": "heating"}, {"section": "overview"}],
        }
        inherit(asset)
        self.assertEqual(asset["capabilities"][1]["section"], "heating")
        asset["object_confirmed"] = False
        asset["capabilities"][1]["section"] = "overview"
        inherit(asset)
        self.assertEqual(asset["capabilities"][1]["section"], "overview")
        asset["object_confirmed"] = True
        asset["capabilities"].append({"section": "solar"})
        inherit(asset)
        self.assertEqual(asset["capabilities"][1]["section"], "overview")

    def test_availability_is_not_inferred_from_cached_entity_state(self):
        availability = self.ns["capability_availability"]
        state = SimpleNamespace(state="42")
        setting = object()
        self.assertEqual(
            availability(state, setting, object(), False, True),
            "telemetry_unavailable",
        )
        self.assertEqual(
            availability(state, setting, object(), True, False),
            "parameter_unavailable",
        )
        self.assertEqual(
            availability(state, setting, None, True, True),
            "requirements_not_confirmed",
        )
        self.assertEqual(availability(state, None, None, True, False), "available")

    def test_assignments_deduplicate_and_reject_conflicting_native_observations(self):
        project = self.ns["project_assignments"]
        observations = [
            ("relay_assignment", "actor", 15),
            ("relay_assignment", "actor", 15),
            ("sensor_reference", "sensor", 3),
            ("sensor_reference", "conflict", 4),
            ("sensor_reference", "conflict", 5),
        ]
        rows = project(observations, {15: "module"}, False)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["source_object"], "module")
        self.assertNotIn("parameter", rows[0])
        self.assertEqual(rows[1]["resolution"], "native_reference_only")
        self.assertIsNone(rows[1]["source_object"])
        self.assertEqual(project(observations, {}, True)[0]["parameter"], "actor")

    def test_reference_inventory_redacts_sensitive_aliases_and_never_grants_access(
        self,
    ):
        public = SimpleNamespace(
            name="Schema", index=1, type_id="enum", storage="persistent", metadata={}
        )
        secret = SimpleNamespace(
            name="Password", index=2, type_id="text", storage="persistent", metadata={}
        )
        alias = SimpleNamespace(
            name="Alias", index=2, type_id="text", storage="persistent", metadata={}
        )
        self.ns.update(
            is_sensitive=lambda item: item.name == "Password",
            configuration_evidence=lambda *_: {"write_access": "not_verified"},
            menu_parameter_reason=lambda *_: "hardware_or_topology_assignment",
        )
        profile = SimpleNamespace(settings=(), binding_review=lambda *_: ((), {}))
        contracts, inventory = self.ns["engineering_catalogue"](
            profile,
            SimpleNamespace(definitions=(public, secret, alias)),
            SimpleNamespace(changeset=1),
        )
        self.assertEqual(contracts, {})
        self.assertEqual(set(inventory), {"Schema"})
        self.assertEqual(inventory["Schema"]["write_access"], "not_confirmed")
        self.assertEqual(
            inventory["Schema"]["write_access_reason"],
            "hardware_or_topology_assignment",
        )

    def test_native_subscription_coalesces_updates_and_detaches_on_unload(self):
        tree = ast.parse(PATH.read_text(encoding="utf-8"))
        method = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "ws_dashboard_subscribe"
        )
        method.decorator_list = []
        callbacks = []
        queued = []

        def listen(*args):
            callback = args[-1]
            callbacks.append(callback)
            return lambda: callbacks.remove(callback)

        def later(delay, callback):
            queued.append(callback)
            return SimpleNamespace(cancel=lambda: queued.remove(callback))

        coordinator = SimpleNamespace(async_add_listener=listen)
        runtime = SimpleNamespace(
            coordinator=coordinator,
            relay_coordinator=coordinator,
            capture_listeners=set(),
        )
        entry = SimpleNamespace(
            entry_id="one",
            runtime_data=runtime,
            async_on_state_change=listen,
        )
        entries = [entry]
        projection = [{"id": "one", "health": {"parameter_current": True}}]
        hass = SimpleNamespace(
            loop=SimpleNamespace(call_later=later),
            bus=SimpleNamespace(async_listen=listen),
            config_entries=SimpleNamespace(async_entries=lambda *_: entries),
        )
        connection = SimpleNamespace(
            user=object(),
            subscriptions={},
            send_result=MagicMock(),
            send_event=MagicMock(),
        )
        namespace = {
            "callback": lambda func: func,
            "DOMAIN": "deltasol",
            "SIGNAL_CONFIG_ENTRY_CHANGED": "changed",
            "async_dispatcher_connect": listen,
            "dashboard_objects": lambda *_: list(projection),
        }
        exec(  # noqa: S102
            compile(ast.Module(body=[method], type_ignores=[]), str(PATH), "exec"),
            namespace,
        )
        namespace["ws_dashboard_subscribe"](hass, connection, {"id": 1})
        self.assertEqual(connection.send_event.call_count, 1)
        self.assertEqual(len(callbacks), 6)
        self.assertEqual(len(runtime.capture_listeners), 1)
        for notify in callbacks:
            notify()
        self.assertEqual(len(queued), 1)
        queued.pop()()
        self.assertEqual(connection.send_event.call_count, 1)
        projection[0] = {"id": "one", "health": {"parameter_current": False}}
        callbacks[-1]()
        queued.pop()()
        self.assertEqual(connection.send_event.call_count, 2)
        self.assertFalse(
            connection.send_event.call_args.args[1][0]["health"]["parameter_current"]
        )
        entries.clear()
        projection.clear()
        callbacks[0]()
        queued.pop()()
        self.assertEqual(connection.send_event.call_args.args, (1, []))
        self.assertEqual(len(callbacks), 3)
        self.assertFalse(runtime.capture_listeners)
        callbacks[0]()
        connection.subscriptions[1]()
        self.assertFalse(callbacks)
        self.assertFalse(queued)

    def test_room_setpoint_is_a_daily_control_but_hysteresis_is_not(self):
        access = self.ns["capability_access"]
        user = SimpleNamespace(
            is_admin=False,
            permissions=SimpleNamespace(check_entity=lambda *_: True),
        )
        registered = SimpleNamespace(
            entity_id="number.room", entity_category=None
        )
        state = SimpleNamespace(state="21")
        setting = SimpleNamespace(
            name="Heizung_Heizkreis2_Raumthermostat3_TRaum",
            save=True,
            kind="number",
        )
        self.assertEqual(access(registered, setting, state, user, False), ("controls", True))
        setting.name = "Heizung_Heizkreis2_Raumthermostat3_dTRaum"
        self.assertEqual(access(registered, setting, state, user, False), ("settings", False))
