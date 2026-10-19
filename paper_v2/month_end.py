"""Guard for research_v2.schedule.build_monthly_matched_schedule when run live.

The research builder groups the available sessions by month and treats the last available session of each
month as its month end. The historical samples always end on a month end, so that is harmless there; live,
mid-month, it turns the as-of session of the unfinished month into a monthly signal. Same rule as
dashboard.shadow.true_month_ends, on the research_v2 (NYSE) calendar.
"""
from typing import List

from research_v2.calendar import next_session
from research_v2.contracts import PeriodEvent


def true_month_ends(monthly_events: List[PeriodEvent]) -> List[PeriodEvent]:
    """Keep only events whose signal session is the last exchange session of its calendar month."""
    return [e for e in monthly_events if next_session(e.signal_session)[:7] != e.signal_session[:7]]
