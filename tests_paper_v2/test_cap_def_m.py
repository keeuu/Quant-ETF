"""CAP_DEF_M (frozen research_v3 candidate) in the paper_v2 shadow: parity, provenance, IRX accrual, costs."""
import hashlib
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from paper_v2 import candidate, config, irx
from paper_v2.asof import normalize_as_of
from paper_v2.candidate import cap_def_m_signal_row, candidates_frozen_sha256, provenance
from paper_v2.config import ALL_MODELS, CANDIDATE_LABEL, MODELS
from paper_v2.journal import AppendOnlyJournal
from paper_v2.phases import _research_bars, run_execution_phase, run_signal_phase
from paper_v2.state import PaperState
from research_v2.calendar import build_session_table, market_as_of_after_close
from research_v2.schedule import build_monthly_matched_schedule, build_weekly_schedule
from research_v3.cash_yield import DATA_DIR, CashYieldCurve, load_irx
from research_v3.protocol_step3 import load_verified
from research_v3.step3 import variant_frames
from tests_paper_v2.conftest import REAL_DOWNLOAD_FORWARD, fake_forward_irx
from tests_paper_v2.test_real_paths import synthetic_bars

ROOT = Path(__file__).resolve().parents[1]
FROZEN = json.loads((ROOT / "research_v3" / "candidates_frozen.json").read_text())
FREEZE = FROZEN["freeze_date"]


@pytest.fixture(autouse=True)
def clean_state():
    shutil.rmtree(config.PAPER_V2_STATE_DIR, ignore_errors=True)
    config.setup_dirs()
    yield
    shutil.rmtree(config.PAPER_V2_STATE_DIR, ignore_errors=True)


def _dir_hashes(d):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(d).iterdir())}


def _research_cap_def_m(bars, sessions, end):
    asof = market_as_of_after_close(end)
    weekly = build_weekly_schedule(sessions, asof)
    monthly = build_monthly_matched_schedule(sessions, asof, weekly)
    proto = load_verified()
    curve = CashYieldCurve(load_irx())
    for name, cad, frame in variant_frames(SimpleNamespace(bars=bars), sessions, weekly, monthly, 40, proto, curve):
        if (name, cad) == ("CAP_DEF", "M"):
            return frame, curve
    raise AssertionError("CAP_DEF/M not produced")


def _assert_same(paper_row, research_row):
    assert paper_row["signal_session"] == research_row["signal_session"]
    assert paper_row["execution_session"] == research_row["execution_session"]
    assert paper_row["weights"] == research_row["weights"]
    assert paper_row["selected_symbols"] == research_row["selected_symbols"]
    assert paper_row["def_ief_weight"] == research_row["def_ief_weight"]


# ---------- 1. parity with research_v3 step3 ----------

PARITY_DATES = ["2015-06-30", "2016-10-31", "2016-12-30", "2017-02-28", "2018-04-30",
                "2019-02-28", "2019-04-30", "2020-12-31"]


def test_parity_synthetic():
    bars = _research_bars(synthetic_bars())
    sessions = build_session_table(start="2005-01-03", end="2020-12-31")["session_date"].tolist()
    frame, curve = _research_cap_def_m(bars, sessions, "2020-12-31")
    by_sig = {r["signal_session"]: r for r in frame.rows}
    seen_ief = seen_cash = False
    for d in PARITY_DATES:
        _, ts = normalize_as_of(d)
        sess_d = [s for s in sessions if s <= d]
        row = cap_def_m_signal_row(bars, sess_d, ts, curve)
        _assert_same(row, by_sig[d])
        seen_ief |= row["def_ief_weight"] > 0
        seen_cash |= row["weights"]["CASH"] > 0
    assert seen_ief and seen_cash  # both defensive outcomes are exercised


