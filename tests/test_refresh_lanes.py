from datetime import datetime, timedelta, timezone

from scout.pipeline import refresh_lanes
from scout.storage.db import DB, Classification, Notice


def _date(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")


def _add(db: DB, notice_id: str, deadline: str, ffrdc: str, quote: str) -> None:
    db.upsert_notice(
        Notice(source="sam.gov", notice_id=notice_id, content_hash="h", title=notice_id,
               response_deadline=deadline)
    )
    db.save_classification(
        Classification(
            source="sam.gov", notice_id=notice_id, content_hash="h", lexical_score=5.0,
            lexical_matches=[], llm_relevance=8, llm_themes=[], llm_fit_notes=None,
            ffrdc_eligible=ffrdc, cost_share="unclear", foreign_entity="unclear",
            eligibility_quote=quote, lane="act-now",
        )
    )


def _lanes(db: DB) -> dict[str, tuple[str, str, str]]:
    return {
        r["notice_id"]: (r["lane"], r["ffrdc_eligible"], r["eligibility_quote"])
        for r in db.latest_llm_classified()
    }


def test_refresh_archives_past_deadline_and_scrubs_fabricated_quote(tmp_path):
    db = DB(tmp_path / "scout.db")
    real = "FFRDCs are eligible to propose as prime contractors."
    _add(db, "expired", _date(-10), "yes", real)
    _add(db, "fabricated", _date(20), "yes", "NASA solicitations are generally open to JPL.")
    _add(db, "fine", _date(20), "yes", real)

    assert refresh_lanes(db) == 2
    lanes = _lanes(db)
    assert lanes["expired"] == ("archive", "yes", real)
    assert lanes["fabricated"] == ("review", "unclear", "")
    assert lanes["fine"] == ("act-now", "yes", real)
    assert refresh_lanes(db) == 0
