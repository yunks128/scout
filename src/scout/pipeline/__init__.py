from __future__ import annotations

import logging
import os

import httpx

from scout.pipeline import eligibility, lane, lexical_gate, llm_classify
from scout.pipeline.llm_classify import LLMVerdict
from scout.storage.db import DB, Classification

log = logging.getLogger(__name__)

# Words that appear in LLM-fabricated eligibility quotes but never in real notice text.
_HALLUCINATION_MARKERS = (
    "historically",
    "typically",
    "generally",
    "normally",
    "by convention",
    "in practice",
    "usually",
)

# SAM.gov description URLs that indicate the text was not fetched at ingest time.
_SAM_DESC_URL_PREFIX = "https://api.sam.gov/prod/opportunities/v1/noticedesc"


def _validate_verdict(verdict: LLMVerdict) -> LLMVerdict:
    """Downgrade yes/as_partner to unclear when the eligibility_quote is absent or fabricated.

    The LLM sometimes uses training knowledge to assert FFRDC eligibility when the
    notice text is silent. A fabricated quote typically contains hedge words like
    'historically' or 'typically' that no real notice would use. An empty quote for a
    positive eligibility call is equally suspect. Both cases should route to human review.
    """
    if verdict.ffrdc_eligible not in ("yes", "as_partner"):
        return verdict
    quote = (verdict.eligibility_quote or "").lower()
    if not quote or any(m in quote for m in _HALLUCINATION_MARKERS):
        log.warning(
            "Downgrading ffrdc_eligible from '%s' to 'unclear': quote is absent or contains "
            "training-knowledge language: %r",
            verdict.ffrdc_eligible,
            verdict.eligibility_quote,
        )
        return LLMVerdict(
            relevance_score=verdict.relevance_score,
            matched_themes=verdict.matched_themes,
            strategic_fit_notes=verdict.strategic_fit_notes,
            ffrdc_eligible="unclear",
            cost_share=verdict.cost_share,
            foreign_entity=verdict.foreign_entity,
            eligibility_quote="",
        )
    return verdict


def _try_fetch_sam_description(url: str) -> str | None:
    """Attempt to fetch description text from a SAM.gov noticedesc URL.

    Called when a notice was ingested before _enrich_description ran or when that
    fetch failed and the raw URL was stored instead of the text.
    """
    api_key = os.environ.get("SAM_GOV_API_KEY")
    if not api_key:
        return None
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.get(url, params={"api_key": api_key})
            if r.status_code != 200:
                log.debug("Re-fetch of SAM description returned %s for %s", r.status_code, url)
                return None
            ctype = r.headers.get("content-type", "")
            if "json" in ctype:
                body = r.json()
                return body.get("description") if isinstance(body, dict) else str(body)
            return r.text or None
    except Exception:
        log.debug("Re-fetch of SAM description failed for %s", url, exc_info=True)
        return None


def classify_unclassified(db: DB) -> tuple[int, int]:
    """Run lexical gate then LLM classifier on every notice without a classification row.

    Returns (num_seen, num_llm_called). The delta is notices that failed the lexical gate
    and got an archive lane without spending LLM tokens.
    """
    rows = db.unclassified()
    seen = 0
    llm_calls = 0
    for row in rows:
        seen += 1
        description = row["description"]

        # If the stored description is still a raw SAM.gov API URL, the ingest-time
        # fetch failed. Try again now so the LLM gets real text instead of a URL.
        if isinstance(description, str) and description.startswith(_SAM_DESC_URL_PREFIX):
            fetched = _try_fetch_sam_description(description)
            if fetched:
                log.info("Re-fetched description for %s/%s", row["source"], row["notice_id"])
                description = fetched

        lex = lexical_gate.score(
            title=row["title"],
            description=description,
            naics=row["naics"],
            psc=row["psc"],
        )
        if not lex.passes:
            db.save_classification(
                Classification(
                    source=row["source"],
                    notice_id=row["notice_id"],
                    content_hash=row["content_hash"],
                    lexical_score=lex.score,
                    lexical_matches=lex.matches,
                    llm_relevance=None,
                    llm_themes=[],
                    llm_fit_notes=None,
                    ffrdc_eligible=None,
                    cost_share=None,
                    foreign_entity=None,
                    eligibility_quote=eligibility.extract_ffrdc_quote(description),
                    lane="archive",
                )
            )
            continue
        try:
            verdict = llm_classify.classify(
                source=row["source"],
                notice_id=row["notice_id"],
                agency=row["agency"],
                title=row["title"],
                description=description,
                posted=row["posted_date"],
                deadline=row["response_deadline"],
                url=row["url"],
            )
            llm_calls += 1
        except Exception:
            log.exception("LLM classify failed for %s/%s", row["source"], row["notice_id"])
            continue
        verdict = _validate_verdict(verdict)
        quote = verdict.eligibility_quote or eligibility.extract_ffrdc_quote(description)
        assigned_lane = lane.compute_lane(
            ffrdc_eligible=verdict.ffrdc_eligible,
            relevance_score=verdict.relevance_score,
            response_deadline=row["response_deadline"],
            phase1_deadline=row["preapp_deadline"],
        )
        db.save_classification(
            Classification(
                source=row["source"],
                notice_id=row["notice_id"],
                content_hash=row["content_hash"],
                lexical_score=lex.score,
                lexical_matches=lex.matches,
                llm_relevance=verdict.relevance_score,
                llm_themes=verdict.matched_themes,
                llm_fit_notes=verdict.strategic_fit_notes,
                ffrdc_eligible=verdict.ffrdc_eligible,
                cost_share=verdict.cost_share,
                foreign_entity=verdict.foreign_entity,
                eligibility_quote=quote,
                lane=assigned_lane,
            )
        )
    return seen, llm_calls
