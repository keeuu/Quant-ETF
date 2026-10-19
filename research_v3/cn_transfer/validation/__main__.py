"""python -m research_v3.cn_transfer.validation fetch | run"""
from __future__ import annotations

import argparse
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="research_v3.cn_transfer.validation")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch", help="One-time network fetch of validation data (throttled), frozen with sha256 manifest")
    sub.add_parser("run", help="Run Part A + Part B (verifies protocol hash and all snapshot hashes first)")
    args = ap.parse_args(argv)
    from .protocol_val import load_validation_protocol
    load_validation_protocol()  # refuses to continue on hash mismatch
    if args.cmd == "fetch":
        from .fetch import run_fetch
        man = run_fetch()
        for k, v in man["status"].items():
            print(f"{k}: {v}")
        return 0
    from .run import run_all
    run_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
