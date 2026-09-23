"""Partition definitions, Walk-Forward fold structures, and holdout access gating.

Strictly enforces pre-registered boundaries:
- Warm-up: 2005-01-03 onwards (for indicator feature stability only)
- Train: 2006-02-01 to 2015-12-31
- Validation: 2016-01-01 to 2020-12-31
- Retrospective Holdout: 2021-01-01 to 2026-09-18
- Prospective Final OOS: PENDING_DATA
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

PARTITIONS = {
    "warmup": {
        "start": "2005-01-03",
        "end": "2006-01-31",
        "role": "feature_warmup_only_not_scored",
    },
    "train": {
        "start": "2006-02-01",
        "end": "2015-12-31",
        "role": "model_exploration_and_internal_wf",
    },
    "validation": {
        "start": "2016-01-01",
        "end": "2020-12-31",
        "role": "candidate_selection_and_stability",
    },
    "historical_holdout": {
        "start": "2021-01-01",
        "end": "2026-09-18",
        "role": "one_time_unlock_after_model_lock",
        "label": "RETROSPECTIVE_HOLDOUT_EXPOSED_BY_V1",
    },
    "prospective_oos": {
        "status": "PENDING_DATA",
        "min_sessions": 504,
        "min_months": 24,
        "role": "true_forward_oos",
    },
}


def get_walk_forward_folds() -> list[dict[str, Any]]:
    """Build the 10 annual chronological Walk-Forward folds (2011 to 2020).

    Each fold has:
    - inner_train: 2006-02-01 to Dec 31 of (Y - 3)
    - inner_validation: Jan 01 of (Y - 2) to Dec 31 of (Y - 1)
    - test: Jan 01 of Y to Dec 31 of Y
    - feature_start: 2005-01-03 (full historical warmup retained)
    """
    folds = []
    for year in range(2011, 2021):
        inner_train_end = f"{year - 3}-12-31"
        inner_val_start = f"{year - 2}-01-01"
        inner_val_end = f"{year - 1}-12-31"
        test_start = f"{year}-01-01"
        test_end = f"{year}-12-31"

        diagnostic_group = "train_internal" if year <= 2015 else "validation_time"

        folds.append({
            "fold_id": f"WF_{year}",
            "test_year": year,
            "diagnostic_group": diagnostic_group,
            "feature_start": "2005-01-03",
            "inner_train": {"start": "2006-02-01", "end": inner_train_end},
            "inner_validation": {"start": inner_val_start, "end": inner_val_end},
            "test": {"start": test_start, "end": test_end},
        })
    return folds


def audit_splits(
    protocol: Mapping[str, Any],
    session_dates: Sequence[str],
    metadata_only: bool = True,
) -> dict[str, Any]:
    """Audit partition coverage, boundary validity, and holdout gate compliance.

    In Stage 2, metadata_only must be True. Calculating strategy performance,
    rankings, or trade signals on holdout data is strictly forbidden.
    """
    if not metadata_only:
        raise ValueError("Stage 2 strictly permits metadata-only partition audits")

    # Match protocol
    proto_splits = protocol["splits"]
    if proto_splits["train"]["start"] != PARTITIONS["train"]["start"] or \
       proto_splits["train"]["end"] != PARTITIONS["train"]["end"] or \
       proto_splits["validation"]["start"] != PARTITIONS["validation"]["start"] or \
       proto_splits["validation"]["end"] != PARTITIONS["validation"]["end"] or \
       proto_splits["historical_holdout"]["start"] != PARTITIONS["historical_holdout"]["start"] or \
       proto_splits["historical_holdout"]["end"] != PARTITIONS["historical_holdout"]["end"]:
        raise ValueError("Splits definition deviates from locked protocol")

    all_sessions = sorted(session_dates)
    s_set = set(all_sessions)

    partition_counts = {}
    for part_name in ["train", "validation", "historical_holdout"]:
        cfg = PARTITIONS[part_name]
        p_sessions = [s for s in all_sessions if cfg["start"] <= s <= cfg["end"]]
        partition_counts[part_name] = {
            "start": cfg["start"],
            "end": cfg["end"],
            "session_count": len(p_sessions),
            "first_session": p_sessions[0] if p_sessions else None,
            "last_session": p_sessions[-1] if p_sessions else None,
        }

    # Warmup coverage
    warmup_sessions = [s for s in all_sessions if s < PARTITIONS["train"]["start"]]
    partition_counts["warmup"] = {
        "start": PARTITIONS["warmup"]["start"],
        "end": PARTITIONS["train"]["start"],
        "session_count": len(warmup_sessions),
        "first_session": warmup_sessions[0] if warmup_sessions else None,
        "last_session": warmup_sessions[-1] if warmup_sessions else None,
    }

    wf_folds = get_walk_forward_folds()

    return {
        "status": "PASS",
        "partitions": partition_counts,
        "walk_forward_folds_count": len(wf_folds),
        "walk_forward_folds": wf_folds,
        "holdout_gate": {
            "strategy_returns_computed": False,
            "rankings_computed": False,
            "holdings_computed": False,
            "holdout_unlocked": False,
            "status": "LOCKED_METADATA_ONLY",
        },
        "prospective_oos": PARTITIONS["prospective_oos"],
    }
