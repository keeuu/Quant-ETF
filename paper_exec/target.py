"""Frozen CAP_DEF_M US target, read from the dashboard's shadow computation (dashboard.shadow). Read-only."""
from __future__ import annotations

from datetime import datetime, timezone


def load_target(now_utc: datetime | None = None) -> dict:
    """Official latest month-end signal + what-if preview, from the frozen rule (same code path as the dashboard).

    official: {signal_session, execution_session, weights, status, pre_freeze} (the latest official signal row)
    what_if:  {signal_session, execution_session_if, weights}  -- a hypothetical, never to be traded
    """
    from dashboard import config
    from dashboard.calendar_hybrid import HybridCalendar  # noqa: F401  (imported for parity with the dashboard)
    from dashboard.shadow import compute_market_view
    from research_v2.calendar import get_trading_calendar
    from dashboard import fetch

    now_utc = now_utc or datetime.now(timezone.utc)
    spec = config.us_spec()
    fr = fetch.fetch_us(spec, now_utc, data_root=config.DATA_ROOT)
    view = compute_market_view(spec, fr.bars, fr.rate, now_utc, base_cal=get_trading_calendar(),
                               freeze_date=config.frozen_spec()["freeze_date"], account={"enabled": False})
    rt, wi = view["reference_target"], view["what_if"]
    return {"as_of": view["as_of"], "universe": list(spec.universe), "names": dict(view["names"]),
            "official": {"signal_session": rt["signal_session"], "execution_session": rt["execution_session"],
                         "weights": rt["weights"], "status": rt["status"], "pre_freeze": rt["pre_freeze"]},
            "what_if": {"signal_session": wi["signal_session"], "execution_session_if": wi["execution_session_if"],
                        "weights": wi["weights"]},
            "next_rebalance": view["next_rebalance"]}
