"""
ARINC 429 Label Database
Standard label definitions from ARINC 429 Part 2.
Each entry specifies codec parameters, transmission rate, and metadata.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List, Dict


@dataclass
class LabelDefinition:
    """Full specification for one ARINC 429 label."""
    label_oct: int          # Label in octal (e.g. 0o203)
    name: str               # Human-readable parameter name
    equipment: List[str]    # LRU types that transmit this label
    format: str             # 'BNR' | 'BCD' | 'DISCRETE' | 'ISO5'
    msb_bit: int            # MSB position in word (1-indexed); BNR/BCD only
    lsb_bit: int            # LSB position in word; BNR/BCD only
    resolution: float       # Value per LSB (engineering units)
    range_min: float        # Minimum valid value
    range_max: float        # Maximum valid value
    units: str              # Engineering units string
    tx_rate_hz: float       # Nominal transmission rate (words/sec)
    speed: str              # 'HS' (100 kbps) | 'LS' (12.5 kbps)
    ssm_type: str           # 'BNR' | 'BCD'
    sdi_used: bool = False  # True if SDI bits carry address info
    sign_magnitude: bool = False  # True for sign-magnitude BNR
    description: str = ""   # Optional free-text description


# ─────────────────────────────────────────────────────────────────────────────
# Standard ARINC 429 Label Dictionary
# Source: ARINC 429 Part 2 (AEEC)
# ─────────────────────────────────────────────────────────────────────────────

LABEL_DATABASE: Dict[int, LabelDefinition] = {

    # ── Inertial / Air Data (ADIRU / ADC) ──────────────────────────────────
    0o101: LabelDefinition(
        label_oct=0o101, name="Pitch Attitude",
        equipment=["ADIRU", "IRS", "AHRS"],
        format="BNR", msb_bit=28, lsb_bit=11,
        resolution=0.0055, range_min=-180.0, range_max=180.0,
        units="deg", tx_rate_hz=50, speed="HS", ssm_type="BNR",
        description="Aircraft pitch angle, positive nose-up"
    ),
    0o102: LabelDefinition(
        label_oct=0o102, name="Roll Attitude",
        equipment=["ADIRU", "IRS", "AHRS"],
        format="BNR", msb_bit=28, lsb_bit=11,
        resolution=0.0055, range_min=-180.0, range_max=180.0,
        units="deg", tx_rate_hz=50, speed="HS", ssm_type="BNR",
        description="Aircraft roll angle, positive right wing down"
    ),

}


# ─────────────────────────────────────────────────────────────────────────────
# Additional labels used in this project
# ─────────────────────────────────────────────────────────────────────────────

LABEL_DATABASE.update({
    0o103: LabelDefinition(
        label_oct=0o103, name="True Heading",
        equipment=["ADIRU", "IRS", "AHRS"],
        format="BNR", msb_bit=28, lsb_bit=11,
        resolution=0.0055, range_min=0.0, range_max=360.0,
        units="deg", tx_rate_hz=25, speed="HS", ssm_type="BNR",
        description="True heading angle"
    ),
    0o173: LabelDefinition(
        label_oct=0o173, name="Localizer Deviation",
        equipment=["ILS"],
        format="BNR", msb_bit=28, lsb_bit=11,
        resolution=0.0000153, range_min=-0.2, range_max=0.2,
        units="DDM", tx_rate_hz=25, speed="HS", ssm_type="BNR",
        description="ILS localizer deviation in DDM"
    ),
    0o175: LabelDefinition(
        label_oct=0o175, name="Glideslope Deviation",
        equipment=["ILS"],
        format="BNR", msb_bit=28, lsb_bit=11,
        resolution=0.0000077, range_min=-0.1, range_max=0.1,
        units="DDM", tx_rate_hz=25, speed="HS", ssm_type="BNR",
        description="ILS glideslope deviation in DDM"
    ),
    0o203: LabelDefinition(
        label_oct=0o203, name="Barometric Altitude",
        equipment=["ADIRU", "ADC"],
        format="BNR", msb_bit=28, lsb_bit=11,
        resolution=4.0, range_min=-2000.0, range_max=50000.0,
        units="ft", tx_rate_hz=25, speed="HS", ssm_type="BNR",
        description="Barometric altitude"
    ),
    0o204: LabelDefinition(
        label_oct=0o204, name="Indicated Airspeed",
        equipment=["ADIRU", "ADC"],
        format="BNR", msb_bit=28, lsb_bit=11,
        resolution=0.0625, range_min=0.0, range_max=500.0,
        units="kts", tx_rate_hz=25, speed="HS", ssm_type="BNR",
        description="Indicated airspeed"
    ),
    0o205: LabelDefinition(
        label_oct=0o205, name="True Airspeed",
        equipment=["ADIRU", "ADC"],
        format="BNR", msb_bit=28, lsb_bit=11,
        resolution=0.0625, range_min=0.0, range_max=600.0,
        units="kts", tx_rate_hz=25, speed="HS", ssm_type="BNR",
        description="True airspeed"
    ),
    0o206: LabelDefinition(
        label_oct=0o206, name="Vertical Speed",
        equipment=["ADIRU", "ADC"],
        format="BNR", msb_bit=28, lsb_bit=11,
        resolution=1.0, range_min=-6000.0, range_max=6000.0,
        units="fpm", tx_rate_hz=25, speed="HS", ssm_type="BNR",
        description="Vertical speed in feet per minute"
    ),
    0o210: LabelDefinition(
        label_oct=0o210, name="Mach Number",
        equipment=["ADIRU", "ADC"],
        format="BNR", msb_bit=28, lsb_bit=11,
        resolution=0.000244, range_min=0.0, range_max=3.0,
        units="", tx_rate_hz=25, speed="HS", ssm_type="BNR",
        description="Mach number"
    ),
    0o211: LabelDefinition(
        label_oct=0o211, name="Total Air Temperature",
        equipment=["ADIRU", "ADC"],
        format="BNR", msb_bit=28, lsb_bit=11,
        resolution=0.0625, range_min=-100.0, range_max=60.0,
        units="degC", tx_rate_hz=25, speed="HS", ssm_type="BNR",
        description="Total air temperature"
    ),
    0o212: LabelDefinition(
        label_oct=0o212, name="Static Air Temperature",
        equipment=["ADIRU", "ADC"],
        format="BNR", msb_bit=28, lsb_bit=11,
        resolution=0.0625, range_min=-100.0, range_max=60.0,
        units="degC", tx_rate_hz=25, speed="HS", ssm_type="BNR",
        description="Static air temperature"
    ),
    0o164: LabelDefinition(
        label_oct=0o164, name="Radio Altitude",
        equipment=["RA"],
        format="BNR", msb_bit=28, lsb_bit=11,
        resolution=0.5, range_min=-20.0, range_max=2500.0,
        units="ft", tx_rate_hz=25, speed="HS", ssm_type="BNR",
        description="Radio altitude above terrain"
    ),
    0o165: LabelDefinition(
        label_oct=0o165, name="Decision Height",
        equipment=["RA"],
        format="BNR", msb_bit=28, lsb_bit=11,
        resolution=1.0, range_min=0.0, range_max=2500.0,
        units="ft", tx_rate_hz=25, speed="HS", ssm_type="BNR",
        description="Decision height setting"
    ),
    0o106: LabelDefinition(
        label_oct=0o106, name="Cross Track Distance",
        equipment=["FMC"],
        format="BNR", msb_bit=28, lsb_bit=11,
        resolution=0.0156, range_min=-100.0, range_max=100.0,
        units="nm", tx_rate_hz=5, speed="HS", ssm_type="BNR",
        description="Cross track deviation from planned route"
    ),
    0o107: LabelDefinition(
        label_oct=0o107, name="Desired Track",
        equipment=["FMC"],
        format="BNR", msb_bit=28, lsb_bit=11,
        resolution=0.0055, range_min=0.0, range_max=360.0,
        units="deg", tx_rate_hz=5, speed="HS", ssm_type="BNR",
        description="Desired track angle"
    ),
    0o031: LabelDefinition(
        label_oct=0o031, name="Selected Altitude",
        equipment=["FMC", "ATC_XPDR"],
        format="BNR", msb_bit=28, lsb_bit=11,
        resolution=1.0, range_min=0.0, range_max=50000.0,
        units="ft", tx_rate_hz=1, speed="HS", ssm_type="BNR",
        description="Selected/target altitude"
    ),
    0o013: LabelDefinition(
        label_oct=0o013, name="Mode C Altitude",
        equipment=["ATC_XPDR"],
        format="BCD", msb_bit=24, lsb_bit=9,
        resolution=100.0, range_min=0.0, range_max=126750.0,
        units="ft", tx_rate_hz=1, speed="HS", ssm_type="BCD",
        description="ATC Mode C altitude in 100ft steps"
    ),
    0o270: LabelDefinition(
        label_oct=0o270, name="Latitude",
        equipment=["ADIRU", "IRS"],
        format="BNR", msb_bit=28, lsb_bit=11,
        resolution=0.0000021, range_min=-90.0, range_max=90.0,
        units="deg", tx_rate_hz=10, speed="HS", ssm_type="BNR",
        description="Aircraft latitude"
    ),
    0o271: LabelDefinition(
        label_oct=0o271, name="Longitude",
        equipment=["ADIRU", "IRS"],
        format="BNR", msb_bit=28, lsb_bit=11,
        resolution=0.0000043, range_min=-180.0, range_max=180.0,
        units="deg", tx_rate_hz=10, speed="HS", ssm_type="BNR",
        description="Aircraft longitude"
    ),
    0o312: LabelDefinition(
        label_oct=0o312, name="Ground Speed",
        equipment=["ADIRU", "IRS"],
        format="BNR", msb_bit=28, lsb_bit=11,
        resolution=0.0625, range_min=0.0, range_max=1000.0,
        units="kts", tx_rate_hz=25, speed="HS", ssm_type="BNR",
        description="Ground speed"
    ),
    0o313: LabelDefinition(
        label_oct=0o313, name="Track Angle True",
        equipment=["ADIRU", "IRS"],
        format="BNR", msb_bit=28, lsb_bit=11,
        resolution=0.0055, range_min=0.0, range_max=360.0,
        units="deg", tx_rate_hz=25, speed="HS", ssm_type="BNR",
        description="Track angle true"
    ),
    0o361: LabelDefinition(
        label_oct=0o361, name="Wind Speed",
        equipment=["ADIRU"],
        format="BNR", msb_bit=28, lsb_bit=11,
        resolution=0.0625, range_min=0.0, range_max=250.0,
        units="kts", tx_rate_hz=10, speed="HS", ssm_type="BNR",
        description="Wind speed"
    ),
    0o362: LabelDefinition(
        label_oct=0o362, name="Wind Direction",
        equipment=["ADIRU"],
        format="BNR", msb_bit=28, lsb_bit=11,
        resolution=0.0055, range_min=0.0, range_max=360.0,
        units="deg", tx_rate_hz=10, speed="HS", ssm_type="BNR",
        description="Wind direction true"
    ),
})


# ─────────────────────────────────────────────────────────────────────────────
# Lookup helper
# ─────────────────────────────────────────────────────────────────────────────

def get_label(label_oct: int) -> Optional[LabelDefinition]:
    """Return the LabelDefinition for a given octal label, or None if not found."""
    return LABEL_DATABASE.get(label_oct)
