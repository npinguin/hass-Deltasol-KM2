"""Local VBus 2.0 parameter transport.

Protocol reference: resol-vbus, Daniel Wippermann (MIT), connection.js,
datagram.js and examples/customizer2/src/utils.js. See VBUS-LICENSE.txt.
"""

import socket
import struct
from contextlib import contextmanager
from dataclasses import dataclass
from time import monotonic

SELF_ADDRESS = 0x0020
VBUS_PORT = 7053


# Only fixed, integration-defined messages can leave the integration. Never
# export arbitrary exception text, as HTTP failures may contain credentials.
ERROR_CODES = {
    "VBus gateway rejected connection or credentials": "authentication",
    "Invalid VBus password": "invalid_credentials",
    "VBus connection failed or timed out": "connection",
    "VBus gateway connection failed": "gateway_connection",
    "VBus gateway handshake failed": "gateway_handshake",
    "VBus bus acquisition failed": "bus_acquisition",
    "VBus command send failed": "command_send",
    "VBus parameter response failed": "parameter_response",
    "VBus write outcome uncertain": "write_outcome_uncertain",
    **{
        f"VBus write outcome uncertain: {outcome}": f"write_outcome_{outcome}"
        for outcome in ("previous", "requested", "partial", "unavailable")
    },
    "VBus response timed out": "timeout",
    "VBus connection closed": "connection_closed",
    "VBus release not confirmed": "release_unconfirmed",
    "Controller telemetry is unavailable": "telemetry_unavailable",
    "Controller parameter access is unavailable": "parameter_unavailable",
    "Unrecognized controller parameter layout": "unsupported_layout",
    "Relay options changed; rediscover first": "options_changed",
    "Unrecognized BX Plus parameter layout": "unsupported_layout",
    "Unrecognized BX Plus relay parameter layout": "unsupported_layout",
    "Controller changeset is unknown": "unknown_changeset",
    "Controller changeset changed; rediscover first": "changeset_changed",
    "Controller changeset changed before write": "changeset_changed",
    "Controller changeset changed during write": "changeset_changed",
    "Controller changeset changed during discovery": "changeset_changed",
    "Relay parameter is no longer supported": "unsupported_parameter",
    "Controller refused parameter read": "read_refused",
    "Unsupported current relay mode": "unsupported_mode",
    "Unsupported relay mode": "unsupported_mode",
    "VBus write acknowledgement differs": "acknowledgement_mismatch",
    "VBus write readback differs": "readback_mismatch",
    "VBus bulk transaction rejected": "bulk_transaction_rejected",
    "Controller does not advertise VBus bulk transactions": "bulk_unsupported",
    "VBus bulk transaction rollback unconfirmed": "bulk_rollback_unconfirmed",
    "Relay capability or controller identity changed": "identity_changed",
    "Configuration capability or controller identity changed": "identity_changed",
    "Configuration parameter is no longer supported": "unsupported_parameter",
    "Configuration reconnect readback differs": "readback_mismatch",
    "Invalid configuration value": "invalid_value",
    "Configuration conditional constraint rejected": "constraint_rejected",
    "Controller identity changed during discovery": "identity_changed",
    "capture_cancelled": "capture_cancelled",
    "parameter_error": "parameter_error",
}


class VBusError(Exception):
    """VBus operation failed."""


class VBusTransientError(VBusError):
    """Recoverable transport failure; only read-only callers may retry."""

    def __init__(self, message, phase, *, command=None, index=None):
        super().__init__(message)
        self.phase = phase
        self.command = command
        self.index = index

    def evidence(self):
        """Fixed protocol context only; never export payloads or credentials."""
        return {
            "phase": self.phase,
            "command": f"0x{self.command:04X}" if self.command is not None else None,
            "reference_index": self.index,
        }


class VBusReadRefused(VBusError):
    """A correlated negative reply, distinct from transport failure."""


@dataclass(frozen=True)
class ParameterObservation:
    """Raw read evidence; metadata and write access are separate concerns."""

    index: int | None
    value: int | None
    status: str
    # Optional raw words from a bounded read of an allocated weekly day.
    # None represents an explicit correlated refusal, never fabricated zero.
    packed_words: tuple[int | None, ...] = ()


