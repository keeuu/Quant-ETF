"""Static configuration for the dashboard. Strategy parameters are read from the frozen files, not hard-coded."""
from __future__ import annotations

import json
import zoneinfo
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DASH = REPO / "dashboard"
DATA_ROOT = DASH / "data"
OUTPUT_DIR = DASH / "output"
LOG_PATH = REPO / "logs" / "dashboard.log"
LAST_SUCCESS_PATH = DATA_ROOT / "last_success.json"

SHANGHAI = zoneinfo.ZoneInfo("Asia/Shanghai")
NEW_YORK = zoneinfo.ZoneInfo("America/New_York")

INITIAL_NAV = 1_000_000.0
HISTORY_DAYS = 1820            # THS accepts at most 5 years per request
SESSION_MARGIN_MIN = 15        # a session counts as finished this long after its official close

US_NAMES = {"SPY": "标普500", "QQQ": "纳指100", "IWM": "罗素2000", "EFA": "发达市场(除美)", "EEM": "新兴市场",
            "GLD": "黄金", "TLT": "20年以上美债", "IEF": "7-10年美债"}
CN_NAMES = {"510300": "沪深300ETF", "510050": "上证50ETF", "510500": "中证500ETF", "159915": "创业板ETF",
            "518880": "黄金ETF", "511010": "国债ETF"}


def frozen_spec() -> dict:
    """CAP_DEF_M entry of research_v3/candidates_frozen.json, cross-checked by paper_v2.candidate."""
    from paper_v2.candidate import candidate_spec, load_frozen
    c = candidate_spec("CAP_DEF_M")
    return {"freeze_date": load_frozen()["freeze_date"], **c}


@dataclass(frozen=True)
class MarketSpec:
    key: str                       # "US" / "CN"
    title: str
    universe: tuple
    groups: dict
    def_symbol: str
    names: dict
    bench_hold: str                # buy-and-hold benchmark symbol
    bench_hold_label: str
    bench_mix: tuple               # (equity symbol, bond symbol) for 60/40
    bench_mix_label: str
    cash_label: str
    tz: zoneinfo.ZoneInfo
    open_hm: tuple
    close_hm: tuple
    suspension_rules: bool
    momentum_months: int = 12
    trend_weeks: int = 40
    top_n: int = 3
    defensive_months: int = 3


def us_spec() -> MarketSpec:
    f = frozen_spec()
    return MarketSpec(
        key="US", title="美股", universe=tuple(f["universe"]), groups=f["asset_class_groups"],
        def_symbol=f["defensive_test"]["symbol"], names=US_NAMES, bench_hold="SPY", bench_hold_label="SPY 买入持有",
        bench_mix=("SPY", "IEF"), bench_mix_label="60/40 SPY/IEF 月度再平衡", cash_label="现金(^IRX 美国13周国库券)",
        tz=NEW_YORK, open_hm=(9, 30), close_hm=(16, 0), suspension_rules=False,
        momentum_months=f["base_momentum_months"], trend_weeks=f["trend_weeks"], top_n=f["top_n"],
        defensive_months=f["defensive_test"]["months"])


def cn_spec() -> MarketSpec:
    from research_v3.cn_transfer.protocol import load_verified
    from research_v3.cn_transfer.runner import protocol_groups
    f = frozen_spec()
    proto = load_verified()
    mp = proto["mapping_from_us_to_cn"]
    rule = proto["rule_unchanged"]
    assert rule["momentum_months"] == f["base_momentum_months"] and rule["trend_weeks"] == f["trend_weeks"]
    return MarketSpec(
        key="CN", title="A股", universe=tuple(mp["universe_order"]), groups=protocol_groups(proto),
        def_symbol=mp["DEFENSIVE_BOND"].split()[0], names=CN_NAMES, bench_hold="510300",
        bench_hold_label="沪深300(510300) 买入持有", bench_mix=("510300", "511010"),
        bench_mix_label="60/40 510300/511010 月度再平衡", cash_label="现金(SHIBOR 3月)",
        tz=SHANGHAI, open_hm=(9, 30), close_hm=(15, 0), suspension_rules=True,
        momentum_months=rule["momentum_months"], trend_weeks=rule["trend_weeks"], top_n=rule["top_n"],
        defensive_months=3)
