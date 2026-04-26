"""
ARINC 429 Word – core data model.
Represents a single 32-bit ARINC 429 word with all decoded fields.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
@dataclass
class ARINC429Word:
    """
    Immutable representation of one ARINC 429 32-bit word.

    Bit layout (transmitted LSB-first on wire):
      Bit 32    : Parity  (odd parity across all 32 bits)
      Bits 31-30: SSM     (Sign/Status Matrix)
      Bits 29-28: SDI     (Source/Destination Identifier, if used)
      Bits 27-11: Data    (BNR / BCD / Discrete payload, variable MSB)
      Bits 10-9 : SDI     (lower SDI bits, label-dependent)
      Bits 8-1  : Label   (transmitted LSB-first; stored reversed here)
    """

    # ── Raw 32-bit word as it appears on the bus ──────────────────────────
    raw_word: int = 0

    # ── Decoded fields ────────────────────────────────────────────────────
    label_oct: int = 0          # Label in octal (e.g. 0o203)
    sdi: int = 0                # 2-bit SDI
    data_raw: int = 0           # 19-bit raw data field (bits 11-29)
    ssm: int = 0b11             # 2-bit SSM
    parity_bit: int = 0         # Value of bit 32 in the word
    parity_ok: bool = True      # True if computed parity matches parity_bit

    # ── Engineering value (set by codec after decoding) ───────────────────
    decoded_value: Optional[float] = None
    units: str = ""

    # ── Metadata ──────────────────────────────────────────────────────────
    timestamp_us: int = 0       # Microseconds since simulation epoch
    bus_id: str = ""            # Originating bus identifier
    lru_id: str = ""            # Originating LRU identifier

    # ── SSM constant definitions ──────────────────────────────────────────
    SSM_FAILURE_WARNING: int = 0b00
    SSM_NCD: int = 0b01
    SSM_FUNCTIONAL_TEST: int = 0b10
    SSM_NORMAL: int = 0b11

    @classmethod
    def from_raw(cls, raw: int, timestamp_us: int = 0,
                 bus_id: str = "", lru_id: str = "") -> "ARINC429Word":
        """Construct an ARINC429Word by decoding a raw 32-bit integer."""
        raw = raw & 0xFFFFFFFF

        # Extract label (bits 1-8), reverse bit order for octal representation
        label_raw = raw & 0xFF
        label_reversed = int(f"{label_raw:08b}"[::-1], 2)

        sdi_low  = (raw >> 8)  & 0x03   # bits 9-10
        data_raw = (raw >> 10) & 0x7FFFF  # bits 11-29
        ssm      = (raw >> 29) & 0x03   # bits 30-31
        parity_b = (raw >> 31) & 0x01   # bit 32

        # Odd parity check: total 1-bits in raw word must be odd
        parity_ok = bin(raw).count("1") % 2 == 1

        return cls(
            raw_word=raw,
            label_oct=label_reversed,
            sdi=sdi_low,
            data_raw=data_raw,
            ssm=ssm,
            parity_bit=parity_b,
            parity_ok=parity_ok,
            timestamp_us=timestamp_us,
            bus_id=bus_id,
            lru_id=lru_id,
        )

    def ssm_description(self, format_type: str = "BNR") -> str:
        """Return human-readable SSM status."""
        if format_type == "BNR":
            return {
                0b00: "Failure Warning",
                0b01: "No Computed Data",
                0b10: "Functional Test",
                0b11: "Normal Operation",
            }.get(self.ssm, "Unknown")
        else:  # BCD / Discrete
            return {
                0b00: "Plus",
                0b01: "No Computed Data",
                0b10: "Functional Test",
                0b11: "Minus",
            }.get(self.ssm, "Unknown")

    def is_valid(self) -> bool:
        """Return True if word has normal SSM and valid parity."""
        return self.parity_ok and self.ssm == self.SSM_NORMAL

    def __repr__(self) -> str:
        return (
            f"ARINC429Word(label=0o{self.label_oct:o}, "
            f"ssm={self.ssm:02b}, "
            f"value={self.decoded_value}, "
            f"parity={'OK' if self.parity_ok else 'ERR'}, "
            f"t={self.timestamp_us}us)"
        )
