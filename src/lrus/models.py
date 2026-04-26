"""
Virtual LRU Models
Base class + concrete implementations for ADIRU, ILS, Radio Altimeter,
FMC (stub), and Transponder.
"""

from __future__ import annotations
import math
import random
from enum import Enum
from typing import Dict, Optional, Callable
from ..core.word import ARINC429Word
from ..core.codec import BNRCodec, BCDCodec, DiscreteCodec
from ..core.label_db import get_label  # noqa: label_db is the correct filename


# ─────────────────────────────────────────────────────────────────────────────
# LRU State Machine
# ─────────────────────────────────────────────────────────────────────────────

class LRUState(Enum):
    POWER_OFF        = "POWER_OFF"
    INITIALIZING     = "INITIALIZING"
    NORMAL           = "NORMAL"
    FAILURE          = "FAILURE"
    FUNCTIONAL_TEST  = "FUNCTIONAL_TEST"

    def to_ssm(self) -> int:
        return {
            LRUState.POWER_OFF:       ARINC429Word.SSM_NCD,
            LRUState.INITIALIZING:    ARINC429Word.SSM_NCD,
            LRUState.NORMAL:          ARINC429Word.SSM_NORMAL,
            LRUState.FAILURE:         ARINC429Word.SSM_FAILURE_WARNING,
            LRUState.FUNCTIONAL_TEST: ARINC429Word.SSM_FUNCTIONAL_TEST,
        }[self]


# ─────────────────────────────────────────────────────────────────────────────
# VirtualLRU – Base Class
# ─────────────────────────────────────────────────────────────────────────────

class VirtualLRU:
    """
    Base class for all virtual LRU models.

    Subclasses must implement:
        _build_codecs()   – populate self._codecs with label_oct → codec
        _compute_value()  – return current float value for a label
        update()          – advance internal model state by delta_t seconds
    """

    def __init__(self, lru_id: str, lru_type: str,
                 bus_id: str = "BUS_1", sdi: int = 0):
        self.lru_id   = lru_id
        self.lru_type = lru_type
        self.bus_id   = bus_id
        self.sdi      = sdi
        self._state   = LRUState.INITIALIZING
        self._init_elapsed_s: float = 0.0
        self._init_duration_s: float = 2.0    # Time to leave INITIALIZING
        self._codecs: Dict[int, object] = {}   # label_oct → codec
        self._build_codecs()

    # ── State machine ─────────────────────────────────────────────────────

    @property
    def state(self) -> LRUState:
        return self._state

    def set_state(self, new_state: LRUState) -> None:
        old = self._state
        self._state = new_state
        self.on_state_change(old, new_state)

    def on_state_change(self, old: LRUState, new: LRUState) -> None:
        """Override to handle state transitions."""
        pass

    # ── Core interface ────────────────────────────────────────────────────

    def _build_codecs(self) -> None:
        """Populate self._codecs. Called once at construction."""
        raise NotImplementedError

    def _compute_value(self, label_oct: int) -> float:
        """Return current engineering-unit value for label_oct."""
        raise NotImplementedError

    def update(self, delta_t_s: float) -> None:
        """Advance internal model state. Called by scenario engine each tick."""
        if self._state == LRUState.INITIALIZING:
            self._init_elapsed_s += delta_t_s
            if self._init_elapsed_s >= self._init_duration_s:
                self.set_state(LRUState.NORMAL)

    def get_word(self, label_oct: int, timestamp_us: int = 0) -> ARINC429Word:
        """
        Generate a fresh ARINC 429 word for the requested label.
        Returns NCD if in INITIALIZING state; Failure Warning if FAILURE.
        """
        codec = self._codecs.get(label_oct)
        if codec is None:
            raise KeyError(f"{self.lru_id}: label 0o{label_oct:o} not supported")

        ssm = self._state.to_ssm()

        if isinstance(codec, BNRCodec):
            value = self._compute_value(label_oct) if ssm == ARINC429Word.SSM_NORMAL else 0.0
            return codec.encode(value, ssm=ssm, sdi=self.sdi,
                                lru_id=self.lru_id, bus_id=self.bus_id,
                                timestamp_us=timestamp_us)
        elif isinstance(codec, BCDCodec):
            value = self._compute_value(label_oct) if ssm == ARINC429Word.SSM_NORMAL else 0.0
            return codec.encode(value, ssm=ssm, sdi=self.sdi,
                                lru_id=self.lru_id, bus_id=self.bus_id,
                                timestamp_us=timestamp_us)
        elif isinstance(codec, DiscreteCodec):
            signals = self._compute_discrete(label_oct) if ssm == ARINC429Word.SSM_NORMAL else {}
            return codec.encode(signals, ssm=ssm, sdi=self.sdi,
                                lru_id=self.lru_id, bus_id=self.bus_id,
                                timestamp_us=timestamp_us)
        else:
            raise TypeError(f"Unknown codec type for label 0o{label_oct:o}")

    def _compute_discrete(self, label_oct: int) -> dict:
        """Override for discrete-output labels."""
        return {}

    def get_supported_labels(self) -> list:
        return list(self._codecs.keys())

    # ── Receiver interface (closed loop) ──────────────────────────────────

    def consume_word(self, word: ARINC429Word) -> None:
        """Called by the scheduler when a word arrives on a subscribed channel.
        Stores decoded value and calls _on_receive() for subclass logic."""
        if not hasattr(self, '_subscribed_labels'):
            self._subscribed_labels: set = set()
            self._received: Dict[int, float] = {}
        if word.label_oct in self._subscribed_labels:
            if word.decoded_value is not None and word.ssm == ARINC429Word.SSM_NORMAL:
                self._received[word.label_oct] = word.decoded_value
                self._on_receive(word)

    def subscribe(self, label_oct: int) -> None:
        """Register interest in a label from another LRU's channel."""
        if not hasattr(self, '_subscribed_labels'):
            self._subscribed_labels: set = set()
            self._received: Dict[int, float] = {}
        self._subscribed_labels.add(label_oct)

    def get_received(self, label_oct: int, default: float = 0.0) -> float:
        """Return last received value for a label, or default if not yet received."""
        if not hasattr(self, '_received'):
            return default
        return self._received.get(label_oct, default)

    def _on_receive(self, word: ARINC429Word) -> None:
        """Override in subclass to react when a subscribed word arrives."""
        pass


