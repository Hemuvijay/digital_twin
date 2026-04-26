"""
Bus Monitor
Real-time tracking of all active labels on all buses.
Maintains current value, rate, and statistics per label.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Optional, List
from ..core.word import ARINC429Word
from ..core.codec import BNRCodec
from ..core.label_db import get_label  # noqa


@dataclass
class LabelStats:
    label_oct: int
    bus_id: str
    lru_id: str
    last_word: Optional[ARINC429Word] = None
    last_value: Optional[float] = None
    last_timestamp_us: int = 0
    word_count: int = 0
    parity_error_count: int = 0
    ncd_count: int = 0
    failure_count: int = 0
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    measured_rate_hz: float = 0.0
    _prev_timestamp_us: int = 0

    def update(self, word: ARINC429Word) -> None:
        self.last_word = word
        self.last_timestamp_us = word.timestamp_us
        self.word_count += 1

        if not word.parity_ok:
            self.parity_error_count += 1

        if word.ssm == ARINC429Word.SSM_NCD:
            self.ncd_count += 1
        elif word.ssm == ARINC429Word.SSM_FAILURE_WARNING:
            self.failure_count += 1

        # Rate estimation from inter-arrival time
        if self._prev_timestamp_us > 0:
            dt_us = word.timestamp_us - self._prev_timestamp_us
            if dt_us > 0:
                self.measured_rate_hz = 1_000_000.0 / dt_us
        self._prev_timestamp_us = word.timestamp_us

        # Decode value using label DB codec params
        if word.decoded_value is not None:
            self.last_value = word.decoded_value
        else:
            ldef = get_label(word.label_oct)
            if ldef and ldef.format == "BNR":
                codec = BNRCodec(ldef.label_oct, ldef.msb_bit, ldef.lsb_bit,
                                 ldef.resolution, ldef.range_min, ldef.range_max)
                try:
                    val = codec.decode(word)
                    self.last_value = val
                    word.decoded_value = val
                    word.units = ldef.units
                except Exception:
                    pass

        if self.last_value is not None:
            if self.min_value is None or self.last_value < self.min_value:
                self.min_value = self.last_value
            if self.max_value is None or self.last_value > self.max_value:
                self.max_value = self.last_value


class BusMonitor:
    """
    Ingests ARINC 429 words and maintains per-label statistics.
    Acts as a subscriber (listener) to the bus scheduler.
    """

    def __init__(self):
        # Key: (bus_id, lru_id, label_oct)
        self._stats: Dict[tuple, LabelStats] = {}
        self.total_words: int = 0
        self.parity_error_count: int = 0

    def ingest(self, word: ARINC429Word) -> None:
        """Called for every word dispatched by the scheduler."""
        self.total_words += 1
        if not word.parity_ok:
            self.parity_error_count += 1

        key = (word.bus_id, word.lru_id, word.label_oct)
        if key not in self._stats:
            self._stats[key] = LabelStats(
                label_oct=word.label_oct,
                bus_id=word.bus_id,
                lru_id=word.lru_id,
            )
        self._stats[key].update(word)

    def get_stats(self, bus_id: str, lru_id: str, label_oct: int
                  ) -> Optional[LabelStats]:
        return self._stats.get((bus_id, lru_id, label_oct))

    def get_current_value(self, bus_id: str, lru_id: str, label_oct: int
                          ) -> Optional[float]:
        s = self.get_stats(bus_id, lru_id, label_oct)
        return s.last_value if s else None

    def all_stats(self) -> List[LabelStats]:
        return list(self._stats.values())

    def print_table(self) -> None:
        """Print a formatted table of all active labels to stdout."""
        print("\n" + "="*85)
        print(f"{'Bus':<10} {'LRU':<12} {'Label':>6} {'Name':<28} "
              f"{'Value':>10} {'Units':<8} {'SSM':<20} {'Rate Hz':>8}")
        print("-"*85)

        for s in sorted(self._stats.values(),
                        key=lambda x: (x.bus_id, x.lru_id, x.label_oct)):
            ldef = get_label(s.label_oct)
            name  = ldef.name if ldef else "?"
            units = ldef.units if ldef else ""
            ssm_str = s.last_word.ssm_description() if s.last_word else "?"
            val_str = f"{s.last_value:.4f}" if s.last_value is not None else "N/A"
            print(f"{s.bus_id:<10} {s.lru_id:<12} {s.label_oct:>6o} "
                  f"{name:<28} {val_str:>10} {units:<8} {ssm_str:<20} "
                  f"{s.measured_rate_hz:>8.1f}")

        print("="*85)
        print(f"Total words: {self.total_words}  |  Parity errors: {self.parity_error_count}")
        print()
