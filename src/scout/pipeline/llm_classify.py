from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

import yaml
from google import genai
from google.genai import types

from scout.config import llm_model, portfolio

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Scout, an analyst at JPL screening federal funding opportunities for fit with JPL's power and energy portfolio.

JPL is an FFRDC. FFRDC eligibility is a HARD GATE per the portfolio's eligibility_posture — many DOE FOAs restrict FFRDC primes or require an FFRDC-partner pathway. Your eligibility call has to be accurate because downstream policy archives any FOA you mark 'no'.

Rules for each field:

ffrdc_eligible — pick exactly one:
- "no": the notice text explicitly excludes FFRDCs / national labs with NO partner pathway. Quote the excluding language in eligibility_quote.
- "as_partner": the notice allows FFRDCs only as subawardees, team members, or partners on a non-FFRDC prime. Quote the enabling language.
- "yes": the notice explicitly invites FFRDCs (or all R&D institutions including national labs) as primes.
- "unclear": the notice text is silent on FFRDC/national-lab eligibility. DO NOT guess from program name or conventions — silence means "unclear", which routes to human review.

Critical: Phase I / Phase II deadline structure is NOT evidence of SBIR/STTR. DOE Office of Science and other agencies routinely use Phase I/II labels for open FOAs available to all eligible entities. Only mark "no" if the notice body explicitly restricts applicants to small businesses or explicitly excludes FFRDCs/national labs.

Same discipline for cost_share and foreign_entity: default to "unclear" when the text is silent.

Do not assign the lane. Scout's downstream applies portfolio policy to (relevance_score, ffrdc_eligible, deadline) to decide act-now / review / archive. You only fill in dimensions.

Return only valid JSON matching the schema in the user message — no prose, no markdown fences."""

USER_TEMPLATE = """Portfolio context:
```yaml
{portfolio_yaml}
```

Notice to classify:
- source: {source}
- notice_id: {notice_id}
- agency: {agency}
- title: {title}
- posted: {posted}
- response_deadline: {deadline}
- url: {url}

Description / body (may be truncated):
{description}

Return JSON with this exact schema (note: no 'lane' field — that is computed downstream):
{{
  "relevance_score": <int 0-10>,
  "matched_themes": [<string>, ...],
  "strategic_fit_notes": "<one to three sentences on why it fits or does not>",
  "ffrdc_eligible": "<yes|no|as_partner|unclear>",
  "cost_share": "<required|not_required|unclear>",
  "foreign_entity": "<restricted|allowed|unclear>",
  "eligibility_quote": "<literal paragraph from the notice that drove the eligibility call, or empty string>"
}}"""


@dataclass
class LLMVerdict:
    relevance_score: int
    matched_themes: list[str]
    strategic_fit_notes: str
    ffrdc_eligible: str
    cost_share: str
    foreign_entity: str
    eligibility_quote: str


_client_cache: genai.Client | None = None


def _client() -> genai.Client:
    global _client_cache
    if _client_cache is None:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY not set")
        _client_cache = genai.Client(api_key=key)
    return _client_cache


def classify(
    source: str,
    notice_id: str,
    agency: str | None,
    title: str,
    description: str | None,
    posted: str | None,
    deadline: str | None,
    url: str | None,
) -> LLMVerdict:
    desc = (description or "")[:8000]
    user_msg = USER_TEMPLATE.format(
        portfolio_yaml=yaml.safe_dump(portfolio(), sort_keys=False).strip(),
        source=source,
        notice_id=notice_id,
        agency=agency or "unknown",
        title=title,
        posted=posted or "unknown",
        deadline=deadline or "unknown",
        url=url or "",
        description=desc or "(no description available)",
    )
    resp = _client().models.generate_content(
        model=llm_model(),
        contents=user_msg,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            temperature=0.2,
            max_output_tokens=1024,
        ),
    )
    data = _parse_json(resp.text or "")
    return LLMVerdict(
        relevance_score=int(data.get("relevance_score", 0)),
        matched_themes=list(data.get("matched_themes") or []),
        strategic_fit_notes=str(data.get("strategic_fit_notes") or ""),
        ffrdc_eligible=str(data.get("ffrdc_eligible") or "unclear"),
        cost_share=str(data.get("cost_share") or "unclear"),
        foreign_entity=str(data.get("foreign_entity") or "unclear"),
        eligibility_quote=str(data.get("eligibility_quote") or ""),
    )


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        log.error("Could not parse LLM JSON: %s", text[:500])
        return {}
