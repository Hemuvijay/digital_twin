"""
Bus Logger
Writes all ARINC 429 words to an in-memory buffer and supports export
to CSV and a compact binary format.
"""

from __future__ import annotations
import csv
import struct
import io
import os
from typing import List, Optional
from ..core.word import ARINC429Word
from ..core.label_db import get_label  # noqa


# Binary record format: timestamp_us(8) + raw_word(4) + bus_id_hash(2) = 14 bytes
_BINARY_FMT = ">QI2s"
_BINARY_RECORD_SIZE = struct.calcsize(_BINARY_FMT)


class BusLogger:
    """
    In-memory logger for all bus words.
    Each entry: (direction, rx_lru_id, ARINC429Word)
    TX rows carry all subscribed rx_lrus for that channel.
    RX rows carry the specific rx_lru that received the word.
    """

    def __init__(self, max_records: int = 5_000_000):
        self._buffer: list = []
        self._max_records = max_records
        # channel_id → list of lru_ids that subscribe to it
        self._channel_receivers: dict = {}

    def register_channel_receivers(self, channel: str, rx_lru_ids: list) -> None:
        """Called during setup to register which LRUs receive from each channel."""
        if channel not in self._channel_receivers:
            self._channel_receivers[channel] = []
        for lru_id in rx_lru_ids:
            if lru_id not in self._channel_receivers[channel]:
                self._channel_receivers[channel].append(lru_id)

    def write(self, word: ARINC429Word) -> None:
        """Called for every dispatched (TX) word.
        rx_lru is filled with all LRUs subscribed to this channel."""
        if len(self._buffer) < self._max_records:
            receivers = self._channel_receivers.get(word.bus_id, [])
            rx_lru = ", ".join(receivers) if receivers else ""
            self._buffer.append(("TX", rx_lru, word))

    def write_rx(self, rx_lru_id: str, word: ARINC429Word) -> None:
        """Called when a word is delivered to a specific receiving LRU."""
        if len(self._buffer) < self._max_records:
            self._buffer.append(("RX", rx_lru_id, word))

    def word_count(self) -> int:
        return len(self._buffer)

    def clear(self) -> None:
        self._buffer.clear()

    # ── Export methods ────────────────────────────────────────────────────

    def to_csv(self, path: str) -> None:
        """Export all logged words to a CSV file with TX/RX direction column.
        Both TX and RX rows carry tx_lru and rx_lru fields."""
        os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp_us", "channel", "direction", "tx_lru", "rx_lru",
                "label_oct", "label_name", "raw_word_hex",
                "ssm", "sdi", "decoded_value", "units", "parity_ok"
            ])
            for direction, rx_lru_id, w in self._buffer:
                ldef  = get_label(w.label_oct)
                name  = ldef.name  if ldef else ""
                units = ldef.units if ldef else ""
                writer.writerow([
                    w.timestamp_us,
                    w.bus_id,
                    direction,
                    w.lru_id,           # tx_lru always filled — same word object
                    rx_lru_id,          # rx_lru filled for RX rows, empty for TX
                    f"0o{w.label_oct:o}",
                    name,
                    f"0x{w.raw_word:08X}",
                    f"{w.ssm:02b}",
                    w.sdi,
                    f"{w.decoded_value:.6f}" if w.decoded_value is not None else "",
                    units,
                    w.parity_ok,
                ])
        print(f"[BusLogger] Exported {len(self._buffer)} records to {path}")

    def to_binary(self, path: str) -> None:
        """Export TX words only to a compact binary file (14 bytes/word)."""
        tx_words = [w for _, _, w in self._buffer if _ == "TX"]
        with open(path, "wb") as f:
            f.write(b"A429")
            f.write(struct.pack(">HI", 1, len(tx_words)))
            for w in tx_words:
                bus_bytes = w.bus_id[:2].encode("ascii").ljust(2, b"\x00")[:2]
                f.write(struct.pack(_BINARY_FMT, w.timestamp_us, w.raw_word, bus_bytes))
        print(f"[BusLogger] Exported {len(tx_words)} TX words to {path}")

    def get_words_for_label(self, label_oct: int,
                            bus_id: Optional[str] = None) -> List[ARINC429Word]:
        """Filter TX logged words by label (and optionally channel)."""
        return [
            w for d, _, w in self._buffer
            if d == "TX" and w.label_oct == label_oct
            and (bus_id is None or w.bus_id == bus_id)
        ]

    def get_words_in_range(self, start_us: int, end_us: int) -> List[ARINC429Word]:
        """Return all TX words with timestamps in [start_us, end_us]."""
        return [
            w for d, _, w in self._buffer
            if d == "TX" and start_us <= w.timestamp_us <= end_us
        ]

    def statistics(self) -> dict:
        """Return basic statistics about the log."""
        tx = [(d, r, w) for d, r, w in self._buffer if d == "TX"]
        if not tx:
            return {"count": 0}
        words     = [w for _, _, w in tx]
        rx_count  = sum(1 for d, _, _ in self._buffer if d == "RX")
        parity_errors = sum(1 for w in words if not w.parity_ok)
        labels_seen   = set(w.label_oct for w in words)
        buses_seen    = set(w.bus_id    for w in words)
        t_start = words[0].timestamp_us
        t_end   = words[-1].timestamp_us
        duration_s = (t_end - t_start) / 1_000_000.0
        return {
            "tx_count":       len(tx),
            "rx_count":       rx_count,
            "total_records":  len(self._buffer),
            "duration_s":     round(duration_s, 3),
            "parity_errors":  parity_errors,
            "labels_seen":    sorted(labels_seen),
            "channels_seen":  sorted(buses_seen),
            "words_per_sec":  round(len(tx) / duration_s, 1) if duration_s > 0 else 0,
        }
