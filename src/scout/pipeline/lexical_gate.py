from __future__ import annotations

from dataclasses import dataclass

from scout.config import keywords, naics_psc


@dataclass
class LexicalResult:
    score: float
    matches: list[str]
    passes: bool


def score(title: str, description: str | None, naics: str | None = None, psc: str | None = None) -> LexicalResult:
    """Cheap keyword/NAICS/PSC filter before spending LLM tokens.

    A notice passes when score >= 3 and no exclusion term dominates. Tune the
    threshold as you see false positives/negatives in the digest output.
    """
    text = f"{title or ''}\n{description or ''}".lower()
    kw = keywords()
    matches: list[str] = []
    total = 0.0

    # Iterate every bucket in keywords.yaml (except 'exclusions') so new
    # categories don't have to be wired into code — just add a bucket.
    for bucket_name, entries in kw.items():
        if bucket_name == "exclusions" or not isinstance(entries, list):
            continue
        for entry in entries:
            term = entry["term"].lower()
            if term in text.lower():
                total += entry["weight"]
                matches.append(entry["term"])

    exclusions = [e.lower() for e in kw.get("exclusions", [])]
    if any(e in text for e in exclusions) and total < 5:
        return LexicalResult(score=total, matches=matches, passes=False)

    codes = naics_psc()
    naics_set = {n["code"] for n in codes["naics"]}
    psc_set = {p["code"] for p in codes["psc"]}
    if naics and naics in naics_set:
        total += 2
        matches.append(f"NAICS:{naics}")
    if psc and psc in psc_set:
        total += 2
        matches.append(f"PSC:{psc}")

    return LexicalResult(score=total, matches=matches, passes=total >= 3)
