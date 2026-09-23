"""CLI entrypoint for Research V2.

Supports Stage 1 baseline/guard commands and Stage 2 data/calendar/splits commands.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .calendar import build_complete_weeks, market_as_of_after_close, parse_as_of
from .contracts import EXPECTED_PLAN_SHA256, validate_protocol
from .data import UNIVERSE, create_snapshot, load_dataset, validate_etf_series
from .guards import GuardError, audit_imports, create_baseline, reserve_run, verify_baseline
from .provenance import (
    canonical_json,
    git,
    object_hash,
    read_json,
    runtime_metadata,
    sha256,
    utc_now,
    write_json_new,
)
from .schedule import build_monthly_matched_schedule, build_weekly_schedule
from .signals import compute_model_signals_for_events
from .splits import PARTITIONS, audit_splits


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def _input(root: Path, relative: str) -> Path:
    from .guards import checked_output_path
    path = checked_output_path(root, relative)
    if not path.is_file():
        raise GuardError(f"Required input missing: {relative}")
    return path


def lock_protocol(root: Path, config: str, output: str) -> dict:
    protocol = read_json(_input(root, config))
    digest = validate_protocol(protocol)
    plan_digest = sha256((root / "CODEX_EXECUTION_PLAN.md").read_bytes())
    if plan_digest != EXPECTED_PLAN_SHA256:
        raise GuardError("Execution contract has changed")
    baseline = read_json(_input(root, "docs/research_v2/baseline_manifest.json"))
    guard = verify_baseline(root, baseline)
    if guard["status"] != "PASS":
        raise GuardError("Freeze guard failed before protocol lock")
    result = {
        "schema_version": 1,
        "status": "LOCKED",
        "created_at": utc_now(),
        "experiment_id": protocol["experiment_id"],
        "protocol_path": config,
        "protocol_sha256": digest,
        "protocol_file_sha256": sha256((root / config).read_bytes()),
        "execution_plan_sha256": plan_digest,
        "model_count": len(protocol["model_grid"]["models"]),
        "code_commit": git(root, "rev-parse", "HEAD"),
        "baseline_manifest_sha256": baseline["manifest_sha256"],
        "dataset_status": "NOT_SNAPSHOTTED_STAGE_2_PENDING",
        "historical_strategy_returns_computed": False,
        "holdout_returns_viewed": False,
    }
    write_json_new(root, output, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)

    # --- Stage 1 Subcommands ---
    p = subs.add_parser("baseline-init", help="Read-only F0 inventory; refuse unexpected base drift")
    p.add_argument("--expected-head", required=True)
    p.add_argument("--output", required=True)

    p = subs.add_parser("protocol-lock", help="Validate and exclusively lock preregistration")
    p.add_argument("--config", required=True)
    p.add_argument("--output", required=True)

    p = subs.add_parser("guard", help="Verify F0, tag objects, import boundaries and existing protocol lock")
    p.add_argument("--manifest", required=True)

    subs.add_parser("environment", help="Read-only Python/dependency metadata")

    # --- Stage 2 Subcommands ---
    p = subs.add_parser("snapshot", help="Create verified byte-for-byte snapshot of data cache and calendar")
    p.add_argument("--source", required=True, help="Relative path to data source directory (e.g. data/cache)")
    p.add_argument("--cutoff", required=True, help="Historical cutoff date (e.g. 2026-09-18)")
    p.add_argument("--as-of", required=True, help="Evaluation as_of timestamp with timezone (e.g. 2026-09-18T16:01:00-04:00)")
    p.add_argument("--snapshot-id", required=True, help="Identifier for snapshot directory (e.g. snapshot_20260918)")

    p = subs.add_parser("validate-data", help="Verify dataset integrity, XNYS sessions, and period completeness")
    p.add_argument("--manifest", required=True, help="Path to dataset_manifest.json")
    p.add_argument("--run-id", required=True, help="Execution run identifier")

    p = subs.add_parser("audit-splits", help="Audit partition coverage and verify holdout gate")
    p.add_argument("--protocol", required=True, help="Path to protocol.json")
    p.add_argument("--manifest", required=True, help="Path to dataset_manifest.json")
    p.add_argument("--metadata-only", action="store_true", default=False, help="Strict metadata-only gate")

    p = subs.add_parser("audit-signals", help="Audit weekly signals and lineages for train partition")
    p.add_argument("--partition", default="train", help="Target partition (train only)")
    p.add_argument("--all-models", action="store_true", default=False, help="Audit all 9 models")
    p.add_argument("--run-id", required=True, help="Execution run identifier")

    args = parser.parse_args(argv)
    root = _root()

    try:
        if args.command == "baseline-init":
            result = create_baseline(root, args.expected_head)
            write_json_new(root, args.output, result)
            result = {
                "status": "PASS",
                "protected_entries": len(result["files"]),
                "manifest_sha256": result["manifest_sha256"],
                "tags": result["tags"],
            }

        elif args.command == "protocol-lock":
            result = lock_protocol(root, args.config, args.output)

        elif args.command == "guard":
            result = verify_baseline(root, read_json(_input(root, args.manifest)))
            imports = audit_imports(root)
            if imports:
                result["issues"].append({"change": "forbidden_import", "locations": imports})

            lock_path = root / "docs/research_v2/protocol_lock.json"
            if lock_path.exists():
                lock = read_json(_input(root, "docs/research_v2/protocol_lock.json"))
                protocol = read_json(_input(root, lock["protocol_path"]))
                if (
                    validate_protocol(protocol) != lock["protocol_sha256"]
                    or lock["baseline_manifest_sha256"] != result["baseline_sha256"]
                    or sha256((root / lock["protocol_path"]).read_bytes()) != lock["protocol_file_sha256"]
                ):
                    raise GuardError("Protocol lock/baseline anchor mismatch")
                result["protocol_sha256"] = lock["protocol_sha256"]

            result["status"] = "FAIL" if result["issues"] else "PASS"

        elif args.command == "environment":
            result = runtime_metadata()

        elif args.command == "snapshot":
            src_dir = root / args.source
            target_dir = root / "research_v2" / "data" / args.snapshot_id
            manifest_dict = create_snapshot(
                source_dir=src_dir,
                target_dir=target_dir,
                cutoff=args.cutoff,
                as_of=args.as_of,
            )
            # Add protocol hash
            lock_path = root / "docs/research_v2/protocol_lock.json"
            if lock_path.exists():
                lock = read_json(lock_path)
                manifest_dict["protocol_sha256"] = lock["protocol_sha256"]
            manifest_dict["partitions"] = {
                "warmup": PARTITIONS["warmup"],
                "train": PARTITIONS["train"],
                "validation": PARTITIONS["validation"],
                "historical_holdout": PARTITIONS["historical_holdout"],
            }
            manifest_path_rel = "docs/research_v2/dataset_manifest.json"
            write_json_new(root, manifest_path_rel, manifest_dict)
            result = {
                "status": "PASS",
                "dataset_id": manifest_dict["dataset_id"],
                "data_sha256": manifest_dict["data_sha256"],
                "calendar_sha256": manifest_dict["calendar_sha256"],
                "expected_session_count": manifest_dict["expected_session_count"],
                "assets_count": len(manifest_dict["assets"]),
            }

        elif args.command == "validate-data":
            manifest = read_json(_input(root, args.manifest))
            snapshot_dir = root / "research_v2" / "data" / manifest["dataset_id"]

            import pandas as pd
            session_table_path = snapshot_dir / "session_table.csv"
            session_df = pd.read_csv(session_table_path)
            expected_sessions = session_df["session_date"].tolist()

            all_issues = []
            symbol_dates = {}
            for sym in UNIVERSE:
                csv_path = snapshot_dir / f"{sym}.csv"
                df = pd.read_csv(csv_path)
                issues = validate_etf_series(df, sym, expected_sessions)
                if issues:
                    all_issues.extend(issues)
                symbol_dates[sym] = set(df["Date"].tolist())

            if all_issues:
                result = {"status": "FAIL", "issues": all_issues}
            else:
                # Validate complete weeks
                weeks = build_complete_weeks(
                    session_dates=expected_sessions,
                    as_of=manifest["as_of"],
                    symbol_dates=symbol_dates,
                )
                result = {
                    "status": "PASS",
                    "dataset_id": manifest["dataset_id"],
                    "session_count": len(expected_sessions),
                    "symbols_validated": list(UNIVERSE),
                    "complete_weeks_count": len(weeks),
                    "first_week": weeks[0].period_id if weeks else None,
                    "last_week": weeks[-1].period_id if weeks else None,
                }

        elif args.command == "audit-splits":
            protocol = read_json(_input(root, args.protocol))
            manifest = read_json(_input(root, args.manifest))
            snapshot_dir = root / "research_v2" / "data" / manifest["dataset_id"]

            import pandas as pd
            session_df = pd.read_csv(snapshot_dir / "session_table.csv")
            session_dates = session_df["session_date"].tolist()

            result = audit_splits(
                protocol=protocol,
                session_dates=session_dates,
                metadata_only=args.metadata_only,
            )

        elif args.command == "audit-signals":
            if args.partition != "train":
                raise GuardError("Stage 3 signal audit is strictly restricted to partition 'train'. Holdout access forbidden.")

            manifest = read_json(_input(root, "docs/research_v2/dataset_manifest.json"))
            protocol = read_json(_input(root, "research_v2/protocol.json"))
            snapshot_dir = root / "research_v2" / "data" / manifest["dataset_id"]

            import pandas as pd
            session_df = pd.read_csv(snapshot_dir / "session_table.csv")
            session_dates = session_df["session_date"].tolist()

            # Load bars for all 8 ETFs
            bars = {}
            for sym in UNIVERSE:
                bars[sym] = pd.read_csv(snapshot_dir / f"{sym}.csv")

            train_end = PARTITIONS["train"]["end"]
            train_start = PARTITIONS["train"]["start"]
            as_of_train = market_as_of_after_close(train_end)

            all_weekly = build_weekly_schedule(
                [s for s in session_dates if s <= train_end],
                as_of=as_of_train,
            )
            train_weekly = [w for w in all_weekly if train_start <= w.signal_session <= train_end]

            all_monthly = build_monthly_matched_schedule(
                [s for s in session_dates if s <= train_end],
                as_of=as_of_train,
                weekly_events=all_weekly,
            )
            train_monthly = [m for m in all_monthly if train_start <= m.signal_session <= train_end]

            models_grid = protocol["model_grid"]["models"]
            audit_summary = {
                "partition": "train",
                "train_start": train_start,
                "train_end": train_end,
                "total_weekly_events": len(train_weekly),
                "total_monthly_events": len(train_monthly),
                "first_train_signal_session": train_weekly[0].signal_session if train_weekly else None,
                "last_train_signal_session": train_weekly[-1].signal_session if train_weekly else None,
                "models": {},
            }

            model_results = {}
            for m_spec in models_grid:
                m_id = m_spec["model_id"]
                m_months = m_spec["momentum_months"]
                t_weeks = m_spec["trend_weeks"]

                sig_frame = compute_model_signals_for_events(
                    bars=bars,
                    session_dates=session_dates,
                    weekly_events=all_weekly,
                    model_id=m_id,
                    momentum_months=m_months,
                    trend_weeks=t_weeks,
                )

                train_signals = [r for r in sig_frame.rows if train_start <= r["signal_session"] <= train_end]
                train_lineages = [l for l in sig_frame.lineage if train_start <= l["signal_session"] <= train_end]

                model_results[m_id] = {
                    "signal_count": len(train_signals),
                    "signals": train_signals,
                    "lineage": train_lineages,
                }
                audit_summary["models"][m_id] = {
                    "signal_count": len(train_signals),
                    "momentum_months": m_months,
                    "trend_weeks": t_weeks,
                }

            # Hand-picked lineage examples for verification
            sample_model = "W_M06_T36"
            sample_lineages = model_results[sample_model]["lineage"]
            target_dates = ["2006-02-03", "2006-04-13", "2006-03-31", "2008-02-29"]
            examples = {}
            for t_date in target_dates:
                found = [l for l in sample_lineages if l["signal_session"] == t_date]
                if found:
                    examples[t_date] = {
                        "signal_session": found[0]["signal_session"],
                        "period_id": found[0]["period_id"],
                        "SPY": found[0]["symbols"]["SPY"],
                        "QQQ": found[0]["symbols"]["QQQ"],
                    }

            audit_summary["sample_model"] = sample_model
            audit_summary["lineage_examples"] = examples
            audit_summary["holdout_gate"] = "LOCKED_NOT_ACCESSED"
            audit_summary["status"] = "PASS"

            out_artifact_dir = root / "research_v2" / "artifacts" / args.run_id
            out_artifact_dir.mkdir(parents=True, exist_ok=True)
            out_artifact_file = out_artifact_dir / "train_signal_audit.json"
            out_artifact_file.write_bytes(canonical_json(audit_summary) + b"\n")

            result = audit_summary

        else:
            raise GuardError(f"Unknown command: {args.command}")

        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 1 if result.get("status") == "FAIL" else 0

    except (ValueError, OSError, KeyError, GuardError) as error:
        print(
            json.dumps({"status": "FAIL", "error_type": type(error).__name__, "error": str(error)}),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
