"""Simulated data-steward review of the 0.75-0.90 band (PLAN §6, DECISIONS D22).

Stewards review one item per pair of clusters left apart by the automatic rules (the best-scoring
record pair between them). The decision is simulated from the synthetic truth with a deterministic
2% error (documented as a simulated human review; storyline clients are decided without it, see
runs.replay); turnaround is a few days, with ~6% of items
parked (waiting on the RM or the client) for one to seven months. Items are worked by the client-data
team (Onboarding Officers). Decisions persist: a reviewed pair is never queued again.
"""
from __future__ import annotations

import datetime as _dt
from typing import List, Optional

from .. import rng

MATCH, NO_MATCH = "match", "no_match"
PARKED_SHARE = 0.06
PARKED_DAYS = (35, 220)
TURNAROUND_MEDIAN_DAYS, TURNAROUND_SIGMA, TURNAROUND_MAX = 5.0, 0.75, 45


def decide(seed: int, item_key: str, is_true_match: bool, error_rate: float) -> str:
    """The steward's call: right except for a deterministic error_rate share of items."""
    wrong = rng.unit(seed, "steward_err", item_key) < error_rate
    return MATCH if is_true_match != wrong else NO_MATCH


def turnaround_days(seed: int, item_key: str) -> int:
    if rng.unit(seed, "steward_park", item_key) < PARKED_SHARE:
        return rng.randint(seed, PARKED_DAYS[0], PARKED_DAYS[1], "steward_parkdays", item_key)
    days = rng.lognormal(seed, 0.0, TURNAROUND_SIGMA, "steward_days", item_key) * TURNAROUND_MEDIAN_DAYS
    return max(1, min(TURNAROUND_MAX, int(round(days))))


def decided_date(seed: int, item_key: str, created: _dt.date) -> _dt.date:
    return created + _dt.timedelta(days=turnaround_days(seed, item_key))


def assign(seed: int, item_key: str, stewards: List[str]) -> Optional[str]:
    if not stewards:
        return None
    return stewards[rng.hash64(seed, "steward_assign", item_key) % len(stewards)]