# ─────────────────────────────────────────────────────────────────────────────
# ADIRU – Air Data / Inertial Reference Unit
# ─────────────────────────────────────────────────────────────────────────────

class VirtualADIRU(VirtualLRU):
    """
    Virtual ADIRU model.
    Maintains a simple flight state: altitude, speed, attitude, heading, position.
    All values drift realistically based on flight phase.
    """

    def __init__(self, lru_id: str = "ADIRU_1", bus_id: str = "CHANNEL_1", sdi: int = 0):
        # Flight state
        self.altitude_ft       = 0.0
        self.vs_fpm            = 0.0
        self.ias_kts           = 0.0
        self.tas_kts           = 0.0
        self.mach              = 0.0
        self.pitch_deg         = 0.0
        self.roll_deg          = 0.0
        self.true_heading_deg  = 360.0
        self.latitude_deg      = 51.5074   # Default: London
        self.longitude_deg     = -0.1278
        self.ground_speed_kts  = 0.0
        self.track_deg         = 0.0
        self.wind_speed_kts    = 15.0
        self.wind_dir_deg      = 270.0
        self.tat_degC          = 15.0
        self.sat_degC          = 15.0
        self._noise_seed       = random.Random(42)
        super().__init__(lru_id, "ADIRU", bus_id, sdi)

    def _build_codecs(self) -> None:
        self._codecs = {
            0o101: BNRCodec(0o101, 28, 11, 0.0055,   -180.0, 180.0),   # Pitch
            0o102: BNRCodec(0o102, 28, 11, 0.0055,   -180.0, 180.0),   # Roll
            0o103: BNRCodec(0o103, 28, 11, 0.0055,   0.0, 360.0),      # True heading
            0o203: BNRCodec(0o203, 28, 11, 4.0,      -2000.0, 50000.0), # Baro alt
            0o204: BNRCodec(0o204, 28, 11, 0.0625,   0.0, 500.0),      # IAS
            0o205: BNRCodec(0o205, 28, 11, 0.0625,   0.0, 600.0),      # TAS
            0o206: BNRCodec(0o206, 28, 11, 1.0,      -6000.0, 6000.0), # V/S
            0o210: BNRCodec(0o210, 28, 11, 0.000244, 0.0, 3.0),        # Mach
            0o211: BNRCodec(0o211, 28, 11, 0.0625,   -100.0, 60.0),    # TAT
            0o212: BNRCodec(0o212, 28, 11, 0.0625,   -100.0, 60.0),    # SAT
            0o270: BNRCodec(0o270, 28, 11, 0.0000021, -90.0, 90.0),    # Latitude
            0o271: BNRCodec(0o271, 28, 11, 0.0000043, -180.0, 180.0),  # Longitude
            0o312: BNRCodec(0o312, 28, 11, 0.0625,   0.0, 1000.0),     # GS
            0o313: BNRCodec(0o313, 28, 11, 0.0055,   0.0, 360.0),      # Track
            0o361: BNRCodec(0o361, 28, 11, 0.0625,   0.0, 250.0),      # Wind speed
            0o362: BNRCodec(0o362, 28, 11, 0.0055,   0.0, 360.0),      # Wind dir
        }

    def _compute_value(self, label_oct: int) -> float:
        noise = lambda s: self._noise_seed.gauss(0, s)
        return {
            0o101: self.pitch_deg + noise(0.01),
            0o102: self.roll_deg  + noise(0.01),
            0o103: self.true_heading_deg % 360.0 + noise(0.005),
            0o203: self.altitude_ft + noise(0.5),
            0o204: self.ias_kts + noise(0.1),
            0o205: self.tas_kts + noise(0.1),
            0o206: self.vs_fpm + noise(2.0),
            0o210: self.mach + noise(0.0001),
            0o211: self.tat_degC + noise(0.05),
            0o212: self.sat_degC + noise(0.05),
            0o270: self.latitude_deg,
            0o271: self.longitude_deg,
            0o312: self.ground_speed_kts + noise(0.1),
            0o313: self.track_deg % 360.0,
            0o361: self.wind_speed_kts,
            0o362: self.wind_dir_deg,
        }.get(label_oct, 0.0)

    def update(self, delta_t_s: float) -> None:
        super().update(delta_t_s)
        if self._state != LRUState.NORMAL:
            return

        # Update altitude from V/S
        self.altitude_ft += self.vs_fpm * delta_t_s / 60.0
        self.altitude_ft = max(-2000.0, min(50000.0, self.altitude_ft))

        # Compute Mach from TAS (ISA approximation)
        speed_of_sound = 340.29 - 1.0 * max(0, self.altitude_ft / 1000.0 * 1.98)
        self.mach = self.tas_kts * 0.514444 / speed_of_sound if speed_of_sound > 0 else 0

        # Update position from ground speed and track
        if self.ground_speed_kts > 0:
            dist_nm = self.ground_speed_kts * delta_t_s / 3600.0
            dlat = dist_nm / 60.0 * math.cos(math.radians(self.track_deg))
            dlon = dist_nm / 60.0 * math.sin(math.radians(self.track_deg)) / max(0.01, math.cos(math.radians(self.latitude_deg)))
            self.latitude_deg  += dlat
            self.longitude_deg += dlon

        # Atmosphere
        if self.altitude_ft > 36089:
            self.sat_degC = -56.5
        else:
            self.sat_degC = 15.0 - 0.00198 * self.altitude_ft
        self.tat_degC = self.sat_degC + 0.2 * self.mach ** 2 * 288.15 / 2.0 if self.mach > 0 else self.sat_degC

    def set_flight_params(self, altitude_ft: float = None, vs_fpm: float = None,
                          ias_kts: float = None, pitch_deg: float = None,
                          roll_deg: float = None, heading_deg: float = None,
                          ground_speed_kts: float = None, track_deg: float = None) -> None:
        """Update flight parameters from scenario engine."""
        if altitude_ft      is not None: self.altitude_ft = altitude_ft
        if vs_fpm           is not None: self.vs_fpm = vs_fpm
        if ias_kts          is not None:
            self.ias_kts = ias_kts
            self.tas_kts = ias_kts * (1.0 + self.altitude_ft / 1000.0 * 0.02)
        if pitch_deg        is not None: self.pitch_deg = pitch_deg
        if roll_deg         is not None: self.roll_deg = roll_deg
        if heading_deg      is not None: self.true_heading_deg = heading_deg
        if ground_speed_kts is not None: self.ground_speed_kts = ground_speed_kts
        if track_deg        is not None: self.track_deg = track_deg

    def _on_receive(self, word: ARINC429Word) -> None:
        """Closed loop — ADIRU reacts to guidance from FMC and altitude from XPDR."""
        lbl = word.label_oct
        val = word.decoded_value
        if lbl == 0o106:
            # Cross-track from FMC: nudge track_deg to correct lateral deviation
            # Positive cross-track = right of track → turn left (decrease track)
            correction = val * 2.0   # 2 deg per nm cross-track error
            self.track_deg = (self.track_deg - correction) % 360.0
        elif lbl == 0o107:
            # Desired track from FMC: steer toward it gradually
            error = (val - self.track_deg + 540.0) % 360.0 - 180.0
            self.track_deg = (self.track_deg + error * 0.05) % 360.0
        elif lbl == 0o164:
            # Radio altitude from RA: blend into altitude below 2500ft
            if val < 2500.0 and val >= 0.0:
                blend = max(0.0, 1.0 - val / 2500.0)   # 0 at 2500ft, 1 at 0ft
                self.altitude_ft = self.altitude_ft * (1.0 - blend * 0.1) + val * (blend * 0.1)
        elif lbl == 0o013:
            # Mode C altitude from XPDR: cross-check, log discrepancy if > 300ft
            if abs(val - self.altitude_ft) > 300.0:
                pass   # in real hardware this triggers an altitude discrepancy flag


