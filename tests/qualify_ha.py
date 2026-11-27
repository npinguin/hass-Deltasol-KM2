"""Real HA lifecycle test with an independent loopback VBus protocol peer.

The peer models all ten local and registered EM relay capabilities.
It is an independent protocol test, not physical RESOL evidence.
"""

import ast
import asyncio
import json
import socketserver
import struct
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import MappingProxyType
from typing import ClassVar
from unittest.mock import patch

from homeassistant import bootstrap, loader
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

ADDRESS = 0x7112
RELAY = 2019
CHANGESET = 0x2734DABC
SENSOR_UID = "00_0010_7112_0100__000_2_0"
SELECT_UID = "isolated-gateway_7112_R4_mode"
EM_UID = "isolated-gateway_7112_M1_R1_mode"
EM_RELAY = 2173
EM_SENSOR_UID = "00_7112_6651_0100__000_4_0"

# Independent wire fixtures: exact 2.08 reference positions, not imported
# production Setting objects. Persistent state belongs to the protocol peer.
CONFIG_SELECTORS = {
    "Anlage_Wf1_Type": (6559, 2),
    "Anlage_Wf1_Aktiviert_Modus": (6566, 1),
    "Anlage_Wf2_Type": (6810, 6),
    "Anlage_Wf2_Aktiviert_Modus": (6817, 1),
    "Heizung_Heizkreis1_Type": (13684, 1),
    "Heizung_Heizkreis1_Aktiviert_Modus": (13689, 1),
    "Heizung_Heizkreis1_Heizsystem": (13978, 1),
    "Wmz1_Type": (17260, 1),
    "Wmz1_Aktiviert_Modus": (17263, 1),
    "Relais_Modul1_R1_AktorFunktion": (2167, 2),
}
CONFIG_SETTINGS = {
    "Anlage_Wf1_Mischer_dtIntervall": (6625, 4, 1, 20),
    "Heizung_Heizkreis1_Heizkurve": (13698, 12, 3, 30),
    "Wmz1_OAlternativAnzeige": (17273, 0, 0, 1),
    "Wmz1_AlternativAnzeigeEinheit": (17274, 0, 0, 3),
    "Wmz1_AlternativAnzeigeFaktor": (17275, 5000000, 1, 1000000000),
    "Relais_Modul1_R1_MinDrehzahl": (2170, 30, 20, 100),
    "Relais_Modul1_R1_MaxDrehzahl": (2171, 80, 20, 100),
    "Anlage_Wf2_Feststoffkessel_dTEin": (6914, 50, 20, 300),
    "Anlage_Wf2_Feststoffkessel_dTAus": (6917, 30, 10, 290),
    "Anlage_Wf2_Feststoffkessel_dTSoll": (6920, 100, 30, 400),
}
# This peer deliberately models the selected-unit parent contract. It is
# independent software evidence, not a physical observation of this encoding.
COMPOUND_NAMES = (
    "Anlage_Wf2_Feststoffkessel_dTEin",
    "Anlage_Wf2_Feststoffkessel_dTAus",
    "Anlage_Wf2_Feststoffkessel_dTSoll",
)
COMPOUND_ALIASES = {
    name + suffix: (CONFIG_SETTINGS[name][0] + delta, CONFIG_SETTINGS[name][0], factor)
    for name in COMPOUND_NAMES
    for suffix, delta, factor in (("_K", 1, 1), ("_R", 2, 2))
}
COMPOUND_PARENTS = {CONFIG_SETTINGS[name][0] for name in COMPOUND_NAMES}


class Peer:
    """Network-side test state; never connect to site devices."""

    mode = 4
    em_mode = 2
    em_present = True
    presence_lookup = True
    deny = False
    writes = 0
    saves = 0
    settings: ClassVar[dict[int, int]] = {
        56: 1,
        60: 0,
        61: 0,
        62: 0,
        63: 0,
        **{index: value for index, value, _, _ in CONFIG_SETTINGS.values()},
    }
    persistent_file = None
    drop_discovery_response = False
    drop_relay_response = False
    drop_next_offer = False
    hold_next_offer = False
    offer_waiting = threading.Event()
    offer_release = threading.Event()
    events: ClassVar[list[str]] = []
    extra_lookups: ClassVar[dict[int, int]] = {}
    extra_values: ClassVar[dict[int, int]] = {}
    release_silence = 0
    release_other_first = False
    silent_offers = 0
    failed_release_sessions = 0
    sessions = 0
    owner = threading.Lock()


def semantic_hash(name):
    value = 0
    for byte in name.encode("ascii"):
        value = (value * 33 + byte) & 0x7FFFFFFF
    return value


def live_packet(address):
    header = struct.pack("<HHBHB", 0x10, address, 0x10, 0x100, 1)
    frame = bytes([1, 2, 3, 4, 0])
    return (
        b"\xaa"
        + header
        + bytes([127 - (sum(header) & 127)])
        + frame
        + bytes([127 - (sum(frame) & 127)])
    )


def additional_relays():
    """Populate other verified local/EM mappings independently of the driver."""
    for prefix, indices, existing in (
        ("Relais_Regler", (1977, 1991, 2005, 2019, 2033), RELAY),
        ("Relais_Modul1", (2173, 2187, 2201, 2215, 2229), EM_RELAY),
    ):
        for number, index in enumerate(indices, 1):
            if index == existing:
                continue
            Peer.extra_lookups[semantic_hash(f"{prefix}_R{number}_Handbetrieb")] = index
            Peer.extra_lookups[semantic_hash(f"{prefix}_R{number}_ODrehzahl")] = (
                index - 4
            )
            Peer.extra_values[index] = 2
            Peer.extra_values[index - 4] = number % 2


