"""Independent pandas recompute of the 10 double-low selections on random development signal dates.

Does not use the CBPanel / eligibility() code path: re-reads the raw Sina / Tencent / Eastmoney files per bond with plain
pandas and re-applies the protocol filters and the score. Shared inputs (declared): the stock calendar and the
underlying inferred-ST flags from the stock_mf panel, and the Eastmoney list fields."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import data as cbdata
from . import guard
from .engine import MAT_WIN, double_low, select_targets

SEED = 20261019


def independent_selection(date: str, sessions: list, meta: pd.DataFrame, st_flag: dict, data_dir=cbdata.DATA_DIR,
                          top_n: int = 10):
    """Returns DataFrame of eligible bonds with score (sorted ascending) for the signal `date`."""
    assert date <= guard.current_limit()
    s_idx = sessions.index(date)
    win = sessions[max(0, s_idx - 19): s_idx + 1]
    hz = sessions[s_idx + MAT_WIN] if s_idx + MAT_WIN < len(sessions) else (pd.Timestamp(sessions[-1]) + pd.offsets.BDay(MAT_WIN - (len(sessions) - 1 - s_idx))).strftime("%Y-%m-%d")
    out = []
    for r in meta.itertuples():
        ps = data_dir / "sina" / f"{r.code}.csv"
        if not ps.exists() or r.listing > date:
            continue
        s = pd.read_csv(ps, dtype={"date": str})
        s = s[s["date"].str[:10] <= date]
        if not len(s) or len(s) < 10:
            continue
        row = s[s["date"].str[:10] == date]
        if not len(row) or not row["volume"].iloc[0] > 0 or not row["close"].iloc[0] > 0:
            continue
        close = float(row["close"].iloc[0])
        if close > 150:
            continue
        t = pd.read_csv(data_dir / "tx" / f"{r.code}.csv", dtype={"date": str}) if (data_dir / "tx" / f"{r.code}.csv").exists() else pd.DataFrame(columns=["date", "amount"])
        t["date"] = t["date"].str[:10]
        s["d"] = s["date"].str[:10]
        amt = s.set_index("d")["close"] * s.set_index("d")["volume"]
        if len(t):
            ta = t.set_index("date")["amount"]
            amt = ta.reindex(amt.index).fillna(amt)
        first_bar = s["d"].iloc[0]
        denom = min(20, max(1, len([x for x in sessions if x >= first_bar and x <= date]) + max(0, len(s[s["d"] < sessions[0]]))))
        mean_amt = amt.reindex(win).fillna(0.0).sum() / denom
        if mean_amt < 5_000_000:
            continue
        if isinstance(r.notice, str) and r.notice <= date:
            continue
        if isinstance(r.maturity, str) and r.maturity <= hz:
            continue
        if st_flag.get((r.stock, date), False):
            continue
        v = pd.read_csv(data_dir / "va" / f"{r.code}.csv")
        v["d"] = v["DATE"].astype(str).str[:10]
        vr = v[v["d"] == date]
        if not len(vr):
            continue
        prem = vr["SWAPOR"].iloc[0]
        if not np.isfinite(prem):
            sv = vr["SWAPVALUE"].iloc[0]
            prem = (close / sv - 1.0) * 100.0 if np.isfinite(sv) and sv > 0 else np.nan
        if not np.isfinite(prem):
            continue
        out.append((r.code, close, prem / 100.0, close + 100.0 * prem / 100.0))
    df = pd.DataFrame(out, columns=["code", "close", "premium", "score"]).sort_values(["score", "code"]).reset_index(drop=True)
    return df


def run(ctx: dict, out_dir, n_dates: int = 3):
    p, E_full = ctx["p"], ctx["E_full"]
    rng = np.random.default_rng(SEED)
    cand = [s for s in ctx["sig"] if "2018-03-01" <= p.sessions[s] <= guard.DEV_END]
    pick = sorted(rng.choice(cand, n_dates, replace=False).tolist())
    stidx = {c: j for j, c in enumerate(p.codes)}
    meta = ctx["meta"]
    st_flag = {}
    for b, sc in enumerate(p.stocks):
        for s in pick:
            st_flag[(sc, p.sessions[s])] = bool(p.st[s, b])
    rows = []
    for s in pick:
        date = p.sessions[s]
        ind = independent_selection(date, p.sessions, meta, st_flag)
        sc = np.where(E_full[s], double_low(p.close, p.premium)[s], np.nan)
        keep, new, rank = select_targets(sc, E_full[s], np.zeros(len(p.codes), bool), p.codes)
        eng = [p.codes[b] for b in new]
        ind10 = ind["code"].head(10).tolist()
        rows.append(dict(date=date, engine_top10="|".join(eng), independent_top10="|".join(ind10),
                         identical_set=set(eng) == set(ind10), identical_order=eng == ind10,
                         n_eligible_engine=int(np.isfinite(sc).sum()), n_eligible_independent=len(ind),
                         max_abs_score_diff=float(np.max(np.abs(
                             [sc[stidx[c]] - ind.loc[ind["code"] == c, "score"].iloc[0] for c in eng if c in set(ind["code"])] or [np.nan])))))
    df = pd.DataFrame(rows)
    df.to_csv(out_dir / "independent_check.csv", index=False)
    return df
