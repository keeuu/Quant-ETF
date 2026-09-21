import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from src.broker.alpaca_client import AlpacaPaperClient, SecurityException
from src.execution.trading_calendar import (
    get_first_trading_day_of_month,
    is_exchange_trading_day,
    is_early_close,
    get_calendar_source
)
from src.execution.execution_guard import (
    check_execution_window,
    get_market_now,
    MARKET_TZ
)
from src.execution.rebalance_planner import generate_rebalance_plan
from src.execution.state_manager import (
    check_rebalance_idempotency,
    save_rebalance_state,
    RebalanceStatus
)
from src.data.fetcher import get_monthly_dataset
from src.signals.selector import select_top_assets
from src.signals.momentum import calculate_12_1_momentum
from src.signals.trend import check_trend_filter
from config.config import ORDER_SUBMISSION_ENABLED, MARKET_TIMEZONE

# ==============================================================================
# Phase 5.6.1: exchange_calendars XNYS 官方日历清理与验证用例
# ==============================================================================

def test_offline_calendar_source_is_xnys():
    """
    【Phase 5.6.1 测试 1】：
    离线模式下，交易日历数据源必须明确标记为 EXCHANGE_CALENDARS_XNYS。
    """
    assert get_calendar_source(client=None) == "EXCHANGE_CALENDARS_XNYS"


def test_good_friday_not_trading_day():
    """
    【Phase 5.6.1 测试 2】：
    使用 exchange_calendars XNYS 确认 2026-04-03 (Good Friday) 为非交易日休市。
    """
    assert is_exchange_trading_day("2026-04-03") is False
    assert is_exchange_trading_day("2026-04-02") is True
    assert is_exchange_trading_day("2026-04-06") is True


def test_exchange_calendar_not_federal_calendar():
    """
    【Phase 5.6.1 测试 3】：
    确保 schedule 使用 exchange_calendars XNYS，对比并证明不依赖 USFederalHolidayCalendar。
    - Good Friday: Federal 不是假日，但 XNYS 是休市 (False)
    - Columbus Day: Federal 是法定假日，但 XNYS 正常开市 (True)
    - Veterans Day: Federal 是法定假日，但 XNYS 正常开市 (True)
    """
    from pandas.tseries.holiday import USFederalHolidayCalendar

    fed_cal = USFederalHolidayCalendar()
    fed_holidays = fed_cal.holidays("2026-01-01", "2026-12-31")

    # 1. Good Friday
    gf = pd.Timestamp("2026-04-03")
    assert gf not in fed_holidays
    assert is_exchange_trading_day(gf) is False

    # 2. Columbus Day
    columbus = pd.Timestamp("2026-10-12")
    assert columbus in fed_holidays
    assert is_exchange_trading_day(columbus) is True

    # 3. Veterans Day
    veterans = pd.Timestamp("2026-11-11")
    assert veterans in fed_holidays
    assert is_exchange_trading_day(veterans) is True


def test_early_close_metadata():
    """
    【Phase 5.6.1 测试 4】：
    验证 exchange_calendars 正确处理提前收盘 (Early Close) 元数据。
    2026-11-27 (Day after Thanksgiving / 黑色星期五) 属于提前收盘日。
    2026-09-01 为普通正常交易日。
    """
    assert is_early_close("2026-11-27") is True
    assert is_early_close("2026-09-01") is False


def test_broker_calendar_priority_over_offline(monkeypatch):
    """
    【Phase 5.6.1 测试 5】：
    在真实 Alpaca Paper 模式下，Alpaca Broker Calendar 必须具有最高权威：
    Alpaca Broker Calendar > exchange_calendars。
    """
    client = AlpacaPaperClient(
        api_key="PKTEST12345678",
        secret_key="SECRET_12345678",
        base_url="https://paper-api.alpaca.markets",
        paper=True
    )
    assert get_calendar_source(client=client) == "ALPACA_BROKER_CALENDAR"

    # 模拟 Broker Calendar 判定某天为交易日
    mock_called = False
    def mock_get_calendar(start_date, end_date):
        nonlocal mock_called
        mock_called = True
        return [{"date": pd.Timestamp(start_date).date(), "open": None, "close": None}]

    monkeypatch.setattr(client, "get_calendar", mock_get_calendar)
    res = is_exchange_trading_day("2026-09-01", client=client)
    assert res is True
    assert mock_called is True


