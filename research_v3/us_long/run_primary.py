"""Primary pre-registered runs on the 1971-2005 proxies: frozen rule + BASE + benchmarks (protocol_us_long.json) and
CRASH_WEEKLY_EXIT vs the frozen rule (protocol_challenger_crash.json)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from research_v3.cash_yield import CashYieldCurve
from research_v3.config import V3Config
from research_v3.step3 import MIN_YEAR_SESSIONS, calendar_year_returns

from . import data as D
from .calendar_proxy import ProxyCalendar
from .engine_ul import run_backtest_ul
from .pipeline import build_challenger_rows, build_core, first_fully_warm_row, strategy_frames
from .proxies import EQUITY_GROUPS, GROUPS, UNIVERSE, build_proxies, price_dicts, signal_bars
from .protocols import load_crash, load_main
from .report import (boot_pairs, classify_challenger, classify_main, daily_rf, metrics_for, partition_bounds,
                     position_weights)

HERE = Path(__file__).resolve().parent
ART = HERE / "artifacts"
CFG = V3Config(cost_bps=5.0, slippage_bps=5.0, cash_yield_source="irx")
SCORE_END = "2005-12-31"
SPLIT_A_END, SPLIT_B_START = "1989-12-31", "1990-01-01"
EQUITY = {s for g in EQUITY_GROUPS for s in GROUPS[g]}


def load_inputs(data_dir=D.DATA_DIR, data_start="1971-01-01", score_end=SCORE_END, **proxy_kw):
    D.verify_manifest(data_dir)
    ps = build_proxies(ff3=D.load_french_ff3(data_dir), size_lo30=D.load_french_size_lo30_vw(data_dir),
                       devxus=D.load_french_devxus(data_dir), ixic=D.load_ixic(data_dir), gold=D.load_gold(data_dir),
                       dgs10=D.load_fred("DGS10", data_dir), dgs30=D.load_fred("DGS30", data_dir),
                       data_start=data_start, score_end=score_end, **proxy_kw)
    curve = CashYieldCurve(D.load_fred("DTB3", data_dir))
    return ps, curve


def evaluate(cal, sessions, score_start, score_end, rows_by_strat, exec_prices, close_prices, curve, universe, *,
             bounds, cfg=CFG, tag="primary", groups=GROUPS, suspension=False, model_prefix="",
             wsyms=None):
    wsyms = wsyms or {}
    navs, rebs, mets, mrows, series, cy_rows, pos = {}, {}, {}, [], {}, [], {}
    scored = [s for s in sessions if score_start <= s <= score_end]
    for name, rows in rows_by_strat.items():
        nav, reb, m = run_backtest_ul(cal, scored, rows, exec_prices, close_prices, cfg, curve, universe, name,
                                      suspension_rules=suspension, record_positions=True)
        navs[name], rebs[name], mets[name] = nav, reb, m
        rf = daily_rf(nav["date"].tolist(), curve)
        nav["rf_accrual"] = rf
        yt = calendar_year_returns(nav, cfg.initial_nav)
        for y, r in yt.iterrows():
            cy_rows.append({"strategy": name, "year": int(y), "return": r["ret"], "sessions": int(r["sessions"]),
                            "full_year": bool(r["sessions"] >= MIN_YEAR_SESSIONS)})
        pw = position_weights(nav, m["positions"], close_prices, groups, **wsyms)
        for part, (a, b) in bounds.items():
            ex, row = metrics_for(cal, nav, reb, rf, part, a, b, cfg.initial_nav, yt, pw)
            series[(part, name)] = ex
            mrows.append({"strategy": name, **row})
    return navs, rebs, mets, pd.DataFrame(mrows), pd.DataFrame(cy_rows), series


def run_primary(out: Path = ART, log=print) -> dict:
    proto = load_main()
    crash = load_crash()
    ps, curve = load_inputs()
    sessions = ps.sessions
    cal = ProxyCalendar(sessions)
    bars = signal_bars(ps)
    core = build_core(cal, sessions, bars, curve)
    i0 = first_fully_warm_row(core.mf, UNIVERSE, ps.first_date)
    score_start = core.mf.rows[i0]["execution_session"]
    score_end = sessions[-1]
    log(f"sessions {sessions[0]}..{sessions[-1]} n={len(sessions)}; first warm signal "
        f"{core.mf.rows[i0]['signal_session']} -> score_start {score_start}")
    frames = strategy_frames(core.mf, core.capdef, score_start, score_end, bars["SPY"])
    weekly_rows = [r for r in core.wf.rows]
    msig = {r.signal_session for r in core.monthly}
    ch_rows, ch_log = build_challenger_rows(
        core.capdef.rows, weekly_rows, msig, core.defensive, equity_symbols=EQUITY, def_symbol="IEF",
        score_start=score_start, score_end=score_end)
    rows = {k: v.rows for k, v in frames.items()}
    rows["CRASH_WEEKLY_EXIT"] = ch_rows
    exec_px, close_px = price_dicts(ps, "close")
    bounds = partition_bounds(score_start, score_end, SPLIT_A_END, SPLIT_B_START)
    navs, rebs, mets, tbl, cy, series = evaluate(cal, sessions, score_start, score_end, rows, exec_px, close_px,
                                                 curve, UNIVERSE, bounds=bounds)
    verdict = classify_main(tbl)
    verdict_ch = classify_challenger(tbl)
    boot = []
    for part in bounds:
        boot += boot_pairs(series, part, "CAP_DEF_M", ["US_60_40", "SPY_BH"])
        boot += boot_pairs(series, part, "CRASH_WEEKLY_EXIT", ["CAP_DEF_M"])
    bt = pd.DataFrame(boot)
    # SPY proxy calendar-year returns (full years, not the scoring window)
    spy = ps.valuation["SPY"]
    yr = spy.groupby(pd.to_datetime(pd.Series(spy.index, index=spy.index)).dt.year.values).last()
    spy_idx_year = (yr / yr.shift(1) - 1.0).dropna()
    # fills touching signals and exits
    fill_signal_cells = {}
    wsess = [r["signal_session"] for r in core.wf.rows]
    for s in UNIVERSE:
        if ps.first_date[s] is None:
            fill_signal_cells[s] = None
            continue
        nan_at = ps.signal[s].reindex(wsess).isna() & (pd.Series(wsess) >= ps.first_date[s]).values
        fill_signal_cells[s] = int(nan_at.sum())
    exec_fill = _exec_fill_counts(rows["CAP_DEF_M"], ps)
    report = {"sessions": [sessions[0], sessions[-1], len(sessions)], "score_start": score_start,
              "score_end": score_end, "first_warm_signal": core.mf.rows[i0]["signal_session"],
              "first_date": ps.first_date, "fills_valuation_sessions": ps.fills,
              "weekly_signal_dates_with_no_observation": fill_signal_cells,
              "frozen_exec_on_filled_price_count": exec_fill,
              "n_weekly_events": len(core.weekly), "n_monthly_events": len(core.monthly),
              "n_weekly_exit_events": len(ch_log), "n_assets_exited": int(sum(len(e["exited"].split(",")) for e in ch_log)),
              "protocol_sha256": {"main": (HERE / "protocol_us_long.sha256").read_text().strip(),
                                  "crash": (HERE / "protocol_challenger_crash.sha256").read_text().strip()}}
    out.mkdir(parents=True, exist_ok=True)
    tbl.to_csv(out / "metrics.csv", index=False)
    cy.to_csv(out / "calendar_year_returns.csv", index=False)
    bt.to_csv(out / "bootstrap.csv", index=False)
    spy_idx_year.rename("spy_proxy_calendar_year_return").to_csv(out / "spy_proxy_calendar_year_returns.csv")
    pd.DataFrame(ch_log).to_csv(out / "crash_weekly_exits.csv", index=False)
    for k, nav in navs.items():
        nav.to_csv(out / f"nav_{k}.csv", index=False)
    pd.DataFrame([{"signal_session": r["signal_session"], "execution_session": r["execution_session"],
                   "selected": ",".join(r["selected_symbols"]), "def_bond_weight": r["def_bond_weight"],
                   **{f"w_{k}": v for k, v in r["weights"].items()}} for r in core.capdef.rows]
                 ).to_csv(out / "signals_CAP_DEF_M.csv", index=False)
    (out / "data_report.json").write_text(json.dumps(report, indent=2, default=str))
    (out / "verdicts.json").write_text(json.dumps({"main": verdict, "challenger": verdict_ch}, indent=2))
    return {"tbl": tbl, "cy": cy, "bt": bt, "verdict": verdict, "verdict_ch": verdict_ch, "report": report,
            "mets": mets, "ch_log": ch_log, "spy_idx_year": spy_idx_year, "proto": proto, "crash": crash}


def _exec_fill_counts(rows, ps):
    n = 0
    for r in rows:
        if r.get("status") != "COMPLETE":
            continue
        for s, w in r["weights"].items():
            if s != "CASH" and w > 1e-12 and s in ps.signal:
                d = r["execution_session"]
                if d in ps.signal[s].index and pd.isna(ps.signal[s].get(d)):
                    n += 1
    return n


if __name__ == "__main__":
    res = run_primary()
    pd.set_option("display.width", 250, "display.max_columns", 40)
    print(res["tbl"][["strategy", "partition", "start", "end", "cagr", "ann_vol", "excess_sharpe", "max_drawdown",
                      "mdd_peak", "mdd_trough", "worst_year", "ann_one_way_turnover"]].to_string())
    print(res["verdict"], res["verdict_ch"], sep="\n")
