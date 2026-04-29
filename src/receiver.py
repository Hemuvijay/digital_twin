"""
Ethernet Receiver
Accepts a TCP connection from the transmitter, decodes 64-byte packets
back into ARINC429Word objects, and feeds them into the local
BusMonitor, BusLogger, and ValidationEngine.
"""

from __future__ import annotations
import socket
import struct
from typing import Optional
from ..core.word import ARINC429Word

PACKET_SIZE = 64
MAGIC       = b"A429"
_FMT        = ">4sQI16s16sd8s"


def decode_packet(data: bytes) -> Optional[ARINC429Word]:
    """Unpack a 64-byte packet into an ARINC429Word. Returns None on bad magic."""
    if len(data) != PACKET_SIZE:
        return None
    magic, ts_us, raw_word, bus_b, lru_b, decoded, _ = struct.unpack(_FMT, data)
    if magic != MAGIC:
        return None
    bus_id = bus_b.rstrip(b"\x00").decode("utf-8", errors="replace")
    lru_id = lru_b.rstrip(b"\x00").decode("utf-8", errors="replace")
    word   = ARINC429Word.from_raw(raw_word, timestamp_us=ts_us,
                                   bus_id=bus_id, lru_id=lru_id)
    word.decoded_value = decoded
    return word


def _recv_exact(conn: socket.socket, n: int) -> bytes:
    """Read exactly n bytes from the socket, blocking until available."""
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionResetError("Transmitter disconnected")
        buf += chunk
    return buf


class EthernetReceiver:
    """
    TCP server that accepts one transmitter connection and feeds
    decoded words into registered listener callbacks.

    Usage
    -----
    from src.monitor.monitor import BusMonitor
    from src.monitor.logger import BusLogger

    monitor  = BusMonitor()
    logger   = BusLogger()

    rx = EthernetReceiver(port=5429)
    rx.add_listener(monitor.ingest)
    rx.add_listener(logger.write)
    rx.run()                          # blocks until connection closes

    monitor.print_table()
    logger.to_csv("received_log.csv")
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 5429):
        self._host      = host
        self._port      = port
        self._listeners = []

    def add_listener(self, cb) -> None:
        self._listeners.append(cb)

    def run(self) -> None:
        """Start the server, accept one connection, process until closed."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self._host, self._port))
        server.listen(1)
        print(f"[EthernetReceiver] Listening on {self._host}:{self._port} ...")

        conn, addr = server.accept()
        print(f"[EthernetReceiver] Transmitter connected from {addr}")
        word_count = 0

        try:
            while True:
                data = _recv_exact(conn, PACKET_SIZE)
                word = decode_packet(data)
                if word is None:
                    print("[EthernetReceiver] Bad packet — skipping")
                    continue
                word_count += 1
                for cb in self._listeners:
                    cb(word)

        except ConnectionResetError:
            print(f"[EthernetReceiver] Connection closed — {word_count} words received")
        finally:
            conn.close()
            server.close()
