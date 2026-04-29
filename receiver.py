"""
ARINC 429 Digital Twin — Ethernet Receiver
Run this on the RECEIVER PC before starting the transmitter.

Usage
-----
  python receiver.py
  python receiver.py --port 5429
  python receiver.py --port 5429 --csv received_log.csv
  python receiver.py --port 5429 --vectors config/scenarios/test_vectors.yaml
"""

import argparse
import sys
from src.network.receiver import EthernetReceiver
from src.monitor.monitor import BusMonitor
from src.monitor.logger import BusLogger
from src.validation.engine import ValidationEngine


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="receiver.py",
        description="ARINC 429 Ethernet Receiver — decodes incoming words and displays results",
    )
    p.add_argument("--host",    default="0.0.0.0",  metavar="IP",
                   help="Interface to listen on (default: 0.0.0.0 = all interfaces)")
    p.add_argument("--port",    default=5429, type=int, metavar="PORT",
                   help="TCP port to listen on (default: 5429)")
    p.add_argument("--csv",     default=None, metavar="FILE",
                   help="Export received words to CSV after session ends")
    p.add_argument("--vectors", default=None, metavar="YAML",
                   help="Test vectors YAML to validate received words against")
    return p


def main():
    args = build_parser().parse_args()

    monitor   = BusMonitor()
    logger    = BusLogger()
    validator = ValidationEngine()

    if args.vectors:
        print(f"[receiver] Loading test vectors: {args.vectors}")
        validator.load_vectors_yaml(args.vectors)

    rx = EthernetReceiver(host=args.host, port=args.port)
    rx.add_listener(monitor.ingest)
    rx.add_listener(logger.write)
    rx.add_listener(validator.check)

    # Blocks here until transmitter disconnects
    rx.run()

    # ── Results ───────────────────────────────────────────────────────────
    monitor.print_table()

    if args.vectors:
        validator.print_report()

    if args.csv:
        logger.to_csv(args.csv)

    print(f"\n[receiver] Session complete — {logger.word_count()} words received")


if __name__ == "__main__":
    main()
