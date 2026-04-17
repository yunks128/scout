from __future__ import annotations

import logging

from scout.pipeline import eligibility, lexical_gate, llm_classify
from scout.storage.db import DB, Classification

log = logging.getLogger(__name__)


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
        lex = lexical_gate.score(
            title=row["title"],
            description=row["description"],
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
                    eligibility_quote=eligibility.extract_ffrdc_quote(row["description"]),
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
                description=row["description"],
                posted=row["posted_date"],
                deadline=row["response_deadline"],
                url=row["url"],
            )
            llm_calls += 1
        except Exception:
            log.exception("LLM classify failed for %s/%s", row["source"], row["notice_id"])
            continue
        quote = verdict.eligibility_quote or eligibility.extract_ffrdc_quote(row["description"])
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
                lane=verdict.lane,
            )
        )
    return seen, llm_calls
