"""Real HA lifecycle test with an independent loopback VBus protocol peer.

The peer models all ten local and registered EM relay capabilities.
It is an independent protocol test, not physical RESOL evidence.
"""

import ast
import asyncio
import json
import shutil
import socketserver
import struct
import sys
import threading
from contextlib import nullcontext
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
HQM_SENSOR_UID = "00_0010_7131_0100__004_4_0"

# Independent wire fixtures: exact 2.08 reference positions, not imported
# production Setting objects. Persistent state belongs to the protocol peer.
CONFIG_SELECTORS = {
    "Anlage_Wf1_Type": (6559, 2),
    "Anlage_Wf1_Aktiviert_Modus": (6566, 1),
    "Anlage_Wf2_Type": (6810, 6),
    "Anlage_Wf2_Aktiviert_Modus": (6817, 1),
    "Anlage_Wf3_Type": (7061, 11),
    "Anlage_Wf3_Aktiviert_Modus": (7068, 1),
    "Anlage_Wf3_Sensor1_Nr": (7078, 1),
    "Anlage_Wf3_Sensor2_Nr": (7082, 2),
    "Anlage_Wf3_Sensor3_Nr": (7086, 3),
    "Anlage_Wf3_Relais1_Nr": (7094, 1),
    "Anlage_Wf3_Relais2_Nr": (7102, 2),
    "Heizung_Heizkreis1_Type": (13684, 1),
    "Heizung_Heizkreis1_Aktiviert_Modus": (13689, 1),
    "Heizung_Heizkreis1_Heizsystem": (13978, 1),
    "Wmz1_Type": (17260, 1),
    "Wmz1_Aktiviert_Modus": (17263, 1),
    "Relais_Modul1_R1_AktorFunktion": (2167, 2),
    "Solar_Relais1_Nr": (3452, 1),
    "Solar_Kol1_Sensor1_Nr": (3536, 1),
    "Solar_Sp1_Sensor1_Nr": (3576, 2),
    "Solar_Sp1_Aktiviert_Modus": (3604, 1),
    "Solar_Sp2_Sensor1_Nr": (3612, 3),
    "Solar_Sp2_Aktiviert_Modus": (3640, 1),
    "Solar_Beladungstyp": (3417, 0),
    "Solar_OPausendrehzahl": (3420, 1),
    "Solar_OPumpenverzoegerung": (3422, 1),
    "Heizung_Wf1_Type": (11266, 4),
    "Heizung_Wf1_Aktiviert_Modus": (11273, 1),
    "Heizung_Wf1_Zirkulation_Variante": (11405, 0),
}
CONFIG_SETTINGS = {
    "Anlage_Wf1_Mischer_dtIntervall": (6625, 4, 1, 20),
    "Heizung_Heizkreis1_Heizkurve": (13698, 12, 3, 30),
    "Wmz1_OAlternativAnzeige": (17273, 0, 0, 1),
    "Wmz1_AlternativAnzeigeEinheit": (17274, 0, 0, 3),
    "Wmz1_AlternativAnzeigeFaktor": (17275, 5000000, 1, 1000000000),
    "Relais_Regler_R1_MinDrehzahl": (1974, 30, 20, 100),
    "Relais_Regler_R1_MaxDrehzahl": (1975, 80, 20, 100),
    "Relais_Modul1_R1_MinDrehzahl": (2170, 30, 20, 100),
    "Relais_Modul1_R1_MaxDrehzahl": (2171, 80, 20, 100),
    "Anlage_Wf2_Feststoffkessel_dTEin": (6914, 50, 20, 300),
    "Anlage_Wf2_Feststoffkessel_dTAus": (6917, 30, 10, 290),
    "Anlage_Wf2_Feststoffkessel_dTSoll": (6920, 100, 30, 400),
    "Anlage_Wf2_Feststoffkessel_TMax": (6923, 800, 40, 950),
    "Anlage_Wf2_Feststoffkessel_TMin": (6926, 600, 40, 950),
    "Anlage_Wf3_RLBeimischung_dTEin": (7261, 50, 10, 250),
    "Anlage_Wf3_RLBeimischung_dTAus": (7264, 30, 5, 240),
    "Anlage_Wf3_RLBeimischung_dTSoll": (7267, 70, -200, 250),
    "Anlage_Wf3_RLBeimischung_Mischerlaufzeit": (7273, 105, 10, 600),
    "Anlage_Wf3_RLBeimischung_Mischerstellzeit": (7274, 2, 1, 20),
    "Solar_Sp1_dTein": (3579, 60, 10, 200),
    "Solar_Sp1_dTaus": (3582, 40, 5, 195),
    "Solar_Sp1_dTsoll": (3585, 100, 15, 300),
    "Solar_Pendelpause": (3418, 2, 1, 5),
    "Solar_Umwaelzzeit": (3419, 15, 1, 60),
    "Solar_Pausendrehzahl": (3421, 30, 20, 100),
    "Solar_Pumpenverzoegerung": (3423, 15, 5, 600),
    "Heizung_Wf1_Zirkulation_TEin": (11408, 400, 100, 590),
    "Heizung_Wf1_Zirkulation_TAus": (11411, 600, 110, 600),
}
# This peer deliberately models the selected-unit parent contract. It is
# independent software evidence, not a physical observation of this encoding.
COMPOUND_NAMES = (
    "Anlage_Wf2_Feststoffkessel_dTEin",
    "Anlage_Wf2_Feststoffkessel_dTAus",
    "Anlage_Wf2_Feststoffkessel_dTSoll",
    "Solar_Sp1_dTein",
    "Solar_Sp1_dTaus",
    "Solar_Sp1_dTsoll",
    "Anlage_Wf3_RLBeimischung_dTEin",
    "Anlage_Wf3_RLBeimischung_dTAus",
    "Anlage_Wf3_RLBeimischung_dTSoll",
)
COMPOUND_ALIASES = {
    name + suffix: (CONFIG_SETTINGS[name][0] + delta, CONFIG_SETTINGS[name][0], factor)
    for name in COMPOUND_NAMES
    for suffix, delta, factor in (("_K", 1, 1), ("_R", 2, 2))
}
COMPOUND_PARENTS = {CONFIG_SETTINGS[name][0] for name in COMPOUND_NAMES}
TEMPERATURE_NAMES = (
    "Anlage_Wf2_Feststoffkessel_TMax",
    "Anlage_Wf2_Feststoffkessel_TMin",
    "Heizung_Wf1_Zirkulation_TEin",
    "Heizung_Wf1_Zirkulation_TAus",
)
TEMPERATURE_PARENTS = {CONFIG_SETTINGS[name][0] for name in TEMPERATURE_NAMES}
TEMPERATURE_ALIASES = {
    name + suffix: (CONFIG_SETTINGS[name][0] + delta, CONFIG_SETTINGS[name][0], delta)
    for name in TEMPERATURE_NAMES
    for suffix, delta in (("_C", 1), ("_F", 2))
}