# ==============================================================================
# Phase 5.7: 真实 Alpaca Paper 只读校验、Broker Clock 与安全用例
# ==============================================================================

def test_market_timezone_is_new_york():
    """
    【Phase 5.7 测试 6】：
    统一美股市场时区为 America/New_York。
    """
    assert MARKET_TIMEZONE == "America/New_York"
    _, market_time, market_date, _ = get_market_now()
    assert str(market_time.tzinfo) == "America/New_York"
    assert market_date == market_time.date()


def test_local_date_cannot_override_market_date():
    """
    【Phase 5.7 测试 7】：
    构造亚洲本地时间已进入次日、但纽约市场日期尚未到达的场景，
    确保执行判断只依据 America/New_York 市场日期。
    """
    tz_asia = ZoneInfo("Asia/Shanghai")
    dt_asia = datetime(2026, 9, 1, 4, 0, 0, tzinfo=tz_asia) # 北京时间 9月1日凌晨4点

    status = check_execution_window(
        signal_month="2026-08",
        signal_date=pd.Timestamp("2026-08-31"),
        market_datetime=dt_asia
    )

    # 对应纽约时间依然是 2026-08-31 16:00:00 (前一日)
    assert status.market_date == pd.Timestamp("2026-08-31").date()
    assert status.execution_allowed is False
    assert "PRE_EXECUTION_WINDOW" in status.execution_block_reason


def test_offline_assets_marked_unverified():
    """
    【Phase 5.7 测试 8】：
    离线无凭证模式下，标的元数据不得标记为 VERIFIED，必须显式标为 OFFLINE_UNVERIFIED / STATIC_ASSUMPTION。
    """
    client = AlpacaPaperClient(api_key="", secret_key="", paper=True)
    meta = client.get_asset_metadata("SPY")

    assert meta["source"] in ["OFFLINE_UNVERIFIED", "STATIC_ASSUMPTION"]
    assert meta["source"] != "OFFLINE_VERIFIED"
    assert "API_VERIFIED" not in meta["source"]
    assert meta["verified_by_api"] is False
    assert "STATIC_ASSUMPTION" in meta["eligibility_display"]

    all_eligible, report = client.check_universe_eligibility(["SPY", "QQQ"])
    assert all_eligible is False
    assert report["SPY"]["verified_by_api"] is False


def test_real_asset_metadata_requires_api(monkeypatch):
    """
    【Phase 5.7 测试 9】：
    只有真实 Alpaca API 返回的有效数据才能被标记为 ALPACA_API_VERIFIED。
    """
    client = AlpacaPaperClient(
        api_key="PKTEST12345678",
        secret_key="SECRET_12345678",
        base_url="https://paper-api.alpaca.markets",
        paper=True
    )

    class MockAsset:
        def __init__(self, symbol, status="active", tradable=True, fractionable=True):
            self.symbol = symbol
            self.status = status
            self.tradable = tradable
            self.fractionable = fractionable

    monkeypatch.setattr(client._client, "get_asset", lambda sym: MockAsset(sym))

    meta = client.get_asset_metadata("SPY")
    assert meta["source"] == "ALPACA_API_VERIFIED"
    assert meta["verified_by_api"] is True
    assert meta["is_eligible"] is True
    assert meta["eligibility_display"] == "PASS (API_VERIFIED)"

    all_eligible, _ = client.check_universe_eligibility(["SPY", "QQQ"])
    assert all_eligible is True


def test_order_submission_kill_switch():
    """
    【Phase 5.7 测试 10】：
    当 ORDER_SUBMISSION_ENABLED = False 时，任何调用 submit_order 均被绝对硬性阻断。
    """
    assert ORDER_SUBMISSION_ENABLED is False

    client = AlpacaPaperClient(api_key="", secret_key="", paper=True)
    with pytest.raises(SecurityException, match="ORDER_SUBMISSION_ENABLED = False"):
        client.submit_order(symbol="SPY", qty=10, side="buy")


