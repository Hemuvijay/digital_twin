
"""
ARINC 429 Bus Scheduler
Discrete-event simulation (DES) core.
Manages per-label transmission timing, inter-word gap enforcement,
and ordered dispatch to the bus monitor and logger.
"""

from __future__ import annotations
import heapq
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple
from .word import ARINC429Word


# ─────────────────────────────────────────────────────────────────────────────
# Transmission timing constants
# ─────────────────────────────────────────────────────────────────────────────

HS_BIT_PERIOD_US   = 10       # 100 kbps → 10 µs per bit
LS_BIT_PERIOD_US   = 80       # 12.5 kbps → 80 µs per bit
HS_WORD_DURATION_US = 320     # 32 bits × 10 µs
LS_WORD_DURATION_US = 2560    # 32 bits × 80 µs
HS_MIN_GAP_US       = 40      # 4 bit-times at HS
LS_MIN_GAP_US       = 320     # 4 bit-times at LS


# ─────────────────────────────────────────────────────────────────────────────
# Scheduler Event
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(order=True)
class SchedulerEvent:
    """One pending word transmission, stored in the min-heap."""
    next_tx_us: int                    # Absolute sim time for next TX (µs)
    period_us: int = field(compare=False)      # Transmission period
    lru_id: str = field(compare=False)
    label_oct: int = field(compare=False)
    bus_id: str = field(compare=False)
    word_gen: Callable = field(compare=False)  # Callable[[], ARINC429Word]


# ─────────────────────────────────────────────────────────────────────────────
# BusState – per-bus state for gap enforcement
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BusState:
    speed: str = "HS"        # 'HS' or 'LS'
    last_word_end_us: int = 0

    @property
    def word_duration_us(self) -> int:
        return HS_WORD_DURATION_US if self.speed == "HS" else LS_WORD_DURATION_US

    @property
    def min_gap_us(self) -> int:
        return HS_MIN_GAP_US if self.speed == "HS" else LS_MIN_GAP_US

    def earliest_tx_us(self, desired_us: int) -> int:
        """Return the earliest time a new word can be transmitted on this bus."""
        earliest = self.last_word_end_us + self.min_gap_us
        return max(desired_us, earliest)

    def record_transmission(self, tx_us: int) -> None:
        self.last_word_end_us = tx_us + self.word_duration_us


# ─────────────────────────────────────────────────────────────────────────────
# BusScheduler
# ─────────────────────────────────────────────────────────────────────────────

