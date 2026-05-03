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
        if days is None or days <= 60:
            return "act-now"
    return "review"


def _earliest_days(
    phase1_deadline: str | None,
    response_deadline: str | None,
) -> int | None:
    """Return the days-until value for the nearest upcoming deadline.

    If both are present, returns the smaller (sooner) positive value.
    A past deadline (negative days) is ignored so it doesn't pull an
    otherwise-future opportunity into act-now prematurely.
    Returns None only when no deadline is known (treats as unknown → act-now).
    """
    candidates = []
    for dl in (phase1_deadline, response_deadline):
        d = _days_until(dl)
        if d is not None and d >= 0:
            candidates.append(d)
    if not candidates:
        # All provided deadlines were unparseable or both None → unknown
        return None
    return min(candidates)


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