def test_alpaca_clock_used_when_available(monkeypatch):
    """
    【Phase 5.7 测试 11】：
    当在线 Paper Client 可用时，系统必须优先调用 Broker Clock API 获取官方市场时钟。
    """
    client = AlpacaPaperClient(
        api_key="PKTEST12345678",
        secret_key="SECRET_12345678",
        base_url="https://paper-api.alpaca.markets",
        paper=True
    )

    fake_broker_ts = datetime(2026, 9, 1, 9, 30, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(client, "get_clock", lambda: {
        "timestamp": fake_broker_ts,
        "is_open": True,
        "next_open": fake_broker_ts,
        "next_close": fake_broker_ts
    })

    _, market_time, market_date, clock_source = get_market_now(client=client)
    assert clock_source == "ALPACA_BROKER_CLOCK"
    # UTC 09:30:00 在 EDT 是 05:30:00，日期为 2026-09-01
    assert market_date == pd.Timestamp("2026-09-01").date()


def test_alpaca_calendar_used_when_available(monkeypatch):
    """
    【Phase 5.7 测试 12】：
    当在线 Paper Client 可用时，计算调仓首个交易日必须优先调用 Alpaca get_calendar。
    """
    client = AlpacaPaperClient(
        api_key="PKTEST12345678",
        secret_key="SECRET_12345678",
        base_url="https://paper-api.alpaca.markets",
        paper=True
    )

    mock_called = False
    def mock_get_cal(start_date, end_date):
        nonlocal mock_called
        mock_called = True
        return [{"date": pd.Timestamp("2026-10-01").date(), "open": None, "close": None}]

    monkeypatch.setattr(client, "get_calendar", mock_get_cal)

    first_day = get_first_trading_day_of_month(2026, 10, client=client)
    assert mock_called is True
    assert first_day.strftime("%Y-%m-%d") == "2026-10-01"


# ==============================================================================
# 其它基础安全与计划测试
# ==============================================================================

def test_execute_blocked_after_rebalance_date():
    status = check_execution_window(
        signal_month="2026-08",
        signal_date=pd.Timestamp("2026-08-31"),
        market_datetime=pd.Timestamp("2026-09-21", tz="America/New_York")
    )
    assert status.execution_allowed is False
    assert "MISSED_REBALANCE_WINDOW" in status.execution_block_reason


def test_execute_allowed_on_scheduled_date():
    status = check_execution_window(
        signal_month="2026-08",
        signal_date=pd.Timestamp("2026-08-31"),
        market_datetime=pd.Timestamp("2026-09-01 09:31:00", tz="America/New_York"),
        is_open=True
    )
    assert status.execution_allowed is True
    assert status.scheduled_execution_date.date() == pd.Timestamp("2026-09-01").date()


def test_weekend_month_start():
    first_bday = get_first_trading_day_of_month(2023, 1)
    assert first_bday.strftime("%Y-%m-%d") == "2023-01-03"

    first_bday_jun = get_first_trading_day_of_month(2024, 6)
    assert first_bday_jun.strftime("%Y-%m-%d") == "2024-06-03"


def test_no_credentials_uses_offline_mock():
    client = AlpacaPaperClient(api_key="", secret_key="", paper=True)
    assert client.has_credentials() is False
    assert client.is_authenticated() is False


def test_invalid_credentials_do_not_fallback_to_mock():
    client = AlpacaPaperClient(
        api_key="PK_INVALID_KEY_12345678",
        secret_key="WRONG_SECRET_abcdef123456",
        base_url="https://paper-api.alpaca.markets",
        paper=True
    )
    assert client.has_credentials() is True
    assert client.is_authenticated() is True

    with pytest.raises(RuntimeError, match="PAPER_AUTHENTICATION_FAILED"):
        client.get_account()


def test_live_endpoint_exact_rejection():
    with pytest.raises(SecurityException, match="不在合法 Paper 白名单"):
        AlpacaPaperClient(
            api_key="PKTEST123456",
            secret_key="SECRET123456",
            base_url="https://api.alpaca.markets",
            paper=True
        )


def test_fake_paper_string_rejected():
    with pytest.raises(SecurityException, match="不在合法 Paper 白名单"):
        AlpacaPaperClient(
            api_key="PKTEST123456",
            secret_key="SECRET123456",
            base_url="https://fake-paper-api.alpaca.markets",
            paper=True
        )

    with pytest.raises(SecurityException, match="不在合法 Paper 白名单"):
        AlpacaPaperClient(
            api_key="PKTEST123456",
            secret_key="SECRET123456",
            base_url="https://paper-api.alpaca.markets.evil.com",
            paper=True
        )


def test_no_key_prefix_dependency():
    client = AlpacaPaperClient(
        api_key="AK_SAMPLE_KEY_123456",
        secret_key="SECRET_VAL_123456",
        base_url="https://paper-api.alpaca.markets",
        paper=True
    )
    assert client.api_key == "AK_SAMPLE_KEY_123456"


def test_asset_not_tradable_blocks(monkeypatch):
    client = AlpacaPaperClient(
        api_key="PKTEST123456",
        secret_key="SECRET123456",
        base_url="https://paper-api.alpaca.markets",
        paper=True
    )

    class MockAsset:
        def __init__(self, sym):
            self.symbol = sym
            self.status = "active"
            self.tradable = False if sym == "TLT" else True
            self.fractionable = True

    monkeypatch.setattr(client._client, "get_asset", lambda s: MockAsset(s))

    all_eligible, report = client.check_universe_eligibility(["SPY", "TLT"])
    assert all_eligible is False
    assert report["TLT"]["tradable"] is False
    assert report["TLT"]["is_eligible"] is False
    assert report["SPY"]["is_eligible"] is True


def test_asset_not_fractionable_blocks(monkeypatch):
    client = AlpacaPaperClient(
        api_key="PKTEST123456",
        secret_key="SECRET123456",
        base_url="https://paper-api.alpaca.markets",
        paper=True
    )

    class MockAsset:
        def __init__(self, sym):
            self.symbol = sym
            self.status = "active"
            self.tradable = True
            self.fractionable = False if sym == "EEM" else True

    monkeypatch.setattr(client._client, "get_asset", lambda s: MockAsset(s))

    all_eligible, report = client.check_universe_eligibility(["SPY", "EEM"])
    assert all_eligible is False
    assert report["EEM"]["fractionable"] is False
    assert report["EEM"]["is_eligible"] is False
    assert report["SPY"]["is_eligible"] is True


def test_margin_buying_power_not_used_for_target():
    equity = 100000.0
    margin_buying_power = 200000.0
    target_weights = {"SPY": 1.0 / 3.0, "QQQ": 1.0 / 3.0, "GLD": 1.0 / 3.0}
    current_pos = {}
    prices = {"SPY": 500.0, "QQQ": 400.0, "GLD": 200.0}

    plan = generate_rebalance_plan(
        portfolio_equity=equity,
        current_positions=current_pos,
        target_weights=target_weights,
        current_prices=prices,
        cash_buffer_pct=0.01
    )

    assert plan.available_execution_capital == pytest.approx(99000.0)
    assert plan.total_buy_notional == pytest.approx(99000.0)
    assert plan.total_buy_notional < equity
    assert plan.total_buy_notional < margin_buying_power


def test_execution_cash_buffer():
    equity = 100000.0
    target_weights = {"SPY": 1.0 / 3.0, "QQQ": 1.0 / 3.0, "GLD": 1.0 / 3.0}
    current_pos = {}
    prices = {"SPY": 500.0, "QQQ": 400.0, "GLD": 200.0}

    plan = generate_rebalance_plan(
        portfolio_equity=equity,
        current_positions=current_pos,
        target_weights=target_weights,
        current_prices=prices,
        cash_buffer_pct=0.01
    )

    assert plan.research_target_portfolio["SPY"] == pytest.approx(1.0 / 3.0)
    assert plan.research_target_portfolio["CASH"] == pytest.approx(0.0)
    assert plan.execution_overlay_portfolio["SPY"] == pytest.approx(0.33)
    assert plan.execution_overlay_portfolio["CASH"] == pytest.approx(0.01)
    assert plan.execution_tracking_error_due_to_buffer == pytest.approx(0.01)


def test_paper_client_requires_paper_true():
    with pytest.raises(SecurityException, match="系统硬性禁止 Live Trading"):
        AlpacaPaperClient(api_key="PKTEST123", secret_key="SECRET123", paper=False)


def test_credentials_not_logged():
    secret = "SUPER_SECRET_VALUE_9988776655"
    client = AlpacaPaperClient(api_key="PKTEST12345678", secret_key=secret, paper=True)
    assert secret not in str(client)
    assert secret not in repr(client)
    assert "PKTE***" in str(client)


def test_signal_uses_latest_completed_month():
    dates_aug = pd.date_range("2026-08-01", "2026-08-31", freq="B")
    dates_sep_partial = pd.date_range("2026-09-01", "2026-09-18", freq="B")
    all_dates = dates_aug.append(dates_sep_partial)

    dummy_df = pd.DataFrame({
        "open": [100.0] * len(all_dates),
        "high": [101.0] * len(all_dates),
        "low": [99.0] * len(all_dates),
        "close": [100.0] * len(all_dates),
        "adj_close": [100.0] * len(all_dates),
        "adj_open": [100.0] * len(all_dates),
        "volume": [1000] * len(all_dates)
    }, index=all_dates)

    data_dict = {"SPY": dummy_df}
    as_of = pd.Timestamp("2026-09-18")
    monthly_close, _ = get_monthly_dataset(data_dict, completed_month_only=True, as_of_date=as_of)

    latest_month = monthly_close.index[-1].to_period("M")
    assert str(latest_month) == "2026-08"
    assert "2026-09" not in [str(d.to_period("M")) for d in monthly_close.index]


def test_target_portfolio_matches_research_modules():
    mom_scores = pd.Series({"SPY": 0.15, "QQQ": 0.25, "IWM": 0.10, "GLD": 0.30})
    trend_passed = pd.Series({"SPY": True, "QQQ": True, "IWM": False, "GLD": True})

    weights = select_top_assets(mom_scores, trend_passed, top_n=3, verbose=False)
    assert "IWM" not in weights
    assert len(weights) == 3
    assert weights["GLD"] == pytest.approx(1.0 / 3.0)
    assert weights["QQQ"] == pytest.approx(1.0 / 3.0)
    assert weights["SPY"] == pytest.approx(1.0 / 3.0)


def test_cash_target_liquidates_assets():
    current_pos = {
        "SPY": {"qty": 10.0, "current_price": 500.0, "market_value": 5000.0},
        "QQQ": {"qty": 20.0, "current_price": 400.0, "market_value": 8000.0}
    }
    prices = {"SPY": 500.0, "QQQ": 400.0}

    plan = generate_rebalance_plan(
        portfolio_equity=13000.0,
        current_positions=current_pos,
        target_weights={},
        current_prices=prices
    )
    assert len(plan.sell_orders) == 2
    assert len(plan.buy_orders) == 0


def test_no_trade_when_already_at_target():
    current_pos = {
        "SPY": {"qty": 10.0, "current_price": 500.0, "market_value": 5000.0},
        "QQQ": {"qty": 12.5, "current_price": 400.0, "market_value": 5000.0}
    }
    prices = {"SPY": 500.0, "QQQ": 400.0}
    target_weights = {"SPY": 0.5, "QQQ": 0.5}

    plan = generate_rebalance_plan(
        portfolio_equity=10000.0,
        current_positions=current_pos,
        target_weights=target_weights,
        current_prices=prices,
        cash_buffer_pct=0.0
    )
    assert len(plan.sell_orders) == 0
    assert len(plan.buy_orders) == 0


def test_rebalance_plan_buy_sell():
    current_pos = {
        "SPY": {"qty": 5.0, "current_price": 500.0, "market_value": 2500.0},
        "QQQ": {"qty": 15.0, "current_price": 400.0, "market_value": 6000.0},
        "GLD": {"qty": 10.0, "current_price": 150.0, "market_value": 1500.0}
    }
    prices = {"SPY": 500.0, "QQQ": 400.0, "GLD": 150.0}
    target_weights = {"SPY": 0.60, "QQQ": 0.40}

    plan = generate_rebalance_plan(
        portfolio_equity=10000.0,
        current_positions=current_pos,
        target_weights=target_weights,
        current_prices=prices,
        cash_buffer_pct=0.0
    )
    sell_syms = {o.symbol: o for o in plan.sell_orders}
    buy_syms = {o.symbol: o for o in plan.buy_orders}

    assert "QQQ" in sell_syms
    assert "GLD" in sell_syms
    assert "SPY" in buy_syms


def test_min_notional_filter():
    current_pos = {
        "SPY": {"qty": 10.0, "current_price": 500.0, "market_value": 5000.0}
    }
    prices = {"SPY": 500.0}
    target_weights = {"SPY": 5003.0 / 10000.0}

    plan = generate_rebalance_plan(
        portfolio_equity=10000.0,
        current_positions=current_pos,
        target_weights=target_weights,
        current_prices=prices,
        cash_buffer_pct=0.0,
        min_notional=5.0
    )
    assert len(plan.buy_orders) == 0
    assert len(plan.sell_orders) == 0
    assert len(plan.ignored_dust_orders) == 1


def test_duplicate_month_state_blocks_execution(tmp_path):
    state_file = tmp_path / "rebalance_state.json"
    state = {
        "rebalance_month": "2026-08",
        "status": RebalanceStatus.COMPLETED,
        "target_portfolio": {"SPY": 0.33, "QQQ": 0.33, "GLD": 0.34}
    }
    save_rebalance_state(state, state_file)

    can_proceed, msg = check_rebalance_idempotency("2026-08", force=False, file_path=state_file)
    assert can_proceed is False

    can_proceed_force, _ = check_rebalance_idempotency("2026-08", force=True, file_path=state_file)
    assert can_proceed_force is True


def test_unresolved_state_requires_reconciliation(tmp_path):
    state_file = tmp_path / "rebalance_state.json"
    state = {
        "rebalance_month": "2026-08",
        "status": RebalanceStatus.SUBMITTED,
        "target_portfolio": {"SPY": 0.33, "QQQ": 0.33, "GLD": 0.34}
    }
    save_rebalance_state(state, state_file)

    can_proceed, msg = check_rebalance_idempotency("2026-08", force=False, file_path=state_file)
    assert can_proceed is False
    assert "未决状态" in msg


# ==============================================================================
# Phase 5.8: Execution Timing Tests (Fix 1, 2, 6)
# ==============================================================================

def test_execute_blocked_at_0300_et():
    dt = pd.Timestamp("2026-09-01 03:00:00", tz="America/New_York")
    status = check_execution_window("2026-08", pd.Timestamp("2026-08-31"), market_datetime=dt)
    assert status.execution_allowed is False
    assert "MISSED_EXECUTION_TIME_WINDOW" in status.execution_block_reason


def test_execute_blocked_at_0920_et():
    dt = pd.Timestamp("2026-09-01 09:20:00", tz="America/New_York")
    status = check_execution_window("2026-08", pd.Timestamp("2026-08-31"), market_datetime=dt)
    assert status.execution_allowed is False
    assert "MISSED_EXECUTION_TIME_WINDOW" in status.execution_block_reason


def test_execute_blocked_at_092959_et():
    dt = pd.Timestamp("2026-09-01 09:29:59", tz="America/New_York")
    status = check_execution_window("2026-08", pd.Timestamp("2026-08-31"), market_datetime=dt)
    assert status.execution_allowed is False
    assert "MISSED_EXECUTION_TIME_WINDOW" in status.execution_block_reason


def test_execute_allowed_at_093000_et():
    dt = pd.Timestamp("2026-09-01 09:30:00", tz="America/New_York")
    status = check_execution_window("2026-08", pd.Timestamp("2026-08-31"), market_datetime=dt, is_open=True)
    assert status.execution_allowed is True
    assert status.execution_block_reason == "NONE (ON_SCHEDULE)"


def test_execute_allowed_inside_open_window():
    dt = pd.Timestamp("2026-09-01 09:34:00", tz="America/New_York")
    status = check_execution_window("2026-08", pd.Timestamp("2026-08-31"), market_datetime=dt, is_open=True)
    assert status.execution_allowed is True
    assert status.execution_block_reason == "NONE (ON_SCHEDULE)"


def test_execute_blocked_at_093500_et():
    dt = pd.Timestamp("2026-09-01 09:35:00", tz="America/New_York")
    status = check_execution_window("2026-08", pd.Timestamp("2026-08-31"), market_datetime=dt)
    assert status.execution_allowed is False
    assert "MISSED_EXECUTION_TIME_WINDOW" in status.execution_block_reason


def test_execute_blocked_at_1559_et():
    dt = pd.Timestamp("2026-09-01 15:59:00", tz="America/New_York")
    status = check_execution_window("2026-08", pd.Timestamp("2026-08-31"), market_datetime=dt)
    assert status.execution_allowed is False
    assert "MISSED_EXECUTION_TIME_WINDOW" in status.execution_block_reason


def test_execute_blocked_after_close():
    dt = pd.Timestamp("2026-09-01 16:01:00", tz="America/New_York")
    status = check_execution_window("2026-08", pd.Timestamp("2026-08-31"), market_datetime=dt, is_open=False)
    assert status.execution_allowed is False
    assert "MISSED_EXECUTION_TIME_WINDOW" in status.execution_block_reason


def test_next_day_queue_never_allowed():
    dt = pd.Timestamp("2026-09-02 09:31:00", tz="America/New_York")
    status = check_execution_window("2026-08", pd.Timestamp("2026-08-31"), market_datetime=dt, is_open=True)
    assert status.execution_allowed is False
    assert "MISSED_REBALANCE_WINDOW" in status.execution_block_reason


# ==============================================================================
# Phase 5.8: Force Safety Tests (Fix 4, 7)
# ==============================================================================

def test_execute_force_is_blocked(tmp_path):
    from src.execution.executor import execute_rebalance_plan
    from unittest.mock import MagicMock

    client = AlpacaPaperClient(api_key="PKTEST12345678", secret_key="SECRET_12345678", paper=True)
    plan = MagicMock()
    plan.rebalance_month = "2026-10"
    
    # 1. execute_rebalance_plan 遇到 force=True 必须 Fail Closed
    with pytest.raises(SecurityException, match="UNSAFE_FORCE_EXECUTION_BLOCKED"):
        execute_rebalance_plan(client=client, plan=plan, force=True)

    # 2. check_rebalance_idempotency 在 is_execute_mode=True 且 force=True 必须 Fail Closed
    can_proceed, msg = check_rebalance_idempotency("2026-10", force=True, is_execute_mode=True)
    assert can_proceed is False
    assert "UNSAFE_FORCE_EXECUTION_BLOCKED" in msg


# ==============================================================================
# Phase 5.8: Residual Sequence Tests (Fix 5, 8)
# ==============================================================================

def test_rejected_terminal_can_create_sequence_02_only_after_reconciliation():
    from src.execution.reconciliation import can_create_residual_sequence
    from unittest.mock import MagicMock

    client = MagicMock()
    client.is_authenticated.return_value = True
    client.get_open_orders.return_value = []
    client.get_order_by_client_order_id.return_value = {
        "status": "rejected",
        "filled_qty": 0.0,
        "qty": 10.0
    }

    can_inc, next_seq, reason = can_create_residual_sequence(
        client=client,
        rebalance_month="2026-10",
        symbol="SPY",
        side="BUY",
        current_sequence=1
    )
    assert can_inc is True
    assert next_seq == 2
    assert "TERMINAL_REJECTED_CONFIRMED" in reason


def test_partial_fill_cannot_create_sequence_02():
    from src.execution.reconciliation import can_create_residual_sequence
    from unittest.mock import MagicMock

    client = MagicMock()
    client.is_authenticated.return_value = True
    client.get_open_orders.return_value = []
    client.get_order_by_client_order_id.return_value = {
        "status": "partially_filled",
        "filled_qty": 5.0,
        "qty": 10.0
    }

    can_inc, next_seq, reason = can_create_residual_sequence(
        client=client,
        rebalance_month="2026-10",
        symbol="SPY",
        side="BUY",
        current_sequence=1
    )
    assert can_inc is False
    assert next_seq == 1
    assert "NON_TERMINAL_ORDER" in reason


def test_submission_unknown_cannot_create_sequence_02():
    from src.execution.reconciliation import can_create_residual_sequence
    from unittest.mock import MagicMock

    client = MagicMock()
    client.is_authenticated.return_value = True
    client.get_open_orders.return_value = []
    client.get_order_by_client_order_id.return_value = None

    can_inc, next_seq, reason = can_create_residual_sequence(
        client=client,
        rebalance_month="2026-10",
        symbol="SPY",
        side="BUY",
        current_sequence=1
    )
    assert can_inc is False
    assert next_seq == 1
    assert "SUBMISSION_UNKNOWN_OR_NOT_FOUND" in reason


def test_active_order_cannot_create_sequence_02():
    from src.execution.reconciliation import can_create_residual_sequence
    from unittest.mock import MagicMock

    client = MagicMock()
    client.is_authenticated.return_value = True
    client.get_open_orders.return_value = [
        {"symbol": "SPY", "id": "order-active-1", "status": "new"}
    ]

    can_inc, next_seq, reason = can_create_residual_sequence(
        client=client,
        rebalance_month="2026-10",
        symbol="SPY",
        side="BUY",
        current_sequence=1
    )
    assert can_inc is False
    assert next_seq == 1
    assert "ACTIVE_ORDER_EXISTS" in reason

