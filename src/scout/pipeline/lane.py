"""Deterministic lane assignment from LLM dimensions + portfolio policy.

Keeping this in Python rather than the LLM prompt ensures the eligibility_posture
('archive when FFRDC-restricted with no partner pathway') is enforced every
time — the LLM can't drift on it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from dateutil import parser as dateparser

log = logging.getLogger(__name__)


def compute_lane(
    ffrdc_eligible: str,
    relevance_score: int,
    response_deadline: str | None,
    phase1_deadline: str | None = None,
) -> str:
    """Map LLM dimensions to a lane per portfolio policy.

    Rules (in priority order):
      1. FFRDC explicitly excluded → archive, regardless of technical fit.
      2. Relevance <= 3 → archive (not worth the pipeline noise).
      3. High relevance + eligible (yes/as_partner) + earliest deadline ≤60 days or unknown → act-now.
         "Earliest deadline" is the sooner of phase1_deadline and response_deadline, so a
         phased FOA (e.g. Phase 1 in 30 days, Phase 2 in 8 months) correctly fires act-now.
      4. Everything else → review (includes the important "unclear" case, where a human
         needs to open the FOA to decide).
    """
    if ffrdc_eligible == "no":
        return "archive"
    if relevance_score <= 3:
        return "archive"
    if relevance_score >= 7 and ffrdc_eligible in ("yes", "as_partner"):
        days = _earliest_days(phase1_deadline, response_deadline)
        if days is not None and days < 0:
            return "archive"  # all known deadlines have passed
        if days is None or days <= 60:
            return "act-now"
    return "review"


def _earliest_days(
    phase1_deadline: str | None,
    response_deadline: str | None,
) -> int | None:
    """Return the days-until value for the nearest upcoming deadline.

    Returns None when no deadline is known at all (→ act-now, treat as urgent).
    Returns a negative sentinel (-1) when all provided deadlines are in the past
    (→ caller should archive).
    Otherwise returns the smallest non-negative value (soonest upcoming deadline).
    """
    any_parsed = False
    future: list[int] = []
    for dl in (phase1_deadline, response_deadline):
        d = _days_until(dl)
        if d is not None:
            any_parsed = True
            if d >= 0:
                future.append(d)
    if not any_parsed:
        return None  # no deadline info at all → unknown
    if not future:
        return -1  # deadlines exist but all are past
    return min(future)


def _days_until(deadline: str | None) -> int | None:
    if not deadline:
        return None
    try:
        parsed = dateparser.parse(deadline, fuzzy=True)
    except (ValueError, TypeError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delta = parsed - datetime.now(timezone.utc)
    return delta.days
