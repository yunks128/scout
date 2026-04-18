from datetime import datetime, timedelta, timezone

from scout.pipeline.lane import compute_lane


def _future(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")


def test_ffrdc_no_always_archives_even_if_high_relevance():
    # Core eligibility_posture rule: FFRDC excluded → archive regardless of fit.
    assert compute_lane(ffrdc_eligible="no", relevance_score=10, response_deadline=_future(5)) == "archive"


def test_low_relevance_archives():
    assert compute_lane(ffrdc_eligible="yes", relevance_score=2, response_deadline=_future(5)) == "archive"


def test_high_relevance_eligible_soon_is_actnow():
    assert compute_lane(ffrdc_eligible="yes", relevance_score=8, response_deadline=_future(10)) == "act-now"


def test_as_partner_with_unknown_deadline_still_actnow():
    assert compute_lane(ffrdc_eligible="as_partner", relevance_score=9, response_deadline=None) == "act-now"


def test_unclear_eligibility_goes_to_review():
    # Human must open the FOA — do NOT archive on "unclear".
    assert compute_lane(ffrdc_eligible="unclear", relevance_score=9, response_deadline=_future(10)) == "review"


def test_high_relevance_but_far_deadline_is_review():
    assert compute_lane(ffrdc_eligible="yes", relevance_score=8, response_deadline=_future(90)) == "review"
