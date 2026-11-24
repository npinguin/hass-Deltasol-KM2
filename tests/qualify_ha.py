"""Real HA lifecycle test with an independent loopback VBus protocol peer.

The peer models one R4 parameter. This is not physical RESOL evidence.
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


class Peer:
    """Network-side test state; never connect to site devices."""

    mode = 2
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
                index = {1222011281: 86, 463603356: RELAY}.get(value, 0)
                response = datagram(source, ADDRESS, 0x1101, index, value)
            elif command == 0x0300:
                value = {0: CHANGESET, 86: 1, RELAY: Peer.mode}[index]
                response = datagram(source, ADDRESS, 0x0100, index, value)
            elif command == 0x0200:
                assert index == RELAY and value in (0, 2, 3)
                Peer.mode = value
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
                            {"id": "000_2_0", "name": "Temperature S1", "unit": "°C"}
                        ],
                    }
                ],
                "headersets": [{"packets": [{"field_values": [{"raw_value": 42.0}]}]}],
            }
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
            result = await flow.async_configure(result["flow_id"], {"scan_interval": 60})
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
            baseline.write_text(json.dumps(identity), encoding="utf-8")
            assert Peer.writes == 0
            return
        assert json.loads(baseline.read_text(encoding="utf-8")) == identity
        if phase == "rollback":
            assert not any(
                state.attributes.get("options") == ["Auto", "Off", "On"]
                and state.state not in ("unavailable", "unknown")
                for state in hass.states.async_all("select")
            )
            assert Peer.writes == 0
            return

        if phase == "upgrade":
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
        assert select, "R4 native select was not discovered"
        assert hass.states.get(select).state == "Auto"
        assert Peer.writes == 0
        for number in (1, 2, 3, 5):
            assert not registry.async_get_entity_id(
                "select", "deltasol", f"isolated-gateway_7112_R{number}_mode"
            )
        select_file = directory / "qualification-select.json"
        if phase == "restart":
            assert select == json.loads(select_file.read_text(encoding="utf-8"))
            return
        select_file.write_text(json.dumps(select), encoding="utf-8")

        async def set_mode(option):
            await hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": select, "option": option},
                blocking=True,
            )
            await hass.async_block_till_done()

        for option, expected in (("Off", 0), ("On", 3), ("Auto", 2)):
            await set_mode(option)
            assert Peer.mode == expected
            assert hass.states.get(select).state == option
        assert Peer.writes == 3
        try:
            await set_mode("unsupported")
        except (HomeAssistantError, ValueError):
            pass
        else:
            raise AssertionError("Unsupported mode accepted")
        assert Peer.writes == 3
        Peer.deny = True
        try:
            await set_mode("Off")
        except HomeAssistantError:
            pass
        else:
            raise AssertionError("Authentication failure accepted")
        assert Peer.writes == 3
        assert hass.states.get(select).state == "unavailable"
        assert float(hass.states.get(sensor).state) == 42
        Peer.deny = False
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert registry.async_get_entity_id("select", "deltasol", SELECT_UID) == select
        assert hass.states.get(select).state == "Auto"
        assert float(hass.states.get(sensor).state) == 42
        assert Peer.writes == 3
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