def additional_configuration():
    for name, (index, value) in CONFIG_SELECTORS.items():
        Peer.extra_lookups[semantic_hash(name)] = index
        Peer.extra_values[index] = value
    for name, (index, _, _, _) in CONFIG_SETTINGS.items():
        Peer.extra_lookups[semantic_hash(name)] = index
    for name, (index, _, _) in COMPOUND_ALIASES.items():
        Peer.extra_lookups[semantic_hash(name)] = index


def datagram(destination, source, command, index=0, value=0):
    """Independent VBus 2.0 frame encoder."""
    payload = struct.pack("<Hi", index, value)
    body = (
        struct.pack("<HHBH", destination, source, 0x20, command)
        + bytes(x & 127 for x in payload)
        + bytes([sum((x >> 7) << i for i, x in enumerate(payload))])
    )
    return bytes([170]) + body + bytes([127 - (sum(body) & 127)])


class VBusHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b"+HELLO\r\n")
        if self.rfile.readline() != b"PASS local-test\r\n" or Peer.deny:
            self.wfile.write(b"-DENIED\r\n")
            return
        self.wfile.write(b"+OK\r\n")
        if self.rfile.readline() != b"DATA\r\n":
            return
        self.wfile.write(b"+OK\r\n")
        if Peer.drop_next_offer:
            Peer.drop_next_offer = False
            return
        if Peer.silent_offers:
            Peer.silent_offers -= 1
            threading.Event().wait(20.2)
            return
        if Peer.hold_next_offer:
            Peer.hold_next_offer = False
            Peer.offer_waiting.set()
            assert Peer.offer_release.wait(timeout=10)
        assert Peer.owner.acquire(blocking=False), "overlapping bus owners"
        Peer.sessions += 1
        try:
            self.exchange()
        finally:
            Peer.owner.release()

    def exchange(self):
        self.wfile.write(datagram(0, ADDRESS, 0x0500))
        while frame := self.rfile.read(16):
            assert len(frame) == 16 and frame[0] == 170
            assert sum(frame[1:]) & 127 == 127
            destination, source, proto, command = struct.unpack("<HHBH", frame[1:8])
            assert (destination, source, proto) == (ADDRESS, 0x20, 0x20)
            payload = bytes(
                x | (((frame[14] >> i) & 1) << 7) for i, x in enumerate(frame[8:14])
            )
            index, value = struct.unpack("<Hi", payload)
            if command == 0x1100:
                if value == 613666522:
                    Peer.events.append("missing")
                if value == 463603356 and Peer.drop_relay_response:
                    Peer.drop_relay_response = False
                    return
                if value == 613666522 and Peer.drop_discovery_response:
                    Peer.drop_discovery_response = False
                    return
                index = {
                    1222011281: 86,
                    463603356: RELAY,
                    530579973: 2015,
                    1121009828: 191,
                    1682631798: 193,
                    1215980970: EM_RELAY,
                    152651155: 2169,
                    852441249: 56,
                    156054146: 60,
                    124814937: 61,
                    1011665082: 62,
                    1893665643: 63,
                }.get(value, Peer.extra_lookups.get(value, 0))
                if value == 1682631798 and not Peer.presence_lookup:
                    index = 0
                response = datagram(source, ADDRESS, 0x1101, index, value)
            elif command == 0x0300:
                value = {
                    0: CHANGESET,
                    86: 1,
                    RELAY: Peer.mode,
                    2015: 0,
                    191: 1,
                    193: int(Peer.em_present),
                    EM_RELAY: Peer.em_mode,
                    2169: 1,
                    **Peer.settings,
                    **Peer.extra_values,
                    **{
                        parent: Peer.settings[parent] * (1 + Peer.settings[60])
                        for parent in COMPOUND_PARENTS
                    },
                    **{
                        alias: Peer.settings[parent] * factor
                        for alias, parent, factor in COMPOUND_ALIASES.values()
                    },
                }[index]
                response = datagram(source, ADDRESS, 0x0100, index, value)
            elif command == 0x0200:
                if index == RELAY:
                    assert value in (0, 2, 4)
                    Peer.mode = value
                else:
                    assert index == EM_RELAY and Peer.em_present
                    assert value in (0, 1, 2, 3)
                    Peer.em_mode = value
                Peer.writes += 1
                Peer.events.append("relay_write")
                response = datagram(source, ADDRESS, 0x0100, index, value)
            elif command == 0x0400:
                assert index in Peer.settings
                bounds = {
                    idx: (low, high) for idx, _, low, high in CONFIG_SETTINGS.values()
                }
                low, high = bounds.get(index, (0, 1))
                stored_value = value
                if index in COMPOUND_PARENTS:
                    factor = 1 + Peer.settings[60]
                    assert value % factor == 0
                    stored_value = value // factor
                    assert stored_value % 10 == 0
                    proposed = {
                        parent: stored_value if parent == index else Peer.settings[parent]
                        for parent in COMPOUND_PARENTS
                    }
                    assert proposed[6917] < proposed[6914] < proposed[6920]
                assert low <= stored_value <= high
                # Peer independently verifies installed program constraints.
                if index in (2170, 2171):
                    assert Peer.em_present and Peer.em_mode == 2
                    assert Peer.extra_values[2167] == 2
                    assert (
                        value <= Peer.settings[2171]
                        if index == 2170
                        else value >= Peer.settings[2170]
                    )
                if index in (17274, 17275):
                    assert Peer.settings[17273] == 1
                Peer.settings[index] = stored_value
                Peer.saves += 1
                Peer.events.append("save")
                temporary = Peer.persistent_file.with_suffix(".tmp")
                temporary.write_text(json.dumps(Peer.settings), encoding="utf-8")
                temporary.replace(Peer.persistent_file)
                response = datagram(source, ADDRESS, 0x0100, index, value)
            elif command == 0x0600:
                if Peer.failed_release_sessions:
                    Peer.failed_release_sessions -= 1
                    return
                if Peer.release_silence:
                    Peer.release_silence -= 1
                    continue
                packet = live_packet(ADDRESS)
                if Peer.release_other_first:
                    Peer.release_other_first = False
                    self.wfile.write(live_packet(0x6651))
                    # The controller's real frame is late and fragmented.
                    threading.Event().wait(1.7)
                    self.wfile.write(packet[:7])
                    threading.Event().wait(0.03)
                    self.wfile.write(packet[7:])
                else:
                    self.wfile.write(packet)
                return
            else:
                raise AssertionError(f"Unexpected command {command}")
            self.wfile.write(response)


class VBusServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class HttpHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        assert self.path == "/cgi-bin/get_resol_device_information"
        data = (
            'product = "KM2"\nvendor = "RESOL"\nserial = "isolated-gateway"\n'
            'version = "2.0"\nbuild = "test"\nname = "KM2"\nfeatures = "VBus"\n'
        )
        self.send(data.encode())

    def do_POST(self):
        assert self.path == "/cgi-bin/resol-webservice"
        raw = self.rfile.read(int(self.headers["Content-Length"])).decode()
        request = ast.literal_eval(raw)[0]
        if request["method"] == "login":
            assert request["params"] == {"username": "admin", "password": "http-test"}
            result = {"authId": "test-auth"}
        else:
            assert request["method"] == "dataGetCurrentData"
            assert request["params"]["authId"] == "test-auth"
            result = {
                "headers": [
                    {
                        "id": "00_0010_7112_0100",
                        "description": "BX Plus",
                        "destination_name": "Broadcast",
                        "source_name": "DeltaSol BX Plus",
                        "fields": [
                            {"id": "000_2_0", "name": "Temperature S1", "unit": "°C"},
                            {"id": "043_1_0", "name": "Relay 4", "unit": "%"},
                        ],
                    }
                ],
                "headersets": [
                    {
                        "packets": [
                            {"field_values": [{"raw_value": 42.0}, {"raw_value": 100}]}
                        ]
                    }
                ],
            }
            if Peer.em_present:
                result["headers"].append(
                    {
                        "id": "00_7112_6651_0100",
                        "description": "EM #1",
                        "destination_name": "BX Plus",
                        "source_name": "EM #1",
                        "fields": [
                            {"id": "000_4_0", "name": "Temperature S1", "unit": "°C"}
                        ],
                    }
                )
                result["headersets"][0]["packets"].append(
                    {"field_values": [{"raw_value": 30.0}]}
                )
        self.send(json.dumps([{"result": result}]).encode(), "application/json")

    def send(self, data, content_type="text/plain"):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


