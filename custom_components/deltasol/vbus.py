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

    def _receive(self, deadline):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise VBusTransientError("VBus response timed out", "receive")
        self.connection.settimeout(remaining)
        data = self.connection.recv(4096)
        if not data:
            raise VBusTransientError("VBus connection closed", "receive")
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
        self.connection.settimeout(2)
        try:
            self.connection.sendall(
                Datagram(address, SELF_ADDRESS, command, index, value).encode()
            )
        except OSError as error:
            if command in (0x0200, 0x0400):
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
                    else packet.index == index
                )
            )

        try:
            return self.receive_matching(matches, 2)
        except (OSError, VBusError) as error:
            if command in (0x0200, 0x0400):
                raise VBusError("VBus write outcome uncertain") from error
            raise VBusTransientError(
                "VBus parameter response failed", "response", command=command, index=index
            ) from error

    def resolve(self, address, name):
        """Resolve a known parameter name to the actual controller index."""
        result = self.request(
            address, 0x1100, value=parameter_hash(name), replies=(0x0100, 0x1101)
        )
        return result.index or None

    def read(self, address, index):
        """Read a parameter without modifying the installed program."""
        reply = self.request(address, 0x0300, index, replies=(0x0100, 0x4300))
        if reply.command != 0x0100:
            raise VBusReadRefused("Controller refused parameter read")
        return reply.value

    def _wait_for_master(self, address):
        """Confirm a valid live-data packet from the controller after release."""
        deadline = monotonic() + 3
        while True:
            if monotonic() >= deadline:
                raise VBusError("VBus release not confirmed")
            start = self.buffer.find(b"\xaa")
            if start == -1:
                self.buffer.clear()
            elif start:
                del self.buffer[:start]
            if len(self.buffer) >= 10:
                header = self.buffer[:10]
                if (
                    header[5] == 0x10
                    and all(byte < 128 for byte in header[1:])
                    and (sum(header[1:]) & 127) == 127
                ):
                    size = 10 + 6 * header[8]
                    if len(self.buffer) < size:
                        self.buffer.extend(self._receive(deadline))
                        continue
                    frame = self.buffer[:size]
                    del self.buffer[:size]
                    if int.from_bytes(header[3:5], "little") == address and all(
                        all(byte < 128 for byte in frame[i : i + 6])
                        and (sum(frame[i : i + 6]) & 127) == 127
                        for i in range(10, size, 6)
                    ):
                        return
                else:
                    del self.buffer[0]
                continue
            self.buffer.extend(self._receive(deadline))

    @contextmanager
    def bus(self, address):
        """Acquire only the expected controller's offer; release in all cases."""
        try:
            self.receive_matching(
                lambda packet: (
                    packet.source == address
                    and packet.destination in (0, SELF_ADDRESS)
                    and packet.command == 0x0500
                ),
                20,
            )
        except (OSError, VBusError) as error:
            raise VBusTransientError(
                "VBus bus acquisition failed", "acquire"
            ) from error
        try:
            yield self
        except BaseException as error:
            try:
                self._release(address)
            except (OSError, VBusError):
                error.add_note("VBus bus release also failed")
            raise
        else:
            self._release(address)

    def _release(self, address):
        """Return ownership and confirm the controller resumed live data."""
        try:
            self.connection.settimeout(2)
            self.connection.sendall(Datagram(address, SELF_ADDRESS, 0x0600).encode())
            self._wait_for_master(address)
        except (OSError, VBusError) as error:
            raise VBusTransientError(
                "VBus release not confirmed", "release", command=0x0600
            ) from error

    def read_parameter_observations(
        self, address, names, changeset, *, strict=False, expected=None, schema=None
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


@contextmanager
def open_vbus(host, password):
    """Open a fresh, local VBus session independent of HTTP sensor polling."""
    try:
        connection = socket.create_connection((host, VBUS_PORT), timeout=5)
    except OSError as error:
        raise VBusTransientError(
            "VBus gateway connection failed", "connect"
        ) from error
    with connection:
        session = VBusSession(connection)
        try:
            session.authenticate(password)
        except (OSError, VBusTransientError) as error:
            raise VBusTransientError(
                "VBus gateway handshake failed", "handshake"
            ) from error
        yield session
