"""
Ethernet Transmitter
Encodes each ARINC429Word into a 64-byte TCP packet and sends it
to a remote receiver over a LAN connection.

Packet layout (64 bytes, big-endian):
  [0:4]   Magic "A429"
  [4:12]  timestamp_us  uint64
  [12:16] raw_word      uint32
  [16:32] bus_id        16-byte UTF-8 null-padded
  [32:48] lru_id        16-byte UTF-8 null-padded
  [48:56] decoded_value float64
  [56:64] reserved      8 bytes zero
"""

from __future__ import annotations
import socket
import struct
import threading
import queue
from typing import Optional
from ..core.word import ARINC429Word

PACKET_SIZE = 64
MAGIC       = b"A429"
_FMT        = ">4sQI16s16sd8s"   # total = 4+8+4+16+16+8+8 = 64


def encode_word(word: ARINC429Word) -> bytes:
    """Pack an ARINC429Word into a 64-byte binary packet."""
    bus_b     = word.bus_id.encode("utf-8")[:16].ljust(16, b"\x00")
    lru_b     = word.lru_id.encode("utf-8")[:16].ljust(16, b"\x00")
    decoded   = word.decoded_value if word.decoded_value is not None else 0.0
    return struct.pack(
        _FMT,
        MAGIC,
        word.timestamp_us,
        word.raw_word & 0xFFFFFFFF,
        bus_b,
        lru_b,
        decoded,
        b"\x00" * 8,
    )


class EthernetTransmitter:
    """
    Listener-compatible transmitter.
    Call send(word) for every dispatched word — same signature as
    BusMonitor.ingest() so it can be registered directly on the scheduler.

    Uses a background thread + queue so the simulation loop is never
    blocked by network I/O.
    """

    def __init__(self, host: str, port: int = 5429,
                 queue_size: int = 100_000):
        self._host  = host
        self._port  = port
        self._sock: Optional[socket.socket] = None
        self._queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._thread = threading.Thread(target=self._sender_loop,
                                        daemon=True, name="eth-tx")
        self._running = False
        self._connected = False

    def connect(self) -> None:
        """Open TCP connection to the receiver. Call before sim.run()."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock.connect((self._host, self._port))
        self._connected = True
        self._running   = True
        self._thread.start()
        print(f"[EthernetTransmitter] Connected to {self._host}:{self._port}")

    def send(self, word: ARINC429Word) -> None:
        """Called by the scheduler for every dispatched word (non-blocking)."""
        if not self._connected:
            return
        try:
            self._queue.put_nowait(encode_word(word))
        except queue.Full:
            pass   # drop if queue is full — simulation timing is not affected

    def close(self) -> None:
        """Flush remaining packets and close the connection."""
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=5.0)
        if self._sock:
            self._sock.close()
        print("[EthernetTransmitter] Connection closed")

    def _sender_loop(self) -> None:
        """Background thread: drain queue and send packets."""
        while self._running or not self._queue.empty():
            try:
                packet = self._queue.get(timeout=0.1)
                self._sock.sendall(packet)
            except queue.Empty:
                continue
            except (BrokenPipeError, OSError):
                print("[EthernetTransmitter] Connection lost")
                break
