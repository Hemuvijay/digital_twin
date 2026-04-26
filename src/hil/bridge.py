"""
HIL Bridge – Hardware-in-the-Loop Abstraction Layer
Provides a hardware-agnostic interface for ARINC 429 interface cards.
Concrete drivers (AIM, Astronics, DDC) implement HilCardBase.
"""

from __future__ import annotations
import queue
import threading
from abc import ABC, abstractmethod
from typing import List, Optional, Callable
from ..core.word import ARINC429Word


class HilCardBase(ABC):
    """
    Abstract base class for ARINC 429 hardware card drivers.
    Subclass this for each vendor card (AIM PBA.pro, Astronics, DDC BU-67120).
    """

    @abstractmethod
    def open(self, channel: int, speed: str = "HS") -> None:
        """Initialize and open a hardware channel."""

    @abstractmethod
    def close(self) -> None:
        """Release the hardware channel."""

    @abstractmethod
    def transmit(self, word: int) -> None:
        """Transmit a raw 32-bit word on the TX channel."""

    @abstractmethod
    def receive(self) -> List[tuple]:
        """
        Non-blocking receive. Returns list of (timestamp_us, raw_word) tuples
        from the hardware RX buffer.
        """

    @abstractmethod
    def sync_clock(self) -> int:
        """Return current hardware timestamp in microseconds."""


class SoftwareLoopbackCard(HilCardBase):
    """
    Software loopback card for testing without physical hardware.
    Words transmitted on TX are immediately available on RX.
    """

    def __init__(self):
        self._rx_queue: queue.Queue = queue.Queue()
        self._clock_us: int = 0
        self._open = False

    def open(self, channel: int = 1, speed: str = "HS") -> None:
        self._open = True
        print(f"[SoftwareLoopbackCard] Channel {channel} opened ({speed})")

    def close(self) -> None:
        self._open = False

    def transmit(self, word: int) -> None:
        self._rx_queue.put((self._clock_us, word & 0xFFFFFFFF))
        self._clock_us += 320  # HS word duration

    def receive(self) -> List[tuple]:
        results = []
        while not self._rx_queue.empty():
            try:
                results.append(self._rx_queue.get_nowait())
            except queue.Empty:
                break
        return results

    def sync_clock(self) -> int:
        return self._clock_us


class HilBridge:
    """
    Bridges a hardware ARINC 429 card into the digital twin simulation.

    In HIL mode:
    - Words received from the physical bus are fed into the twin's monitor and validator.
    - The twin's virtual LRU words are transmitted back on the hardware card TX.

    Usage
    -----
    card = SoftwareLoopbackCard()   # or AimCard(), AstroniCard(), etc.
    bridge = HilBridge(card)
    bridge.start()
    # ... simulation runs ...
    bridge.stop()
    words = bridge.drain_received()
    """

    def __init__(self, card: HilCardBase,
                 channel: int = 1, speed: str = "HS"):
        self._card = card
        self._channel = channel
        self._speed = speed
        self._listeners: List[Callable[[ARINC429Word], None]] = []
        self._rx_buffer: List[ARINC429Word] = []
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._clock_offset_us: int = 0

    def add_listener(self, cb: Callable[[ARINC429Word], None]) -> None:
        """Register a callback invoked for each word received from hardware."""
        self._listeners.append(cb)

    def start(self, bus_id: str = "HIL_BUS") -> None:
        """Open the card and start the background RX polling thread."""
        self._card.open(self._channel, self._speed)
        self._bus_id = bus_id
        self._calibrate_clock()
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        print(f"[HilBridge] Started on channel {self._channel} ({self._speed})")

    def stop(self) -> None:
        """Stop polling and close the card."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        self._card.close()

    def transmit(self, word: ARINC429Word) -> None:
        """Transmit a twin-generated word onto the hardware bus."""
        self._card.transmit(word.raw_word)

    def drain_received(self) -> List[ARINC429Word]:
        """Return and clear all words received since last drain."""
        words = list(self._rx_buffer)
        self._rx_buffer.clear()
        return words

    def _calibrate_clock(self) -> None:
        """Compute clock offset between hardware card and simulation."""
        hw_us = self._card.sync_clock()
        self._clock_offset_us = hw_us
        print(f"[HilBridge] Clock calibrated: hardware offset = {hw_us} µs")

    def _poll_loop(self) -> None:
        """Background thread: continuously drain hardware RX buffer."""
        import time
        while self._running:
            received = self._card.receive()
            for hw_ts_us, raw_word in received:
                sim_ts_us = hw_ts_us - self._clock_offset_us
                word = ARINC429Word.from_raw(
                    raw_word, timestamp_us=sim_ts_us,
                    bus_id=self._bus_id, lru_id="HW_LRU"
                )
                self._rx_buffer.append(word)
                for cb in self._listeners:
                    cb(word)
            time.sleep(0.001)  # 1 ms poll interval