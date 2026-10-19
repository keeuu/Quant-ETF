"""Part A data verification (protocol_cn_validation.json, part_A_data_verification).

Sources actually used are recorded in data/fetch_log.json. When Eastmoney bar endpoints (EM_HFQ / EM_UNADJ) are
unreachable, A1 and A4 cannot be done as written (protocol: 'if_A_unreachable': report, do not substitute invented
data); this module then reports them NOT RUN and, separately and clearly labelled, computes the closest faithful
independent check from sources that WERE reachable (Tencent unadjusted bars re-adjusted with Eastmoney distribution /
split records through the baseline adjustment code).
"""
from __future__ import annotations

import bisect
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from ..data import DATA_DIR, build_adjusted, load_bars, parse_hfq_js
from .fetch import BASE_SYMBOLS, VDATA

START, END = "2013-07-29", "2026-09-30"


# ------------------------------------------------------------------ pure computations (unit-tested)
def ann_return(dates: list[str], rets: np.ndarray) -> float:
    """Annualised compound return of consecutive-common-date returns. Years = calendar days from the first common date
    (the base of the first return) to the last date / 365.25."""
    days = (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[0])).days
    tot = float(np.prod(1.0 + rets))
    return tot ** (365.25 / days) - 1.0


def aligned_returns(ours: pd.Series, ref: pd.Series, start: str = START, end: str = END):
    """Daily returns of both series between consecutive dates present in BOTH (index = 'YYYY-MM-DD')."""
    common = sorted(d for d in ours.index.intersection(ref.index) if start <= d <= end)
    a = ours.loc[common].to_numpy(float)
    b = ref.loc[common].to_numpy(float)
    return common, a[1:] / a[:-1] - 1.0, b[1:] / b[:-1] - 1.0


def a1_diff(ours: pd.Series, ref: pd.Series, event_dates: list[str] | None = None, start: str = START,
            end: str = END) -> dict:
    """A1 statistics: mean |diff|, max |diff| (+date), count |diff| > 5 bp, annualised returns and their difference,
    and the days with |diff| > 50 bp that are NOT explained by an event (distribution/conversion) within 3 sessions."""
    common, ra, rb = aligned_returns(ours, ref, start, end)
    d = ra - rb
    ad = np.abs(d)
    imax = int(np.argmax(ad))
    ev_idx = sorted({bisect.bisect_left(common, e) for e in (event_dates or [])})
    big = [i for i in range(len(d)) if ad[i] > 0.005]
    unexplained = []
    for i in big:
        pos = i + 1  # return i is the move into common[i+1]
        if not any(abs(pos - e) <= 3 for e in ev_idx):
            unexplained.append({"date": common[pos], "diff_bp": float(d[i] * 1e4)})
    ann_a, ann_b = ann_return(common, ra), ann_return(common, rb)
    return {"n_days": int(len(d)), "mean_abs_diff_bp": float(ad.mean() * 1e4), "max_abs_diff_bp": float(ad[imax] * 1e4),
            "max_abs_diff_date": common[imax + 1], "n_days_gt_5bp": int((ad > 5e-4).sum()),
            "n_days_gt_50bp": len(big), "n_days_gt_50bp_unexplained": len(unexplained),
            "unexplained": unexplained, "ann_ours": ann_a, "ann_ref": ann_b, "ann_diff_pp": (ann_a - ann_b) * 100,
            "corr": float(np.corrcoef(ra, rb)[0, 1])}


def a1_pass(st: dict) -> bool:
    return abs(st["ann_diff_pp"]) <= 0.10 and st["n_days_gt_50bp_unexplained"] == 0


def nav_total_return_index(nav_df: pd.DataFrame) -> pd.Series:
    """Cumulative NAV total-return index from Eastmoney's daily NAV growth (percent), indexed by 'YYYY-MM-DD'."""
    g = pd.to_numeric(nav_df["日增长率"], errors="coerce").fillna(0.0).to_numpy() / 100.0
    idx = pd.to_datetime(nav_df["净值日期"]).dt.strftime("%Y-%m-%d")
    return pd.Series(np.cumprod(1.0 + g), index=idx.values)