def temperature_wire(celsius_raw, fahrenheit):
    # Test values use exactly representable temperatures in both unit variants.
    # This is a peer fixture, not a claim about physical vendor rounding.
    return celsius_raw * 9 // 5 + 320 if fahrenheit else celsius_raw


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
    release_offer_after_write = False
    release_offer_count = 0
    redundant_release_commands = 0
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


# Independent fixture: exact BX Plus 2.08 catalogue indices and native SET
# byte evidence. The peer models the candidate word order, not proof of a
# physical controller's multiword mapping or persistence.
TIMER_VALUES = {
    "Wsu1_Belegt": (18414, 1),
    "Wsu1_Schaltuhr_Woche_Initialisiert": (18418, 1),
    "Wsu1_Schaltuhr_Woche_TagMo_Daten": (18419, 0x50482820),
    "Wsu1_Schaltuhr_Woche_TagDi_Daten": (18420, -48326),
    "Anlage_Wf1_SchaltuhrNr": (6620, 1),
}


def additional_configuration():
    for name, (index, value) in CONFIG_SELECTORS.items():
        Peer.extra_lookups[semantic_hash(name)] = index
        Peer.extra_values[index] = value
    for name, (index, _, _, _) in CONFIG_SETTINGS.items():
        Peer.extra_lookups[semantic_hash(name)] = index
    for name, (index, _, _) in {**COMPOUND_ALIASES, **TEMPERATURE_ALIASES}.items():
        Peer.extra_lookups[semantic_hash(name)] = index
    for name, (index, value) in TIMER_VALUES.items():
        Peer.extra_lookups[semantic_hash(name)] = index
        Peer.extra_values[index] = value


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
        relay_changed = False
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
                    **{
                        parent: temperature_wire(
                            Peer.settings[parent], Peer.settings[60]
                        )
                        for parent in TEMPERATURE_PARENTS
                    },
                    **{
                        alias: temperature_wire(Peer.settings[parent], delta == 2)
                        for alias, parent, delta in TEMPERATURE_ALIASES.values()
                    },
                }[index]
                response = datagram(source, ADDRESS, 0x0100, index, value)
            elif command in (0x0301, 0x0302):
                assert index in (18419, 18420)
                subindex = command & 0xFF
                if index == 18420 and subindex == 2:
                    response = datagram(source, ADDRESS, 0x4302, index)
                else:
                    response = datagram(source, ADDRESS, 0x0100 | subindex, index, -1)
            elif command == 0x0200:
                if index == RELAY:
                    assert value in (0, 2, 4)
                    Peer.mode = value
                else:
                    assert index == EM_RELAY and Peer.em_present
                    assert value in (0, 1, 2, 3)
                    Peer.em_mode = value
                Peer.writes += 1
                relay_changed = True
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
                    step = 5 if index in (3579, 3582, 3585, 7261, 7264) else 10
                    assert stored_value % step == 0
                    proposed = {
                        parent: stored_value
                        if parent == index
                        else Peer.settings[parent]
                        for parent in COMPOUND_PARENTS
                    }
                    if index in (3579, 3582, 3585):
                        assert Peer.extra_values[3452] > 0
                        assert Peer.extra_values[3536] > 0
                        assert Peer.extra_values[3576] > 0
                        assert Peer.extra_values[3604] == 1
                        assert proposed[3582] < proposed[3579] < proposed[3585]
                    elif index in (7261, 7264, 7267):
                        assert Peer.extra_values[7061] == 11
                        assert Peer.extra_values[7068] == 1
                        assert all(
                            Peer.extra_values[i] > 0
                            for i in (7078, 7082, 7086, 7094, 7102)
                        )
                        assert proposed[7261] > proposed[7264]
                    else:
                        assert proposed[6917] < proposed[6914] < proposed[6920]
                if index in TEMPERATURE_PARENTS:
                    if index in (11408, 11411):
                        assert Peer.extra_values[11266] == 4
                        assert Peer.extra_values[11273] == 1
                        assert Peer.extra_values[11405] == 0
                    else:
                        assert Peer.extra_values[6810] == 6
                        assert Peer.extra_values[6817] == 1
                    if Peer.settings[60]:
                        assert (value - 320) * 5 % 9 == 0
                        stored_value = (value - 320) * 5 // 9
                    assert stored_value % 10 == 0
                    if index == 11408:
                        assert stored_value < Peer.settings[11411]
                    elif index == 11411:
                        assert stored_value > Peer.settings[11408]
                assert low <= stored_value <= high
                # Peer independently verifies installed program constraints.
                if index in (7273, 7274):
                    assert Peer.extra_values[7061] == 11
                    assert Peer.extra_values[7068] == 1
                    assert all(
                        Peer.extra_values[i] > 0 for i in (7078, 7082, 7086, 7094, 7102)
                    )
                if index in (1974, 1975):
                    assert Peer.extra_values[1977] == 2
                    assert Peer.extra_values[1973] == 1
                    assert (
                        value <= Peer.settings[1975]
                        if index == 1974
                        else value >= Peer.settings[1974]
                    )
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
                if index in (3418, 3419, 3421, 3423):
                    assert Peer.extra_values[3452] > 0
                    assert Peer.extra_values[3536] > 0
                    assert Peer.extra_values[3576] > 0
                    assert Peer.extra_values[3604] == 1
                    if index == 3423:
                        assert Peer.extra_values[3422] == 1
                        assert value % 5 == 0
                    else:
                        assert Peer.extra_values[3612] > 0
                        assert Peer.extra_values[3640] == 1
                        assert Peer.extra_values[3417] == 0
                        if index == 3421:
                            assert Peer.extra_values[3420] == 1
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
                if Peer.release_offer_after_write and relay_changed:
                    # Regression: after a real write the master resumes with
                    # a fresh offer rather than a VBus 1.0 live-data packet.
                    packet = datagram(0, ADDRESS, 0x0500)
                    self.wfile.write(packet[:9])
                    threading.Event().wait(0.03)
                    self.wfile.write(packet[9:])
                    Peer.release_offer_count += 1
                    # The client must not send another command on this
                    # released session. It must close and acquire afresh.
                    self.connection.settimeout(3)
                    if self.rfile.read(16):
                        Peer.redundant_release_commands += 1
                    return
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
            result["headers"].append(
                {
                    "id": "00_0010_7131_0100",
                    "description": "HQM #1",
                    "destination_name": "Broadcast",
                    "source_name": "DeltaSol BX Plus [HQM #1]",
                    "fields": [{"id": "004_4_0", "name": "Power", "unit": "W"}],
                }
            )
            result["headersets"][0]["packets"].append(
                {"field_values": [{"raw_value": 1000.0}]}
            )
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
    # Load the installed candidate's own catalogue types from the actual
    # isolated HA config directory, before entry setup can start Discovery.
    sys.path.insert(0, str(directory))
    from custom_components.deltasol.catalog import Catalog, CatalogError, Definition

    # Restrict only the independent peer's long discovery workload. The
    # candidate still loads the complete, checksum-verified vendor archive
    # and binds all runtime contracts against its original definitions.
    # The separate 3,200-item soak and exact-archive source test retain
    # large-inventory coverage. This is not a full physical catalogue scan.
    qualification_names = {
        "Schema",
        "Relais_Regler_R4_Handbetrieb",
        "AutoSommerWinterzeit",
        "TemperatureHysteresisSelector",
        "FlowSelector",
        "PressureSelector",
        "EnergySelector",
        *CONFIG_SELECTORS,
        *CONFIG_SETTINGS,
        *COMPOUND_ALIASES,
        *TEMPERATURE_ALIASES,
        *TIMER_VALUES,
    }

    def assert_weekly_evidence(diagnostic):
        weekly = diagnostic["configuration_discovery"]["weekly_programs"]
        assert weekly["write_access"] == "not_verified"
        week = weekly["programs"]["Wsu1"]
        assert week["allocated"] and week["initialized"]
        monday = week["days"]["Mo"]
        assert monday["observed_words"] == [0x50482820, -1, -1]
        assert monday["candidate_status"] == "wire_word_mapping_unverified"
        assert not monday["payload_verified"]
        assert monday["candidate_windows"] == [
            {"start_minute": 480, "end_minute": 600},
            {"start_minute": 1080, "end_minute": 1200},
        ]
        tuesday = week["days"]["Di"]
        assert tuesday["observed_words"] == [-48326, -1, None]
        assert "candidate_windows" not in tuesday
        assert not tuesday["payload_verified"]
        assert (
            weekly["references"]["Anlage_Wf1_SchaltuhrNr"]["candidate_program"]
            == "Wsu1"
        )

    candidate_filter = nullcontext()
    if phase in ("upgrade", "restart"):
        from custom_components.deltasol.discovery import ConfigurationCapture

        original_base = ConfigurationCapture.base_candidates

        def qualification_base(capture):
            base = original_base.__get__(capture, type(capture))
            if capture.catalog.matches(ADDRESS, CHANGESET):
                return tuple(d for d in base if d.name in qualification_names)
            return base

        candidate_filter = patch.object(
            ConfigurationCapture, "base_candidates", property(qualification_base)
        )
    candidate_filter.__enter__()

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
    if phase == "upgrade":
        initial_catalog = patch(
            "custom_components.deltasol.load_catalog", return_value=fixture
        )
    elif phase == "restart":
        # A temporarily unusable catalogue must not overwrite the checkpoint.
        # The existing reload test below then restores the verified fixture.
        initial_catalog = patch(
            "custom_components.deltasol.load_catalog",
            side_effect=CatalogError("Qualification catalogue unavailable"),
        )
    else:
        initial_catalog = nullcontext()
    stopped = False
    try:
        with initial_catalog:
            assert await bootstrap.async_from_config_dict(config, hass) is hass
            await hass.async_start()
            await hass.async_block_till_done()
            if phase == "upgrade":
                (initial_entry,) = hass.config_entries.async_entries("deltasol")
                initial = initial_entry.runtime_data
                assert initial.auto_start_task is not None
                await initial.auto_start_task
                assert initial.capture_task is not None
                await initial.capture_task
                assert initial.capture.status == "complete"
                assert initial.capture.offset == 3 and Peer.writes == 0
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
        hqm_sensor = registry.async_get_entity_id("sensor", "deltasol", HQM_SENSOR_UID)
        assert hqm_sensor and float(hass.states.get(hqm_sensor).state) == 1000
        identity["hqm_sensor"] = hqm_sensor
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

        expected_config_count = len(qualification_names)
        button = registry.async_get_entity_id(
            "button", "deltasol", f"{entry.entry_id}_configuration_discovery"
        )
        assert button
        progress = registry.async_get_entity_id(
            "sensor", "deltasol", f"{entry.entry_id}_configuration_discovery_status"
        )
        expected_status = "complete" if phase == "upgrade" else "not_started"
        assert progress and hass.states.get(progress).state == expected_status
        percent = registry.async_get_entity_id(
            "sensor", "deltasol", f"{entry.entry_id}_configuration_discovery_progress"
        )
        assert percent and float(hass.states.get(percent).state) == (
            100 if phase == "upgrade" else 0
        )
        assert hass.states.get(percent).attributes["unit_of_measurement"] == "%"
        assert (
            registry.async_get(percent).device_id
            == registry.async_get(progress).device_id
        )
        for entity_id in (button, progress, percent):
            assert registry.async_get(entity_id).entity_category == "diagnostic"
        assert diagnostic["configuration_discovery"]["status"] == expected_status
        if phase == "restart":
            assert (
                diagnostic["configuration_discovery"]["checkpoint_error"]
                == "checkpoint_incompatible"
            )
        with (
            nullcontext()
            if phase == "restart"
            else patch("custom_components.deltasol.load_catalog", return_value=fixture)
        ):
            if phase == "restart":
                # Restore through the real local archive loader, not a mock.
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
                assert resumed.catalog.matches(ADDRESS, CHANGESET)
                assert {d.name for d in resumed.candidates} == qualification_names
                assert_weekly_evidence(
                    await async_get_config_entry_diagnostics(hass, entry)
                )
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
                    ("Relais_Regler_R1_MinDrehzahl", "number", 40),
                    ("Relais_Regler_R1_MaxDrehzahl", "number", 70),
                    ("Anlage_Wf2_Feststoffkessel_TMin", "number", 158),
                    ("Anlage_Wf2_Feststoffkessel_TMax", "number", 194),
                    ("Solar_Sp1_dTein", "number", 14),
                    ("Heizung_Wf1_Zirkulation_TEin", "number", 122),
                    ("Solar_Pendelpause", "number", 3),
                    ("Solar_Umwaelzzeit", "number", 20),
                    ("Solar_Pausendrehzahl", "number", 40),
                    ("Solar_Pumpenverzoegerung", "number", 25),
                    ("Anlage_Wf3_RLBeimischung_dTEin", "number", 11),
                    ("Anlage_Wf3_RLBeimischung_dTSoll", "number", -20),
                    ("Anlage_Wf3_RLBeimischung_Mischerlaufzeit", "number", 120),
                    ("Anlage_Wf3_RLBeimischung_Mischerstellzeit", "number", 5),
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
        Peer.release_offer_after_write = True
        try:
            for option, expected in (("Off", 0), ("On", 4), ("Auto", 2)):
                await set_mode(select, option)
                assert Peer.mode == expected
                assert hass.states.get(select).state == option
                assert hass.states.get(em).state == "Auto"
            for option, expected in (("Min", 1), ("Max", 3), ("Off", 0), ("Auto", 2)):
                await set_mode(em, option)
                assert Peer.em_mode == expected
                assert Peer.mode == 2
                assert hass.states.get(em).state == option
                assert hass.states.get(select).state == "Auto"
            assert Peer.release_offer_count == 7
            assert Peer.redundant_release_commands == 0
        finally:
            Peer.release_offer_after_write = False
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

            # An intentional pause must survive a reload without auto-start.
            active = await start_held_capture()
            started_at = active.started_at
            try:
                await hass.services.async_call(
                    "button", "press", {"entity_id": button}, blocking=True
                )
            finally:
                Peer.offer_release.set()
            await entry.runtime_data.capture_task
            assert active.status == "paused" and 0 < active.offset < 3
            assert await hass.config_entries.async_reload(entry.entry_id)
            paused = entry.runtime_data.capture
            assert paused.started_at == started_at and paused.offset == active.offset
            assert paused.status == "paused"
            assert entry.runtime_data.capture_task is None
            assert entry.runtime_data.auto_start_task is None
            await hass.services.async_call(
                "button", "press", {"entity_id": button}, blocking=True
            )
            await entry.runtime_data.capture_task
            assert paused.status == "complete" and paused.offset == 3

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
        archive = directory / "deltasol" / "BXPLUS_2.08.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile("/reference/BXPLUS_2.08.zip", archive)
        await hass.services.async_call(
            "button", "press", {"entity_id": button}, blocking=True
        )
        await entry.runtime_data.capture_task
        capture = entry.runtime_data.capture
        assert capture.offset == expected_config_count
        assert {d.name for d in capture.candidates} == qualification_names
        assert capture.catalog.matches(ADDRESS, CHANGESET)
        assert len(capture.catalog.definitions) == 15680
        await entry.runtime_data.relay_coordinator.async_refresh()
        await hass.async_block_till_done()
        diagnostic = await async_get_config_entry_diagnostics(hass, entry)
        assert (
            diagnostic["configuration_discovery"]["catalogue_source"]["compatibility"]
            == "verified"
        )
        assert_weekly_evidence(diagnostic)
        bound = entry.runtime_data.relay_coordinator.data.configuration
        assert any(cap.setting.value_definition is not None for cap in bound.values())

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
        mixer_device = registry.async_get(
            entity("Anlage_Wf1_Mischer_dtIntervall", "number")
        ).device_id
        assert mixer_device != registry.async_get(sensor).device_id
        from homeassistant.helpers import device_registry as dr

        mixer = dr.async_get(hass).async_get(mixer_device)
        assert mixer.via_device_id == registry.async_get(sensor).device_id
        assert mixer.name == "RESOL Arrangement function 1"
        await number("Heizung_Heizkreis1_Heizkurve", 1.5)
        alternative = entity("Wmz1_OAlternativAnzeige", "switch")
        assert alternative and hass.states.get(alternative).state == "off"
        assert (
            registry.async_get(alternative).device_id
            == registry.async_get(hqm_sensor).device_id
        )
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

        # The local relay has no resolved volatile role. Native speed control
        # and exact Auto/registration/readback still authorize its own bounds.
        await number("Relais_Regler_R1_MinDrehzahl", 40)
        await number("Relais_Regler_R1_MaxDrehzahl", 70)
        assert Peer.settings[1974] == 40 and Peer.settings[1975] == 70
        assert Peer.saves == 17
        await set_mode(display, "°C")
        minimum, maximum = TEMPERATURE_NAMES[1], TEMPERATURE_NAMES[0]
        minimum_entity = entity(minimum, "number")
        assert float(hass.states.get(minimum_entity).state) == 60
        await number(minimum, 70)
        await number(maximum, 90)
        assert Peer.settings[6926] == 700 and Peer.settings[6923] == 900
        await set_mode(display, "°F")
        assert entity(minimum, "number") == minimum_entity
        assert float(hass.states.get(minimum_entity).state) == 158
        assert float(hass.states.get(entity(maximum, "number")).state) == 194
        assert Peer.saves == 21

        # Solar settings require the observed native store, collector and output.
        # Removing a native assignment cannot leave a writable stale control.
        solar_name = "Solar_Sp1_dTein"
        solar = entity(solar_name, "number")
        assert solar and float(hass.states.get(solar).state) == 12
        await number(solar_name, 14)
        assert Peer.settings[3579] == 70 and Peer.saves == 22
        Peer.extra_values[3452] = 0
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert hass.states.get(solar).state == "unavailable"
        Peer.extra_values[3452] = 1
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert entity(solar_name, "number") == solar
        assert float(hass.states.get(solar).state) == 14

        # Thermally controlled circulation has its own selected-unit pair.
        # Switching to timer-only removes the temperature controls.
        name = "Heizung_Wf1_Zirkulation_TEin"
        circulation = entity(name, "number")
        assert circulation and float(hass.states.get(circulation).state) == 104
        await number(name, 122)
        assert Peer.settings[11408] == 500 and Peer.saves == 23
        Peer.extra_values[11405] = 1
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert hass.states.get(circulation).state == "unavailable"
        Peer.extra_values[11405] = 0
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert entity(name, "number") == circulation
        assert float(hass.states.get(circulation).state) == 122

        # Native solar sequence settings remain tied to live topology/flags.
        for name, value in (
            ("Solar_Pendelpause", 3),
            ("Solar_Umwaelzzeit", 20),
            ("Solar_Pausendrehzahl", 40),
            ("Solar_Pumpenverzoegerung", 25),
        ):
            await number(name, value)
        assert Peer.saves == 27
        sequence = entity("Solar_Pendelpause", "number")
        Peer.extra_values[3612] = 0
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert hass.states.get(sequence).state == "unavailable"
        Peer.extra_values[3612] = 3
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert entity("Solar_Pendelpause", "number") == sequence
        assert float(hass.states.get(sequence).state) == 3
        assert Peer.saves == 27

        # Return mixing uses the selected-unit parent and live assignments.
        mixing = "Anlage_Wf3_RLBeimischung_dTEin"
        target = entity(mixing, "number")
        assert target and float(hass.states.get(target).state) == 10
        await number(mixing, 11)
        assert Peer.settings[7261] == 55 and Peer.saves == 28
        try:
            await number(mixing, 6)
        except HomeAssistantError:
            pass
        else:
            raise AssertionError("Invalid return mixing pair accepted")
        Peer.extra_values[7102] = 0
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert hass.states.get(target).state == "unavailable"
        Peer.extra_values[7102] = 2
        await entry.runtime_data.relay_coordinator.async_refresh()
        assert entity(mixing, "number") == target
        assert float(hass.states.get(target).state) == 11
        assert Peer.saves == 28
        for suffix, value in (
            ("dTSoll", -20),
            ("Mischerlaufzeit", 120),
            ("Mischerstellzeit", 5),
        ):
            await number(f"Anlage_Wf3_RLBeimischung_{suffix}", value)
        assert Peer.settings[7267] == -100 and Peer.settings[7273] == 120
        assert Peer.settings[7274] == 5 and Peer.saves == 31

        # Stop the actual HA process with an incomplete active capture. The
        # following restart phase must restore the same committed checkpoint.
        with (
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
        try:
            if not stopped:
                await hass.async_stop(force=True)
        finally:
            candidate_filter.__exit__(None, None, None)


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