@dataclass(frozen=True)
class Datagram:
    """VBus 2.0 parameter datagram."""

    destination: int
    source: int
    command: int
    index: int = 0
    value: int = 0

    def encode(self):
        """Encode with septet and checksum."""
        header = struct.pack("<HHBH", self.destination, self.source, 0x20, self.command)
        if any(byte > 127 for byte in header):
            raise VBusError("Invalid VBus header")
        payload = struct.pack("<Hi", self.index, self.value)
        septet = sum((byte >> 7) << i for i, byte in enumerate(payload))
        body = header + bytes(byte & 127 for byte in payload) + bytes([septet])
        return b"\xaa" + body + bytes([127 - (sum(body) & 127)])

    @classmethod
    def decode(cls, frame):
        """Validate and decode a complete VBus 2.0 datagram."""
        if (
            len(frame) != 16
            or frame[0] != 0xAA
            or frame[5] != 0x20
            or any(byte > 127 for byte in frame[1:])
            or (sum(frame[1:]) & 127) != 127
        ):
            raise VBusError("Invalid VBus datagram")
        destination, source, _, command = struct.unpack("<HHBH", frame[1:8])
        payload = bytes(
            byte | (((frame[14] >> i) & 1) << 7) for i, byte in enumerate(frame[8:14])
        )
        index, value = struct.unpack("<Hi", payload)
        return cls(destination, source, command, index, value)


def parameter_hash(name):
    """Calculate the RESOL hash of a known semantic parameter identifier."""
    result = 0
    for byte in name.encode("ascii"):
        result = (33 * result + byte) & 0x7FFFFFFF
    return result


