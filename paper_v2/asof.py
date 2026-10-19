"""As-of parsing for paper_v2.

Documented input format: ``YYYY-MM-DD`` = a market date, meaning "as of just after the
XNYS close of that date, America/New_York". A timezone-aware ISO-8601 timestamp is also
accepted (converted to its New York calendar date). Naive date-times are rejected.
"""
from datetime import datetime
from typing import Tuple
import zoneinfo

from research_v2.calendar import market_as_of_after_close

NY_TZ = zoneinfo.ZoneInfo("America/New_York")


def normalize_as_of(as_of) -> Tuple[str, str]:
    """Return (date_str 'YYYY-MM-DD', tz-aware ISO string accepted by research_v2.parse_as_of)."""
    if isinstance(as_of, datetime):
        dt = as_of
    else:
        text = str(as_of).strip()
        if len(text) == 10:
            try:
                datetime.strptime(text, "%Y-%m-%d")
            except ValueError as e:
                raise ValueError(f"invalid as-of date {text!r}; expected YYYY-MM-DD") from e
            return text, market_as_of_after_close(text)
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as e:
            raise ValueError(f"invalid as-of {text!r}; expected YYYY-MM-DD or tz-aware ISO-8601") from e
    if dt.tzinfo is None:
        raise ValueError("as-of timestamp must be timezone-aware; use YYYY-MM-DD for a market date")
    ny = dt.astimezone(NY_TZ)
    return ny.strftime("%Y-%m-%d"), ny.isoformat()
