"""
Scenario Engine
Loads YAML flight scenario files and drives LRU models and fault injection.
"""

from __future__ import annotations
import yaml
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from ..lrus.models import (
    VirtualLRU, VirtualADIRU, VirtualILS, VirtualRadioAltimeter,
    VirtualFMC, VirtualTransponder
)
from ..engine.fault import FaultInjector, FaultRecord, FaultType


@dataclass
class FlightPhase:
    at_s: float
    state: str
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ScenarioConfig:
    name: str
    duration_s: float
    time_scale: float
    lru_configs: List[Dict]
    phases: List[FlightPhase]
    fault_configs: List[Dict]


class ScenarioEngine:
    """
    Loads a scenario YAML file and manages:
    - LRU instantiation and configuration
    - Flight phase transitions
    - Fault injection scheduling

    Example YAML structure:
    -------------------------
    scenario:
      name: "ILS Approach"
      duration_s: 600
      time_scale: 1.0

    lrus:
      - id: ADIRU_1
        type: ADIRU
        bus: BUS_1
        speed: HS
        labels:
          0o101: { rate_hz: 50 }
          0o203: { rate_hz: 25, initial: 3000.0 }

    phases:
      - at_s: 0
        state: CRUISE
        params:
          altitude_ft: 35000
          vs_fpm: 0
          ias_kts: 250
      - at_s: 120
        state: DESCENT
        params:
          altitude_ft: 35000
          vs_fpm: -1500
          ias_kts: 280

    faults:
      - id: F001
        type: SSM_FAILURE_WARNING
        trigger_time_s: 300
        duration_s: 30
        lru: ADIRU_1
        label: 0o203
    """

    # LRU type factory map
    LRU_FACTORY = {
        "ADIRU":    VirtualADIRU,
        "ILS":      VirtualILS,
        "RA":       VirtualRadioAltimeter,
        "FMC":      VirtualFMC,
        "ATC_XPDR": VirtualTransponder,
    }

    def __init__(self):
        self.lrus: Dict[str, VirtualLRU] = {}
        self.fault_injector = FaultInjector()
        self._phases: List[FlightPhase] = []
        self._current_phase_idx: int = 0
        self._sim_time_s: float = 0.0
        self.config: Optional[ScenarioConfig] = None

    def load_yaml(self, path: str) -> ScenarioConfig:
        """Load and parse a YAML scenario file."""
        with open(path, "r") as f:
            raw = yaml.safe_load(f)
        return self._parse(raw)

    def load_dict(self, raw: dict) -> ScenarioConfig:
        """Load scenario from an in-memory dict (useful for tests)."""
        return self._parse(raw)

    def _parse(self, raw: dict) -> ScenarioConfig:
        sc = raw.get("scenario", {})
        config = ScenarioConfig(
            name=sc.get("name", "unnamed"),
            duration_s=float(sc.get("duration_s", 600)),
            time_scale=float(sc.get("time_scale", 1.0)),
            lru_configs=raw.get("lrus", []),
            phases=[
                FlightPhase(
                    at_s=float(p["at_s"]),
                    state=p.get("state", "UNKNOWN"),
                    params=p.get("params", {})
                )
                for p in raw.get("phases", [])
            ],
            fault_configs=raw.get("faults", []),
        )
        config.phases.sort(key=lambda p: p.at_s)
        self.config = config
        return config

    def build_lrus(self) -> Dict[str, VirtualLRU]:
        """Instantiate all LRUs from the loaded config."""
        if self.config is None:
            raise RuntimeError("No scenario loaded. Call load_yaml() or load_dict() first.")

        for lru_cfg in self.config.lru_configs:
            lru_id = lru_cfg["id"]
            lru_type = lru_cfg["type"]
            bus_id = lru_cfg.get("bus", "BUS_1")
            sdi = int(lru_cfg.get("sdi", 0))

            factory = self.LRU_FACTORY.get(lru_type)
            if factory is None:
                raise ValueError(f"Unknown LRU type: {lru_type}")

            lru = factory(lru_id=lru_id, bus_id=bus_id, sdi=sdi)

            # Apply initial label values if specified
            labels_cfg = lru_cfg.get("labels", {})
            if isinstance(lru, VirtualADIRU):
                init_params = {}
                for label_key, lcfg in labels_cfg.items():
                    initial = lcfg.get("initial")
                    if initial is not None:
                        label_oct = int(str(label_key), 8) if isinstance(label_key, str) else label_key
                        if label_oct == 0o203:
                            init_params["altitude_ft"] = float(initial)
                        elif label_oct == 0o204:
                            init_params["ias_kts"] = float(initial)
                        elif label_oct == 0o206:
                            init_params["vs_fpm"] = float(initial)
                        elif label_oct == 0o101:
                            init_params["pitch_deg"] = float(initial)
                        elif label_oct == 0o102:
                            init_params["roll_deg"] = float(initial)
                if init_params:
                    lru.set_flight_params(**init_params)

            self.lrus[lru_id] = lru

        return self.lrus

    def build_fault_injector(self) -> FaultInjector:
        """Schedule all faults from loaded config into the fault injector."""
        if self.config is None:
            raise RuntimeError("No scenario loaded.")

        for fc in self.config.fault_configs:
            fault_type_str = fc.get("type", "").upper()
            try:
                fault_type = FaultType[fault_type_str]
            except KeyError:
                print(f"[ScenarioEngine] Warning: unknown fault type '{fault_type_str}' – skipping")
                continue

            label_raw = fc.get("label")
            label_oct = None
            if label_raw is not None:
                if isinstance(label_raw, str) and label_raw.startswith("0o"):
                    label_oct = int(label_raw, 8)
                else:
                    label_oct = int(label_raw)

            record = FaultRecord(
                fault_id=fc.get("id", f"fault_{len(self.fault_injector._faults)}"),
                fault_type=fault_type,
                trigger_time_s=float(fc.get("trigger_time_s", 0)),
                duration_s=float(fc.get("duration_s", 0)),
                target_lru=fc.get("lru"),
                target_label=label_oct,
                target_bus=fc.get("bus"),
                probability=float(fc.get("probability", 1.0)),
                override_value=fc.get("override_value"),
                bit_positions=fc.get("bit_positions"),
                sdi_override=fc.get("sdi_override"),
            )
            self.fault_injector.schedule(record)

        return self.fault_injector

    def update(self, sim_time_s: float, delta_t_s: float) -> None:
        """
        Advance the scenario: apply phase transitions and update all LRU models.
        Should be called once per simulation tick.
        """
        self._sim_time_s = sim_time_s

        # Check for phase transitions
        if self.config:
            while (self._current_phase_idx < len(self.config.phases) - 1 and
                   sim_time_s >= self.config.phases[self._current_phase_idx + 1].at_s):
                self._current_phase_idx += 1
                self._apply_phase(self.config.phases[self._current_phase_idx])

        # Update all LRU models
        for lru in self.lrus.values():
            lru.update(delta_t_s)

    def _apply_phase(self, phase: FlightPhase) -> None:
        """Apply a flight phase's parameters to the appropriate LRUs."""
        params = phase.params
        for lru in self.lrus.values():
            if isinstance(lru, VirtualADIRU):
                lru.set_flight_params(
                    altitude_ft=params.get("altitude_ft"),
                    vs_fpm=params.get("vs_fpm"),
                    ias_kts=params.get("ias_kts"),
                    pitch_deg=params.get("pitch_deg"),
                    roll_deg=params.get("roll_deg"),
                    heading_deg=params.get("heading_deg"),
                    ground_speed_kts=params.get("ground_speed_kts"),
                )
            elif isinstance(lru, VirtualILS):
                if "localizer_ddm" in params:
                    lru.localizer_ddm = params["localizer_ddm"]
                if "glideslope_ddm" in params:
                    lru.glideslope_ddm = params["glideslope_ddm"]
            elif isinstance(lru, VirtualRadioAltimeter):
                if "radio_alt_ft" in params:
                    lru.radio_alt_ft = params["radio_alt_ft"]

    def get_label_rate(self, lru_id: str, label_oct: int) -> float:
        """Return transmission rate for a label from config, or label DB default."""
        if self.config is None:
            return 25.0
        for lru_cfg in self.config.lru_configs:
            if lru_cfg["id"] == lru_id:
                labels = lru_cfg.get("labels", {})
                for lk, lv in labels.items():
                    oct_val = int(str(lk), 8) if isinstance(lk, str) and str(lk).startswith("0o") else int(lk)
                    if oct_val == label_oct:
                        return float(lv.get("rate_hz", 25.0))
        return 25.0
