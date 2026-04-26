"""
ARINC 429 Word Codec
Encodes and decodes BNR, BCD, Discrete, and ISO-5 word formats.
All arithmetic uses IEEE 754 double precision throughout.
"""

from __future__ import annotations
import math
from typing import Optional, Union
from .word import ARINC429Word


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _reverse_label_bits(label_oct: int) -> int:
    """
    Reverse the 8 bits of a label byte.
    ARINC 429 transmits the label LSB-first, so label 0o203 (decimal 131,
    binary 10000011) is transmitted as 11000001.
    We store the 'reversed' form in the word's bits 1-8.
    """
    return int(f"{label_oct & 0xFF:08b}"[::-1], 2)


def _apply_odd_parity(word: int) -> int:
    """Set bit 32 so the total number of 1-bits in the 32-bit word is odd."""
    word &= 0x7FFFFFFF          # clear bit 32 first
    if bin(word).count("1") % 2 == 0:
        word |= 0x80000000      # set bit 32 to make parity odd
    return word


def _twos_complement(value: int, bits: int) -> int:
    """Return the two's complement representation of value in 'bits' bits."""
    if value < 0:
        value = (1 << bits) + value
    return value & ((1 << bits) - 1)


def _from_twos_complement(raw: int, bits: int) -> int:
    """Interpret a raw unsigned integer as a two's complement signed value."""
    if raw & (1 << (bits - 1)):
        return raw - (1 << bits)
    return raw


# ─────────────────────────────────────────────────────────────────────────────
# BNR (Binary) Codec
# ─────────────────────────────────────────────────────────────────────────────

class BNRCodec:
    """
    Encode / decode ARINC 429 words using BNR (binary) format.

    The data field occupies bits lsb_bit through msb_bit (1-indexed from LSB
    of the word).  Bit positions follow ARINC 429 convention:
      - Bit 1  = LSB of label (transmitted first)
      - Bit 32 = parity
      - Data bits typically span bits 11-29 (after label + SDI at 9-10).

    Parameters
    ----------
    label_oct : int
        Label in octal notation (e.g. 0o203 for barometric altitude).
    msb_bit : int
        Position of the most significant data bit (1-indexed). Typically 28 or 29.
    lsb_bit : int
        Position of the least significant data bit (1-indexed). Typically 11.
    resolution : float
        Engineering-unit value per LSB (e.g. 0.125 ft for altitude).
    range_min : float
        Minimum valid engineering value.
    range_max : float
        Maximum valid engineering value.
    sign_magnitude : bool
        If True, use sign-magnitude encoding; otherwise two's complement.
    """

    def __init__(self, label_oct: int, msb_bit: int, lsb_bit: int,
                 resolution: float, range_min: float, range_max: float,
                 sign_magnitude: bool = False):
        self.label_oct = label_oct
        self.msb_bit = msb_bit
        self.lsb_bit = lsb_bit
        self.resolution = resolution
        self.range_min = range_min
        self.range_max = range_max
        self.sign_magnitude = sign_magnitude
        self._n_bits = msb_bit - lsb_bit + 1

    def encode(self, value: float, ssm: int = ARINC429Word.SSM_NORMAL,
               sdi: int = 0, lru_id: str = "", bus_id: str = "",
               timestamp_us: int = 0) -> ARINC429Word:
        """Encode a floating-point engineering value into a 32-bit word."""
        # Clamp to valid range
        value = max(self.range_min, min(self.range_max, value))

        # Scale to integer
        scaled = int(round(value / self.resolution))

        if self.sign_magnitude:
            sign_bit = 1 if scaled < 0 else 0
            magnitude = abs(scaled) & ((1 << (self._n_bits - 1)) - 1)
            raw_data = (sign_bit << (self._n_bits - 1)) | magnitude
        else:
            raw_data = _twos_complement(scaled, self._n_bits)

        # Build word
        label_rev = _reverse_label_bits(self.label_oct)
        word = label_rev
        word |= (sdi & 0x03) << 8           # SDI bits 9-10
        word |= (raw_data << (self.lsb_bit - 1))  # data field
        word |= (ssm & 0x03) << 29          # SSM bits 30-31
        word = _apply_odd_parity(word)

        w = ARINC429Word.from_raw(word, timestamp_us, bus_id, lru_id)
        w.decoded_value = value
        return w

    def decode(self, word: ARINC429Word) -> float:
        """Decode a word and return the engineering value."""
        # Extract data bits from raw_word
        mask = (1 << self._n_bits) - 1
        raw_data = (word.raw_word >> (self.lsb_bit - 1)) & mask

        if self.sign_magnitude:
            sign_bit = (raw_data >> (self._n_bits - 1)) & 1
            magnitude = raw_data & ((1 << (self._n_bits - 1)) - 1)
            scaled = -magnitude if sign_bit else magnitude
        else:
            scaled = _from_twos_complement(raw_data, self._n_bits)

        return scaled * self.resolution


# ─────────────────────────────────────────────────────────────────────────────
# BCD Codec
# ─────────────────────────────────────────────────────────────────────────────

