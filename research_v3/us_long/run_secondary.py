"""Secondary, information-only runs of CRASH_WEEKLY_EXIT vs the frozen CAP_DEF_M: research_v3 US 2006-2020 (research_v2
snapshot, real ETFs, next-open execution) and research_v3/cn_transfer CN 2014-2026 (amendment-1 suspension rules)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from research_v2.calendar import get_trading_calendar
from research_v3.cash_yield import CashYieldCurve, load_irx
from research_v3.config import V3Config
from research_v3.guards import assert_date_allowed, assert_partition_allowed
from research_v3.step2 import _setup

from .pipeline import build_challenger_rows, build_core, first_fully_warm_row, strategy_frames
from .proxies import EQUITY_GROUPS, GROUPS, UNIVERSE
from .report import boot_pairs, classify_challenger, partition_bounds
from .run_primary import ART, CFG, EQUITY, evaluate

US_SCORE_START, US_SCORE_END = "2006-02-01", "2020-12-31"


def bars_to_prices(bars, universe):
    o = {s: {str(d): float(v) for d, v in zip(bars[s]["Date"], bars[s]["adj_open"]) if v == v} for s in universe}
    c = {s: {str(d): float(v) for d, v in zip(bars[s]["Date"], bars[s]["adj_close"]) if v == v} for s in universe}
    return o, c


def us_2006_2020(log=print):
    assert_partition_allowed("validation")
    assert_date_allowed(US_SCORE_END)
    view, sessions, weekly, monthly = _setup("validation")      # bars 2005-01-03..2020-12-31 only
    curve = CashYieldCurve(load_irx())
    cal = get_trading_calendar()
    from research_v2.signals import compute_matched_monthly_signals
    from research_v3.cn_transfer.signals_cn import cap_def_frame, compute_model_signals_cn
    from research_v3.signals_step3 import make_defensive_provider
    wf = compute_model_signals_cn(view.bars, UNIVERSE, sessions, weekly, "W_M12_T40", 12, 40)
    mf = compute_matched_monthly_signals(wf, monthly, "M_M12_T40")
    defensive = make_defensive_provider(view.bars, sessions, curve, "IEF", 3)
    capdef = cap_def_frame(mf, "CAP_DEF_M", universe=UNIVERSE, cap_groups=GROUPS, defensive=defensive,
                           def_symbol="IEF", top_n=3)
    frames = strategy_frames(mf, capdef, US_SCORE_START, US_SCORE_END, view.bars["SPY"])
    ch_rows, ch_log = build_challenger_rows(
        capdef.rows, wf.rows, {m.signal_session for m in monthly}, defensive, equity_symbols=EQUITY,
        def_symbol="IEF", score_start=US_SCORE_START, score_end=US_SCORE_END)
    rows = {k: v.rows for k, v in frames.items() if k in ("CAP_DEF_M", "SPY_BH", "US_60_40")}
    rows["CRASH_WEEKLY_EXIT"] = ch_rows
    o, c = bars_to_prices(view.bars, UNIVERSE)
    bounds = partition_bounds(US_SCORE_START, US_SCORE_END, "2015-12-31", "2016-01-01")
    navs, rebs, mets, tbl, cy, series = evaluate(cal, sessions, US_SCORE_START, US_SCORE_END, rows, o, c, curve,
                                                 UNIVERSE, bounds=bounds)
    bt = pd.DataFrame(sum((boot_pairs(series, p, "CRASH_WEEKLY_EXIT", ["CAP_DEF_M"]) for p in bounds), []))
    return {"tbl": tbl, "cy": cy, "bt": bt, "verdict": classify_challenger(tbl), "ch_log": ch_log, "navs": navs,
            "note": "partitions: SUB_A = train (2006-02..2015-12), SUB_B = validation (2016..2020); one continuous run"}


def cn_2014_2026(log=print):
    from research_v3.cn_transfer import runner as R
    from research_v3.cn_transfer.calendar_cn import (CN_TZ, build_monthly_matched_schedule_cal,
                                                     build_weekly_schedule_cal, get_xshg_calendar,
                                                     market_as_of_after_close_cal)
    from research_v3.cn_transfer.data import DATA_DIR, load_bars, load_shibor, verify_manifest
    from research_v3.cn_transfer.protocol import load_amendment_verified, load_verified
    from research_v3.cn_transfer.signals_cn import ShiborCurve, cap_def_frame
    from research_v3.signals_step3 import make_defensive_provider

    proto = load_verified()
    load_amendment_verified()
    verify_manifest(DATA_DIR)
    mp = proto["mapping_from_us_to_cn"]
    universe = tuple(mp["universe_order"])
    groups = R.protocol_groups(proto)
    def_symbol = "511010"
    cal = get_xshg_calendar()
    raw = load_bars(universe, DATA_DIR)
    data_start = max(df["Date"].iloc[0] for df in raw.values())
    data_last = min(df["Date"].iloc[-1] for df in raw.values())
    score_end = R.choose_score_end(cal, data_last)
    bars = {s: df[(df["Date"] >= data_start) & (df["Date"] <= score_end)].reset_index(drop=True) for s, df in raw.items()}
    rep = R.data_checks(bars, cal, data_start, score_end)
    sessions = [x.strftime("%Y-%m-%d") for x in cal.sessions_in_range(data_start, score_end)]
    curve = ShiborCurve(load_shibor(DATA_DIR))
    vp = Path(DATA_DIR) / "tencent_verification_raw.csv"
    ver = pd.read_csv(vp, dtype={"symbol": str}) if vp.is_file() else None
    bars, _res = R.resolve_missing(bars, rep["missing_in_data_range"], ver)
    as_of = market_as_of_after_close_cal(cal, score_end, CN_TZ)
    weekly = build_weekly_schedule_cal(cal, sessions, as_of)
    monthly = build_monthly_matched_schedule_cal(cal, sessions, as_of, weekly)
    wf, mf = R.build_frames(proto, bars, universe, groups, sessions, weekly, monthly, curve, def_symbol)
    i0 = R.first_fully_warm_row(mf, universe)
    score_start = mf.rows[i0]["execution_session"]
    defensive = make_defensive_provider(bars, sessions, curve, symbol=def_symbol, months=3)
    capdef = cap_def_frame(mf, "CAP_DEF_M", universe=universe, cap_groups=groups, defensive=defensive,
                           def_symbol=def_symbol, top_n=3)
    frames = R.strategy_frames(mf, capdef, score_start, score_end, universe)
    eq = {s for g in ("CN_LARGE", "CN_MIDSMALL_GROWTH") for k, v in groups.items() if k.startswith(g) for s in v}
    assert eq == {"510300", "510050", "510500", "159915"}, eq
    ch_rows, ch_log = build_challenger_rows(
        capdef.rows, wf.rows, {m.signal_session for m in monthly}, defensive, equity_symbols=eq,
        def_symbol=def_symbol, score_start=score_start, score_end=score_end)
    rows = {"CAP_DEF_M": frames["CAP_DEF_M"].rows, "CN_60_40": frames["CN_60_40"].rows,
            "CSI300_BH": frames["CSI300_BH"].rows, "CRASH_WEEKLY_EXIT": ch_rows}
    o, c = bars_to_prices(bars, universe)
    bounds = R.partition_bounds(score_start, score_end)
    navs, rebs, mets, tbl, cy, series = evaluate(
        cal, sessions, score_start, score_end, rows, o, c, curve, universe, bounds=bounds, suspension=True,
        wsyms=dict(bond_syms=("511010",), eq_syms=tuple(eq), gold_syms=("518880",)))
    bt = pd.DataFrame(sum((boot_pairs(series, p, "CRASH_WEEKLY_EXIT", ["CAP_DEF_M"]) for p in bounds), []))
    # cross-check: frozen NAV vs the committed cn_transfer artifact (same engine rules)
    ref = pd.read_csv(R.ART_DIR / "nav_primary_CAP_DEF_M.csv")
    d = float((navs["CAP_DEF_M"]["nav"].values - ref["nav"].values).__abs__().max()) \
        if len(ref) == len(navs["CAP_DEF_M"]) else float("nan")
    post = {k: m.get("postponements", []) for k, m in mets.items()}
    return {"tbl": tbl, "cy": cy, "bt": bt, "verdict": classify_challenger(tbl), "ch_log": ch_log, "navs": navs,
            "score_start": score_start, "score_end": score_end, "ref_nav_maxabs_diff": d, "postponements": post,
            "note": "partitions: SUB_A = ..2020-12-31, SUB_B = 2021..; one continuous run, amendment-1 suspension rules"}


def run_all(out: Path = ART, log=print):
    out = Path(out)
    res = {}
    for name, fn in (("us_2006_2020", us_2006_2020), ("cn_2014_2026", cn_2014_2026)):
        r = fn(log)
        res[name] = r
        r["tbl"].to_csv(out / f"secondary_{name}_metrics.csv", index=False)
        r["cy"].to_csv(out / f"secondary_{name}_calendar_year_returns.csv", index=False)
        r["bt"].to_csv(out / f"secondary_{name}_bootstrap.csv", index=False)
        pd.DataFrame(r["ch_log"]).to_csv(out / f"secondary_{name}_weekly_exits.csv", index=False)
        for k, nav in r["navs"].items():
            nav.to_csv(out / f"secondary_{name}_nav_{k}.csv", index=False)
        (out / f"secondary_{name}_verdict.json").write_text(json.dumps(r["verdict"], indent=2))
    return res


if __name__ == "__main__":
    pd.set_option("display.width", 250, "display.max_columns", 40)
    res = run_all()
    for k, r in res.items():
        print(k, r.get("note"), "ref diff", r.get("ref_nav_maxabs_diff"))
        print(r["tbl"][["strategy", "partition", "start", "end", "cagr", "ann_vol", "excess_sharpe", "max_drawdown",
                        "ann_one_way_turnover"]].to_string())
        print(r["verdict"], "exits", len(r["ch_log"]))