# ------------------------------------------------------------------ event extraction
def sina_events(sym: str, data_dir: Path = DATA_DIR) -> dict:
    """Distributions (increments of Sina cumulative u) and share-conversion multipliers (changes of s)."""
    text = (Path(data_dir) / f"{sym}_sina_hfqjs_raw.json").read_text()
    cum_div, share_pts = parse_hfq_js(text)
    divs, prev = [], 0.0
    for d, u in cum_div:
        inc = u - prev
        prev = u
        if abs(inc) > 1e-12:
            divs.append((d, round(inc, 6)))
    convs, prev_s = [], 1.0
    for d, s in share_pts:
        if abs(s - prev_s) > 1e-12:
            convs.append((d, s / prev_s))
        prev_s = s
    return {"div": divs, "conv": convs}


def em_events(sym: str, val_dir: Path = VDATA) -> dict:
    """Eastmoney distribution records (ex-date, amount per share) and splits/conversions (date, raw ratio field)."""
    out = {"div": [], "split_raw": []}
    f = Path(val_dir) / "em_div.csv"
    if f.is_file():
        df = pd.read_csv(f, dtype=str)
        x = df[df["code"] == sym]
        out["div"] = sorted({(r.ex_date, float(r.amount_per_share)) for r in x.itertuples()})
    g = Path(val_dir) / "em_split.csv"
    if g.is_file():
        df = pd.read_csv(g, dtype=str)
        x = df[df.iloc[:, 1] == sym]
        out["split_raw"] = [list(r) for r in x.itertuples(index=False)]
    return out


def match_events(a: list[tuple[str, float]], b: list[tuple[str, float]], sessions: list[str], date_tol: int = 3,
                 amt_tol: float = 5e-4) -> dict:
    """Pair events of source a (Sina) with source b (EM). exact = same date & |amount diff| <= amt_tol;
    offset = amounts agree and dates differ by <= date_tol sessions; unmatched lists the rest."""
    def pos(d):
        return bisect.bisect_left(sessions, d)
    used, exact, offset = set(), [], []
    for da, xa in a:
        best = None
        for j, (db, xb) in enumerate(b):
            if j in used or abs(xa - xb) > amt_tol:
                continue
            k = abs(pos(da) - pos(db))
            if k <= date_tol and (best is None or k < best[0]):
                best = (k, j)
        if best is not None:
            used.add(best[1])
            (exact if best[0] == 0 else offset).append((da, b[best[1]][0], xa, b[best[1]][1], best[0]))
    only_a = [e for e in a if not any(e[0] == x[0] and e[1] == x[2] for x in exact + offset)]
    only_b = [e for j, e in enumerate(b) if j not in used]
    return {"exact": exact, "offset": offset, "only_sina": only_a, "only_em": only_b}


# ------------------------------------------------------------------ independent reconstruction (A1 substitute)
def reconstruct_adjusted(sym: str, val_dir: Path = VDATA, log: list | None = None) -> pd.DataFrame:
    """Independent total-return series: Tencent UNADJUSTED bars + Eastmoney distribution/split records, adjusted with the
    baseline adjustment code (build_adjusted). Event dates without a Tencent bar are moved to the next bar (logged)."""
    bars = pd.read_csv(Path(val_dir) / f"tencent_unadj_{sym}.csv", dtype={"date": str}).rename(columns={"date": "Date"})
    bars = bars[["Date", "open", "high", "low", "close", "volume"]].sort_values("Date").reset_index(drop=True)
    dates = bars["Date"].tolist()
    ev = em_events(sym, val_dir)

    def snap(d):
        if d <= dates[0]:       # at/before the first bar (pre-listing or pre-sample): no effect on any return
            if log is not None:
                log.append(f"{sym}: EM event {d} is at/before the first Tencent bar {dates[0]}; ignored")
            return None
        i = bisect.bisect_left(dates, d)
        if i >= len(dates):
            return None
        if dates[i] != d and log is not None:
            log.append(f"{sym}: EM event date {d} has no Tencent bar; moved to {dates[i]}")
        return dates[i]
    div_inc: dict[str, float] = {}
    for d, x in ev["div"]:
        s = snap(d)
        if s:
            div_inc[s] = div_inc.get(s, 0.0) + x
    cum, u = [], 0.0
    pts = {}
    for d in sorted(div_inc):
        u += div_inc[d]
        pts[d] = [u, None]
    s_val, mult = 1.0, {}
    for r in ev["split_raw"]:
        d, ratio = r[3], float(r[5])
        s = snap(d)
        if s:
            mult[s] = mult.get(s, 1.0) * ratio
    allpts = sorted(set(pts) | set(mult))
    cum_div, share_pts, uu, ss = [], [], 0.0, 1.0
    for d in allpts:
        if d in div_inc:
            uu += div_inc[d]
        if d in mult:
            ss *= mult[d]
        cum_div.append((d, uu))
        share_pts.append((d, ss))
    return build_adjusted(bars, cum_div, share_pts)


