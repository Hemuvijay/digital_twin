"""
Simulation Orchestrator
Wires together the Bus Scheduler, Scenario Engine, LRU models,
Fault Injector, Bus Monitor, and Data Logger into a single runnable simulation.
"""

from __future__ import annotations
import time
from typing import Dict, List, Optional, Callable
from ..core.bus import BusScheduler
from ..core.label_db import get_label
from ..core.word import ARINC429Word
from ..engine.scenario import ScenarioEngine
from ..engine.fault import FaultInjector
from ..monitor.logger import BusLogger
from ..monitor.monitor import BusMonitor
from ..validation.engine import ValidationEngine


class Simulation:
    """
    Top-level simulation controller.

    Usage
    -----
    sim = Simulation()
    sim.load_scenario("config/scenarios/ils_approach.yaml")
    sim.run()
    report = sim.validation_report()
    """

    def __init__(self, real_time: bool = True, time_scale: float = 1.0):
        self.real_time   = real_time
        self.time_scale  = time_scale

        self.scenario    = ScenarioEngine()
        self.scheduler   = BusScheduler(real_time=real_time, time_scale=time_scale)
        self.monitor     = BusMonitor()
        self.logger      = BusLogger()
        self.validator   = ValidationEngine()
        self._extra_listeners: List[Callable[[ARINC429Word], None]] = []
        self._loaded     = False

    def load_scenario(self, path: str) -> None:
        """Load a YAML scenario file."""
        self.scenario.load_yaml(path)
        self._setup()

    def load_scenario_dict(self, raw: dict) -> None:
        """Load a scenario from an in-memory dictionary (useful for tests)."""
        self.scenario.load_dict(raw)
        self._setup()

    def _setup(self) -> None:
        """Wire all subsystems together after scenario is loaded."""
        config = self.scenario.config

        # ── Build LRUs ────────────────────────────────────────────────────
        lrus = self.scenario.build_lrus()

        # ── Register channels ─────────────────────────────────────────────
        seen_buses = set()
        for lru_cfg in config.lru_configs:
            bus_id = lru_cfg.get("bus", "CHANNEL_1")
            speed  = lru_cfg.get("speed", "HS")
            if bus_id not in seen_buses:
                self.scheduler.register_bus(bus_id, speed)
                seen_buses.add(bus_id)

        # ── Schedule labels ───────────────────────────────────────────────
        for lru_cfg in config.lru_configs:
            lru_id  = lru_cfg["id"]
            bus_id  = lru_cfg.get("bus", "CHANNEL_1")
            lru     = lrus.get(lru_id)
            if lru is None:
                continue

            labels_cfg = lru_cfg.get("labels", {})

            if labels_cfg:
                scheduled_labels = []
                for lk, lv in labels_cfg.items():
                    if isinstance(lk, str) and lk.startswith("0o"):
                        lo = int(lk, 8)
                    else:
                        lo = int(lk)
                    rate = float(lv.get("rate_hz", 25.0)) if isinstance(lv, dict) else 25.0
                    if lo in lru.get_supported_labels():
                        scheduled_labels.append((lo, rate))
            else:
                scheduled_labels = [
                    (lo, self.scenario.get_label_rate(lru_id, lo))
                    for lo in lru.get_supported_labels()
                ]

            for i, (label_oct, rate_hz) in enumerate(scheduled_labels):
                offset_us = i * max(1, int(1_000_000 / rate_hz /
                                            max(1, len(scheduled_labels))))

                def make_gen(l=lru, lo=label_oct):
                    def gen():
                        return l.get_word(lo, timestamp_us=self.scheduler.clock_us)
                    return gen

                self.scheduler.schedule_label(
                    bus_id=bus_id,
                    lru_id=lru_id,
                    label_oct=label_oct,
                    rate_hz=rate_hz,
                    word_gen=make_gen(),
                    initial_offset_us=offset_us,
                )

        # ── Build fault injector ──────────────────────────────────────────
        fault_injector = self.scenario.build_fault_injector()
        self.scheduler.set_fault_hook(fault_injector.process)

        # ── Wire passive listeners FIRST so TX is logged before RX ───────
        self.scheduler.add_listener(self.monitor.ingest)
        self.scheduler.add_listener(self.logger.write)
        self.scheduler.add_listener(self.validator.check)
        for cb in self._extra_listeners:
            self.scheduler.add_listener(cb)

        # ── Wire closed-loop subscriptions AFTER passive listeners ────────
        # RX callbacks fire after TX is already written to the logger,
        # so TX row always appears before its RX rows in the CSV.
        for lru_cfg in config.lru_configs:
            lru = lrus.get(lru_cfg["id"])
            if lru is None:
                continue
            for sub in lru_cfg.get("subscribes", []):
                channel = sub.get("channel", "")
                for label_raw in sub.get("labels", []):
                    if isinstance(label_raw, str) and label_raw.startswith("0o"):
                        lo = int(label_raw, 8)
                    else:
                        lo = int(label_raw)
                    lru.subscribe(lo)

                self.logger.register_channel_receivers(channel, [lru.lru_id])

                def make_rx(receiving_lru=lru, target_channel=channel):
                    def rx(word: ARINC429Word):
                        if word.bus_id == target_channel:
                            receiving_lru.consume_word(word)
                            self.logger.write_rx(receiving_lru.lru_id, word)
                    return rx

                self.scheduler.add_listener(make_rx())

        self._loaded = True

    def add_listener(self, cb: Callable[[ARINC429Word], None]) -> None:
        """Register an additional word listener (call before run)."""
        self._extra_listeners.append(cb)
        if self._loaded:
            self.scheduler.add_listener(cb)

    def load_test_vectors(self, path: str) -> None:
        """Load YAML test vectors into the validation engine."""
        self.validator.load_vectors_yaml(path)

    def run(self, duration_s: Optional[float] = None) -> None:
        """
        Run the simulation.
        If duration_s is not provided, uses the scenario's configured duration.
        """
        if not self._loaded:
            raise RuntimeError("No scenario loaded. Call load_scenario() first.")

        config = self.scenario.config
        total_s = duration_s if duration_s is not None else config.duration_s
        total_us = int(total_s * 1_000_000)

        # Run in chunks of 100 ms to allow scenario update (phase transitions)
        chunk_us = 100_000   # 100 ms
        sim_time_s = 0.0
        elapsed = 0

        print(f"[Simulation] Starting '{config.name}' – {total_s:.1f}s simulated time")
        t_wall_start = time.monotonic()

        while elapsed < total_us:
            step_us = min(chunk_us, total_us - elapsed)
            self.scheduler.run(step_us)
            elapsed += step_us
            sim_time_s = elapsed / 1_000_000.0
            self.scenario.update(sim_time_s, step_us / 1_000_000.0)

        t_wall_end = time.monotonic()
        wall_s = t_wall_end - t_wall_start
        print(f"[Simulation] Completed in {wall_s:.3f}s wall-clock time")
        print(f"[Simulation] Total words dispatched: {self.monitor.total_words}")
        print(f"[Simulation] Parity errors seen:     {self.monitor.parity_error_count}")

    def validation_report(self) -> dict:
        """Return a summary dict of validation results."""
        return self.validator.summary()

    def print_monitor_table(self) -> None:
        """Print current value of all active labels to stdout."""
        self.monitor.print_table()