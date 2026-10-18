"""Holdout guard: research_v3 may only touch train / validation (and warmup features)."""
from __future__ import annotations

ALLOWED_PARTITIONS = ("train", "validation")
MAX_ALLOWED_DATE = "2020-12-31"  # last validation session; anything later is holdout territory


class HoldoutAccessError(ValueError):
    pass


def assert_partition_allowed(partition: str) -> None:
    if partition not in ALLOWED_PARTITIONS:
        raise HoldoutAccessError(
            f"research_v3 forbids partition {partition!r}; allowed: {ALLOWED_PARTITIONS}. "
            "Holdout/prospective data must not be loaded or scored."
        )


def assert_date_allowed(date: str) -> None:
    if str(date)[:10] > MAX_ALLOWED_DATE:
        raise HoldoutAccessError(f"date {date} is after {MAX_ALLOWED_DATE} (holdout range)")
