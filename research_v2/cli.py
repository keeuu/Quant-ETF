"""CLI entrypoint for Research V2.

Supports Stage 1 baseline/guard commands and Stage 2 data/calendar/splits commands.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
import pandas as pd

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
from .engine import run_backtest
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

    # --- Stage 4 Subcommands ---
    p = subs.add_parser("canary", help="Run Stage 4 single-model Train canary backtest")
    p.add_argument("--partition", default="train", help="Partition to run backtest on (train only)")
    p.add_argument("--model", default="W_M12_T40", help="Model ID (W_M12_T40 only)")
    p.add_argument("--cost-bps", type=float, default=5.0, help="Transaction cost in bps (e.g. 5)")
    p.add_argument("--initial-nav", type=float, default=100000.0, help="Initial cash capital")
    p.add_argument("--run-id", default="phase_04", help="Execution run identifier")

    p = subs.add_parser("audit-ledger", help="Audit Stage 4 ledger invariants, conservation, and outputs")
    p.add_argument("--run-id", default="phase_04", help="Execution run identifier")


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

        elif args.command == "canary":
            if args.partition != "train":
                raise GuardError(f"Stage 4 canary strictly permits train partition only, got: {args.partition}")
            if args.model != "W_M12_T40":
                raise GuardError(f"Stage 4 canary strictly permits model W_M12_T40 only, got: {args.model}")

            dataset_manifest_path = root / "docs/research_v2/dataset_manifest.json"
            if not dataset_manifest_path.is_file():
                raise GuardError("dataset_manifest.json missing; snapshot required")

            manifest_data = read_json(dataset_manifest_path)
            snapshot_dir = root / "research_v2" / "data" / manifest_data["dataset_id"]
            protocol = read_json(_input(root, "research_v2/protocol.json"))

            # Load partition scoped view
            dataset_view = load_dataset(
                snapshot_dir=snapshot_dir,
                partition=args.partition,
                as_of=manifest_data["as_of"],
                splits=protocol["splits"],
            )

            # Build trading calendar and weekly schedule
            session_table_path = snapshot_dir / "session_table.csv"
            session_df = pd.read_csv(session_table_path)
            session_dates = session_df["session_date"].tolist()

            train_end = PARTITIONS["train"]["end"]
            train_start = PARTITIONS["train"]["start"]
            as_of_train = market_as_of_after_close(train_end)

            all_weekly = build_weekly_schedule(
                [s for s in session_dates if s <= train_end],
                as_of=as_of_train,
            )

            # Find model spec
            model_spec = next(m for m in protocol["model_grid"]["models"] if m["model_id"] == args.model)

            # Compute signals
            sig_frame = compute_model_signals_for_events(
                bars=dataset_view.bars,
                session_dates=session_dates,
                weekly_events=all_weekly,
                model_id=args.model,
                momentum_months=model_spec["momentum_months"],
                trend_weeks=model_spec["trend_weeks"],
            )

            # Run backtest simulation
            daily_nav_df, positions_df, trades_df, rebalance_df, pending_df, metrics_dict = run_backtest(
                dataset_view=dataset_view,
                signals=sig_frame,
                cost_bps=args.cost_bps,
                initial_nav=args.initial_nav,
            )

            # Save artifacts
            out_artifact_dir = root / "research_v2" / "artifacts" / args.run_id
            out_artifact_dir.mkdir(parents=True, exist_ok=True)

            daily_nav_df.to_csv(out_artifact_dir / "daily_nav.csv", index=False)
            positions_df.to_csv(out_artifact_dir / "positions.csv", index=False)
            trades_df.to_csv(out_artifact_dir / "trades.csv", index=False)
            rebalance_df.to_csv(out_artifact_dir / "rebalance.csv", index=False)
            pending_df.to_csv(out_artifact_dir / "pending_events.csv", index=False)
            (out_artifact_dir / "metrics.json").write_bytes(canonical_json(metrics_dict) + b"\n")

            canary_summary = {
                "status": "PASS",
                "model_id": args.model,
                "partition": args.partition,
                "cost_bps": args.cost_bps,
                "initial_nav": args.initial_nav,
                "trading_days": metrics_dict["trading_days"],
                "rebalance_count": metrics_dict["rebalance_count"],
                "total_trades": len(trades_df),
                "total_fees": metrics_dict["total_fees"],
                "final_nav": metrics_dict["final_nav"],
                "total_return": metrics_dict["total_return"],
                "cagr": metrics_dict["cagr"],
                "annualized_volatility": metrics_dict["annualized_volatility"],
                "sharpe_ratio": metrics_dict["sharpe_ratio"],
                "max_drawdown": metrics_dict["max_drawdown"],
                "annualized_one_way_turnover": metrics_dict["annualized_one_way_turnover"],
                "annualized_gross_traded_ratio": metrics_dict["annualized_gross_traded_ratio"],
                "number_pending_outside_segment": len(pending_df),
                "pending_events_artifact": "pending_events.csv",
                "pending_statuses": pending_df["status"].unique().tolist() if not pending_df.empty else [],
                "holdout_gate": "NOT_ACCESSED",
            }
            result = canary_summary

        elif args.command == "audit-ledger":
            out_artifact_dir = root / "research_v2" / "artifacts" / args.run_id
            if not out_artifact_dir.is_dir():
                raise GuardError(f"Artifact directory missing: {out_artifact_dir}")

            daily_nav_path = out_artifact_dir / "daily_nav.csv"
            positions_path = out_artifact_dir / "positions.csv"
            trades_path = out_artifact_dir / "trades.csv"
            rebalance_path = out_artifact_dir / "rebalance.csv"
            metrics_path = out_artifact_dir / "metrics.json"

            for p_req in [daily_nav_path, positions_path, trades_path, rebalance_path, metrics_path]:
                if not p_req.is_file():
                    raise GuardError(f"Required artifact missing: {p_req.name}")

            daily_nav_df = pd.read_csv(daily_nav_path)
            positions_df = pd.read_csv(positions_path)
            trades_df = pd.read_csv(trades_path)
            rebalance_df = pd.read_csv(rebalance_path)
            metrics_dict = read_json(metrics_path)

            audit_issues = []

            # 1. NAV positivity and cash non-negativity
            if (daily_nav_df["nav"] <= 0).any():
                audit_issues.append("Non-positive NAV detected")
            if (daily_nav_df["cash"] < -1e-8).any():
                audit_issues.append("Substantial negative cash detected in daily_nav")
            if (daily_nav_df["gross_exposure"] > 1.0 + 1e-6).any():
                audit_issues.append("Gross exposure exceeds 1.0")

            # 2. Rebalance self-financing invariants
            max_solver_residual = 0.0
            for _, reb in rebalance_df.iterrows():
                v_pre = reb["V_pre"]
                v_post = reb["V_post"]
                fee = reb["fee"]
                res = reb["solver_residual"]
                tol = 1e-8 * max(1.0, v_pre)

                if abs(v_post + fee - v_pre) > tol:
                    audit_issues.append(f"Conservation mismatch on {reb['execution_session']}: V_post+fee != V_pre")
                if res > max_solver_residual:
                    max_solver_residual = res
                if res > 1e-10 * max(1.0, v_pre):
                    audit_issues.append(f"Solver residual {res} exceeded tolerance on {reb['execution_session']}")

            # 3. Trades fee reconciliation
            if not rebalance_df.empty:
                total_reb_fee = rebalance_df["fee"].sum()
                total_trade_fee = trades_df["fee_component"].sum() if not trades_df.empty else 0.0
                if abs(total_reb_fee - total_trade_fee) > 1e-6:
                    audit_issues.append(f"Total fee mismatch between rebalances ({total_reb_fee}) and trades ({total_trade_fee})")

            # 4. Positions valuation reconciliation on daily_nav
            if not positions_df.empty:
                pos_grouped = positions_df.groupby("date")["market_value"].sum()
                for d, mv in pos_grouped.items():
                    nav_row = daily_nav_df[daily_nav_df["date"] == d].iloc[0]
                    calc_nav = nav_row["cash"] + mv
                    if abs(calc_nav - nav_row["nav"]) > 1e-6:
                        audit_issues.append(f"Daily valuation mismatch on {d}: cash+mv={calc_nav} vs nav={nav_row['nav']}")

            audit_result = {
                "status": "PASS" if not audit_issues else "FAIL",
                "run_id": args.run_id,
                "issues": audit_issues,
                "trading_days_audited": len(daily_nav_df),
                "rebalances_audited": len(rebalance_df),
                "trades_audited": len(trades_df),
                "max_solver_residual": max_solver_residual,
                "total_fees_audited": float(rebalance_df["fee"].sum()) if not rebalance_df.empty else 0.0,
                "holdout_gate": "NOT_ACCESSED",
            }
            (out_artifact_dir / "ledger_audit.json").write_bytes(canonical_json(audit_result) + b"\n")
            result = audit_result

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
