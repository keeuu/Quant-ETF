import argparse
import json
import os
import sys

from .config import setup_dirs
from .market_data import OFFLINE_ENV
from .phases import run_signal_phase, run_execution_phase
from .state import PaperState

NOT_IMPLEMENTED = {
    "mark": "`mark` is not implemented (NAV is marked at each execution; no standalone mark-to-market yet)",
    "verify": "`verify` is not implemented (no journal/state verification yet)",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paper-v2")
    sub = parser.add_subparsers(dest="command")
    for name in ("signal", "execute"):
        p = sub.add_parser(name)
        p.add_argument("--as-of", required=True,
                       help="market date YYYY-MM-DD (as of just after that XNYS close, America/New_York); "
                            "a tz-aware ISO-8601 timestamp is also accepted")
        p.add_argument("--bars-dir", default=None,
                       help=f"offline mode: directory of <SYM>.csv bars in research_v2 snapshot format "
                            f"(optional 'dividends' column); also settable via ${OFFLINE_ENV}")
    sub.add_parser("mark")
    sub.add_parser("status")
    sub.add_parser("verify")
    return parser


def main(argv=None) -> int:
    setup_dirs()
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "bars_dir", None):
        os.environ[OFFLINE_ENV] = args.bars_dir

    if args.command == "signal":
        run_signal_phase(args.as_of)
        print("Signal phase complete")
    elif args.command == "execute":
        run_execution_phase(args.as_of)
        print("Execution phase complete")
    elif args.command == "status":
        print(json.dumps(PaperState()._state, indent=2))
    elif args.command in NOT_IMPLEMENTED:
        print(NOT_IMPLEMENTED[args.command], file=sys.stderr)
        return 2
    else:
        parser.print_help()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
