"""
Validation Engine
Evaluates test vectors against live bus traffic.
Produces pass/fail results per vector and a structured summary report.
"""

from __future__ import annotations
import yaml
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional
from ..core.word import ARINC429Word
from ..core.label_db import get_label  # noqa


class VectorResult(Enum):
    PASS    = "PASS"
    FAIL    = "FAIL"
    PENDING = "PENDING"   # Trigger time not yet reached
    MISSED  = "MISSED"    # Trigger time passed but no word seen


@dataclass
class TestVector:
    """A single test assertion against a bus label at a given simulation time."""
    vector_id: str
    description: str
    at_sim_time_s: float
    bus_id: Optional[str]
    lru_id: Optional[str]
    label_oct: int
    expected_ssm: Optional[int]         # e.g. 0b11 for NORMAL
    expected_value_min: Optional[float]
    expected_value_max: Optional[float]
    expected_units: Optional[str]
    expected_parity_ok: bool = True
    tolerance_pct: float = 1.0          # % tolerance on value checks
    window_us: int = 1_000_000          # µs window after at_sim_time_s to look for word

    # Runtime state
    result: VectorResult = VectorResult.PENDING
    actual_value: Optional[float] = None
    actual_ssm: Optional[int] = None
    actual_parity_ok: Optional[bool] = None
    actual_timestamp_us: int = 0
    failure_reason: str = ""


