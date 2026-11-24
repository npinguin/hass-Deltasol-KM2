"""Test the real VBus protocol implementation without physical equipment."""

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

SOURCE = Path(__file__).resolve().parents[1] / "custom_components/deltasol/vbus.py"
SPEC = importlib.util.spec_from_file_location("deltasol_vbus_test", SOURCE)
vbus = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = vbus
SPEC.loader.exec_module(vbus)

ADDRESS = 0x7112
NAME = "Relais_Regler_R4_Handbetrieb"


def response(command, index=0, value=0, source=ADDRESS):
    return vbus.Datagram(0x20, source, command, index, value).encode()


def live_packet():
    header = bytes.fromhex("1000127110000100")
    return b"\\xaa" + header + bytes([127 - (sum(header) & 127)])


class VBusTests(unittest.TestCase):
    def test_reference_datagram(self):
        packet = vbus.Datagram(0x2336, 0x3335, 0x4334, 0x5333, 0x63328330)
        raw = bytes.fromhex("aa362335332034433353300332630851")
        self.assertEqual(packet.encode(), raw)
        self.assertEqual(vbus.Datagram.decode(raw), packet)

    def test_corrupt_frame_rejected(self):
        raw = bytearray(response(0x0100, 1321, 2))
        raw[-1] ^= 1
        with self.assertRaises(vbus.VBusError):
            vbus.Datagram.decode(raw)

    def test_discovery_is_read_only_and_skips_unsupported(self):
        conn = Mock()
        unsupported = "Relais_Regler_R99_Handbetrieb"
        stream = b"".join(
            (
                response(0x0500, source=0x7721),
                response(0x0500),
                response(0x0100, 0, 12345),
                response(0x0100, 1321, vbus.parameter_hash(NAME)),
                response(0x1101, 0, vbus.parameter_hash(unsupported)),
                response(0x0100, 0, 12345),
                response(0x0100, 1321, 2),
                live_packet(),
            )
        )
        conn.recv.side_effect = [stream[:19], stream[19:]]
        session = vbus.VBusSession(conn)
        self.assertEqual(
            session.discover(ADDRESS, [NAME, unsupported]),
            (12345, {NAME: (1321, 2)}),
        )
        sent = [
            vbus.Datagram.decode(call.args[0])
            for call in conn.sendall.call_args_list
        ]
        self.assertEqual(
            [p.command for p in sent],
            [0x0300, 0x1100, 0x1100, 0x0300, 0x0300, 0x0600],
        )
        self.assertTrue(all(p.destination == ADDRESS for p in sent))

    def test_discovery_releases_bus_on_failure(self):
        conn = Mock()
        conn.recv.side_effect = [response(0x0500), b"", live_packet()]
        session = vbus.VBusSession(conn)
        with self.assertRaises(vbus.VBusError):
            session.discover(ADDRESS, [NAME])
        packet = vbus.Datagram.decode(conn.sendall.call_args.args[0])
        self.assertEqual(packet.command, 0x0600)

    def test_authentication_preserves_buffered_bus_data(self):
        conn = Mock()
        conn.recv.side_effect = [
            b"+HELLO\r\n",
            b"+OK\r\n",
            b"+DATA\r\n" + response(0x0500),
            live_packet(),
        ]
        session = vbus.VBusSession(conn)
        session.authenticate("test")
        with session.bus(ADDRESS):
            pass
        self.assertEqual(conn.sendall.call_args_list[0].args[0], b"PASS test\r\n")
        self.assertEqual(conn.sendall.call_args_list[1].args[0], b"DATA\r\n")

    def test_authentication_rejection(self):
        conn = Mock()
        conn.recv.side_effect = [b"+HELLO\r\n", b"-DENIED\r\n"]
        with self.assertRaises(vbus.VBusError):
            vbus.VBusSession(conn).authenticate("test")
        self.assertEqual(conn.sendall.call_count, 1)

    def test_write_requires_independent_readback_and_is_not_retried(self):
        conn = Mock()
        conn.recv.return_value = (
            response(0x0100, 1321, 2)
            + response(0x0100, 0, 12345)
            + response(0x0100, 1321, 0)
        )
        session = vbus.VBusSession(conn)
        with self.assertRaises(vbus.VBusError):
            session.write_verified(ADDRESS, 1321, 2, 12345)
        sent = [
            vbus.Datagram.decode(call.args[0])
            for call in conn.sendall.call_args_list
        ]
        self.assertEqual([p.command for p in sent], [0x0200, 0x0300, 0x0300])

    def test_write_readback_success(self):
        conn = Mock()
        conn.recv.return_value = (
            response(0x0100, 1321, 2)
            + response(0x0100, 0, 12345)
            + response(0x0100, 1321, 2)
        )
        session = vbus.VBusSession(conn)
        self.assertEqual(session.write_verified(ADDRESS, 1321, 2, 12345), 2)