# ------------------------------------------------------------------ Part A driver
def _series(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.Series(df[col].to_numpy(float), index=df["Date"].tolist())


def run_part_a(out_dir: Path, data_dir: Path = DATA_DIR, val_dir: Path = VDATA, log=print) -> dict:
    out_dir = Path(out_dir)
    fl = json.loads((Path(val_dir) / "fetch_log.json").read_text())
    status = fl["status"]
    em_hfq_ok = all(status.get(f"EM_HFQ:{s}") == "OK" for s in BASE_SYMBOLS)
    em_unadj_ok = all(status.get(f"EM_UNADJ:{s}") == "OK" for s in BASE_SYMBOLS)
    bars = load_bars(BASE_SYMBOLS, data_dir)
    notes: list[str] = []
    res = {"em_hfq_reachable": em_hfq_ok, "em_unadj_reachable": em_unadj_ok, "status": status}
    sess_by_sym = {s: bars[s]["Date"].tolist() for s in BASE_SYMBOLS}

    # ---- A3 events (needed by A1 for the 'explained by event' rule)
    a3_rows, a3_detail = [], {}
    for s in BASE_SYMBOLS:
        sev, eev = sina_events(s, data_dir), em_events(s, val_dir)
        sess = sess_by_sym[s]
        in_range = lambda lst: [e for e in lst if START <= e[0] <= END]
        md = match_events(in_range(sev["div"]), in_range(eev["div"]), sess)
        em_conv = [(r[3], float(r[5])) for r in eev["split_raw"]]
        mc = match_events([(d, round(m, 6)) for d, m in in_range(sev["conv"])], [(d, round(m, 6)) for d, m in in_range(em_conv)],
                          sess, amt_tol=5e-4)
        a3_detail[s] = {"div": md, "conv": mc, "sina_div_all": sev["div"], "sina_conv_all": sev["conv"], "em_div_all": eev["div"],
                        "em_conv_all": em_conv}
        for kind, m in (("distribution", md), ("conversion", mc)):
            a3_rows.append({"symbol": s, "kind": kind, "exact_matches": len(m["exact"]), "date_offset_matches": len(m["offset"]),
                            "only_in_sina": len(m["only_sina"]), "only_in_em": len(m["only_em"]),
                            "offset_detail": "; ".join(f"sina {a} vs em {b} ({k} sessions)" for a, b, _, _, k in m["offset"]),
                            "only_sina_detail": "; ".join(f"{d}:{x}" for d, x in m["only_sina"]),
                            "only_em_detail": "; ".join(f"{d}:{x}" for d, x in m["only_em"])})
    a3 = pd.DataFrame(a3_rows)
    a3.to_csv(out_dir / "a3_corporate_actions.csv", index=False)
    res["a3"], res["a3_detail"] = a3, a3_detail

    # ---- A1: price adjustment
    a1_rows, a1_detail = [], {}
    recon: dict[str, pd.DataFrame] = {}
    recon_log: list[str] = []
    for s in BASE_SYMBOLS:
        ours = _series(bars[s], "adj_close")
        events = [d for d, _ in sina_events(s, data_dir)["div"]] + [d for d, _ in sina_events(s, data_dir)["conv"]] + \
                 [d for d, _ in a3_detail[s]["em_div_all"]] + [d for d, _ in a3_detail[s]["em_conv_all"]]
        row = {"symbol": s}
        if em_hfq_ok:
            hf = pd.read_csv(Path(val_dir) / f"em_hfq_{s}.csv")
            ref = pd.Series(hf["收盘"].to_numpy(float), index=pd.to_datetime(hf["日期"]).dt.strftime("%Y-%m-%d").tolist())
            st = a1_diff(ours, ref, events)
            row["reference"] = "EM_HFQ"
        else:
            rb = reconstruct_adjusted(s, val_dir, recon_log)
            recon[s] = rb
            ref = _series(rb, "adj_close")
            st = a1_diff(ours, ref, events)
            row["reference"] = "SUBSTITUTE: Tencent unadjusted + Eastmoney distribution/split records, baseline adjustment code"
        row.update({k: v for k, v in st.items() if k != "unexplained"})
        row["pass"] = a1_pass(st)
        row["unexplained_detail"] = "; ".join(f"{u['date']}:{u['diff_bp']:+.0f}bp" for u in st["unexplained"][:10])
        a1_rows.append(row)
        a1_detail[s] = st
    a1 = pd.DataFrame(a1_rows)
    a1.to_csv(out_dir / "a1_price_adjustment.csv", index=False)
    res["a1"], res["a1_detail"], res["recon_log"], res["recon"] = a1, a1_detail, recon_log, recon

    # raw-price diagnostic: Sina unadjusted close vs Tencent unadjusted close
    diag = []
    for s in BASE_SYMBOLS:
        t = pd.read_csv(Path(val_dir) / f"tencent_unadj_{s}.csv", dtype={"date": str})
        tc = pd.Series(t["close"].to_numpy(float), index=t["date"].tolist())
        sc = _series(bars[s], "close")
        com = sorted(d for d in sc.index.intersection(tc.index) if START <= d <= END)
        rel = np.abs(sc.loc[com].to_numpy() / tc.loc[com].to_numpy() - 1.0)
        miss_s = [d for d in tc.index if START <= d <= END and d not in sc.index]
        miss_t = [d for d in sc.index if START <= d <= END and d not in tc.index]
        diag.append({"symbol": s, "common_days": len(com), "raw_close_mismatch_days(>0.1%)": int((rel > 1e-3).sum()),
                     "max_rel_raw_diff": float(rel.max()), "days_in_tencent_not_sina": len(miss_s),
                     "days_in_sina_not_tencent": len(miss_t)})
    pd.DataFrame(diag).to_csv(out_dir / "a1_raw_price_agreement.csv", index=False)
    res["raw_diag"] = pd.DataFrame(diag)

    # ---- A2: NAV tracking
    a2_rows = []
    for s in BASE_SYMBOLS:
        nv = pd.read_csv(Path(val_dir) / f"em_nav_{s}.csv")
        idx = nav_total_return_index(nv)
        ours = _series(bars[s], "adj_close")
        st = a1_diff(ours, idx, [], START, END)
        unit = pd.Series(pd.to_numeric(nv["单位净值"]).to_numpy(float), index=pd.to_datetime(nv["净值日期"]).dt.strftime("%Y-%m-%d").tolist())
        st_unit = a1_diff(ours, unit, [], START, END)
        # drops of NAV on distribution days vs growth rate: does 日增长率 include distributions?
        a2_rows.append({"symbol": s, "n_days": st["n_days"], "ann_ours": st["ann_ours"], "ann_nav_tr": st["ann_ref"],
                        "ann_diff_pp": st["ann_diff_pp"], "corr_daily": st["corr"], "mean_abs_diff_bp": st["mean_abs_diff_bp"],
                        "max_abs_diff_bp": st["max_abs_diff_bp"], "max_abs_diff_date": st["max_abs_diff_date"],
                        "ann_unit_nav_price_only": st_unit["ann_ref"], "pass": abs(st["ann_diff_pp"]) <= 0.50})
    a2 = pd.DataFrame(a2_rows)
    a2.to_csv(out_dir / "a2_nav_tracking.csv", index=False)
    res["a2"] = a2

    # ---- A4: suspensions
    a4_rows = []
    targets = [("510500", "2015-04-13"), ("510500", "2015-04-14"), ("159915", "2021-02-08")]
    for s, d in targets:
        t = pd.read_csv(Path(val_dir) / f"tencent_unadj_{s}.csv", dtype={"date": str})
        raw = pd.read_csv(Path(data_dir) / f"{s}_sina_raw.csv", dtype={"Date": str})
        nv = pd.read_csv(Path(val_dir) / f"em_nav_{s}.csv")
        nvd = nv[nv["净值日期"] == d]
        a4_rows.append({"symbol": s, "session": d,
                        "EM_unadjusted_bar": "NOT RUN (EM bar endpoint unreachable)" if not em_unadj_ok else "see em_unadj file",
                        "tencent_unadjusted_bar_exists": bool((t["date"] == d).any()),
                        "sina_bar_exists": bool((raw["Date"] == d).any()),
                        "EM_NAV_row_exists": bool(len(nvd)),
                        "EM_NAV_unit": float(nvd["单位净值"].iloc[0]) if len(nvd) else None,
                        "EM_NAV_status": f"{nvd['申购状态'].iloc[0]}/{nvd['赎回状态'].iloc[0]}" if len(nvd) else None})
    a4 = pd.DataFrame(a4_rows)
    a4.to_csv(out_dir / "a4_suspensions.csv", index=False)
    res["a4"] = a4

    # ---- verdicts
    a1_runnable_pass = bool(a1["pass"].all())
    res["verdicts"] = {
        "A1": ("NOT RUN as written (EM_HFQ unreachable); substitute " + ("PASS" if a1_runnable_pass else "FAIL")) if not em_hfq_ok
              else ("PASS" if a1_runnable_pass else "FAIL"),
        "A2": "PASS" if bool(a2["pass"].all()) else "FAIL",
        "A3": "REPORTED (no pass threshold in protocol)",
        "A4": ("EM part NOT RUN (unreachable); " if not em_unadj_ok else "") + "independent source: Tencent",
    }
    res["a_failed"] = (not a1_runnable_pass) or (not bool(a2["pass"].all()))
    write_data_check_md(out_dir, res)
    return res


def _f(x, d=3):
    return "n/a" if x is None or (isinstance(x, float) and x != x) else f"{x:.{d}f}"


def write_data_check_md(out_dir: Path, res: dict) -> None:
    L = ["# Part A: data verification (protocol_cn_validation.json)", ""]
    st = res["status"]
    L += ["## Source reachability (data/fetch_log.json)", "", "| source | status |", "|---|---|"]
    for k, v in st.items():
        L.append(f"| {k} | {v} |")
    L += ["", f"EM_HFQ reachable: **{res['em_hfq_reachable']}**; EM unadjusted reachable: **{res['em_unadj_reachable']}**. "
          "Per protocol `if_A_unreachable`, checks that need an unreachable source are reported NOT RUN; invented data is "
          "never substituted. Where a closest-faithful check from a reachable independent source exists it is shown and "
          "labelled as a substitute.", ""]
    L += ["## A1: price adjustment " + ("(EM_HFQ)" if res["em_hfq_reachable"] else "(SUBSTITUTE reference; A1 as written NOT RUN)"), "",
          "Reference (when EM_HFQ is unreachable): Tencent unadjusted bars + Eastmoney distribution/split records, adjusted by "
          "the baseline adjustment code. Differences therefore test the Sina price source and the Sina hfq.js distribution/"
          "conversion records against two independent sources (Tencent, Eastmoney). Annualisation: compound return over "
          "consecutive common sessions / calendar years (365.25 d). Day is 'explained' if a Sina or EM event date lies within "
          "3 common sessions.", "",
          "| symbol | days | mean abs diff (bp) | max abs diff (bp) @ date | days >5bp | days >50bp (unexplained) | ann ours | ann ref | diff (pp) | PASS |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for _, r in res["a1"].iterrows():
        L.append(f"| {r['symbol']} | {r['n_days']} | {_f(r['mean_abs_diff_bp'], 2)} | {_f(r['max_abs_diff_bp'], 1)} @ {r['max_abs_diff_date']} | "
                 f"{r['n_days_gt_5bp']} | {r['n_days_gt_50bp']} ({r['n_days_gt_50bp_unexplained']}) | {r['ann_ours'] * 100:.3f}% | "
                 f"{r['ann_ref'] * 100:.3f}% | {r['ann_diff_pp']:+.3f} | {'PASS' if r['pass'] else 'FAIL'} |")
    for r in res["a1"].itertuples():
        if r.unexplained_detail:
            L.append(f"\nUnexplained >50 bp days {r.symbol}: {r.unexplained_detail}")
    if res["recon_log"]:
        L += ["", "Reconstruction notes:"] + [f"- {x}" for x in res["recon_log"]]
    L += ["", "Raw (unadjusted) close agreement, Sina vs Tencent:", "", "| symbol | common days | days differing >0.1% | max rel diff | days in Tencent not Sina | days in Sina not Tencent |",
          "|---|---|---|---|---|---|"]
    for r in res["raw_diag"].itertuples(index=False):
        v = list(r)
        L.append(f"| {v[0]} | {v[1]} | {v[2]} | {v[3]:.5f} | {v[4]} | {v[5]} |")
    L += ["", "## A2: NAV tracking (EM_NAV daily growth, 2-decimal percent)", "",
          "| symbol | days | ann ours | ann NAV TR | diff (pp) | corr daily | max abs diff (bp) @ date | PASS (<=0.50pp) |",
          "|---|---|---|---|---|---|---|---|"]
    for _, r in res["a2"].iterrows():
        L.append(f"| {r['symbol']} | {r['n_days']} | {r['ann_ours'] * 100:.3f}% | {r['ann_nav_tr'] * 100:.3f}% | {r['ann_diff_pp']:+.3f} | "
                 f"{r['corr_daily']:.4f} | {r['max_abs_diff_bp']:.0f} @ {r['max_abs_diff_date']} | {'PASS' if r['pass'] else 'FAIL'} |")
    L += ["", "## A3: corporate actions, Sina hfq.js (baseline) vs Eastmoney records (within 2013-07-29..2026-09-30)", "",
          "EM_DIV = fund.eastmoney.com Data/funddataIndex_Interface.aspx dt=8 (same endpoint as `ak.fund_fh_em`); EM_SPLIT = dt=9 "
          "(`ak.fund_cf_em`). Match = amount equal (|diff|<=5e-4), date offset reported in sessions (<=3 allowed).", "",
          "| symbol | kind | exact | date-offset | only in Sina | only in EM |", "|---|---|---|---|---|---|"]
    for r in res["a3"].itertuples():
        L.append(f"| {r.symbol} | {r.kind} | {r.exact_matches} | {r.date_offset_matches} | {r.only_in_sina} | {r.only_in_em} |")
    for r in res["a3"].itertuples():
        if r.offset_detail or r.only_sina_detail or r.only_em_detail:
            L.append(f"\n{r.symbol} {r.kind}: " + " | ".join(x for x in [
                f"offsets: {r.offset_detail}" if r.offset_detail else "", f"only Sina: {r.only_sina_detail}" if r.only_sina_detail else "",
                f"only EM: {r.only_em_detail}" if r.only_em_detail else ""] if x))
    L += ["", "## A4: suspensions", "", res["a4"].to_markdown(index=False), ""]
    L += ["## Verdicts", ""] + [f"- {k}: {v}" for k, v in res["verdicts"].items()]
    L += ["", f"Data-corrected rerun triggered by Part A: **{res['a_failed']}**"]
    (Path(out_dir) / "data_check.md").write_text("\n".join(L) + "\n")
