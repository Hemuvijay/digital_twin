"""
ARINC 429 ILS Digital Twin – Command Line Interface
Usage:
  python main.py run --scenario <yaml>
  python main.py run --vectors  <yaml>
  python main.py demo
  python main.py test
  python main.py decode <hex_word>
  python main.py encode --label <octal> --value <float> --msb <int> --lsb <int> --res <float>
"""

import argparse
import sys
import unittest


# ─────────────────────────────────────────────────────────────────────────────
# Sub-command handlers
# ─────────────────────────────────────────────────────────────────────────────

def cmd_run(args):
    from src.engine.simulation import Simulation

    sim = Simulation()

    if args.scenario:
        print(f"[main] Loading scenario: {args.scenario}")
        sim.load_scenario(args.scenario)
    else:
        print("[main] No scenario specified. Use --scenario <path>")
        sys.exit(1)

    if args.vectors:
        print(f"[main] Loading test vectors: {args.vectors}")
        sim.load_test_vectors(args.vectors)

    # ── Ethernet transmitter (optional) ───────────────────────────────────
    eth_tx = None
    if args.eth:
        from src.network.transmitter import EthernetTransmitter
        host, _, port_str = args.eth.partition(":")
        port = int(port_str) if port_str else 5429
        eth_tx = EthernetTransmitter(host=host, port=port)
        eth_tx.connect()
        sim.add_listener(eth_tx.send)
        print(f"[main] Ethernet transmitter active → {host}:{port}")

    sim.run(duration_s=args.duration)  # None = use YAML value
    sim.print_monitor_table()

    if args.vectors:
        sim.validator.print_report()

    if args.csv:
        sim.logger.to_csv(args.csv)

    if args.bin:
        sim.logger.to_binary(args.bin)

    if eth_tx:
        eth_tx.close()


def cmd_demo(args):
    """Run a built-in 10-second ILS approach demo."""
    from src.engine.simulation import Simulation

    DEMO_SCENARIO = {
        "scenario": {
            "name": "Built-in ILS Demo",
            "duration_s": 10,
            "time_scale": 1.0,
        },
        "lrus": [
            {
                "id": "ADIRU_1", "type": "ADIRU", "bus": "CHANNEL_1", "speed": "HS",
                "labels": {
                    "0o101": {"rate_hz": 50, "initial": 2.5},
                    "0o102": {"rate_hz": 50, "initial": -1.0},
                    "0o203": {"rate_hz": 25, "initial": 3000.0},
                    "0o204": {"rate_hz": 25, "initial": 140.0},
                    "0o206": {"rate_hz": 25, "initial": -700.0},
                },
            },
            {
                "id": "ILS_1", "type": "ILS", "bus": "CHANNEL_2", "speed": "HS",
                "labels": {
                    "0o173": {"rate_hz": 25, "initial": 0.01},
                    "0o175": {"rate_hz": 25, "initial": 0.005},
                },
            },
            {
                "id": "RA_1", "type": "RA", "bus": "CHANNEL_3", "speed": "HS",
                "labels": {
                    "0o164": {"rate_hz": 25, "initial": 2500.0},
                },
            },
            {
                "id": "FMC_1", "type": "FMC", "bus": "CHANNEL_4", "speed": "HS",
                "labels": {
                    "0o106": {"rate_hz": 5},
                    "0o107": {"rate_hz": 5},
                    "0o031": {"rate_hz": 1},
                },
                "subscribes": [
                    {"channel": "CHANNEL_1", "labels": ["0o203", "0o204", "0o103"]},
                    {"channel": "CHANNEL_2", "labels": ["0o173", "0o175"]},
                    {"channel": "CHANNEL_3", "labels": ["0o164"]},
                ],
            },
        ],
        "phases": [
            {
                "at_s": 0, "state": "APPROACH",
                "params": {
                    "altitude_ft": 3000.0, "vs_fpm": -700.0,
                    "ias_kts": 140.0, "heading_deg": 270.0,
                    "ground_speed_kts": 135.0, "track_deg": 270.0,
                },
            },
        ],
        "faults": [],
    }

    print("[main] Running built-in 10-second ILS demo...")
    sim = Simulation()
    sim.load_scenario_dict(DEMO_SCENARIO)
    sim.run()
    sim.print_monitor_table()