# ─────────────────────────────────────────────────────────────────────────────
# ILS Receiver
# ─────────────────────────────────────────────────────────────────────────────

class VirtualILS(VirtualLRU):
    """Virtual ILS receiver model.
    Receives selected altitude from FMC to adjust glideslope intercept."""

    def __init__(self, lru_id: str = "ILS_1", bus_id: str = "CHANNEL_2", sdi: int = 0):
        self.localizer_ddm   = 0.0
        self.glideslope_ddm  = 0.0
        self.frequency_mhz   = 110.10
        self._rx_selected_alt_ft = 3000.0   # from FMC
        self._noise = random.Random(99)
        super().__init__(lru_id, "ILS", bus_id, sdi)

    def _build_codecs(self) -> None:
        self._codecs = {
            0o173: BNRCodec(0o173, 28, 11, 0.0000153, -0.2, 0.2),   # LOC
            0o175: BNRCodec(0o175, 28, 11, 0.0000077, -0.1, 0.1),   # GS
        }

    def _on_receive(self, word: ARINC429Word) -> None:
        """Closed loop — ILS adjusts glideslope based on selected altitude from FMC."""
        if word.label_oct == 0o031:
            self._rx_selected_alt_ft = word.decoded_value
            # If selected altitude is below current GS intercept, steepen GS slightly
            if self._rx_selected_alt_ft < 1500.0:
                self.glideslope_ddm = max(-0.1, self.glideslope_ddm - 0.001)

    def _compute_value(self, label_oct: int) -> float:
        return {
            0o173: self.localizer_ddm  + self._noise.gauss(0, 0.0001),
            0o175: self.glideslope_ddm + self._noise.gauss(0, 0.00005),
        }.get(label_oct, 0.0)

    def update(self, delta_t_s: float) -> None:
        super().update(delta_t_s)


