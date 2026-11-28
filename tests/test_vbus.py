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
    return b"\xaa" + header + bytes([127 - (sum(header) & 127)])


class VBusTests(unittest.TestCase):
    def test_reference_datagram(self):
        packet = vbus.Datagram(0x2336, 0x3335, 0x4334, 0x5333, 0x63328330)
        raw = bytes.fromhex("aa362335332034433353300332630851")
        self.assertEqual(packet.encode(), raw)
        self.assertEqual(vbus.Datagram.decode(raw), packet)

    def test_read_subindex_correlates_word_and_refusal(self):
        conn = Mock()
        conn.recv.return_value = (
            response(0x0100, 18419, 17)
            + response(0x0101, 18420, 18)
            + response(0x0101, 18419, -1)
            + response(0x4302, 18419)
        )
        session = vbus.VBusSession(conn)
        self.assertEqual(session.read(ADDRESS, 18419, subindex=1), -1)
        with self.assertRaises(vbus.VBusReadRefused):
            session.read(ADDRESS, 18419, subindex=2)
        for invalid in (-1, 128, True, 1.5):
            with self.assertRaises(vbus.VBusError):
                session.read(ADDRESS, 18419, subindex=invalid)
        sent = [vbus.Datagram.decode(c.args[0]) for c in conn.sendall.call_args_list]
        self.assertEqual([p.command for p in sent], [0x0301, 0x0302])
        self.assertEqual([p.index for p in sent], [18419, 18419])

    def test_packed_words_require_live_allocation_and_keep_refusal(self):
        names = {"Day": 10, "Allocated": 11, "Initialized": 12}
        for allocated, first_refused in ((0, False), (1, False), (1, True)):
            conn = Mock()
            conn.recv.return_value = b"".join(
                (
                    *(
                        response(0x0100, i, vbus.parameter_hash(n))
                        for n, i in names.items()
                    ),
                    response(0x0100, 0, 123),
                    response(0x0100, 10, 1),
                    response(0x0100, 11, allocated),
                    response(0x0100, 12, 1),
                    *(
                        (
                            (response(0x4301, 10), response(0x0102, 10, 0))
                            if first_refused
                            else (response(0x0101, 10, -1), response(0x4302, 10))
                        )
                        if allocated
                        else ()
                    ),
                    response(0x0100, 0, 123),
                )
            )
            result = vbus.VBusSession(conn).read_parameter_observations(
                ADDRESS,
                names,
                123,
                expected=names,
                packed={"Day": {"Allocated": 11, "Initialized": 12}},
            )
            expected_words = (1, None, 0) if first_refused else (1, -1, None)
            self.assertEqual(
                result["Day"].packed_words, expected_words if allocated else ()
            )
            sent = [
                vbus.Datagram.decode(c.args[0]) for c in conn.sendall.call_args_list
            ]
            self.assertEqual(
                [p.command for p in sent if p.command in (0x0301, 0x0302)],
                [0x0301, 0x0302] if allocated else [],
            )

    def test_bulk_packed_transaction_correlates_and_verifies_all_words(self):
        index = 18419
        changeset = 0x2734DABC
        before = (-1, -1, -1)
        wanted = (0x50482820, -1, -1)
        conn = Mock()
        conn.recv.return_value = b"".join(
            (
                response(0x0100, 0, changeset),
                *(response(0x0100 | n, index, word) for n, word in enumerate(before)),
                response(0x1301, value=8, source=0x7111),
                response(0x1301, index=7, value=0x18),
                response(0x1401),
                response(0x1601, index),  # unrelated staged reply
                response(0x1600, index + 1),
                response(0x1600, index),
                response(0x1601, index),
                response(0x1602, index),
                response(0x0100, 0, changeset),
                response(0x1403),
                response(0x0100, 0, changeset),
                *(response(0x0100 | n, index, word) for n, word in enumerate(wanted)),
            )
        )
        session = vbus.VBusSession(conn)
        self.assertEqual(
            session.write_packed_verified(ADDRESS, index, before, wanted, changeset),
            wanted,
        )
        sent = [vbus.Datagram.decode(c.args[0]) for c in conn.sendall.call_args_list]
        self.assertEqual(
            [p.command for p in sent],
            [
                0x0300,
                0x0300,
                0x0301,
                0x0302,
                0x1300,
                0x1400,
                0x1500,
                0x1501,
                0x1502,
                0x0300,
                0x1402,
                0x0300,
                0x0300,
                0x0301,
                0x0302,
            ],
        )
        self.assertEqual([p.value for p in sent[6:9]], list(wanted))
        self.assertEqual(sent[5].value, 10)
        self.assertEqual(sent[10].value, 10)

    def test_bulk_variable_words_and_multiple_native_ids(self):
        changeset = 123
        keys = ((18419, 0), (18419, 1), (18419, 2), (18419, 3), (18420, 127))
        before = dict.fromkeys(keys, -1)
        wanted = dict(zip(keys, (11, 22, 33, 44, 55), strict=True))
        conn = Mock()
        conn.recv.return_value = b"".join(
            (
                response(0x0100, 0, changeset),
                *(
                    response(0x0100 | sub, index, value)
                    for (index, sub), value in before.items()
                ),
                response(0x1301, value=8),
                response(0x1401, value=1),
                *(response(0x1600 | sub, index, 2) for index, sub in wanted),
                response(0x0100, 0, changeset),
                response(0x1403, value=3),
                response(0x0100, 0, changeset),
                *(
                    response(0x0100 | sub, index, value)
                    for (index, sub), value in wanted.items()
                ),
            )
        )
        self.assertEqual(
            vbus.VBusSession(conn).write_bulk_verified(
                ADDRESS, before, wanted, changeset
            ),
            wanted,
        )
        sent = [vbus.Datagram.decode(c.args[0]) for c in conn.sendall.call_args_list]
        self.assertEqual(
            [
                (p.index, p.command & 127, p.value)
                for p in sent
                if p.command & 0xFF80 == 0x1500
            ],
            [(index, sub, value) for (index, sub), value in wanted.items()],
        )
        self.assertEqual(sum(p.command == 0x1402 for p in sent), 1)

    def test_variable_packed_words_and_invalid_shape(self):
        index, changeset = 18419, 123
        conn = Mock()
        conn.recv.return_value = b"".join(
            (
                response(0x0100, 0, changeset),
                *(response(0x0100 | n, index, n) for n in range(5)),
            )
        )
        session = vbus.VBusSession(conn)
        words = tuple(range(5))
        self.assertEqual(
            session.write_packed_verified(ADDRESS, index, words, words, changeset),
            words,
        )
        for before, wanted in (((), ()), ((0,), (0, 1)), ((0,) * 129, (0,) * 129)):
            with self.assertRaises(vbus.VBusError):
                session.write_packed_verified(ADDRESS, index, before, wanted, changeset)
        for invalid in (0, -1, 65536, True, []):
            with self.assertRaises(vbus.VBusError):
                session.write_packed_verified(
                    ADDRESS, invalid, words, words, changeset
                )
        self.assertEqual(conn.sendall.call_count, 6)

    def test_bulk_packed_stage_failure_stops_without_rollback_or_commit(self):
        index, changeset = 18419, 123
        before, wanted = (-1, -1, -1), (0x50482820, 0x10203040, -1)
        for lost_response in (False, True):
            with self.subTest(lost_response=lost_response):
                conn = Mock()
                # The independent response fixture applies the first word
                # immediately. The second mutation is refused or its ACK lost.
                applied = list(before)
                sent = []
                replies = [
                    response(0x0100, 0, changeset),
                    *(response(0x0100 | n, index, -1) for n in range(3)),
                    response(0x1301, value=8),
                    response(0x1401),
                    response(0x1600, index),
                    TimeoutError() if lost_response else response(0x1601, index, -1),
                ]

                def on_send(
                    data, sent=sent, applied=applied, lost_response=lost_response
                ):
                    packet = vbus.Datagram.decode(data)
                    sent.append(packet.command)
                    if packet.command == 0x1500 or (
                        lost_response and packet.command == 0x1501
                    ):
                        applied[packet.command & 0xFF] = packet.value

                conn.sendall.side_effect = on_send
                conn.recv.side_effect = replies
                with self.assertRaisesRegex(vbus.VBusError, "write outcome uncertain"):
                    vbus.VBusSession(conn).write_packed_verified(
                        ADDRESS, index, before, wanted, changeset
                    )
                self.assertEqual(sent[-3:], [0x1400, 0x1500, 0x1501])
                self.assertEqual(applied[0], wanted[0])
                self.assertEqual(applied[1], wanted[1] if lost_response else before[1])
                self.assertNotIn(0x1402, sent)
                self.assertNotIn(0x1404, sent)
                self.assertNotIn(0x1502, sent)

    def test_bulk_packed_precommit_and_commit_rejections_are_uncertain(self):
        index, changeset = 18419, 123
        before, wanted = (-1, -1, -1), (0x50482820, -1, -1)
        for changed in (False, True):
            with self.subTest(changed=changed):
                conn = Mock()
                conn.recv.return_value = b"".join(
                    (
                        response(0x0100, 0, changeset),
                        *(response(0x0100 | n, index, -1) for n in range(3)),
                        response(0x1301, value=8),
                        response(0x1401),
                        *(response(0x1600 | n, index) for n in range(3)),
                        response(0x0100, 0, changeset + changed),
                        response(0x1403, value=-1),
                    )
                )
                with self.assertRaisesRegex(vbus.VBusError, "write outcome uncertain"):
                    vbus.VBusSession(conn).write_packed_verified(
                        ADDRESS, index, before, wanted, changeset
                    )
                commands = [
                    vbus.Datagram.decode(c.args[0]).command
                    for c in conn.sendall.call_args_list
                ]
                self.assertEqual(commands.count(0x1402), 0 if changed else 1)
                self.assertNotIn(0x1404, commands)
                self.assertEqual(commands.count(0x1400), 1)

    def test_bulk_packed_uncertain_commit_never_retries_or_rolls_back(self):
        index, changeset = 18419, 123
        conn = Mock()
        conn.recv.side_effect = [
            b"".join(
                (
                    response(0x0100, 0, changeset),
                    *(response(0x0100 | n, index, -1) for n in range(3)),
                    response(0x1301, value=8),
                    response(0x1401),
                    *(response(0x1600 | n, index) for n in range(3)),
                    response(0x0100, 0, changeset),
                )
            ),
            TimeoutError(),
        ]
        with self.assertRaisesRegex(vbus.VBusError, "write outcome uncertain"):
            vbus.VBusSession(conn).write_packed_verified(
                ADDRESS, index, (-1, -1, -1), (0x50482820, -1, -1), changeset
            )
        sent = [vbus.Datagram.decode(c.args[0]) for c in conn.sendall.call_args_list]
        self.assertEqual([p.command for p in sent[-2:]], [0x0300, 0x1402])
        self.assertNotIn(0x1404, [p.command for p in sent])

    def test_bulk_packed_requires_advertised_capability(self):
        index, changeset = 18419, 123
        before, wanted = (-1, -1, -1), (0x50482820, -1, -1)
        for flags in (0, 4, None):
            with self.subTest(flags=flags):
                conn = Mock()
                conn.recv.side_effect = [
                    response(0x0100, 0, changeset)
                    + b"".join(response(0x0100 | n, index, -1) for n in range(3)),
                    response(0x1301, value=flags)
                    if flags is not None
                    else TimeoutError(),
                ]
                with self.assertRaises(vbus.VBusError) as raised:
                    vbus.VBusSession(conn).write_packed_verified(
                        ADDRESS, index, before, wanted, changeset
                    )
                if flags is None:
                    self.assertIsInstance(raised.exception, vbus.VBusTransientError)
                else:
                    self.assertEqual(
                        vbus.ERROR_CODES[str(raised.exception)], "bulk_unsupported"
                    )
                sent = [
                    vbus.Datagram.decode(c.args[0]) for c in conn.sendall.call_args_list
                ]
                self.assertEqual(
                    [p.command for p in sent],
                    [0x0300, 0x0300, 0x0301, 0x0302, 0x1300],
                )

    def test_bulk_packed_stale_or_noop_never_begins_transaction(self):
        index, changeset = 18419, 123
        for before in ((-1, -1, -1), (1, -1, -1)):
            conn = Mock()
            conn.recv.return_value = b"".join(
                (
                    response(0x0100, 0, changeset),
                    *(response(0x0100 | n, index, -1) for n in range(3)),
                )
            )
            session = vbus.VBusSession(conn)
            if before[0] == -1:
                self.assertEqual(
                    session.write_packed_verified(
                        ADDRESS, index, before, before, changeset
                    ),
                    before,
                )
            else:
                with self.assertRaisesRegex(vbus.VBusError, "readback differs"):
                    session.write_packed_verified(
                        ADDRESS, index, before, before, changeset
                    )
            sent = [
                vbus.Datagram.decode(c.args[0]) for c in conn.sendall.call_args_list
            ]
            self.assertEqual(
                [p.command for p in sent], [0x0300, 0x0300, 0x0301, 0x0302]
            )

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
                response(0x0100, 0, 12345),
                live_packet(),
            )
        )
        conn.recv.side_effect = [stream[:19], stream[19:]]
        session = vbus.VBusSession(conn)
        with session.bus(ADDRESS):
            changeset = session.read(ADDRESS, 0)
            self.assertEqual(
                session.read_parameters(ADDRESS, [NAME, unsupported], changeset),
                {NAME: (1321, 2)},
            )
        sent = [
            vbus.Datagram.decode(call.args[0]) for call in conn.sendall.call_args_list
        ]
        self.assertEqual(
            [p.command for p in sent],
            [0x0300, 0x1100, 0x1100, 0x0300, 0x0300, 0x0300, 0x0600],
        )
        self.assertTrue(all(p.destination == ADDRESS for p in sent))

    def test_discovery_releases_bus_on_failure(self):
        conn = Mock()
        conn.recv.side_effect = [response(0x0500), b"", live_packet()]
        session = vbus.VBusSession(conn)
        with self.assertRaises(vbus.VBusError), session.bus(ADDRESS):
            session.read(ADDRESS, 0)
        packet = vbus.Datagram.decode(conn.sendall.call_args.args[0])
        self.assertEqual(packet.command, 0x0600)

    def test_release_retries_only_release_on_silence(self):
        conn = Mock()
        conn.recv.side_effect = [response(0x0500), TimeoutError(), live_packet()]
        session = vbus.VBusSession(conn)
        with session.bus(ADDRESS):
            pass
        sent = [vbus.Datagram.decode(c.args[0]) for c in conn.sendall.call_args_list]
        self.assertEqual([p.command for p in sent], [0x0600, 0x0600])
        self.assertEqual(session.release_attempts, 2)
        self.assertFalse(session.release_failed)

    def test_release_waits_for_expected_master_after_other_live_packet(self):
        conn = Mock()
        header = bytes.fromhex("1000516610000100")
        other = b"\xaa" + header + bytes([127 - (sum(header) & 127)])
        conn.recv.side_effect = [
            response(0x0500),
            other,
            TimeoutError(),
            live_packet(),
        ]
        session = vbus.VBusSession(conn)
        with session.bus(ADDRESS):
            pass
        self.assertEqual(session.release_attempts, 1)
        self.assertEqual(session.other_live_packets, 1)

    def test_release_accepts_correlated_offer_without_second_release(self):
        for destination in (0, vbus.SELF_ADDRESS):
            with self.subTest(destination=destination):
                conn = Mock()
                offer = vbus.Datagram(destination, ADDRESS, 0x0500).encode()
                conn.recv.side_effect = [response(0x0500), offer[:9], offer[9:]]
                session = vbus.VBusSession(conn)
                with session.bus(ADDRESS):
                    pass
                self.assertEqual(session.release_attempts, 1)
                self.assertEqual(session.release_confirmation, "controller_offer")
                self.assertFalse(session.release_failed)

    def test_release_other_offer_prevents_retry_but_does_not_confirm(self):
        for offer in (
            response(0x0500, source=0x7111),
            vbus.Datagram(0x21, ADDRESS, 0x0500).encode(),
        ):
            with self.subTest(offer=offer):
                conn = Mock()
                conn.recv.side_effect = [
                    response(0x0500),
                    offer,
                    TimeoutError(),
                    TimeoutError(),
                ]
                session = vbus.VBusSession(conn)
                with self.assertRaises(vbus.VBusTransientError), session.bus(ADDRESS):
                    pass
                self.assertEqual(session.release_attempts, 1)
                self.assertEqual(session.other_release_offers, 1)
                self.assertIsNone(session.release_confirmation)
                self.assertTrue(session.release_failed)

    def test_changed_relay_and_return_to_auto_with_release_offer(self):
        for wanted in (1, 2):
            conn = Mock()
            conn.recv.side_effect = [
                response(0x0500),
                response(0x0100, 1321, wanted),
                response(0x0100, 0, 12345),
                response(0x0100, 1321, wanted),
                response(0x0500),
            ]
            session = vbus.VBusSession(conn)
            with session.bus(ADDRESS):
                self.assertEqual(
                    session.write_verified(ADDRESS, 1321, wanted, 12345), wanted
                )
            sent = [
                vbus.Datagram.decode(c.args[0]) for c in conn.sendall.call_args_list
            ]
            self.assertEqual(
                [p.command for p in sent], [0x0200, 0x0300, 0x0300, 0x0600]
            )
            self.assertEqual(sent[0].value, wanted)
            self.assertEqual(session.release_confirmation, "controller_offer")

    def test_release_retry_does_not_repeat_verified_write(self):
        conn = Mock()
        conn.recv.side_effect = [
            response(0x0500),
            response(0x0100, 1321, 2),
            response(0x0100, 0, 12345),
            response(0x0100, 1321, 2),
            TimeoutError(),
            live_packet(),
        ]
        session = vbus.VBusSession(conn)
        with session.bus(ADDRESS):
            session.write_verified(ADDRESS, 1321, 2, 12345)
        sent = [vbus.Datagram.decode(c.args[0]) for c in conn.sendall.call_args_list]
        self.assertEqual(
            [p.command for p in sent], [0x0200, 0x0300, 0x0300, 0x0600, 0x0600]
        )

    def test_release_resyncs_after_truncated_frame(self):
        conn = Mock()
        header = bytes.fromhex("100012711000017f")
        truncated = b"\xaa" + header + bytes([127 - (sum(header) & 127)])
        conn.recv.side_effect = [
            response(0x0500),
            truncated + live_packet()[:6],
            live_packet()[6:],
        ]
        session = vbus.VBusSession(conn)
        with session.bus(ADDRESS):
            pass
        self.assertEqual(session.release_attempts, 1)

    def test_acquisition_distinguishes_silence_from_rejected_offers(self):
        other_offer = response(0x0500, source=0x7111)
        wrong_destination = vbus.Datagram(0x21, ADDRESS, 0x0500).encode()
        non_offer = response(0x0100, 1, 2)
        traffic = live_packet() + other_offer + wrong_destination + non_offer
        for chunks in ([], [traffic[:7], traffic[7:]]):
            with self.subTest(traffic=bool(chunks)):
                conn = Mock()
                conn.recv.side_effect = [*chunks, TimeoutError()]
                session = vbus.VBusSession(conn)
                with (
                    self.assertRaisesRegex(vbus.VBusTransientError, "acquisition"),
                    session.bus(ADDRESS),
                ):
                    self.fail("Unexpected bus ownership")
                self.assertEqual(
                    session.acquisition_stats,
                    {
                        "received_bytes": len(traffic) if chunks else 0,
                        "controller_live_headers": int(bool(chunks)),
                        "other_live_headers": 0,
                        "non_offer_datagrams": int(bool(chunks)),
                        "other_source_offers": int(bool(chunks)),
                        "other_destination_offers": int(bool(chunks)),
                        "accepted_offers": 0,
                    },
                )
                conn.sendall.assert_not_called()

    def test_release_unconfirmed_preserves_failure(self):
        conn = Mock()
        conn.recv.side_effect = [response(0x0500), TimeoutError(), TimeoutError()]
        session = vbus.VBusSession(conn)
        with self.assertRaises(vbus.VBusTransientError), session.bus(ADDRESS):
            pass
        self.assertTrue(session.release_failed)
        self.assertEqual(session.release_attempts, 2)

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
            vbus.Datagram.decode(call.args[0]) for call in conn.sendall.call_args_list
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

    def test_persistent_save_uses_distinct_command_and_readback(self):
        conn = Mock()
        conn.recv.return_value = (
            response(0x0100, 1321, 2)
            + response(0x0100, 0, 12345)
            + response(0x0100, 1321, 2)
        )
        session = vbus.VBusSession(conn)
        self.assertEqual(session.write_verified(ADDRESS, 1321, 2, 12345, save=True), 2)
        sent = [
            vbus.Datagram.decode(call.args[0]) for call in conn.sendall.call_args_list
        ]
        self.assertEqual([p.command for p in sent], [0x0400, 0x0300, 0x0300])

    def test_scalar_write_subindex_correlates_ack_and_readback(self):
        for save in (False, True):
            conn = Mock()
            conn.recv.return_value = (
                response(0x0100, 1321, 17)
                + response(0x0105, 1322, 17)
                + response(0x0105, 1321, 17)
                + response(0x0100, 0, 12345)
                + response(0x0105, 1321, 17)
            )
            session = vbus.VBusSession(conn)
            self.assertEqual(
                session.write_verified(ADDRESS, 1321, 17, 12345, save=save, subindex=5),
                17,
            )
            sent = [
                vbus.Datagram.decode(c.args[0]) for c in conn.sendall.call_args_list
            ]
            self.assertEqual(
                [p.command for p in sent],
                [0x0405 if save else 0x0205, 0x0300, 0x0305],
            )
            for invalid in (-1, 128, True, 1.5):
                with self.assertRaises(vbus.VBusError):
                    session.write_verified(ADDRESS, 1321, 17, 12345, subindex=invalid)
            self.assertEqual(conn.sendall.call_count, 3)

    def test_persistent_save_timeout_never_retries(self):
        conn = Mock()
        conn.recv.side_effect = TimeoutError()
        session = vbus.VBusSession(conn)
        with self.assertRaisesRegex(vbus.VBusError, "write outcome uncertain"):
            session.write_verified(ADDRESS, 1321, 2, 12345, save=True)
        self.assertEqual(conn.sendall.call_count, 1)
        self.assertEqual(
            vbus.Datagram.decode(conn.sendall.call_args.args[0]).command, 0x0400
        )

    def test_persistent_send_failure_is_uncertain(self):
        conn = Mock()
        conn.sendall.side_effect = OSError()
        session = vbus.VBusSession(conn)
        with self.assertRaisesRegex(vbus.VBusError, "write outcome uncertain"):
            session.write_verified(ADDRESS, 1321, 2, 12345, save=True)
        self.assertEqual(conn.sendall.call_count, 1)