class ValidationEngine:
    """
    Subscribes to the word stream from the scheduler.
    Matches words against registered test vectors and records pass/fail.
    """

    def __init__(self):
        self._vectors: List[TestVector] = []
        self._word_cache: Dict[tuple, ARINC429Word] = {}  # (bus, lru, label) → last word
        self._deviation_log: List[dict] = []

    def load_vectors_yaml(self, path: str) -> None:
        """Load test vectors from a YAML file."""
        with open(path, "r") as f:
            raw = yaml.safe_load(f)
        for tv in raw.get("test_vectors", []):
            label_raw = tv.get("label", 0)
            if isinstance(label_raw, str) and label_raw.startswith("0o"):
                label_oct = int(label_raw, 8)
            else:
                label_oct = int(label_raw)

            exp = tv.get("expected", {})
            ssm_raw = exp.get("ssm")
            if isinstance(ssm_raw, str):
                ssm_val = int(ssm_raw, 2) if ssm_raw.startswith("0b") else int(ssm_raw)
            elif ssm_raw is not None:
                ssm_val = int(ssm_raw)
            else:
                ssm_val = None

            self._vectors.append(TestVector(
                vector_id=tv.get("id", f"TV-{len(self._vectors)+1:03d}"),
                description=tv.get("description", ""),
                at_sim_time_s=float(tv.get("at_sim_time_s", 0)),
                bus_id=tv.get("bus"),
                lru_id=tv.get("lru"),
                label_oct=label_oct,
                expected_ssm=ssm_val,
                expected_value_min=exp.get("value_min"),
                expected_value_max=exp.get("value_max"),
                expected_units=exp.get("units"),
                expected_parity_ok=exp.get("parity_ok", True),
                tolerance_pct=float(tv.get("tolerance_pct", 1.0)),
                window_us=int(tv.get("window_us", 1_000_000)),
            ))

    def add_vector(self, vector: TestVector) -> None:
        """Add a test vector programmatically."""
        self._vectors.append(vector)

    def check(self, word: ARINC429Word) -> None:
        """
        Called for every dispatched word.
        Checks the word against all pending test vectors.
        """
        key = (word.bus_id, word.lru_id, word.label_oct)
        self._word_cache[key] = word

        sim_time_s = word.timestamp_us / 1_000_000.0

        for tv in self._vectors:
            if tv.result != VectorResult.PENDING:
                continue

            # Check if this word matches the vector's target
            if tv.label_oct != word.label_oct:
                continue
            if tv.bus_id and tv.bus_id != word.bus_id:
                continue
            if tv.lru_id and tv.lru_id != word.lru_id:
                continue

            # Check if we're within the evaluation window
            trigger_s = tv.at_sim_time_s
            window_end_s = trigger_s + tv.window_us / 1_000_000.0
            if sim_time_s < trigger_s:
                continue                          # Word arrived before trigger — wait
            if sim_time_s > window_end_s:
                tv.result = VectorResult.MISSED
                tv.failure_reason = (f"No word seen in window "
                                     f"[{trigger_s:.3f}, {window_end_s:.3f}]s")
                continue

            # Evaluate the word
            tv.actual_timestamp_us = word.timestamp_us
            tv.actual_ssm = word.ssm
            tv.actual_parity_ok = word.parity_ok
            tv.actual_value = word.decoded_value

            failures = []

            if tv.expected_parity_ok and not word.parity_ok:
                failures.append(f"parity FAIL (expected OK)")

            if tv.expected_ssm is not None and word.ssm != tv.expected_ssm:
                failures.append(
                    f"SSM FAIL: expected {tv.expected_ssm:02b} got {word.ssm:02b}"
                )

            if tv.expected_value_min is not None or tv.expected_value_max is not None:
                val = word.decoded_value
                if val is None:
                    failures.append("value FAIL: no decoded value available")
                else:
                    vmin = tv.expected_value_min
                    vmax = tv.expected_value_max
                    # Apply tolerance
                    if vmin is not None and val < vmin * (1 - tv.tolerance_pct / 100.0):
                        failures.append(f"value {val:.4f} < min {vmin} (tol {tv.tolerance_pct}%)")
                    if vmax is not None and val > vmax * (1 + tv.tolerance_pct / 100.0):
                        failures.append(f"value {val:.4f} > max {vmax} (tol {tv.tolerance_pct}%)")

            if failures:
                tv.result = VectorResult.FAIL
                tv.failure_reason = "; ".join(failures)
                self._deviation_log.append({
                    "vector_id":  tv.vector_id,
                    "result":     "FAIL",
                    "timestamp_us": word.timestamp_us,
                    "label_oct":  f"0o{tv.label_oct:o}",
                    "reason":     tv.failure_reason,
                    "actual_value": tv.actual_value,
                })
            else:
                tv.result = VectorResult.PASS

    def finalize(self) -> None:
        """Mark any still-pending vectors as MISSED after simulation ends."""
        for tv in self._vectors:
            if tv.result == VectorResult.PENDING:
                tv.result = VectorResult.MISSED
                tv.failure_reason = "Simulation ended before trigger time"

    def summary(self) -> dict:
        """Return a structured summary of all test results."""
        self.finalize()
        total   = len(self._vectors)
        passed  = sum(1 for v in self._vectors if v.result == VectorResult.PASS)
        failed  = sum(1 for v in self._vectors if v.result == VectorResult.FAIL)
        missed  = sum(1 for v in self._vectors if v.result == VectorResult.MISSED)
        pending = sum(1 for v in self._vectors if v.result == VectorResult.PENDING)

        return {
            "total":   total,
            "passed":  passed,
            "failed":  failed,
            "missed":  missed,
            "pending": pending,
            "pass_rate_pct": round(100.0 * passed / total, 1) if total > 0 else 0.0,
            "vectors": [
                {
                    "id":           v.vector_id,
                    "description":  v.description,
                    "result":       v.result.value,
                    "actual_value": v.actual_value,
                    "actual_ssm":   f"{v.actual_ssm:02b}" if v.actual_ssm is not None else None,
                    "reason":       v.failure_reason,
                }
                for v in self._vectors
            ],
            "deviations": self._deviation_log,
        }

    def print_report(self) -> None:
        """Print a human-readable validation report."""
        s = self.summary()
        print("\n" + "="*70)
        print("VALIDATION REPORT")
        print("="*70)
        print(f"  Total vectors : {s['total']}")
        print(f"  PASSED        : {s['passed']}")
        print(f"  FAILED        : {s['failed']}")
        print(f"  MISSED        : {s['missed']}")
        print(f"  Pass rate     : {s['pass_rate_pct']}%")
        print("-"*70)
        for v in s["vectors"]:
            icon = {"PASS": "✓", "FAIL": "✗", "MISSED": "?", "PENDING": "…"}.get(v["result"], "?")
            print(f"  [{icon}] {v['id']:<12} {v['result']:<8}  {v['description']}")
            if v["result"] in ("FAIL", "MISSED") and v["reason"]:
                print(f"           → {v['reason']}")
        print("="*70 + "\n")