class BCDCodec:
    """
    Encode / decode ARINC 429 words using BCD (Binary Coded Decimal) format.

    Parameters
    ----------
    label_oct : int
        Label in octal.
    digit_positions : list[tuple[int,int]]
        List of (msb_bit, lsb_bit) pairs for each decimal digit, ordered from
        most-significant to least-significant digit.
        Example for a 5-digit value: [(28,25), (24,21), (20,17), (16,13), (12,9)]
    scale : float
        Multiply decoded integer by this to get engineering units.
        E.g. 0.1 if the value represents tenths.
    """

    def __init__(self, label_oct: int,
                 digit_positions: list,
                 scale: float = 1.0):
        self.label_oct = label_oct
        self.digit_positions = digit_positions  # [(msb, lsb), ...]
        self.scale = scale

    def encode(self, value: float, ssm: int = ARINC429Word.SSM_NORMAL,
               sdi: int = 0, lru_id: str = "", bus_id: str = "",
               timestamp_us: int = 0) -> ARINC429Word:
        """Encode a numeric value into a BCD ARINC 429 word."""
        int_val = int(round(abs(value) / self.scale))
        digits = []
        for _ in self.digit_positions:
            digits.append(int_val % 10)
            int_val //= 10
        digits.reverse()

        label_rev = _reverse_label_bits(self.label_oct)
        word = label_rev
        word |= (sdi & 0x03) << 8

        for i, (msb_bit, lsb_bit) in enumerate(self.digit_positions):
            digit = digits[i] & 0xF
            word |= digit << (lsb_bit - 1)

        word |= (ssm & 0x03) << 29
        word = _apply_odd_parity(word)

        w = ARINC429Word.from_raw(word, timestamp_us, bus_id, lru_id)
        w.decoded_value = value
        return w

    def decode(self, word: ARINC429Word) -> float:
        """Decode a BCD word and return the engineering value."""
        result = 0
        for msb_bit, lsb_bit in self.digit_positions:
            n_bits = msb_bit - lsb_bit + 1
            mask = (1 << n_bits) - 1
            digit = (word.raw_word >> (lsb_bit - 1)) & mask
            result = result * 10 + digit
        return result * self.scale


# ─────────────────────────────────────────────────────────────────────────────
# Discrete Codec
# ─────────────────────────────────────────────────────────────────────────────

class DiscreteCodec:
    """
    Encode / decode ARINC 429 Discrete words.

    Parameters
    ----------
    label_oct : int
        Label in octal.
    bit_map : dict[str, int]
        Maps signal name to word bit position (1-indexed).
        Example: {"gear_down": 11, "gear_locked": 12, "gear_in_transit": 13}
    """

    def __init__(self, label_oct: int, bit_map: dict):
        self.label_oct = label_oct
        self.bit_map = bit_map

    def encode(self, signals: dict, ssm: int = ARINC429Word.SSM_NORMAL,
               sdi: int = 0, lru_id: str = "", bus_id: str = "",
               timestamp_us: int = 0) -> ARINC429Word:
        """
        Encode a dict of {signal_name: bool/int} into a Discrete word.
        Any signal not in `signals` defaults to 0.
        """
        label_rev = _reverse_label_bits(self.label_oct)
        word = label_rev
        word |= (sdi & 0x03) << 8

        for name, bit_pos in self.bit_map.items():
            if signals.get(name, 0):
                word |= (1 << (bit_pos - 1))

        word |= (ssm & 0x03) << 29
        word = _apply_odd_parity(word)

        w = ARINC429Word.from_raw(word, timestamp_us, bus_id, lru_id)
        return w

    def decode(self, word: ARINC429Word) -> dict:
        """Decode a Discrete word into {signal_name: bool}."""
        result = {}
        for name, bit_pos in self.bit_map.items():
            result[name] = bool((word.raw_word >> (bit_pos - 1)) & 1)
        return result


# ─────────────────────────────────────────────────────────────────────────────
# General-purpose encode helper used by the label database
# ─────────────────────────────────────────────────────────────────────────────

def encode_bnr_simple(label_oct: int, value: float,
                      msb_bit: int, lsb_bit: int, resolution: float,
                      range_min: float, range_max: float,
                      ssm: int = 0b11, sdi: int = 0,
                      timestamp_us: int = 0,
                      lru_id: str = "", bus_id: str = "") -> ARINC429Word:
    """Convenience wrapper: create a BNRCodec and encode in one call."""
    codec = BNRCodec(label_oct, msb_bit, lsb_bit, resolution,
                     range_min, range_max)
    return codec.encode(value, ssm, sdi, lru_id, bus_id, timestamp_us)


def decode_word(word: ARINC429Word, codec: Union[BNRCodec, BCDCodec, DiscreteCodec]
                ) -> Union[float, dict]:
    """Decode a word using the supplied codec; updates word.decoded_value."""
    result = codec.decode(word)
    if isinstance(result, float):
        word.decoded_value = result
    return result