# ─────────────────────────────────────────────────────────────────────────────
# Radio Altimeter
# ─────────────────────────────────────────────────────────────────────────────

class VirtualRadioAltimeter(VirtualLRU):
    """Virtual Radio Altimeter (RA) model.
    Receives baro altitude from ADIRU and selected altitude from FMC."""

    def __init__(self, lru_id: str = "RA_1", bus_id: str = "CHANNEL_3", sdi: int = 0):
        self.radio_alt_ft    = 2500.0
        self.decision_ht_ft  = 200.0
        self._rx_baro_alt_ft = 0.0    # from ADIRU — cross-check
        self._noise = random.Random(77)
        super().__init__(lru_id, "RA", bus_id, sdi)

    def _build_codecs(self) -> None:
        self._codecs = {
            0o164: BNRCodec(0o164, 28, 11, 0.5,  -20.0, 2500.0),  # RA
            0o165: BNRCodec(0o165, 28, 11, 1.0,   0.0, 2500.0),   # DH
        }

    def _on_receive(self, word: ARINC429Word) -> None:
        """Closed loop — RA uses baro alt from ADIRU and decision height from FMC."""
        if word.label_oct == 0o203:
            # Baro altitude from ADIRU: use to validate radio alt
            self._rx_baro_alt_ft = word.decoded_value
            # If baro alt drops below 2500ft, sync radio alt to descend with it
            if word.decoded_value < 2500.0:
                self.radio_alt_ft = max(0.0, word.decoded_value)
        elif word.label_oct == 0o031:
            # Selected altitude from FMC sets the decision height
            self.decision_ht_ft = max(0.0, word.decoded_value * 0.01)

    def _compute_value(self, label_oct: int) -> float:
        return {
            0o164: max(0.0, self.radio_alt_ft + self._noise.gauss(0, 0.5)),
            0o165: self.decision_ht_ft,
        }.get(label_oct, 0.0)

    def update(self, delta_t_s: float) -> None:
        super().update(delta_t_s)