class ObservationTests(unittest.TestCase):
    def test_read_batch_preserves_zero_missing_refused_and_other_values(self):
        conn = Mock()
        names = ("Schema", "Missing", "Refused", NAME)
        conn.recv.return_value = b"".join(
            (
                response(0x0100, 86, vbus.parameter_hash(names[0])),
                response(0x1101, 0, vbus.parameter_hash(names[1])),
                response(0x0100, 99, vbus.parameter_hash(names[2])),
                response(0x0100, 2019, vbus.parameter_hash(names[3])),
                response(0x0100, 0, 12345),
                response(0x0100, 86, 0),
                response(0x4300, 99),
                response(0x0100, 2019, 2),
                response(0x0100, 0, 12345),
            )
        )
        result = vbus.VBusSession(conn).read_parameter_observations(
            ADDRESS, names, 12345
        )
        self.assertEqual(
            result,
            {
                "Schema": vbus.ParameterObservation(86, 0, "read"),
                "Missing": vbus.ParameterObservation(None, None, "not_resolved"),
                "Refused": vbus.ParameterObservation(99, None, "read_refused"),
                NAME: vbus.ParameterObservation(2019, 2, "read"),
            },
        )
        sent = [vbus.Datagram.decode(c.args[0]) for c in conn.sendall.call_args_list]
        self.assertTrue(all(p.command in (0x1100, 0x0300) for p in sent))

    def test_layout_mismatch_is_not_read(self):
        conn = Mock()
        conn.recv.return_value = b"".join(
            (
                response(0x1101, 86, vbus.parameter_hash("Schema")),
                response(0x1101, 55, vbus.parameter_hash(NAME)),
                response(0x0100, 0, 12345),
                response(0x0100, 86, 1),
                response(0x0100, 0, 12345),
            )
        )
        result = vbus.VBusSession(conn).read_parameter_observations(
            ADDRESS, {NAME: 2019}, 12345, expected={NAME: 2019}, schema=86
        )
        self.assertEqual(
            result[NAME], vbus.ParameterObservation(55, None, "layout_mismatch")
        )
        sent = [vbus.Datagram.decode(c.args[0]) for c in conn.sendall.call_args_list]
        self.assertNotIn(55, [p.index for p in sent if p.command == 0x0300])
        self.assertEqual(len(sent), 5)

    def test_changeset_change_invalidates_batch(self):
        conn = Mock()
        conn.recv.return_value = (
            response(0x0100, 86, vbus.parameter_hash("Schema"))
            + response(0x0100, 0, 12345)
            + response(0x0100, 86, 0)
            + response(0x0100, 0, 12346)
        )
        with self.assertRaisesRegex(vbus.VBusError, "changeset changed"):
            vbus.VBusSession(conn).read_parameter_observations(
                ADDRESS, ["Schema"], 12345
            )

    def test_transport_error_is_not_refusal(self):
        conn = Mock()
        conn.recv.side_effect = TimeoutError()
        with self.assertRaises(vbus.VBusTransientError) as raised:
            vbus.VBusSession(conn).read_parameter_observations(
                ADDRESS, ["Schema"], 12345, expected={"Schema": 86}
            )
        self.assertEqual(
            raised.exception.evidence(),
            {"phase": "response", "command": "0x1100", "reference_index": 86},
        )
        self.assertEqual(conn.sendall.call_count, 1)
