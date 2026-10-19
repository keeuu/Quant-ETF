"""python -m research_v3.cn_transfer run | snapshot"""
from __future__ import annotations

import argparse
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="research_v3.cn_transfer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("snapshot", help="Download ETF + SHIBOR data once (network) and freeze with sha256 manifest")
    sub.add_parser("run", help="Run the pre-registered CN transfer test (verifies protocol + data hashes)")
    args = ap.parse_args(argv)
    if args.cmd == "snapshot":
        from .data import create_snapshot
        from .protocol import load_verified
        proto = load_verified()
        create_snapshot(proto["mapping_from_us_to_cn"]["universe_order"])
        return 0
    from .runner import CNRunAbort, run
    try:
        res = run()
    except CNRunAbort as e:
        print(f"RUN STOPPED (fail-closed): {e}", file=sys.stderr)
        return 2
    print("outcome:", res["verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
