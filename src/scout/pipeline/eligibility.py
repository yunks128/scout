from __future__ import annotations

import re

# Quick-and-dirty FFRDC mention regex to seed the eligibility_quote when the LLM
# does not surface one. The LLM call is authoritative; this is a safety net.

FFRDC_PATTERNS = [
    re.compile(r"(?i)FFRDC[^.]{0,400}\."),
    re.compile(r"(?i)federally\s+funded\s+research\s+and\s+development\s+cent(?:er|re)[^.]{0,400}\."),
    re.compile(r"(?i)(?:DOE\s+)?national\s+laborator(?:y|ies)[^.]{0,400}\."),
]

COST_SHARE_PATTERNS = [
    re.compile(r"(?i)cost\s*shar(?:e|ing)[^.]{0,300}\."),
]


def extract_ffrdc_quote(description: str | None) -> str:
    if not description:
        return ""
    for pat in FFRDC_PATTERNS:
        m = pat.search(description)
        if m:
            return m.group(0).strip()
    return ""


def extract_cost_share_quote(description: str | None) -> str:
    if not description:
        return ""
    for pat in COST_SHARE_PATTERNS:
        m = pat.search(description)
        if m:
            return m.group(0).strip()
    return ""