class BusScheduler:
    """
    Priority-queue–based discrete event scheduler.

    Each (lru_id, label_oct) pair has one SchedulerEvent in the heap.
    When the event fires, the word_gen callable is invoked to produce a
    fresh ARINC429Word, which is then dispatched to all registered listeners.

    Parameters
    ----------
    real_time : bool
        If True, the scheduler sleeps until wall-clock time matches each event.
        If False (default), the logical clock jumps to each event instantly.
    time_scale : float
        Multiply simulated time by this for real-time pacing (1.0 = real-time).
    """

    def __init__(self, real_time: bool = True, time_scale: float = 1.0):
        self._heap: List[SchedulerEvent] = []
        self._buses: Dict[str, BusState] = {}
        self._listeners: List[Callable[[ARINC429Word], None]] = []
        self._fault_hook: Optional[Callable[[ARINC429Word], Optional[ARINC429Word]]] = None
        self._clock_us: int = 0
        self._real_time = real_time
        self._time_scale = time_scale
        self._rt_start_wall: float = 0.0
        self._rt_start_sim: int = 0
        self._running = False

    # ── Bus registration ──────────────────────────────────────────────────

    def register_bus(self, bus_id: str, speed: str = "HS") -> None:
        """Register a bus with its speed. Must be called before scheduling labels."""
        self._buses[bus_id] = BusState(speed=speed)

    # ── Label scheduling ──────────────────────────────────────────────────

    def schedule_label(self, bus_id: str, lru_id: str, label_oct: int,
                       rate_hz: float, word_gen: Callable[[], ARINC429Word],
                       initial_offset_us: int = 0) -> None:
        """
        Register a (lru, label) pair to be transmitted at rate_hz.

        Parameters
        ----------
        bus_id : str
            Target bus identifier.
        lru_id : str
            Originating LRU identifier.
        label_oct : int
            Label in octal.
        rate_hz : float
            Transmission rate in words per second.
        word_gen : Callable[[], ARINC429Word]
            Called each time the label is due; must return a fresh word.
        initial_offset_us : int
            Phase offset in µs from t=0 to spread initial bursts.
        """
        if bus_id not in self._buses:
            raise ValueError(f"Bus '{bus_id}' not registered. Call register_bus() first.")

        period_us = int(1_000_000 / rate_hz)
        event = SchedulerEvent(
            next_tx_us=self._clock_us + initial_offset_us,
            period_us=period_us,
            lru_id=lru_id,
            label_oct=label_oct,
            bus_id=bus_id,
            word_gen=word_gen,
        )
        heapq.heappush(self._heap, event)

    # ── Listener / fault hook ─────────────────────────────────────────────

    def add_listener(self, cb: Callable[[ARINC429Word], None]) -> None:
        """Register a callback invoked for every dispatched word."""
        self._listeners.append(cb)

    def set_fault_hook(self, hook: Callable[[ARINC429Word], Optional[ARINC429Word]]) -> None:
        """
        Register a fault-injection hook.
        The hook receives the word before dispatch.
        Return None to drop the word; return a (possibly modified) word to pass through.
        """
        self._fault_hook = hook

    # ── Simulation control ────────────────────────────────────────────────

    def run(self, duration_us: int) -> None:
        """
        Run the simulation for duration_us microseconds of simulated time.
        In real-time mode this will block for ~(duration_us / 1e6 * time_scale) seconds.
        """
        self._running = True
        end_us = self._clock_us + duration_us

        if self._real_time:
            self._rt_start_wall = time.monotonic()
            self._rt_start_sim = self._clock_us

        while self._running and self._heap:
            event = self._heap[0]
            if event.next_tx_us >= end_us:
                break

            heapq.heappop(self._heap)

            if self._real_time:
                # Sleep until wall clock reaches simulated event time
                elapsed_sim_us = event.next_tx_us - self._rt_start_sim
                target_wall = self._rt_start_wall + (elapsed_sim_us / 1_000_000.0) * self._time_scale
                sleep_s = target_wall - time.monotonic()
                if sleep_s > 0:
                    time.sleep(sleep_s)

            # Advance logical clock
            bus = self._buses[event.bus_id]
            actual_tx_us = bus.earliest_tx_us(event.next_tx_us)
            self._clock_us = actual_tx_us

            # Generate word
            word = event.word_gen()
            word.timestamp_us = actual_tx_us
            word.bus_id = event.bus_id
            word.lru_id = event.lru_id

            # Fault injection hook
            if self._fault_hook:
                word = self._fault_hook(word)
                if word is None:
                    # Word dropped by fault injector
                    event.next_tx_us += event.period_us
                    heapq.heappush(self._heap, event)
                    continue

            # Record transmission on bus (for gap enforcement)
            bus.record_transmission(actual_tx_us)

            # Dispatch to listeners
            for cb in self._listeners:
                cb(word)

            # Reschedule
            event.next_tx_us += event.period_us
            heapq.heappush(self._heap, event)

        self._clock_us = end_us

    def stop(self) -> None:
        """Stop a running simulation (thread-safe)."""
        self._running = False

    @property
    def clock_us(self) -> int:
        """Current simulation logical clock in microseconds."""
        return self._clock_us

    def peek_next_event_us(self) -> Optional[int]:
        """Return time of next scheduled event, or None if queue is empty."""
        return self._heap[0].next_tx_us if self._heap else None

    def stats(self) -> Dict[str, int]:
        """Return number of pending events per bus."""
        counts: Dict[str, int] = {}
        for ev in self._heap:
            counts[ev.bus_id] = counts.get(ev.bus_id, 0) + 1
        return counts
