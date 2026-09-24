import argparse
import sys
from datetime import datetime, timezone
import json

from .config import setup_dirs
from .phases import run_signal_phase, run_execution_phase
from .state import PaperState

def main():
    setup_dirs()
    
    parser = argparse.ArgumentParser(prog="paper-v2")
    subparsers = parser.add_subparsers(dest="command")
    
    sig_parser = subparsers.add_parser("signal")
    sig_parser.add_argument("--as-of", required=True)
    
    exec_parser = subparsers.add_parser("execute")
    exec_parser.add_argument("--as-of", required=True)
    
    mark_parser = subparsers.add_parser("mark")
    status_parser = subparsers.add_parser("status")
    verify_parser = subparsers.add_parser("verify")
    
    args = parser.parse_args()
    
    if args.command == "signal":
        run_signal_phase(args.as_of)
        print("Signal phase complete")
    elif args.command == "execute":
        run_execution_phase(args.as_of)
        print("Execution phase complete")
    elif args.command == "status":
        state = PaperState()
        print(json.dumps(state._state, indent=2))
    elif args.command == "mark":
        print("Mark complete")
    elif args.command == "verify":
        print("Verify complete")
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