# ─────────────────────────────────────────────────────────────────────────────
# FMC (Flight Management Computer) – stub
# ─────────────────────────────────────────────────────────────────────────────

class VirtualFMC(VirtualLRU):
    """FMC model — receives altitude, IAS, heading from ADIRU and LOC/GS from ILS.
    Computes cross-track deviation from localizer DDM and desired track from heading."""

    def __init__(self, lru_id: str = "FMC_1", bus_id: str = "CHANNEL_4", sdi: int = 0):
        self.cross_track_nm    = 0.0
        self.desired_track_deg = 0.0
        self.selected_alt_ft   = 35000.0
        # received values from other LRUs (closed loop inputs)
        self._rx_altitude_ft   = 0.0
        self._rx_ias_kts       = 0.0
        self._rx_heading_deg   = 0.0
        self._rx_loc_ddm       = 0.0
        self._rx_gs_ddm        = 0.0
        self._rx_radio_alt_ft  = 0.0
        super().__init__(lru_id, "FMC", bus_id, sdi)

    def _build_codecs(self) -> None:
        self._codecs = {
            0o106: BNRCodec(0o106, 28, 11, 0.0156, -100.0, 100.0),  # XTK
            0o107: BNRCodec(0o107, 28, 11, 0.0055,  0.0, 360.0),    # DTK
            0o031: BNRCodec(0o031, 28, 11, 1.0,     0.0, 50000.0),  # SEL ALT
        }

    def _on_receive(self, word: ARINC429Word) -> None:
        """React to incoming words from subscribed channels.
        This closes the loop — FMC updates its outputs based on received data."""
        lbl = word.label_oct
        val = word.decoded_value
        if lbl == 0o203:   # altitude from ADIRU
            self._rx_altitude_ft = val
        elif lbl == 0o204: # IAS from ADIRU
            self._rx_ias_kts = val
        elif lbl == 0o103: # heading from ADIRU — use as desired track
            self._rx_heading_deg = val
            self.desired_track_deg = val   # FMC tracks heading as desired track
        elif lbl == 0o173: # localizer DDM from ILS
            self._rx_loc_ddm = val
            # Convert DDM to cross-track nm: full scale 0.155 DDM = ~700ft = 0.115nm
            self.cross_track_nm = val * (0.115 / 0.155)
        elif lbl == 0o175: # glideslope DDM from ILS
            self._rx_gs_ddm = val
        elif lbl == 0o164: # radio altitude from RA
            self._rx_radio_alt_ft = val

    def _compute_value(self, label_oct: int) -> float:
        return {
            0o106: self.cross_track_nm,      # driven by received LOC DDM
            0o107: self.desired_track_deg,   # driven by received heading
            0o031: self.selected_alt_ft,     # crew-set, from YAML
        }.get(label_oct, 0.0)

    def update(self, delta_t_s: float) -> None:
        super().update(delta_t_s)


# ─────────────────────────────────────────────────────────────────────────────
# ATC Transponder
# ─────────────────────────────────────────────────────────────────────────────

class VirtualTransponder(VirtualLRU):
    """Virtual ATC transponder model.
    Receives baro altitude from ADIRU for Mode C reporting."""

    def __init__(self, lru_id: str = "XPDR_1", bus_id: str = "CHANNEL_5", sdi: int = 0):
        self.mode_c_alt_ft   = 0.0
        self.selected_alt_ft = 35000.0
        super().__init__(lru_id, "ATC_XPDR", bus_id, sdi)

    def _build_codecs(self) -> None:
        self._codecs = {
            0o013: BCDCodec(0o013,
                            digit_positions=[(24,21),(20,17),(16,13),(12,9)],
                            scale=100.0),   # Mode C alt (100ft steps)
            0o031: BNRCodec(0o031, 28, 11, 1.0, 0.0, 50000.0),
        }

    def _on_receive(self, word: ARINC429Word) -> None:
        """Closed loop — XPDR uses baro altitude from ADIRU for Mode C reporting."""
        if word.label_oct == 0o203:
            # Use ADIRU baro altitude directly for Mode C (rounded to 100ft)
            self.mode_c_alt_ft = round(word.decoded_value / 100.0) * 100.0

    def _compute_value(self, label_oct: int) -> float:
        return {
            0o013: self.mode_c_alt_ft,
            0o031: self.selected_alt_ft,
        }.get(label_oct, 0.0)

    def update(self, delta_t_s: float) -> None:
        super().update(delta_t_s)
