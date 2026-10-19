"""Information-only sensitivity: signals see forward-filled values on sessions where a proxy has no observation
(the primary run, per protocol, shows them as missing). Same pipeline, nothing else changes."""
from __future__ import annotations

import pandas as pd

from .calendar_proxy import ProxyCalendar
from .pipeline import build_challenger_rows, build_core, first_fully_warm_row, strategy_frames
from .proxies import UNIVERSE, price_dicts, signal_bars
from .report import boot_pairs, classify_challenger, classify_main, partition_bounds
from .run_primary import ART, EQUITY, SCORE_END, SPLIT_A_END, SPLIT_B_START, evaluate, load_inputs


def run_ffill_signals(out=ART):
    ps, curve = load_inputs()
    ps.signal = {s: ps.valuation[s].copy() for s in UNIVERSE}
    sessions = ps.sessions
    cal = ProxyCalendar(sessions)
    bars = signal_bars(ps)
    core = build_core(cal, sessions, bars, curve)
    i0 = first_fully_warm_row(core.mf, UNIVERSE, ps.first_date)
    ss, se = core.mf.rows[i0]["execution_session"], sessions[-1]
    fr = strategy_frames(core.mf, core.capdef, ss, se, bars["SPY"])
    ch, log = build_challenger_rows(core.capdef.rows, core.wf.rows, {m.signal_session for m in core.monthly},
                                    core.defensive, equity_symbols=EQUITY, def_symbol="IEF", score_start=ss, score_end=se)
    rows = {k: v.rows for k, v in fr.items() if k in ("CAP_DEF_M", "SPY_BH", "US_60_40")}
    rows["CRASH_WEEKLY_EXIT"] = ch
    o, c = price_dicts(ps, "close")
    navs, rebs, mets, tbl, cy, series = evaluate(cal, sessions, ss, se, rows, o, c, curve, UNIVERSE,
                                                 bounds=partition_bounds(ss, se, SPLIT_A_END, SPLIT_B_START))
    tbl.to_csv(out / "sensitivity_ffill_signals_metrics.csv", index=False)
    return tbl, classify_main(tbl), classify_challenger(tbl), ss, len(log)


if __name__ == "__main__":
    t, v, vc, ss, n = run_ffill_signals()
    pd.set_option("display.width", 200)
    print(ss, n)
    print(t[["strategy", "partition", "cagr", "excess_sharpe", "max_drawdown"]].to_string())
    print(v, vc)