def test_parity_through_signal_phase():
    """The journaled signal equals the research frame row (weights, selected symbols, IEF defensive weight)."""
    bars = synthetic_bars()
    rbars = _research_bars(bars)
    sessions = build_session_table(start="2005-01-03", end="2020-12-31")["session_date"].tolist()
    frame, _ = _research_cap_def_m(rbars, sessions, "2020-12-31")
    ref = {r["signal_session"]: r for r in frame.rows}["2019-02-28"]
    with patch("paper_v2.phases.fetch_market_data", return_value=(bars, "h")):
        run_signal_phase("2019-02-28")
    ev = [s for s in AppendOnlyJournal(config.SIGNAL_JOURNAL_PATH).read_all() if s["model_id"] == CANDIDATE_LABEL]
    assert len(ev) == 1
    assert ev[0]["weights"] == {k: v for k, v in ref["weights"].items() if k != "CASH"}
    assert ev[0]["cash_weight"] == ref["weights"]["CASH"]
    assert ev[0]["selected_symbols"] == ref["selected_symbols"]
    assert ev[0]["def_ief_weight"] == ref["def_ief_weight"] > 0
    assert ev[0]["expected_execution_session"] == ref["execution_session"]


REAL_DIR = ROOT / "research_v2" / "data" / "snapshot_20260918"


@pytest.mark.skipif(not REAL_DIR.exists(), reason="research_v2 real-data snapshot absent")
def test_parity_real_data():
    from research_v3.step2 import _setup
    view, sessions, weekly, monthly = _setup("validation")
    curve = CashYieldCurve(load_irx())
    proto = load_verified()
    frame = next(f for n, c, f in variant_frames(view, sessions, weekly, monthly, 40, proto, curve)
                 if (n, c) == ("CAP_DEF", "M"))
    by_sig = {r["signal_session"]: r for r in frame.rows}
    for d in ["2017-03-31", "2018-12-31", "2020-03-31", "2020-12-31"]:
        _, ts = normalize_as_of(d)
        row = cap_def_m_signal_row(view.bars, [s for s in sessions if s <= d], ts, curve)
        _assert_same(row, by_sig[d])


# ---------- 2. provenance ----------

def test_provenance_eligibility_boundary():
    for d, expect in [("2026-10-18", False), ("2026-10-19", False), ("2026-10-20", True), ("2027-01-04", True)]:
        p = provenance(d, "irxhash", "c" * 40)
        assert p["evaluation_eligible"] is expect
        assert p["freeze_date"] == "2026-10-19"
        assert p["candidates_frozen_sha256"] == hashlib.sha256((ROOT / "research_v3/candidates_frozen.json").read_bytes()).hexdigest()
        assert p["candidate_code_commit"] == FROZEN["code_git_commit"]
        assert p["strategy_commit"] == "c" * 40 and p["irx_data_hash"] == "irxhash"
        assert (p["cost_bps"], p["slippage_bps"]) == (5.0, 5.0)


PROV_KEYS = ["candidates_frozen_sha256", "candidate_code_commit", "strategy_commit", "freeze_date",
             "evaluation_eligible", "irx_data_hash"]


def _run_forward(bars, signal_date, execute_dates):
    with patch("paper_v2.phases.fetch_market_data", return_value=(bars, "h")):
        run_signal_phase(signal_date)
        for d in execute_dates:
            run_execution_phase(d)


@pytest.fixture(scope="module")
def forward_bars():
    return synthetic_bars("2004-06-01", "2026-11-13")


def test_pre_freeze_entries_flagged_false():
    with patch("paper_v2.phases.fetch_market_data", return_value=(synthetic_bars(), "h")):
        run_signal_phase("2020-12-31")
        run_execution_phase("2020-12-31")
    sigs = [s for s in AppendOnlyJournal(config.SIGNAL_JOURNAL_PATH).read_all() if s["model_id"] == CANDIDATE_LABEL]
    assert len(sigs) == 1 and sigs[0]["evaluation_eligible"] is False
    nav = [n for n in AppendOnlyJournal(config.NAV_JOURNAL_PATH).read_all() if n["model_id"] == CANDIDATE_LABEL]
    assert nav and all(n["evaluation_eligible"] is False for n in nav)
    # existing models' entries carry no candidate provenance
    others = [s for s in AppendOnlyJournal(config.SIGNAL_JOURNAL_PATH).read_all() if s["model_id"] != CANDIDATE_LABEL]
    assert others and all("evaluation_eligible" not in s for s in others)