async def qualify(directory, phase):
    hass = HomeAssistant(str(directory))
    loader.async_setup(hass)
    hass.config.skip_pip = True
    config = {
        "homeassistant": {
            "name": "Isolated DeltaSol qualification",
            "latitude": 0,
            "longitude": 0,
            "elevation": 0,
            "unit_system": "metric",
            "time_zone": "UTC",
        },
        "deltasol": {},
    }
    stopped = False
    try:
        assert await bootstrap.async_from_config_dict(config, hass) is hass
        await hass.async_start()
        await hass.async_block_till_done()
        if phase == "baseline":
            flow = hass.config_entries.flow
            result = await flow.async_init(
                "deltasol",
                context={"source": "user"},
                data={"host": "127.0.0.1", "port": 18080},
            )
            assert result["step_id"] == "auth", result
            result = await flow.async_configure(
                result["flow_id"], {"username": "admin", "password": "http-test"}
            )
            assert result["step_id"] == "options", result
            result = await flow.async_configure(
                result["flow_id"], {"scan_interval": 60}
            )
            assert result["type"] == "create_entry", result
        await hass.async_block_till_done()
        (entry,) = hass.config_entries.async_entries("deltasol")
        assert entry.state == ConfigEntryState.LOADED, entry
        registry = er.async_get(hass)
        sensor = registry.async_get_entity_id("sensor", "deltasol", SENSOR_UID)
        assert sensor and float(hass.states.get(sensor).state) == 42
        baseline = directory / "qualification-baseline.json"
        identity = {"entry": entry.entry_id, "sensor": sensor, "data": dict(entry.data)}
        if phase == "baseline":
            options = hass.config_entries.options
            result = await options.async_init(entry.entry_id)
            assert result["step_id"] == "init", result
            result = await options.async_configure(
                result["flow_id"], {"vbus_password": "local-test"}
            )
            assert result["type"] == "create_entry", result
            await hass.async_block_till_done()
        assert entry.options["vbus_password"] == "local-test"
        assert entry.state == ConfigEntryState.LOADED
        select = registry.async_get_entity_id("select", "deltasol", SELECT_UID)
        assert select
        identity["select"] = select
        em_sensor = registry.async_get_entity_id("sensor", "deltasol", EM_SENSOR_UID)
        assert em_sensor and float(hass.states.get(em_sensor).state) == 30
        identity["em_sensor"] = em_sensor
        em = registry.async_get_entity_id("select", "deltasol", EM_UID)
        assert em and hass.states.get(em).state == "Auto"
        identity["em_select"] = em
        identity["display"] = registry.async_get_entity_id(
            "select",
            "deltasol",
            "isolated-gateway_7112_TemperatureHysteresisSelector_configuration",
        )
        identity["daylight"] = registry.async_get_entity_id(
            "switch",
            "deltasol",
            "isolated-gateway_7112_AutoSommerWinterzeit_configuration",
        )
        assert identity["display"] and identity["daylight"]
        if phase == "baseline":
            baseline.write_text(json.dumps(identity), encoding="utf-8")
            assert Peer.writes == 0
            return
        assert json.loads(baseline.read_text(encoding="utf-8")) == identity
        if phase == "rollback":
            assert hass.states.get(em).attributes["options"] == [
                "Auto",
                "Off",
                "Min",
                "Max",
            ]
            assert Peer.writes == 0
            return

        assert hass.states.get(select).state == "On"
        assert hass.states.get(select).attributes["options"] == ["Auto", "Off", "On"]
        em = registry.async_get_entity_id("select", "deltasol", EM_UID)
        assert em and hass.states.get(em).state == "Auto"
        assert hass.states.get(em).attributes["options"] == [
            "Auto",
            "Off",
            "Min",
            "Max",
        ]
        assert (
            registry.async_get(em).device_id == registry.async_get(em_sensor).device_id
        )
        assert Peer.writes == 0
        from custom_components.deltasol.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        if phase == "upgrade":
            duplicate = ConfigEntry(
                version=1,
                minor_version=1,
                domain="deltasol",
                title="Duplicate gateway",
                data={**entry.data, "host": "localhost"},
                source="user",
                unique_id="duplicate-isolated-gateway",
                options=dict(entry.options),
                discovery_keys=MappingProxyType({}),
                subentries_data=None,
            )
            sessions_before = Peer.sessions
            await hass.config_entries.async_add(duplicate)
            await hass.async_block_till_done()
            assert duplicate.state == ConfigEntryState.SETUP_ERROR
            assert Peer.sessions == sessions_before
            assert await hass.config_entries.async_remove(duplicate.entry_id)
            assert entry.state == ConfigEntryState.LOADED
        diagnostic = await async_get_config_entry_diagnostics(hass, entry)
        encoded = json.dumps(diagnostic)
        for secret in ("local-test", "http-test", "127.0.0.1", "isolated-gateway"):
            assert secret not in encoded
        assert diagnostic["telemetry"]["available"]
        access = diagnostic["parameter_access"]
        assert access["status"] == "supported"
        assert access["relays"]["R4"]["mode"] == "On"
        assert access["relays"]["R4"]["mode_value"] == 4
        assert access["relays"]["R1"]["writable_options"] == [
            "Auto",
            "Off",
            "Min",
            "Max",
        ]
        assert (
            sum(relay["status"] == "supported" for relay in access["relays"].values())
            == 10
        )
        assert access["relays"]["M1_R1"]["writable_options"] == [
            "Auto",
            "Off",
            "Min",
            "Max",
        ]
        assert access["relays"]["M2_R1"]["writable_options"] == []
        assert any(d["source_address"] == "0x6651" for d in access["devices"])
        flags = access["module_flags"]["M1"]
        assert flags["Modul1_Aktiviert"]["raw_value"] == 1
        assert flags["Modul1_Vorhanden"]["status"] == "read"
        assert Peer.writes == 0
        from custom_components.deltasol.catalog import Catalog, Definition

        display = registry.async_get_entity_id(
            "select",
            "deltasol",
            "isolated-gateway_7112_TemperatureHysteresisSelector_configuration",
        )
        daylight = registry.async_get_entity_id(
            "switch",
            "deltasol",
            "isolated-gateway_7112_AutoSommerWinterzeit_configuration",
        )
        assert display and daylight
        assert (
            registry.async_get(display).device_id
            == registry.async_get(sensor).device_id
        )
        assert registry.async_get(display).entity_category == "config"
        assert hass.states.get(display).state == ("°F" if phase == "restart" else "°C")
        assert hass.states.get(daylight).state == (
            "off" if phase == "restart" else "on"
        )
        assert (
            diagnostic["configuration_controls"]["TemperatureHysteresisSelector"][
                "write_access"
            ]
            == "profile_verified"
        )
        assert Peer.saves == 0

        fixture = Catalog(
            "isolated-test-catalogue",
            (
                Definition(
                    "Schema", 86, "unspecified", "Number", {"type_chain": ["Number"]}
                ),
                Definition(
                    "Relais_Regler_R4_Handbetrieb", RELAY, "volatile", "Handbetrieb", {}
                ),
                Definition("Missing", 123, "unspecified", None, {}),
                Definition("Password", 124, "persistent", "Number", {}, "sensitive"),
                Definition("Bedienercode", 55, "unspecified", "Number", {}),
            ),
        )
        config_fixture = Catalog(
            "isolated-full-configuration",
            (
                *fixture.definitions,
                *(
                    Definition(name, index, "unspecified", None, {})
                    for name, (index, _) in CONFIG_SELECTORS.items()
                ),
                *(
                    Definition(name, index, "unspecified", None, {})
                    for name, (index, _, _, _) in CONFIG_SETTINGS.items()
                ),
                *(
                    Definition(name, index, "unspecified", None, {})
                    for name, (index, _, _) in COMPOUND_ALIASES.items()
                ),
            ),
        )
        expected_config_count = (
            3 + len(CONFIG_SELECTORS) + len(CONFIG_SETTINGS) + len(COMPOUND_ALIASES)
        )
        button = registry.async_get_entity_id(
            "button", "deltasol", f"{entry.entry_id}_configuration_discovery"
        )
        assert button
        progress = registry.async_get_entity_id(
            "sensor", "deltasol", f"{entry.entry_id}_configuration_discovery_status"
        )
        assert progress and hass.states.get(progress).state == "not_started"
        percent = registry.async_get_entity_id(
            "sensor", "deltasol", f"{entry.entry_id}_configuration_discovery_progress"
        )
        assert percent and float(hass.states.get(percent).state) == 0
        assert hass.states.get(percent).attributes["unit_of_measurement"] == "%"
        assert (
            registry.async_get(percent).device_id
            == registry.async_get(progress).device_id
        )
        for entity_id in (button, progress, percent):
            assert registry.async_get(entity_id).entity_category == "diagnostic"
        assert diagnostic["configuration_discovery"]["status"] == "not_started"
        with patch(
            "custom_components.deltasol.load_catalog",
            return_value=config_fixture if phase == "restart" else fixture,
        ):
            if phase == "restart":
                # The independent peer's small catalogue is injected in the test
                # only; the real pinned archive is verified by the source gate.
                assert await hass.config_entries.async_reload(entry.entry_id)
                resumed = entry.runtime_data.capture
                marker = json.loads(
                    (directory / "qualification-active.json").read_text(
                        encoding="utf-8"
                    )
                )
                assert resumed.started_at == marker["started_at"]
                assert resumed.offset >= marker["offset"]
                if entry.runtime_data.capture_task:
                    await entry.runtime_data.capture_task
                assert resumed.status == "complete"
                assert resumed.offset == expected_config_count
                assert float(hass.states.get(percent).state) == 100
                assert Peer.writes == 0
                assert Peer.saves == 0
                assert Peer.settings[60] == 1 and Peer.settings[56] == 0
                await entry.runtime_data.relay_coordinator.async_refresh()
                await hass.async_block_till_done()
                for name, kind, value in (
                    ("Anlage_Wf1_Mischer_dtIntervall", "number", 5),
                    ("Heizung_Heizkreis1_Heizkurve", "number", 1.5),
                    ("Wmz1_OAlternativAnzeige", "switch", "on"),
                    ("Wmz1_AlternativAnzeigeEinheit", "select", "Gas"),
                    ("Wmz1_AlternativAnzeigeFaktor", "number", 0.5000001),
                    ("Relais_Modul1_R1_MinDrehzahl", "number", 40),
                    ("Relais_Modul1_R1_MaxDrehzahl", "number", 70),
                    ("Anlage_Wf2_Feststoffkessel_dTEin", "number", 14),
                ):
                    entity = registry.async_get_entity_id(
                        kind, "deltasol", f"isolated-gateway_7112_{name}_configuration"
                    )
                    assert entity
                    observed = hass.states.get(entity).state
                    assert (float(observed) if kind == "number" else observed) == value
                assert hass.states.get(display).state == "°F"
                assert hass.states.get(daylight).state == "off"
                assert em == json.loads(
                    (directory / "qualification-select.json").read_text(
                        encoding="utf-8"
                    )
                )
                return
            # A real transport failure is resumable and never becomes a write.
            Peer.deny = True
            await hass.services.async_call(
                "button", "press", {"entity_id": button}, blocking=True
            )
            await entry.runtime_data.capture_task
            capture = (await async_get_config_entry_diagnostics(hass, entry))[
                "configuration_discovery"
            ]
            assert capture["status"] == "incomplete", capture
            assert hass.states.get(progress).state == "incomplete"
            assert capture["scanned_count"] == 0
            assert float(hass.states.get(percent).state) == 0
            assert Peer.writes == 0
            Peer.deny = False
            # Baseline and rollback have already returned above.
            resilient = hasattr(entry.runtime_data.capture, "retry_count")
            assert resilient, "Candidate must implement automatic Discovery recovery"
            Peer.drop_discovery_response = True
            await hass.services.async_call(
                "button", "press", {"entity_id": button}, blocking=True
            )
            await entry.runtime_data.capture_task
            capture = (await async_get_config_entry_diagnostics(hass, entry))[
                "configuration_discovery"
            ]
            assert capture["status"] == "complete", capture
            assert capture["scanned_count"] == 3
            if resilient:
                assert capture["retry_count"] == 1
                assert capture["consecutive_failures"] == 0
                assert capture["last_failure"]["phase"] == "response"
                assert capture["last_failure"]["reference_index"] == 123
                assert not Peer.drop_discovery_response
            assert capture["parameters"]["Schema"]["decoded_value"] == 1
            assert capture["parameters"]["Missing"]["status"] == "not_resolved"
            assert "Password" not in capture["parameters"]
            assert "Bedienercode" not in capture["parameters"]
            assert "metadata" not in capture["parameters"]["Missing"]
            assert hass.states.get(progress).state == "complete"
            assert hass.states.get(progress).attributes["progress_percent"] == 100
            assert float(hass.states.get(percent).state) == 100
            attrs = hass.states.get(progress).attributes
            assert attrs["read_count"] == 2
            assert attrs["not_resolved_count"] == 1
            assert attrs["remaining_count"] == 0
            assert attrs["last_error"] is None
            assert attrs["started_at"] and attrs["finished_at"]
            assert capture["summary"]["progress_percent"] == 100
            assert Peer.writes == 0
            Peer.mode = 2
            await hass.services.async_call(
                "button", "press", {"entity_id": button}, blocking=True
            )
            await entry.runtime_data.capture_task
            capture = (await async_get_config_entry_diagnostics(hass, entry))[
                "configuration_discovery"
            ]
            change = capture["comparison"]["changes"]["Relais_Regler_R4_Handbetrieb"]
            assert change["after_raw"] == 2
            assert Peer.writes == 0
            Peer.mode = 4
            # Native storage must preserve the completed inventory and comparison
            # across a real config-entry unload/reload, without another scan.
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            assert entry.runtime_data.capture.status == "complete"
            assert entry.runtime_data.capture_task is None
            restored = (await async_get_config_entry_diagnostics(hass, entry))[
                "configuration_discovery"
            ]
            assert restored["scanned_count"] == 3
            assert restored["summary"]["progress_percent"] == 100
            assert (
                restored["comparison"]["changes"]["Relais_Regler_R4_Handbetrieb"][
                    "after_raw"
                ]
                == 2
            )
            assert (
                registry.async_get_entity_id(
                    "sensor",
                    "deltasol",
                    f"{entry.entry_id}_configuration_discovery_progress",
                )
                == percent
            )
            assert float(hass.states.get(percent).state) == 100
            assert Peer.writes == 0
        if resilient:
            Peer.drop_relay_response = True
            await entry.runtime_data.relay_coordinator.async_refresh()
            assert not Peer.drop_relay_response
            assert entry.runtime_data.relay_coordinator.last_update_success
            assert hass.states.get(select).state == "On"
            assert hass.states.get(em).state == "Auto"
            assert Peer.writes == 0
        select_file = directory / "qualification-select.json"
        if phase == "restart":
            assert em == json.loads(select_file.read_text(encoding="utf-8"))
            return
        select_file.write_text(json.dumps(em), encoding="utf-8")
        Peer.presence_lookup = False
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert hass.states.get(em).state == "Auto"
        assert (
            hass.states.get(em).attributes["registration_source"]
            == "activation_and_observed_module"
        )

        async def set_mode(entity_id, option):
            await hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": entity_id, "option": option},
                blocking=True,
            )
            await hass.async_block_till_done()

        # Both actions use the independent peer's 0x0400 persistence path.
        await set_mode(display, "°F")
        assert Peer.saves == 1 and Peer.settings[60] == 1
        assert hass.states.get(display).state == "°F"
        await set_mode(display, "°F")
        assert Peer.saves == 1
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": daylight}, blocking=True
        )
        assert Peer.saves == 2 and Peer.settings[56] == 0
        assert hass.states.get(daylight).state == "off"
        assert Peer.writes == 0
        for option, expected in (("Off", 0), ("On", 4), ("Auto", 2)):
            await set_mode(select, option)
            assert Peer.mode == expected
            assert hass.states.get(select).state == option
        for option, expected in (("Min", 1), ("Max", 3), ("Off", 0), ("Auto", 2)):
            await set_mode(em, option)
            assert Peer.em_mode == expected
            assert Peer.mode == 2
            assert hass.states.get(em).state == option
        assert Peer.writes == 7
        try:
            await set_mode(em, "On")
        except (HomeAssistantError, ValueError):
            pass
        else:
            raise AssertionError("Unsupported mode accepted")
        assert Peer.writes == 7

        Peer.em_present = False
        try:
            await set_mode(em, "Off")
        except HomeAssistantError:
            pass
        else:
            raise AssertionError("Missing EM accepted")
        assert Peer.writes == 7
        assert hass.states.get(em).state == "unavailable"
        Peer.em_present = True
        with patch("custom_components.deltasol.load_catalog", return_value=fixture):
            assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert registry.async_get_entity_id("select", "deltasol", EM_UID) == em
        assert hass.states.get(em).state == "Auto"

        Peer.deny = True
        try:
            await set_mode(select, "Off")
        except HomeAssistantError:
            pass
        else:
            raise AssertionError("Authentication failure accepted")
        assert Peer.writes == 7
        assert hass.states.get(select).state == "unavailable"
        assert float(hass.states.get(sensor).state) == 42
        diagnostic = await async_get_config_entry_diagnostics(hass, entry)
        access = diagnostic["parameter_access"]
        assert access["status"] == "unavailable"
        assert access["last_error"] == "authentication"
        assert access["relays"]["R4"]["mode"] is None
        assert access["relays"]["R4"]["last_observed_mode"] == "Auto"
        assert access["relays"]["R4"]["writable_options"] == []
        Peer.deny = False
        with patch("custom_components.deltasol.load_catalog", return_value=fixture):
            assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert registry.async_get_entity_id("select", "deltasol", SELECT_UID) == select
        assert hass.states.get(select).state == "Auto"
        assert float(hass.states.get(sensor).state) == 42
        assert Peer.writes == 7
        assert Peer.saves == 2

        # Exercise real 16-item batches across 200+ independent TCP sessions.
        # Accelerate only pacing here; earlier and later lifecycle tests use
        # production delays and real socket timeout behavior.
        soak_definitions = tuple(
            Definition(f"Soak{n}", 10000 + n, "volatile", None, {}) for n in range(3200)
        )
        for n in range(0, 3200, 2):
            Peer.extra_lookups[semantic_hash(f"Soak{n}")] = 10000 + n
            Peer.extra_values[10000 + n] = n
        soak = Catalog("isolated-soak", soak_definitions)
        sessions_before = Peer.sessions
        with (
            patch("custom_components.deltasol.load_catalog", return_value=soak),
            patch("custom_components.deltasol.DISCOVERY_BATCH_GAP_SECONDS", 0.02),
            patch("custom_components.deltasol.parameters.SESSION_GAP_SECONDS", 0.05),
        ):
            Peer.offer_waiting.clear()
            Peer.offer_release.clear()
            Peer.hold_next_offer = True
            Peer.failed_release_sessions = 1
            await hass.services.async_call(
                "button", "press", {"entity_id": button}, blocking=True
            )
            assert await hass.async_add_executor_job(Peer.offer_waiting.wait, 10)
            action = asyncio.create_task(set_mode(select, "Auto"))
            try:
                await asyncio.sleep(0.1)
                assert not action.done()
            finally:
                Peer.offer_release.set()
            await asyncio.wait_for(action, timeout=15)
            capture = entry.runtime_data.capture
            assert capture.offset < 3200
            await entry.runtime_data.capture_task
            assert capture.status == "complete" and capture.offset == 3200
            assert capture.summary()["read_count"] == 1600
            assert capture.summary()["not_resolved_count"] == 1600
            assert Peer.sessions - sessions_before >= 201
            assert capture.retry_count >= 1
            assert capture.last_failure["phase"] == "release"
            assert Peer.writes == 7 and Peer.saves == 2
            assert hass.states.get(select).state == "Auto"
            assert float(hass.states.get(sensor).state) == 42

        # Release loss, late unrelated traffic and repeated acquisition
        # failures are exercised through the actual coordinator, not mocks.
        relay = entry.runtime_data.relay_coordinator
        Peer.release_silence = 1
        await relay.async_refresh()
        assert relay.last_update_success
        Peer.release_other_first = True
        await relay.async_refresh()
        assert relay.last_update_success
        Peer.failed_release_sessions = 1
        Peer.silent_offers = 1
        await relay.async_refresh()
        assert relay.last_update_success
        health = (await async_get_config_entry_diagnostics(hass, entry))[
            "parameter_access"
        ]["transport"]
        assert health["release_failures"] >= 1
        assert health["other_live_packets"] >= 1
        assert health["failure_counts"]["bus_acquisition"] >= 1
        assert health["consecutive_transport_failures"] == 0
        assert Peer.writes == 7 and Peer.saves == 2
        assert float(hass.states.get(sensor).state) == 42

        # A real native action must take its turn before the next Discovery
        # batch. Hold the independent peer's first offer, queue two actions,
        # then allow that batch to finish. No second bus or optimistic state.
        def hold_offer():
            Peer.offer_waiting.clear()
            Peer.offer_release.clear()
            Peer.hold_next_offer = True

        async def start_held_capture():
            hold_offer()
            await hass.services.async_call(
                "button", "press", {"entity_id": button}, blocking=True
            )
            assert await hass.async_add_executor_job(Peer.offer_waiting.wait, 10)
            assert entry.runtime_data.capture.status == "running"
            return entry.runtime_data.capture

        with (
            patch("custom_components.deltasol.load_catalog", return_value=fixture),
            patch("custom_components.deltasol.discovery.BATCH_SIZE", 1),
        ):
            Peer.events.clear()
            active = await start_held_capture()
            relay_action = asyncio.create_task(
                hass.services.async_call(
                    "select",
                    "select_option",
                    {"entity_id": select, "option": "Off"},
                    blocking=True,
                )
            )
            config_action = asyncio.create_task(
                hass.services.async_call(
                    "select",
                    "select_option",
                    {"entity_id": display, "option": "°C"},
                    blocking=True,
                )
            )
            try:
                await asyncio.sleep(0.1)
                assert not relay_action.done() and not config_action.done()
            finally:
                Peer.offer_release.set()
            await asyncio.gather(relay_action, config_action)
            await entry.runtime_data.capture_task
            assert active.status == "complete" and active.offset == 3
            assert Peer.events.index("relay_write") < Peer.events.index("missing")
            assert Peer.events.index("save") < Peer.events.index("missing")
            assert hass.states.get(select).state == "Off"
            assert hass.states.get(display).state == "°C"
            assert Peer.mode == 0 and Peer.settings[60] == 0

            # Reload while an actual read is in progress. The committed batch
            # must survive and the same capture must resume, not restart.
            active = await start_held_capture()
            started_at = active.started_at
            reload_task = asyncio.create_task(
                hass.config_entries.async_reload(entry.entry_id)
            )
            try:
                await asyncio.sleep(0.1)
                assert not reload_task.done()
            finally:
                Peer.offer_release.set()
            assert await reload_task
            resumed = entry.runtime_data.capture
            assert resumed.started_at == started_at
            assert resumed.offset >= 1
            if entry.runtime_data.capture_task:
                await entry.runtime_data.capture_task
            assert resumed.status == "complete" and resumed.offset == 3
            assert float(hass.states.get(percent).state) == 100

        # An acquisition failure must recover the native entity without
        # repeating a write. The user sees the failed action and can retry.
        writes_before = Peer.writes
        Peer.drop_next_offer = True
        try:
            await set_mode(select, "Auto")
        except HomeAssistantError:
            pass
        else:
            raise AssertionError("Failed acquisition reported success")
        assert not Peer.drop_next_offer
        assert Peer.writes == writes_before and Peer.mode == 0
        assert hass.states.get(select).state == "Off"
        assert hass.states.get(em).state == "Auto"
        await set_mode(select, "Auto")
        await set_mode(display, "°F")
        assert Peer.mode == 2 and Peer.settings[60] == 1
        assert Peer.writes == writes_before + 1
        assert Peer.saves == 4

        # Discover the actual installed-function selectors through TCP, not
        # direct runtime injection. Then operate native Number/Select/Switch
        # entities and check both wire persistence and HA observations.
        with patch(
            "custom_components.deltasol.load_catalog", return_value=config_fixture
        ):
            await hass.services.async_call(
                "button", "press", {"entity_id": button}, blocking=True
            )
            await entry.runtime_data.capture_task
            assert entry.runtime_data.capture.offset == expected_config_count
        await entry.runtime_data.relay_coordinator.async_refresh()
        await hass.async_block_till_done()

        def entity(name, kind):
            return registry.async_get_entity_id(
                kind, "deltasol", f"isolated-gateway_7112_{name}_configuration"
            )

        async def number(name, value):
            target = entity(name, "number")
            assert target
            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": target, "value": value},
                blocking=True,
            )
            assert float(hass.states.get(target).state) == value

        await number("Anlage_Wf1_Mischer_dtIntervall", 5)
        await number("Heizung_Heizkreis1_Heizkurve", 1.5)
        alternative = entity("Wmz1_OAlternativAnzeige", "switch")
        assert alternative and hass.states.get(alternative).state == "off"
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": alternative}, blocking=True
        )
        await hass.async_block_till_done()
        assert hass.states.get(alternative).state == "on"
        await set_mode(entity("Wmz1_AlternativAnzeigeEinheit", "select"), "Gas")
        await number("Wmz1_AlternativAnzeigeFaktor", 0.5000001)
        await number("Relais_Modul1_R1_MinDrehzahl", 40)
        await number("Relais_Modul1_R1_MaxDrehzahl", 70)
        assert Peer.saves == 11
        await number("Relais_Modul1_R1_MinDrehzahl", 40)
        assert Peer.saves == 11
        try:
            await number("Relais_Modul1_R1_MinDrehzahl", 71)
        except HomeAssistantError:
            pass
        else:
            raise AssertionError("Invalid pump pair accepted")
        assert Peer.saves == 11 and Peer.settings[2170] == 40
        pump = entity("Relais_Modul1_R1_MinDrehzahl", "number")
        assert (
            registry.async_get(pump).device_id
            == registry.async_get(em_sensor).device_id
        )
        await set_mode(em, "Off")
        assert hass.states.get(pump).state == "unavailable"
        await set_mode(em, "Auto")
        assert float(hass.states.get(pump).state) == 40
        assert Peer.saves == 11
        assert float(hass.states.get(sensor).state) == 42

        # The native Number remains the same entity as the active display
        # unit changes. Write the parent only and verify reconnect/persistence.
        name = COMPOUND_NAMES[0]
        compound = entity(name, "number")
        assert compound
        assert float(hass.states.get(compound).state) == 10
        await number(name, 12)
        assert Peer.settings[6914] == 60 and Peer.saves == 12
        await set_mode(display, "°C")
        assert entity(name, "number") == compound
        assert float(hass.states.get(compound).state) == 6
        assert hass.states.get(compound).attributes["unit_of_measurement"] == "K"
        await number(name, 7)
        assert Peer.settings[6914] == 70 and Peer.saves == 14
        await set_mode(display, "°F")
        assert entity(name, "number") == compound
        assert float(hass.states.get(compound).state) == 14
        assert hass.states.get(compound).attributes["unit_of_measurement"] == "°R"
        assert Peer.saves == 15

        # Stop the actual HA process with an incomplete active capture. The
        # following restart phase must restore the same committed checkpoint.
        with (
            patch(
                "custom_components.deltasol.load_catalog", return_value=config_fixture
            ),
            patch("custom_components.deltasol.discovery.BATCH_SIZE", 1),
            patch("custom_components.deltasol.DISCOVERY_BATCH_GAP_SECONDS", 3),
        ):
            await hass.services.async_call(
                "button", "press", {"entity_id": button}, blocking=True
            )
            capture = entry.runtime_data.capture
            async with asyncio.timeout(20):
                while capture.offset < 1:
                    await asyncio.sleep(0.05)
            assert capture.status == "running" and capture.offset == 1
            marker = {"started_at": capture.started_at, "offset": capture.offset}
            (directory / "qualification-active.json").write_text(
                json.dumps(marker), encoding="utf-8"
            )
            await hass.async_stop(force=True)
            stopped = True
        checkpoint = json.loads(
            (directory / ".storage" / f"deltasol.{entry.entry_id}.discovery").read_text(
                encoding="utf-8"
            )
        )["data"]
        assert checkpoint["resume"] is True
        assert checkpoint["capture"]["started_at"] == marker["started_at"]
        assert 1 <= checkpoint["capture"]["offset"] < expected_config_count
    finally:
        if not stopped:
            await hass.async_stop(force=True)


def main():
    directory = Path(sys.argv[1])
    phase = sys.argv[2]
    additional_relays()
    additional_configuration()
    Peer.persistent_file = directory / "qualification-peer-settings.json"
    if Peer.persistent_file.exists():
        Peer.settings = {
            int(index): value
            for index, value in json.loads(
                Peer.persistent_file.read_text(encoding="utf-8")
            ).items()
        }
    with (
        ThreadingHTTPServer(("127.0.0.1", 18080), HttpHandler) as http,
        VBusServer(("127.0.0.1", 7053), VBusHandler) as vbus,
    ):
        for server in (http, vbus):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        asyncio.run(qualify(directory, phase))
    print(f"ISOLATED_HA {phase} PASS (controlled loopback protocol peer)")


if __name__ == "__main__":
    main()
