"""Tasks A and B: cash-proxy adequacy and CN small-account bond substitutes. Reads the local snapshot only.

Run: python -m research_v3.implementation.analyze  -> prints tables, writes results.json next to this file.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from research_v3.cash_yield import CashYieldCurve
from research_v3.cn_transfer.data import build_adjusted, load_shibor, parse_hfq_js
from research_v3.cn_transfer.signals_cn import ShiborCurve

DATA = Path(__file__).resolve().parent / "data"
OUT = Path(__file__).resolve().parent / "results.json"
END = "2026-10-16"          # last finished session in the snapshot (2026-10-19 bar excluded as possibly partial)
GAP_LIMIT_PP, MDD_LIMIT = 0.5, 0.005


def ann(total: float, d0: str, d1: str) -> float:
    days = (pd.Timestamp(d1) - pd.Timestamp(d0)).days
    return float(total ** (365.0 / days) - 1.0) if days > 0 and total > 0 else float("nan")


def max_dd(v: pd.Series) -> float:
    v = v.astype(float)
    return float((v / v.cummax() - 1.0).min())


def curve_total(curve, dates: list[str]) -> float:
    f = 1.0
    for a, b in zip(dates[:-1], dates[1:]):
        f *= curve.accrual_factor(a, b)
    return f


def compare(label: str, adj: pd.Series, curve, start: str | None = None, end: str = END) -> dict:
    """adj: total-return price indexed by date str. Returns annualized return of proxy vs curve over [start, end]."""
    s = adj[(adj.index <= end) & ((adj.index >= start) if start else True)]
    d = list(s.index)
    pr = float(s.iloc[-1] / s.iloc[0])
    cr = curve_total(curve, d)
    a, b = ann(pr, d[0], d[-1]), ann(cr, d[0], d[-1])
    return {"label": label, "start": d[0], "end": d[-1], "sessions": len(d), "proxy_ann": a, "curve_ann": b,
            "gap_pp": (a - b) * 100, "proxy_total": pr - 1, "curve_total": cr - 1, "max_dd": max_dd(s)}


def verdict(r: dict) -> str:
    return "合格" if abs(r["gap_pp"]) <= GAP_LIMIT_PP and abs(r["max_dd"]) < MDD_LIMIT else "不合格"


def ths(sym: str) -> pd.DataFrame:
    df = pd.read_csv(DATA / f"ths_{sym}.csv", dtype={"Date": str})
    return df[df["Date"] <= END].reset_index(drop=True)


def sina_adjusted(sym: str) -> pd.DataFrame:
    raw = pd.read_csv(DATA / f"sina_{sym}.csv", dtype={"Date": str}).sort_values("Date").reset_index(drop=True)
    cum_div, share = parse_hfq_js((DATA / f"sina_{sym}_hfq.json").read_text())
    return build_adjusted(raw[["Date", "open", "high", "low", "close", "volume"]], cum_div, share)


def task_a() -> dict:
    out: dict = {"cn": [], "us": [], "gc001": None}
    shibor = ShiborCurve(load_shibor())
    for sym in ("511880", "511990"):
        t = ths(sym)
        s5 = pd.Series(t["close_price"].values, index=t["Date"].values)
        if sym == "511990":      # price-only series excludes the daily income: handled separately below
            continue
        out["cn"].append({**compare(f"{sym} THS近5年", s5, shibor), "sym": sym, "src": "THS"})
        for y, lab in ((3, "近3年"), (1, "近1年")):
            st = (pd.Timestamp(END) - pd.DateOffset(years=y)).strftime("%Y-%m-%d")
            ss = s5[s5.index >= st]
            out["cn"].append({**compare(f"{sym} THS{lab}", ss, shibor), "sym": sym, "src": "THS"})
        sa = sina_adjusted(sym)
        sl = pd.Series(sa["adj_close"].values, index=sa["Date"].values)
        out["cn"].append({**compare(f"{sym} 新浪自建复权·上市以来", sl, shibor), "sym": sym, "src": "Sina"})
    # 511990: price stays ~100 and income is paid out daily, so rebuild total return from income per 10k units
    m = pd.read_csv(DATA / "em_moneyfund_511990.csv", dtype={"净值日期": str})
    m = m[(m["净值日期"] > out["cn"][0]["start"]) & (m["净值日期"] <= END)]
    tot = float(np.prod(1.0 + m["每万份收益"].astype(float).values / 1e4))
    c0 = out["cn"][0]
    cf = c0["curve_total"] + 1
    a990 = ann(tot, c0["start"], END)
    out["cn"].append({"label": "511990 日收益再投资·近5年(东财每万份收益)", "sym": "511990", "src": "EM income",
                      "start": c0["start"], "end": END, "sessions": int(len(m)), "proxy_ann": a990,
                      "curve_ann": c0["curve_ann"], "gap_pp": (a990 - c0["curve_ann"]) * 100,
                      "proxy_total": tot - 1, "curve_total": c0["curve_total"], "max_dd": 0.0})
    # THS vs Sina adjusted agreement for 511880 over the THS window
    t = ths("511880")
    sa = sina_adjusted("511880").set_index("Date")["adj_close"]
    j = pd.concat([t.set_index("Date")["close_price"], sa], axis=1, join="inner").dropna()
    ra = j.iloc[:, 0].pct_change().dropna()
    rb = j.iloc[:, 1].pct_change().dropna()
    out["ths_vs_sina_511880"] = {"corr": float(ra.corr(rb)),
                                 "ann_ths": ann(j.iloc[-1, 0] / j.iloc[0, 0], j.index[0], j.index[-1]),
                                 "ann_sina": ann(j.iloc[-1, 1] / j.iloc[0, 1], j.index[0], j.index[-1])}
    # GC001 / 204001 (close = last trade annualized rate in %)
    g = pd.read_csv(DATA / "gc001_204001_sina.csv", dtype={"date": str})
    g = g[g["date"] <= END]
    gser = pd.Series(g["close"].values, index=g["date"].values, dtype=float)
    gcurve = ShiborCurve(gser)
    sessions = list(gser.index)
    f = curve_total(gcurve, sessions)
    out["gc001"] = {"start": sessions[0], "end": sessions[-1], "n": len(sessions), "mean_close_pct": float(gser.mean()),
                    "ann_gross": ann(f, sessions[0], sessions[-1])}
    # same window vs SHIBOR and 511880 (5y window)
    w0 = out["cn"][0]["start"]
    gs = gser[gser.index >= w0]
    d = list(gs.index)
    fg = curve_total(gcurve, d)
    fs = curve_total(shibor, d)
    g_ann, s_ann = ann(fg, d[0], d[-1]), ann(fs, d[0], d[-1])
    # fee drag: ~0.001% of principal per 1-day trade (assumption, broker dependent); rolled each session
    fee = 1e-5
    net = 1.0
    for a, b in zip(d[:-1], d[1:]):
        net *= gcurve.accrual_factor(a, b) * (1 - fee)
    out["gc001"].update({"win_start": d[0], "win_end": d[-1], "win_ann_gross": g_ann, "win_shibor_ann": s_ann,
                         "win_gap_pp_gross": (g_ann - s_ann) * 100, "win_ann_net_fee": ann(net, d[0], d[-1]),
                         "win_gap_pp_net": (ann(net, d[0], d[-1]) - s_ann) * 100,
                         "min_close_pct": float(gs.min()), "max_close_pct": float(gs.max())})
    # US
    irx = pd.read_csv(DATA / "yf_IRX.csv", dtype={"Date": str})
    icurve = CashYieldCurve(pd.Series(irx["Close"].values, index=irx["Date"].values, dtype=float).dropna())
    for sym in ("BIL", "SGOV"):
        y = pd.read_csv(DATA / f"yf_{sym}.csv", dtype={"Date": str}).dropna(subset=["Adj Close"])
        ser = pd.Series(y["Adj Close"].values, index=y["Date"].values)
        ser = ser[ser.index >= "2007-05-30"]
        out["us"].append({**compare(f"{sym} 上市以来", ser, icurve), "sym": sym})
        if sym == "BIL":
            for yrs in (5, 3, 1):
                st = (pd.Timestamp(END) - pd.DateOffset(years=yrs)).strftime("%Y-%m-%d")
                out["us"].append({**compare(f"BIL 近{yrs}年", ser[ser.index >= st], icurve), "sym": sym})
            out["us"].append({**compare("BIL 2007-2020(研究区间)", ser, icurve, end="2020-12-31"), "sym": sym})
    return out


def task_b() -> dict:
    ref = ths("511010")
    rows = []
    refc = ref.set_index("Date")["close_price"]
    names = {}
    for sym in ["511010", "511020", "511260", "511090", "511130", "511100", "511160", "511580", "511520", "511060",
                "159972", "511270", "159649", "511220"]:
        t = ths(sym)
        c = t.set_index("Date")["close_price"]
        sina = pd.read_csv(DATA / f"sina_{sym}.csv", dtype={"Date": str})
        price = float(t["close_price"].iloc[-1])
        j = pd.concat([c, refc], axis=1, join="inner").dropna()
        j.columns = ["c", "r"]
        rc, rr = j["c"].pct_change().dropna(), j["r"].pct_change().dropna()
        diff = rc - rr
        a_c, a_r = ann(j["c"].iloc[-1] / j["c"].iloc[0], j.index[0], j.index[-1]), \
            ann(j["r"].iloc[-1] / j["r"].iloc[0], j.index[0], j.index[-1])
        row = {"sym": sym, "price": price, "lot_cost": price * 100, "listing": str(sina["Date"].min()),
               "turnover_60d": float(t["turnover"].iloc[-60:].mean()), "overlap_start": j.index[0],
               "overlap_end": j.index[-1], "overlap_days": len(j), "corr": float(rc.corr(rr)),
               "ann_cand": a_c, "ann_ref": a_r, "ann_diff_pp": (a_c - a_r) * 100,
               "tracking_error": float(diff.std() * math.sqrt(252)), "vol_ann": float(rc.std() * math.sqrt(252))}
        if sym != "511010":
            row["pass_corr"] = row["corr"] >= 0.90
            row["pass_diff"] = abs(row["ann_diff_pp"]) <= 1.0
            row["pass_turnover"] = row["turnover_60d"] >= 1e7
            row["pass_lot"] = row["lot_cost"] <= 5000
            row["acceptable"] = all(row[k] for k in ("pass_corr", "pass_diff", "pass_turnover", "pass_lot"))
        rows.append(row)
    return {"rows": rows}


def main():
    a, b = task_a(), task_b()
    OUT.write_text(json.dumps({"A": a, "B": b}, indent=1, ensure_ascii=False, default=float) + "\n")
    pd.set_option("display.width", 250)
    print("== A CN / US")
    for r in a["cn"] + a["us"]:
        print(f'{r["label"]:28s} {r["start"]}~{r["end"]} n={r["sessions"]:5d} proxy={r["proxy_ann"]*100:6.3f}% '
              f'curve={r["curve_ann"]*100:6.3f}% gap={r["gap_pp"]:+6.3f}pp mdd={r["max_dd"]*100:7.4f}% {verdict(r)}')
    print(json.dumps(a["ths_vs_sina_511880"]), json.dumps(a["gc001"], ensure_ascii=False))
    print("== B")
    print(pd.DataFrame(b["rows"]).round(4).to_string())


if __name__ == "__main__":
    main()