def test_forward_provenance_in_all_entries(forward_bars):
    _run_forward(forward_bars, "2026-10-30", ["2026-11-03"])
    sig = [s for s in AppendOnlyJournal(config.SIGNAL_JOURNAL_PATH).read_all() if s["model_id"] == CANDIDATE_LABEL]
    execs = [e for e in AppendOnlyJournal(config.EXEC_JOURNAL_PATH).read_all() if e.get("model_id") == CANDIDATE_LABEL]
    navs = [n for n in AppendOnlyJournal(config.NAV_JOURNAL_PATH).read_all() if n["model_id"] == CANDIDATE_LABEL]
    kinds = {e.get("status") for e in execs}
    assert {"EXECUTED", "CASH_INTEREST"} <= kinds
    assert len(sig) == 1 and sig[0]["signal_session"] == "2026-10-30"
    expect_sha = candidates_frozen_sha256()
    for entry in sig + execs + navs:
        for k in PROV_KEYS:
            assert k in entry, (k, entry)
        assert entry["candidates_frozen_sha256"] == expect_sha
        assert entry["candidate_code_commit"] == FROZEN["code_git_commit"]
        assert entry["freeze_date"] == "2026-10-19"
        assert len(entry["strategy_commit"]) in (40, 7) or entry["strategy_commit"] == "UNKNOWN"
        assert len(entry["irx_data_hash"]) == 64
    assert all(e["evaluation_eligible"] for e in sig + execs + navs)  # all dates after 2026-10-19
    assert {n["kind"] for n in navs} == {"EXEC", "MARK"}


# ---------- 3. IRX accrual ----------

def _expected_cash(start, end):
    curve = CashYieldCurve(pd.concat([load_irx(), fake_forward_irx(end)]))
    sessions = build_session_table(start="2026-10-01", end=end)["session_date"].tolist()
    return curve, [s for s in sessions if s > start]


def test_accrual_matches_research_factors_and_is_idempotent(forward_bars):
    # Nothing to trade at the first signal (force an all-cash order) so only interest moves cash.
    with patch("paper_v2.phases.fetch_market_data", return_value=(forward_bars, "h")):
        run_signal_phase("2026-10-30")  # model starts 2026-10-30
        st = PaperState()
        st.get_model_state(CANDIDATE_LABEL)["pending_order"] = None
        st.save()
        run_execution_phase("2026-11-05")
        s1 = json.dumps(PaperState()._state, sort_keys=True)
        n1 = len(AppendOnlyJournal(config.EXEC_JOURNAL_PATH).read_all())
        run_execution_phase("2026-11-05")
        run_execution_phase("2026-11-05")
        assert json.dumps(PaperState()._state, sort_keys=True) == s1
        assert len(AppendOnlyJournal(config.EXEC_JOURNAL_PATH).read_all()) == n1
        run_execution_phase("2026-11-06")  # exactly one more session accrues
    curve, sess = _expected_cash("2026-10-30", "2026-11-06")
    cash, prev = 100000.0, "2026-10-30"
    for s in sess:
        cash *= curve.accrual_factor(prev, s)
        prev = s
    ms = PaperState().get_model_state(CANDIDATE_LABEL)
    assert ms["cash"] == pytest.approx(cash, rel=1e-12)
    assert ms["cash_interest_total"] == pytest.approx(cash - 100000.0, rel=1e-9)
    assert ms["last_accrual_session"] == "2026-11-06" == sess[-1]
    ci = [e for e in AppendOnlyJournal(config.EXEC_JOURNAL_PATH).read_all()
          if e.get("status") == "CASH_INTEREST" and e["model_id"] == CANDIDATE_LABEL]
    assert [e["session"] for e in ci] == sess
    assert sum(e["amount"] for e in ci) == pytest.approx(cash - 100000.0, rel=1e-9)


