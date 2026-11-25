"""Real HA lifecycle test with an independent loopback VBus protocol peer.

The peer models R4 and an independently registered EM relay. This is not physical RESOL evidence.
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
from unittest.mock import patch

from homeassistant import bootstrap, loader
from homeassistant.config_entries import ConfigEntryState
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


class Peer:
    """Network-side test state; never connect to site devices."""

    mode = 4
    em_mode = 2
    em_present = True
    deny = False
    writes = 0


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
        self.wfile.write(b"+OK\r\n" + datagram(0, ADDRESS, 0x0500))
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
                index = {
                    1222011281: 86,
                    463603356: RELAY,
                    530579973: 2015,
                    1121009828: 191,
                    1682631798: 193,
                    1215980970: EM_RELAY,
                    152651155: 2169,
                }.get(value, 0)
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
                response = datagram(source, ADDRESS, 0x0100, index, value)
            elif command == 0x0600:
                header = bytes.fromhex("1000127110000100")
                self.wfile.write(
                    bytes([170]) + header + bytes([127 - (sum(header) & 127)])
                )
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

        diagnostic = await async_get_config_entry_diagnostics(hass, entry)
        encoded = json.dumps(diagnostic)
        for secret in ("local-test", "http-test", "127.0.0.1", "isolated-gateway"):
            assert secret not in encoded
        assert diagnostic["telemetry"]["available"]
        access = diagnostic["parameter_access"]
        assert access["status"] == "supported"
        assert access["relays"]["R4"]["mode"] == "On"
        assert access["relays"]["R4"]["mode_value"] == 4
        assert access["relays"]["R1"]["writable_options"] == []
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
            ),
        )
        button = registry.async_get_entity_id(
            "button", "deltasol", f"{entry.entry_id}_configuration_discovery"
        )
        assert button
        assert diagnostic["configuration_discovery"]["status"] == "not_started"
        with patch("custom_components.deltasol.load_catalog", return_value=fixture):
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
            assert capture["scanned_count"] == 0
            assert Peer.writes == 0
            Peer.deny = False
            await hass.services.async_call(
                "button", "press", {"entity_id": button}, blocking=True
            )
            await entry.runtime_data.capture_task
            capture = (await async_get_config_entry_diagnostics(hass, entry))[
                "configuration_discovery"
            ]
            assert capture["status"] == "complete", capture
            assert capture["scanned_count"] == 3
            assert capture["parameters"]["Schema"]["decoded_value"] == 1
            assert capture["parameters"]["Missing"]["status"] == "not_resolved"
            assert "Password" not in capture["parameters"]
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
        select_file = directory / "qualification-select.json"
        if phase == "restart":
            assert em == json.loads(select_file.read_text(encoding="utf-8"))
            return
        select_file.write_text(json.dumps(em), encoding="utf-8")

        async def set_mode(entity_id, option):
            await hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": entity_id, "option": option},
                blocking=True,
            )
            await hass.async_block_till_done()

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
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert registry.async_get_entity_id("select", "deltasol", SELECT_UID) == select
        assert hass.states.get(select).state == "Auto"
        assert float(hass.states.get(sensor).state) == 42
        assert Peer.writes == 7
    finally:
        await hass.async_stop(force=True)


def main():
    directory = Path(sys.argv[1])
    phase = sys.argv[2]
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
