"""Builds the CN backtest world (calendar, bars, signals, cash curve) with the baseline runner's own steps, but with the
universe / groups / momentum / trend as parameters. All heavy lifting is imported from the baseline modules."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from research_v2.contracts import SignalFrame
from research_v2.signals import compute_matched_monthly_signals
from research_v3.signals_step3 import make_defensive_provider

from .. import runner as R
from ..calendar_cn import (CN_TZ, build_monthly_matched_schedule_cal, build_weekly_schedule_cal, get_xshg_calendar,
                           market_as_of_after_close_cal)
from ..data import DATA_DIR, load_bars, load_shibor
from ..protocol import load_amendment_verified, load_verified
from ..signals_cn import ShiborCurve, cap_def_frame, compute_model_signals_cn
from .fetch import VDATA, verify_manifest as verify_val_manifest

BASE_UNIVERSE = ("510300", "510050", "510500", "159915", "518880", "511010")


def load_bars_any(universe, base_dir: Path = DATA_DIR, val_dir: Path = VDATA) -> dict[str, pd.DataFrame]:
    """Baseline symbols come from the hash-verified baseline snapshot, B3 substitutes from the validation snapshot."""
    base_syms = [s for s in universe if (Path(base_dir) / f"{s}.csv").is_file()]
    out = dict(load_bars(base_syms, base_dir)) if base_syms else {}
    other = [s for s in universe if s not in out]
    if other:
        verify_val_manifest(val_dir)
        for s in other:
            out[s] = pd.read_csv(Path(val_dir) / f"{s}.csv", dtype={"Date": str})
    return {s: out[s] for s in universe}


@dataclass
class World:
    universe: tuple
    groups: dict
    cal: object
    bars: dict
    raw_bars: dict
    sessions: list
    curve: ShiborCurve
    weekly: list
    monthly: list
    data_start: str
    score_end: str
    def_symbol: str
    defensive: object
    report: dict
    resolution: list
    top_n: int = 3
    _frames: dict = field(default_factory=dict)
    _wfs: dict = field(default_factory=dict)

    def frames(self, momentum_months: int, trend_weeks: int):
        key = (momentum_months, trend_weeks)
        if key not in self._frames:
            wf = compute_model_signals_cn(self.bars, self.universe, self.sessions, self.weekly, "W_VAL",
                                          momentum_months=momentum_months, trend_weeks=trend_weeks)
            mf = compute_matched_monthly_signals(wf, self.monthly, "M_VAL")
            capdef = cap_def_frame(mf, "CAP_DEF_M", universe=self.universe, cap_groups=self.groups,
                                   defensive=self.defensive, def_symbol=self.def_symbol, top_n=self.top_n)
            self._frames[key] = (mf, capdef)
            self._wfs[key] = wf
        return self._frames[key]

    def weekly_frame(self, momentum_months: int, trend_weeks: int) -> SignalFrame:
        self.frames(momentum_months, trend_weeks)
        return self._wfs[(momentum_months, trend_weeks)]

    def first_warm_start(self, momentum_months: int = 12, trend_weeks: int = 40) -> str | None:
        mf, _ = self.frames(momentum_months, trend_weeks)
        i0 = R.first_fully_warm_row(mf, self.universe)
        return None if i0 is None else mf.rows[i0]["execution_session"]


def build_world(universe=BASE_UNIVERSE, groups=None, bars_override=None, data_dir: Path = DATA_DIR,
                val_dir: Path = VDATA) -> World:
    proto = load_verified()
    load_amendment_verified()
    mp = proto["mapping_from_us_to_cn"]
    if groups is None:
        groups = R.protocol_groups(proto)
    universe = tuple(universe)
    def_symbol = mp["DEFENSIVE_BOND"].split()[0]
    cal = get_xshg_calendar()
    raw_bars = bars_override if bars_override is not None else load_bars_any(universe, data_dir, val_dir)
    data_start = max(df["Date"].iloc[0] for df in raw_bars.values())
    data_last = min(df["Date"].iloc[-1] for df in raw_bars.values())
    score_end = R.choose_score_end(cal, data_last)
    bars = {s: df[(df["Date"] >= data_start) & (df["Date"] <= score_end)].reset_index(drop=True)
            for s, df in raw_bars.items()}
    rep = R.data_checks(bars, cal, data_start, score_end)
    if rep["errors"]:
        raise R.CNRunAbort(f"data validation errors: {rep['errors']}")
    sessions = [x.strftime("%Y-%m-%d") for x in cal.sessions_in_range(data_start, score_end)]
    curve = ShiborCurve(load_shibor(data_dir))
    vp = Path(data_dir) / "tencent_verification_raw.csv"
    ver = pd.read_csv(vp, dtype={"symbol": str}) if vp.is_file() else None
    bars_res, resolution = R.resolve_missing(bars, rep["missing_in_data_range"], ver)
    as_of = market_as_of_after_close_cal(cal, score_end, CN_TZ)
    weekly = build_weekly_schedule_cal(cal, sessions, as_of)
    monthly = build_monthly_matched_schedule_cal(cal, sessions, as_of, weekly)
    defensive = make_defensive_provider(bars_res, sessions, curve, symbol=def_symbol, months=3)
    return World(universe=universe, groups=groups, cal=cal, bars=bars_res, raw_bars=bars, sessions=sessions,
                 curve=curve, weekly=weekly, monthly=monthly, data_start=data_start, score_end=score_end,
                 def_symbol=def_symbol, defensive=defensive, report=rep, resolution=resolution,
                 top_n=proto["rule_unchanged"]["top_n"])


def benchmark_frames(world: World, mf: SignalFrame, score_start: str, csi_symbol: str, bond_symbol: str):
    """CSI300_BH / CN_60_40 on the same monthly execution sessions as runner.strategy_frames, with symbols as arguments."""
    sched = [r for r in mf.rows if r["status"] == "COMPLETE" and score_start <= r["execution_session"] <= world.score_end]
    u = world.universe
    return {"CSI300_BH": R._mk("CSI300_BH", sched[:1], lambda r: {csi_symbol: 1.0}, u),
            "CN_60_40": R._mk("CN_60_40", sched, lambda r: {csi_symbol: 0.6, bond_symbol: 0.4}, u)}