def test_accrual_interleaved_with_execution_and_costs(forward_bars):
    """Exec on 2026-11-02: interest for 10-30 -> 11-02 (3 days) is credited BEFORE the rebalance."""
    _run_forward(forward_bars, "2026-10-30", ["2026-11-03"])
    execs = AppendOnlyJournal(config.EXEC_JOURNAL_PATH).read_all()
    sigs = AppendOnlyJournal(config.SIGNAL_JOURNAL_PATH).read_all()
    mine = [e for e in execs if e.get("model_id") == CANDIDATE_LABEL and e["status"] == "EXECUTED"]
    assert len(mine) == 1 and mine[0]["execution_session"] == "2026-11-02"
    curve = CashYieldCurve(pd.concat([load_irx(), fake_forward_irx("2026-11-03")]))
    expect_cash_before = 100000.0 * curve.accrual_factor("2026-10-30", "2026-11-02")
    assert mine[0]["cash_before"] == pytest.approx(expect_cash_before, rel=1e-12)

    # 4. costs: candidate 10 bps effective on traded notional, existing models 5 bps
    def ratio(ev):
        px = ev["prices"]
        traded = sum(abs(ev["shares_after"].get(s, 0.0) - ev["shares_before"].get(s, 0.0)) * px[s] for s in px)
        return ev["fees"] / traded

    assert ratio(mine[0]) == pytest.approx(0.0010, rel=1e-6)
    assert mine[0]["effective_cost_bps"] == 10.0 and mine[0]["cost_bps"] == 5.0 and mine[0]["slippage_bps"] == 5.0
    fb = mine[0]["fee_breakdown"]
    assert fb["cost"] == pytest.approx(fb["slippage"]) and fb["cost"] + fb["slippage"] == pytest.approx(mine[0]["fees"])
    hash_to_model = {s["signal_hash"]: s["model_id"] for s in sigs}
    existing = [e for e in execs if e.get("status") == "EXECUTED" and "model_id" not in e]
    assert existing
    for ev in existing:
        assert hash_to_model.get(ev["signal_hash"]) in {m.model_id for m in MODELS}
        assert ratio(ev) == pytest.approx(0.0005, rel=1e-6)
    ms = PaperState().get_model_state(CANDIDATE_LABEL)
    assert ms["fees"] == pytest.approx(mine[0]["fees"])
    # NAV mark at the last session close
    navs = [n for n in AppendOnlyJournal(config.NAV_JOURNAL_PATH).read_all()
            if n["model_id"] == CANDIDATE_LABEL and n["kind"] == "MARK"]
    assert [n["date"] for n in navs] == ["2026-11-03"]
    assert navs[0]["nav"] == pytest.approx(ms["cash"] + sum(
        q * float(forward_bars[s].loc[forward_bars[s]["Date"] == "2026-11-03", "close"].iloc[0])
        for s, q in ms["shares"].items()))
    assert ms["nav"] == pytest.approx(navs[0]["nav"])