class VBusSession:
    """One authenticated, bounded VBus connection."""

    def __init__(self, connection):
        self.connection = connection
        self.buffer = bytearray()
        self.phase_seconds = {}
        self.release_attempts = 0
        self.other_live_packets = 0
        self.other_release_offers = 0
        self.release_confirmation = None
        self.release_failed = False
        self.acquisition_stats = None
        self._acquiring = False

    def _receive(self, deadline):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("VBus response timed out")
        self.connection.settimeout(remaining)
        data = self.connection.recv(4096)
        if not data:
            raise VBusTransientError("VBus connection closed", "receive")
        if self._acquiring:
            self.acquisition_stats["received_bytes"] += len(data)
        return data

    def _line(self, deadline):
        while b"\n" not in self.buffer:
            self.buffer.extend(self._receive(deadline))
            if len(self.buffer) > 4096:
                raise VBusError("Invalid VBus greeting")
        end = self.buffer.index(10) + 1
        line = bytes(self.buffer[:end])
        del self.buffer[:end]
        if not line.startswith(b"+"):
            raise VBusError("VBus gateway rejected connection or credentials")

    def authenticate(self, password):
        """Authenticate with the local VBus gateway and enter data mode."""
        if not password or "\r" in password or "\n" in password:
            raise VBusError("Invalid VBus password")
        deadline = monotonic() + 5
        self._line(deadline)
        self.connection.sendall(b"PASS " + password.encode("utf-8") + b"\r\n")
        self._line(deadline)
        self.connection.sendall(b"DATA\r\n")
        self._line(deadline)

    def _datagram(self, deadline):
        while True:
            if monotonic() >= deadline:
                raise VBusTransientError("VBus response timed out", "receive")
            start = self.buffer.find(b"\xaa")
            if start == -1:
                self.buffer.clear()
            elif start:
                del self.buffer[:start]
            if len(self.buffer) >= 6 and self.buffer[5] != 0x20:
                # During acquisition distinguish a silent TCP stream from
                # live VBus traffic without a usable offer. Count validated
                # headers only; no payloads or device addresses are exported.
                if self._acquiring and self.buffer[5] == 0x10:
                    if len(self.buffer) < 10:
                        self.buffer.extend(self._receive(deadline))
                        continue
                    header = self.buffer[:10]
                    if (
                        all(byte < 128 for byte in header[1:])
                        and (sum(header[1:]) & 127) == 127
                    ):
                        source = int.from_bytes(header[3:5], "little")
                        key = (
                            "controller_live_headers"
                            if source == self._acquisition_address
                            else "other_live_headers"
                        )
                        self.acquisition_stats[key] += 1
                del self.buffer[0]
                continue
            if len(self.buffer) >= 16:
                try:
                    packet = Datagram.decode(self.buffer[:16])
                except VBusError:
                    del self.buffer[0]
                    continue
                del self.buffer[:16]
                return packet
            self.buffer.extend(self._receive(deadline))

    def receive_matching(self, predicate, timeout):
        """Ignore unrelated and corrupt frames until the bounded deadline."""
        deadline = monotonic() + timeout
        while True:
            packet = self._datagram(deadline)
            if predicate(packet):
                return packet

    def request(self, address, command, index=0, value=0, *, replies=(0x0100,)):
        """Send exactly once and correlate the response to its address and ID."""
        mutating = command & 0xFF80 in (0x0200, 0x0400, 0x1500) or command in (
            0x1400,
            0x1402,
            0x1404,
        )
        self.connection.settimeout(2)
        try:
            self.connection.sendall(
                Datagram(address, SELF_ADDRESS, command, index, value).encode()
            )
        except OSError as error:
            if mutating:
                raise VBusError("VBus write outcome uncertain") from error
            raise VBusTransientError(
                "VBus command send failed", "send", command=command, index=index
            ) from error

        def matches(packet):
            return (
                packet.destination == SELF_ADDRESS
                and packet.source == address
                and packet.command in replies
                and (
                    packet.value == value
                    if command == 0x1100
                    else command == 0x1300 or packet.index == index
                )
            )

        try:
            return self.receive_matching(matches, 2)
        except (OSError, VBusError) as error:
            if mutating:
                raise VBusError("VBus write outcome uncertain") from error
            raise VBusTransientError(
                "VBus parameter response failed",
                "response",
                command=command,
                index=index,
            ) from error

    def resolve(self, address, name):
        """Resolve a known parameter name to the actual controller index."""
        result = self.request(
            address, 0x1100, value=parameter_hash(name), replies=(0x0100, 0x1101)
        )
        return result.index or None

    def read_capabilities(self, address):
        """Read controller capability flags (MIT getCaps1, RESOL RPT 2.02)."""
        return self.request(address, 0x1300, replies=(0x1301,)).value

    def read(self, address, index, *, subindex=0):
        """Read one correlated 32-bit parameter word.

        MIT resol-vbus connection.js getValueById encodes the seven-bit
        subindex in the command. A subindex does not change the semantic
        parameter ID. No inference about Blob length or bit ordering.
        """
        if type(subindex) is not int or not 0 <= subindex <= 127:
            raise VBusError("Invalid VBus parameter subindex")
        ack = 0x0100 | subindex
        reply = self.request(
            address,
            0x0300 | subindex,
            index,
            replies=(ack, 0x4300 | subindex),
        )
        if reply.command != ack:
            raise VBusReadRefused("Controller refused parameter read")
        return reply.value

    def _wait_for_master(self, address, timeout):
        """Observe the expected master return via live data or a fresh offer."""
        deadline = monotonic() + timeout
        while True:
            if monotonic() >= deadline:
                raise TimeoutError("VBus release not confirmed")
            start = self.buffer.find(b"\xaa")
            if start == -1:
                self.buffer.clear()
            elif start:
                del self.buffer[:start]
            if len(self.buffer) >= 6 and self.buffer[5] == 0x20:
                if len(self.buffer) < 16:
                    self.buffer.extend(self._receive(deadline))
                    continue
                try:
                    packet = Datagram.decode(self.buffer[:16])
                except VBusError:
                    del self.buffer[0]
                    continue
                del self.buffer[:16]
                # A new bus offer is also positive evidence that the master
                # has resumed scheduling. RPT resynchronizes on 0x0500.
                # Never consume another master's offer as our own authority.
                if packet.command == 0x0500:
                    if packet.source == address and packet.destination in (
                        0,
                        SELF_ADDRESS,
                    ):
                        self.release_confirmation = "controller_offer"
                        return
                    self.other_release_offers += 1
                continue
            if len(self.buffer) >= 10:
                header = self.buffer[:10]
                if (
                    header[5] == 0x10
                    and all(byte < 128 for byte in header[1:])
                    and (sum(header[1:]) & 127) == 127
                ):
                    size = 10 + 6 * header[8]
                    next_sync = self.buffer.find(b"\xaa", 1, size)
                    if next_sync != -1:
                        del self.buffer[:next_sync]
                        continue
                    if len(self.buffer) < size:
                        self.buffer.extend(self._receive(deadline))
                        continue
                    frame = self.buffer[:size]
                    del self.buffer[:size]
                    if all(
                        all(byte < 128 for byte in frame[i : i + 6])
                        and (sum(frame[i : i + 6]) & 127) == 127
                        for i in range(10, size, 6)
                    ):
                        if int.from_bytes(header[3:5], "little") == address:
                            self.release_confirmation = "controller_live_packet"
                            return
                        self.other_live_packets += 1
                else:
                    del self.buffer[0]
                continue
            self.buffer.extend(self._receive(deadline))

    @contextmanager
    def bus(self, address):
        """Acquire only the expected controller's offer; release in all cases."""
        started = monotonic()
        self._acquisition_address = address
        self.acquisition_stats = {
            "received_bytes": len(self.buffer),
            "controller_live_headers": 0,
            "other_live_headers": 0,
            "non_offer_datagrams": 0,
            "other_source_offers": 0,
            "other_destination_offers": 0,
            "accepted_offers": 0,
        }

        def offered(packet):
            if packet.command != 0x0500:
                key = "non_offer_datagrams"
            elif packet.source != address:
                key = "other_source_offers"
            elif packet.destination not in (0, SELF_ADDRESS):
                key = "other_destination_offers"
            else:
                self.acquisition_stats["accepted_offers"] += 1
                return True
            self.acquisition_stats[key] += 1
            return False

        self._acquiring = True
        try:
            self.receive_matching(offered, 20)
        except (OSError, VBusError) as error:
            raise VBusTransientError(
                "VBus bus acquisition failed", "acquire"
            ) from error
        finally:
            self._acquiring = False
            self.phase_seconds["acquire"] = monotonic() - started
        started = monotonic()
        try:
            yield self
        except BaseException as error:
            self.phase_seconds["operation"] = monotonic() - started
            try:
                self._release(address)
            except (OSError, VBusError):
                error.add_note("VBus bus release also failed")
            raise
        else:
            self.phase_seconds["operation"] = monotonic() - started
            self._release(address)

    def _release(self, address):
        """Retry release only on silence, never repeat a parameter write.

        Other live traffic or offers are not proof of the expected master's
        return. If observed, wait rather than sending another release on an
        active bus.
        """
        started = monotonic()
        self.release_failed = True
        self.release_confirmation = None
        self.other_live_packets = 0
        self.other_release_offers = 0
        try:
            for attempt, timeout in enumerate((1.5, 4.5)):
                if attempt == 0 or not (
                    self.other_live_packets or self.other_release_offers
                ):
                    self.connection.settimeout(2)
                    self.connection.sendall(
                        Datagram(address, SELF_ADDRESS, 0x0600).encode()
                    )
                    self.release_attempts += 1
                try:
                    self._wait_for_master(address, timeout)
                except TimeoutError:
                    if attempt == 1:
                        raise
                else:
                    self.release_failed = False
                    return
        except (OSError, VBusError) as error:
            raise VBusTransientError(
                "VBus release not confirmed", "release", command=0x0600
            ) from error
        finally:
            self.phase_seconds["release"] = monotonic() - started

    def read_parameter_observations(
        self,
        address,
        names,
        changeset,
        *,
        strict=False,
        expected=None,
        schema=None,
        packed=None,
    ):
        """Resolve and read known IDs; a refused value is not a bus outage.

        Caller owns the bus and bounds the batch. Transport or changeset
        failures invalidate the batch, not individual parameter capabilities.
        """
        indices = {}
        if schema is not None:
            try:
                index = self.resolve(address, "Schema")
            except VBusTransientError as error:
                error.index = schema
                raise
            if index != schema:
                raise VBusError("Unrecognized controller parameter layout")
            indices["Schema"] = index
        for name in names:
            if name in indices:
                continue
            try:
                indices[name] = self.resolve(address, name)
            except VBusTransientError as error:
                # The wire lookup uses a hash; retain only the safe catalogue index.
                error.index = expected.get(name) if expected is not None else None
                raise
        if self.read(address, 0) != changeset:
            raise VBusError("Controller changeset changed during discovery")
        observations = {}
        if schema is not None:
            # Read the known layout anchor once before other values.
            observations["Schema"] = ParameterObservation(
                schema, self.read(address, schema), "read"
            )
        for name, index in indices.items():
            if name in observations:
                continue
            if index is None:
                observations[name] = ParameterObservation(None, None, "not_resolved")
                continue
            if expected is not None and index != expected.get(name):
                observations[name] = ParameterObservation(
                    index, None, "layout_mismatch"
                )
                continue
            try:
                value = self.read(address, index)
            except VBusReadRefused:
                if strict:
                    raise
                observations[name] = ParameterObservation(index, None, "read_refused")
            else:
                observations[name] = ParameterObservation(index, value, "read")
        # The caller supplies only exact, nonsensitive catalogue days and
        # their two allocation flags. Read extra words only for an allocated,
        # initialized day; the result is raw evidence, not a decoded schedule.
        for name, flags in (packed or {}).items():
            observed = observations.get(name)
            if (
                observed is None
                or observed.status != "read"
                or len(flags) != 2
                or not all(
                    flag in observations
                    and observations[flag].status == "read"
                    and observations[flag].value == 1
                    for flag in flags
                )
            ):
                continue
            words = [observed.value]
            for subindex in (1, 2):
                try:
                    words.append(self.read(address, observed.index, subindex=subindex))
                except VBusReadRefused:
                    # Refusal is specific to this correlated subindex, not
                    # evidence that subsequent words are inaccessible.
                    words.append(None)
            observations[name] = ParameterObservation(
                observed.index, observed.value, observed.status, tuple(words)
            )
        if self.read(address, 0) != changeset:
            raise VBusError("Controller changeset changed during discovery")
        return {name: observations[name] for name in names}

    def read_parameters(self, address, names, changeset):
        """Existing strict capability read, backed by the same observation path."""
        observations = self.read_parameter_observations(
            address, names, changeset, strict=True
        )
        return {
            name: (result.index, result.value)
            for name, result in observations.items()
            if result.status == "read"
        }

    def write_verified(self, address, index, value, changeset, *, save=False):
        """Internal write primitive; caller must hold the bus.

        The MIT resol-vbus connection.js reference distinguishes volatile
        writes (0x0200) from writes with save (0x0400). The caller's verified
        parameter profile must determine whether save is allowed. Readback
        confirms the live value, not physical power-cycle retention.

        Only a verified semantic mapping may invoke this primitive. Do not
        retry an uncertain write: report the error and re-read separately.
        """
        ack = self.request(address, 0x0400 if save else 0x0200, index, value)
        if ack.value != value:
            raise VBusError("VBus write acknowledgement differs")
        if self.read(address, 0) != changeset:
            raise VBusError("Controller changeset changed during write")
        if self.read(address, index) != value:
            raise VBusError("VBus write readback differs")
        return value

    def write_packed_verified(self, address, index, before, wanted, changeset):
        """Internal non-atomic three-word transport for a previously verified Blob.

        MIT resol-vbus connection.js bulk transaction and upstream
        connection.spec.js zero-status acknowledgements, including subindex
        correlation. The caller must establish controller support, semantic
        authorization, layout, packing and storage semantics before calling.
        A fresh capability response must advertise bulk transactions (bit
        0x08, observed in RESOL RPT 2.02). This is transport evidence only,
        not authorization to write any particular Blob. BX Plus mutates the
        parameter buffer during each stage. No rollback is established.
        A failed stage or commit requires a fresh read-only reconciliation;
        never replay the request or commit a partially applied value.
        """
        if (
            type(index) is not int
            or not 0 < index <= 65535
            or any(
                not isinstance(words, tuple)
                or len(words) != 3
                or any(
                    type(word) is not int or not -(2**31) <= word < 2**31
                    for word in words
                )
                for words in (before, wanted)
            )
        ):
            raise VBusError("Invalid configuration value")
        if self.read(address, 0) != changeset:
            raise VBusError("Controller changeset changed before write")

        def read_words():
            return tuple(self.read(address, index, subindex=n) for n in range(3))

        if read_words() != before:
            raise VBusError("VBus write readback differs")
        if before == wanted:
            return before
        if not self.read_capabilities(address) & 0x08:
            raise VBusError("Controller does not advertise VBus bulk transactions")

        def acknowledged(command, reply, index=0, value=0):
            response = self.request(address, command, index, value, replies=(reply,))
            if response.value != 0:
                raise VBusError("VBus bulk transaction rejected")

        # Begin rejection is not an acknowledged transaction. A lost begin
        # response is already classified as uncertain by request().
        acknowledged(0x1400, 0x1401, value=10)
        try:
            for subindex, word in enumerate(wanted):
                acknowledged(0x1500 | subindex, 0x1600 | subindex, index, word)
            if self.read(address, 0) != changeset:
                raise VBusError("Controller changeset changed during write")
            acknowledged(0x1402, 0x1403, value=10)
        except (OSError, VBusError) as error:
            # Prior words may already have changed even on an explicit
            # rejection. Do not invent rollback, commit, or another begin.
            # Let the bus owner recover and reconcile via a fresh read.
            raise VBusError("VBus write outcome uncertain") from error
        if self.read(address, 0) != changeset:
            raise VBusError("Controller changeset changed during write")
        if read_words() != wanted:
            raise VBusError("VBus write readback differs")
        return wanted


@contextmanager
def open_vbus(host, password):
    """Open a fresh, local VBus session independent of HTTP sensor polling."""
    try:
        connection = socket.create_connection((host, VBUS_PORT), timeout=5)
    except OSError as error:
        raise VBusTransientError("VBus gateway connection failed", "connect") from error
    with connection:
        session = VBusSession(connection)
        try:
            session.authenticate(password)
        except (OSError, VBusTransientError) as error:
            raise VBusTransientError(
                "VBus gateway handshake failed", "handshake"
            ) from error
        yield session