def cmd_test(args):
    """Discover and run all unit tests under the tests/ directory."""
    loader = unittest.TestLoader()
    suite  = loader.discover(start_dir="tests", pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


def cmd_decode(args):
    """Decode a raw 32-bit hex ARINC 429 word and print all fields."""
    from src.core.word import ARINC429Word
    from src.core.label_db import get_label

    try:
        raw = int(args.hex_word, 16)
    except ValueError:
        print(f"[main] Invalid hex word: '{args.hex_word}'. Expected format: 0x1A2B3C4D or 1A2B3C4D")
        sys.exit(1)

    word = ARINC429Word.from_raw(raw)
    ldef = get_label(word.label_oct)

    print(f"\n{'─'*50}")
    print(f"  Raw word (hex)  : 0x{raw:08X}")
    print(f"  Raw word (bin)  : {raw:032b}")
    print(f"  Label (octal)   : 0o{word.label_oct:o}")
    print(f"  Label (decimal) : {word.label_oct}")
    if ldef:
        print(f"  Label name      : {ldef.name}  [{ldef.units}]")
        print(f"  Equipment       : {', '.join(ldef.equipment)}")
    print(f"  SDI             : {word.sdi:02b}")
    print(f"  Data (raw)      : {word.data_raw}  (0x{word.data_raw:05X})")
    print(f"  SSM             : {word.ssm:02b}  → {word.ssm_description()}")
    print(f"  Parity bit      : {word.parity_bit}")
    print(f"  Parity check    : {'OK' if word.parity_ok else 'FAIL'}")

    # Attempt BNR decode if label is known
    if ldef and ldef.format == "BNR":
        from src.core.codec import BNRCodec
        codec = BNRCodec(ldef.label_oct, ldef.msb_bit, ldef.lsb_bit,
                         ldef.resolution, ldef.range_min, ldef.range_max)
        try:
            value = codec.decode(word)
            print(f"  Decoded value   : {value:.6f} {ldef.units}")
        except Exception as e:
            print(f"  Decoded value   : (decode error: {e})")
    print(f"{'─'*50}\n")


def cmd_encode(args):
    """Encode a value into an ARINC 429 BNR word and print the result."""
    from src.core.codec import BNRCodec

    try:
        label_oct = int(args.label, 8) if args.label.startswith("0o") else int(args.label, 8)
    except ValueError:
        print(f"[main] Invalid octal label: '{args.label}'. Example: 0o203")
        sys.exit(1)

    codec = BNRCodec(
        label_oct=label_oct,
        msb_bit=args.msb,
        lsb_bit=args.lsb,
        resolution=args.res,
        range_min=args.range_min,
        range_max=args.range_max,
    )

    word = codec.encode(args.value, ssm=0b11, sdi=args.sdi)

    print(f"\n{'─'*50}")
    print(f"  Label (octal)   : 0o{label_oct:o}")
    print(f"  Input value     : {args.value}")
    print(f"  MSB bit / LSB   : {args.msb} / {args.lsb}")
    print(f"  Resolution      : {args.res}")
    print(f"  Encoded (hex)   : 0x{word.raw_word:08X}")
    print(f"  Encoded (bin)   : {word.raw_word:032b}")
    print(f"  SSM             : {word.ssm:02b}  → {word.ssm_description()}")
    print(f"  Parity          : {'OK' if word.parity_ok else 'FAIL'}")
    print(f"  Decoded back    : {word.decoded_value}")
    print(f"{'─'*50}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Argument parser
# ─────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="ARINC 429 ILS Digital Twin – Simulation & Analysis Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py run --scenario config/scenarios/ils_approch.yaml
  python main.py run --scenario config/scenarios/ils_approch.yaml --vectors config/scenarios/test_vectors.yaml
  python main.py run --scenario config/scenarios/ils_approch.yaml --csv output.csv
  python main.py demo
  python main.py test
  python main.py decode 0x1A2B3C4D
  python main.py encode --label 0o203 --value 3000.0 --msb 28 --lsb 11 --res 4.0
        """,
    )

    sub = parser.add_subparsers(dest="command", metavar="{run,demo,test,decode,encode}")
    sub.required = True

    # ── run ──────────────────────────────────────────────────────────────
    p_run = sub.add_parser("run", help="Run a YAML scenario")
    p_run.add_argument("--scenario", metavar="YAML", required=True,
                       help="Path to scenario YAML file")
    p_run.add_argument("--vectors", metavar="YAML", default=None,
                       help="Path to test vectors YAML file")
    p_run.add_argument("--csv", metavar="FILE", default=None,
                       help="Export logged words to CSV")
    p_run.add_argument("--bin", metavar="FILE", default=None,
                       help="Export logged words to binary format")
    p_run.add_argument("--duration", metavar="SECONDS", type=float, default=None,
                       help="Override scenario duration (e.g. --duration 30)")
    p_run.add_argument("--eth", metavar="HOST:PORT", default=None,
                       help="Send words over Ethernet to receiver (e.g. 192.168.1.50:5429)")

    # ── demo ─────────────────────────────────────────────────────────────
    sub.add_parser("demo", help="Run built-in 10-second ILS approach demo")

    # ── test ─────────────────────────────────────────────────────────────
    sub.add_parser("test", help="Run unit tests (discovers tests/ directory)")

    # ── decode ───────────────────────────────────────────────────────────
    p_dec = sub.add_parser("decode", help="Decode a raw 32-bit hex ARINC 429 word")
    p_dec.add_argument("hex_word", metavar="HEX",
                       help="32-bit hex word, e.g. 0x1A2B3C4D")

    # ── encode ───────────────────────────────────────────────────────────
    p_enc = sub.add_parser("encode", help="Encode a value into a BNR ARINC 429 word")
    p_enc.add_argument("--label",     required=True, metavar="OCT",
                       help="Label in octal, e.g. 0o203")
    p_enc.add_argument("--value",     required=True, type=float, metavar="FLOAT",
                       help="Engineering value to encode")
    p_enc.add_argument("--msb",       required=True, type=int,   metavar="INT",
                       help="MSB bit position (e.g. 28)")
    p_enc.add_argument("--lsb",       required=True, type=int,   metavar="INT",
                       help="LSB bit position (e.g. 11)")
    p_enc.add_argument("--res",       required=True, type=float, metavar="FLOAT",
                       help="Resolution (value per LSB)")
    p_enc.add_argument("--range-min", dest="range_min", type=float, default=-1e9,
                       metavar="FLOAT", help="Minimum valid value (default: -1e9)")
    p_enc.add_argument("--range-max", dest="range_max", type=float, default=1e9,
                       metavar="FLOAT", help="Maximum valid value (default: 1e9)")
    p_enc.add_argument("--sdi",       type=int, default=0, metavar="INT",
                       help="SDI value 0-3 (default: 0)")

    return parser


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

COMMAND_MAP = {
    "run":    cmd_run,
    "demo":   cmd_demo,
    "test":   cmd_test,
    "decode": cmd_decode,
    "encode": cmd_encode,
}

if __name__ == "__main__":
    parser = build_parser()
    args   = parser.parse_args()
    COMMAND_MAP[args.command](args)
