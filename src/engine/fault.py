"""
ARINC 429 Fault Injection Engine
Schedules and applies fault conditions to words in the simulation pipeline.
"""

from __future__ import annotations
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional
from ..core.word import ARINC429Word


class FaultType(Enum):
    PARITY_ERROR          = "PARITY_ERROR"
    SSM_FAILURE_WARNING   = "SSM_FAILURE_WARNING"
    SSM_NCD               = "SSM_NCD"
    SSM_FUNCTIONAL_TEST   = "SSM_FUNCTIONAL_TEST"
    OUT_OF_RANGE          = "OUT_OF_RANGE"
    LABEL_DROP            = "LABEL_DROP"
    BIT_FLIP              = "BIT_FLIP"
    BUS_FREEZE            = "BUS_FREEZE"
    SDI_MISMATCH          = "SDI_MISMATCH"


@dataclass
class FaultRecord:
    fault_id: str
    fault_type: FaultType
    trigger_time_s: float          # Simulation time (seconds) to start
    duration_s: float = 0.0        # 0 = one-shot; > 0 = sustained
    target_lru: Optional[str] = None    # None = any LRU
    target_label: Optional[int] = None  # None = any label
    target_bus: Optional[str] = None
    probability: float = 1.0       # 0.0-1.0 for probabilistic faults
    override_value: Optional[float] = None   # For OUT_OF_RANGE
    bit_positions: Optional[List[int]] = None  # For BIT_FLIP
    sdi_override: Optional[int] = None         # For SDI_MISMATCH
    active: bool = False
    triggered: bool = False


class FaultInjector:
    """
    Maintains a list of scheduled faults and applies them to words
    as the simulation runs.

    Usage:
        fi = FaultInjector()
        fi.schedule(FaultRecord(...))
        scheduler.set_fault_hook(fi.process)
    """

    def __init__(self):
        self._faults: List[FaultRecord] = []
        self._rng = random.Random(12345)
        self._frozen_buses: set = set()

    def schedule(self, record: FaultRecord) -> None:
        """Add a fault to the schedule."""
        self._faults.append(record)

    def add_fault(self, fault_id: str, fault_type: FaultType,
                  trigger_time_s: float, duration_s: float = 0.0,
                  target_lru: str = None, target_label: int = None,
                  probability: float = 1.0, **kwargs) -> None:
        """Convenience method to schedule a fault without creating a FaultRecord."""
        self.schedule(FaultRecord(
            fault_id=fault_id,
            fault_type=fault_type,
            trigger_time_s=trigger_time_s,
            duration_s=duration_s,
            target_lru=target_lru,
            target_label=target_label,
            probability=probability,
            **kwargs
        ))

    def process(self, word: ARINC429Word) -> Optional[ARINC429Word]:
        """
        Fault hook: called for every word before dispatch.
        Returns the (possibly modified) word, or None to drop it.
        """
        sim_time_s = word.timestamp_us / 1_000_000.0

        # Update fault activation states
        for f in self._faults:
            if not f.triggered and sim_time_s >= f.trigger_time_s:
                f.active = True
                f.triggered = True
                if f.fault_type == FaultType.BUS_FREEZE and f.target_bus:
                    self._frozen_buses.add(f.target_bus)
            if f.active and f.duration_s > 0:
                if sim_time_s >= f.trigger_time_s + f.duration_s:
                    f.active = False
                    if f.fault_type == FaultType.BUS_FREEZE and f.target_bus:
                        self._frozen_buses.discard(f.target_bus)

        # Bus freeze: drop all words on frozen buses
        if word.bus_id in self._frozen_buses:
            return None

        # Apply active faults matching this word
        for f in self._faults:
            if not f.active:
                continue
            if f.target_lru and f.target_lru != word.lru_id:
                continue
            if f.target_label and f.target_label != word.label_oct:
                continue
            if f.target_bus and f.target_bus != word.bus_id:
                continue
            if self._rng.random() > f.probability:
                continue

            word = self._apply_fault(word, f)
            if word is None:
                return None

        return word

    def _apply_fault(self, word: ARINC429Word,
                     fault: FaultRecord) -> Optional[ARINC429Word]:
        """Apply a single fault to a word. Returns None to drop the word."""

        if fault.fault_type == FaultType.LABEL_DROP:
            return None

        if fault.fault_type == FaultType.PARITY_ERROR:
            # Flip parity bit (bit 32)
            raw = word.raw_word ^ 0x80000000
            return ARINC429Word.from_raw(raw, word.timestamp_us, word.bus_id, word.lru_id)

        if fault.fault_type == FaultType.SSM_FAILURE_WARNING:
            raw = (word.raw_word & 0x9FFFFFFF) | (0b00 << 29)
            return ARINC429Word.from_raw(raw, word.timestamp_us, word.bus_id, word.lru_id)

        if fault.fault_type == FaultType.SSM_NCD:
            raw = (word.raw_word & 0x9FFFFFFF) | (0b01 << 29)
            return ARINC429Word.from_raw(raw, word.timestamp_us, word.bus_id, word.lru_id)

        if fault.fault_type == FaultType.SSM_FUNCTIONAL_TEST:
            raw = (word.raw_word & 0x9FFFFFFF) | (0b10 << 29)
            return ARINC429Word.from_raw(raw, word.timestamp_us, word.bus_id, word.lru_id)

        if fault.fault_type == FaultType.BIT_FLIP:
            raw = word.raw_word
            bits = fault.bit_positions or [self._rng.randint(1, 31)]
            for bit_pos in bits:
                raw ^= (1 << (bit_pos - 1))
            return ARINC429Word.from_raw(raw, word.timestamp_us, word.bus_id, word.lru_id)

        if fault.fault_type == FaultType.SDI_MISMATCH:
            sdi_val = fault.sdi_override if fault.sdi_override is not None else 0b11
            raw = (word.raw_word & ~(0x03 << 8)) | ((sdi_val & 0x03) << 8)
            return ARINC429Word.from_raw(raw, word.timestamp_us, word.bus_id, word.lru_id)

        if fault.fault_type == FaultType.OUT_OF_RANGE:
            # Force max or user-specified value into data bits
            raw = word.raw_word | 0x1FFFF800   # Set all data bits
            return ARINC429Word.from_raw(raw, word.timestamp_us, word.bus_id, word.lru_id)

        return word

    def clear_all(self) -> None:
        """Remove all scheduled faults."""
        self._faults.clear()
        self._frozen_buses.clear()

    def active_faults(self) -> List[FaultRecord]:
        """Return list of currently active fault records."""
        return [f for f in self._faults if f.active]

    def fault_summary(self) -> Dict[str, int]:
        """Return count of faults by type."""
        counts: Dict[str, int] = {}
        for f in self._faults:
            key = f.fault_type.value
            counts[key] = counts.get(key, 0) + 1
        return counts
