"""Local VBus 2.0 parameter transport.

Protocol reference: resol-vbus, Daniel Wippermann (MIT), connection.js,
datagram.js and examples/customizer2/src/utils.js. See VBUS-LICENSE.txt.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import socket
import struct
from time import monotonic

SELF_ADDRESS = 0x0020
VBUS_PORT = 7053


class VBusError(Exception):
    """VBus operation failed."""


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
            byte | (((frame[14] >> i) & 1) << 7)
            for i, byte in enumerate(frame[8:14])
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
            raise VBusError("VBus response timed out")
        self.connection.settimeout(remaining)
        data = self.connection.recv(4096)
        if not data:
            raise VBusError("VBus connection closed")
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
                raise VBusError("VBus response timed out")
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
        self.connection.sendall(
            Datagram(address, SELF_ADDRESS, command, index, value).encode()
        )

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

        return self.receive_matching(matches, 2)

    def resolve(self, address, name):
        """Resolve a known parameter name to the actual controller index."""
        result = self.request(
            address, 0x1100, value=parameter_hash(name), replies=(0x0100, 0x1101)
        )
        return result.index or None

    def read(self, address, index):
        """Read a parameter without modifying the installed program."""
        return self.request(address, 0x0300, index).value

    @contextmanager
    def bus(self, address):
        """Acquire only the expected controller's offer; release in all cases."""
        self.receive_matching(
            lambda packet: (
                packet.source == address
                and packet.destination in (0, SELF_ADDRESS)
                and packet.command == 0x0500
            ),
            20,
        )
        try:
            yield self
        finally:
            self.connection.settimeout(2)
            self.connection.sendall(Datagram(address, SELF_ADDRESS, 0x0600).encode())

    def discover(self, address, names):
        """Read changeset and resolve/read supported named parameters.

        Caller supplies the controller address from observed device telemetry.
        Discovery makes no parameter writes and does not infer enum semantics.
        """
        with self.bus(address):
            changeset = self.read(address, 0)
            if not changeset:
                raise VBusError("Controller changeset is unknown")
            parameters = {}
            for name in names:
                index = self.resolve(address, name)
                if index is not None:
                    parameters[name] = (index, self.read(address, index))
        return changeset, parameters

    def write_verified(self, address, index, value):
        """Internal volatile write primitive; caller must hold the bus.

        Only a verified semantic mapping may invoke this primitive. Do not
        retry an uncertain write: report the error and re-read separately.
        """
        ack = self.request(address, 0x0200, index, value)
        if ack.value != value:
            raise VBusError("VBus write acknowledgement differs")
        if self.read(address, index) != value:
            raise VBusError("VBus write readback differs")
        return value


@contextmanager
def open_vbus(host, password):
    """Open a fresh, local VBus session independent of HTTP sensor polling."""
    try:
        with socket.create_connection((host, VBUS_PORT), timeout=5) as connection:
            session = VBusSession(connection)
            session.authenticate(password)
            yield session
    except OSError as error:
        raise VBusError("VBus connection failed or timed out") from error