def test_forward_irx_fetch_mocked_persisted_and_snapshot_untouched(monkeypatch):
    before = _dir_hashes(DATA_DIR)
    idx = pd.DatetimeIndex(pd.bdate_range("2020-12-29", "2021-01-12"), name="Date")
    raw = pd.DataFrame({"Close": [0.1 + 0.01 * i for i in range(len(idx))],
                        "Open": 0.0}, index=idx)
    raw.columns = pd.MultiIndex.from_product([raw.columns, ["^IRX"]])
    calls = []
    monkeypatch.setattr(irx, "_download_forward", REAL_DOWNLOAD_FORWARD)  # real fn; only yf.download is mocked
    with patch.object(irx.yf, "download", side_effect=lambda *a, **k: calls.append((a, k)) or raw):
        curve, h = irx.load_irx_curve("2021-01-08")
    assert calls[0][0] == ("^IRX",) and calls[0][1]["start"] == "2021-01-01" and calls[0][1]["auto_adjust"] is False
    base = load_irx()
    # snapshot wins on overlap (2020-12-29..31 not taken from the download); dates > as_of dropped
    assert curve.pct_as_of("2020-12-31") == base.loc["2020-12-31"]
    assert curve.pct_as_of("2021-01-12") == curve.pct_as_of("2021-01-08")
    assert curve.pct_as_of("2021-01-04") == pytest.approx(raw[("Close", "^IRX")].loc["2021-01-04"])
    files = os.listdir(config.DATA_SNAPSHOTS_DIR)
    assert any(f.startswith("irx_forward_") and f.endswith(".csv") for f in files)
    assert any(f.startswith("irx_forward_") and f.endswith(".json") for f in files)
    assert len(h) == 64
    assert _dir_hashes(DATA_DIR) == before  # research_v3 snapshot untouched
    # fail closed: empty download in the forward period
    with patch.object(irx.yf, "download", return_value=pd.DataFrame()):
        with pytest.raises(irx.IrxError):
            irx.load_irx_curve("2021-01-08")


def test_forward_irx_offline_file(tmp_path):
    pd.DataFrame({"Date": ["2021-01-04", "2021-01-05"], "irx_pct": [0.09, 0.10]}).to_csv(tmp_path / "IRX.csv", index=False)
    curve, _ = irx.load_irx_curve("2021-01-05", bars_dir=str(tmp_path))  # no download: autouse stub would also work
    assert curve.pct_as_of("2021-01-05") == 0.10
    # no network: the download path must not be used when IRX.csv exists
    with patch.object(irx, "_download_forward", side_effect=AssertionError("network")):
        irx.load_irx_curve("2021-01-05", bars_dir=str(tmp_path))


def test_irx_not_needed_before_2021():
    with patch.object(irx, "_download_forward", side_effect=AssertionError("network")):
        curve, h = irx.load_irx_curve("2020-12-31")
    assert curve.pct_as_of("2020-12-31") == load_irx().iloc[-1]


# ---------- 5. state migration / config hash ----------

def test_old_state_without_candidate_gets_it():
    old = PaperState()
    raw = json.loads(json.dumps(old._state))
    del raw["models"][CANDIDATE_LABEL]
    raw["models"]["W_M06_T36"]["cash"] = 4242.0
    Path(config.STATE_FILE_PATH).write_text(json.dumps(raw))
    st = PaperState()
    assert st.get_model_state("W_M06_T36")["cash"] == 4242.0
    ms = st.get_model_state(CANDIDATE_LABEL)
    assert ms["cash"] == config.INITIAL_CASH and ms["shares"] == {} and ms["pending_order"] is None
    assert ms["last_accrual_session"] is None and ms["cash_interest_total"] == 0.0
    assert len(st._state["models"]) == len(ALL_MODELS) == len(MODELS) + 1
    # candidate entry that lacks new fields gets them
    del raw["models"]["W_M06_T36"]
    raw["models"][CANDIDATE_LABEL] = {"cash": 1.0, "shares": {}}
    Path(config.STATE_FILE_PATH).write_text(json.dumps(raw))
    ms = PaperState().get_model_state(CANDIDATE_LABEL)
    assert ms["cash"] == 1.0 and ms["cash_interest_total"] == 0.0 and "dividends_credited" in ms
    assert "W_M06_T36" in PaperState()._state["models"]


def test_config_hash_includes_candidate():
    d = config.config_dict()
    assert [m["model_id"] for m in d["models"]] == [m.model_id for m in MODELS]
    assert d["candidates"][0]["model_id"] == CANDIDATE_LABEL
    assert d["candidates"][0]["frozen_sha256"] == candidates_frozen_sha256()


def test_candidate_constants_match_frozen_json():
    c = candidate.candidate_spec()
    assert c["label"] == CANDIDATE_LABEL and c["cadence"] == "M" and c["trend_weeks"] == 40
