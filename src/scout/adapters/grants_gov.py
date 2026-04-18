from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import httpx

from scout.adapters.base import Adapter
from scout.storage.db import Notice

log = logging.getLogger(__name__)

SEARCH_URL = "https://api.grants.gov/v1/api/search2"
FETCH_URL = "https://api.grants.gov/v1/api/fetchOpportunity"

# Grants.gov funding category codes that cover power/energy broadly.
FUNDING_CATEGORIES = ["ENG", "ST", "EN"]

# Agencies that actually post energy R&D work.
AGENCY_CODES = ["DOE", "DOD", "NSF", "DARPA", "USDA-NIFA", "NASA"]

# Keyword filter applied server-side by Grants.gov's Search2. Tuned for
# Section 345 (spacecraft power, avionics, electronics) — biases toward
# space-flight relevance while keeping broader "power" coverage so terrestrial
# R&D with space crossover isn't missed entirely.
KEYWORD_FILTER = (
    "spacecraft OR avionics OR photovoltaic OR \"solar array\" OR RTG "
    "OR thermoelectric OR \"power electronics\" OR \"motor control\" "
    "OR FPGA OR \"radiation hardened\" OR \"rad-hard\" OR \"flight electronics\" "
    "OR battery OR \"energy storage\" OR lunar OR Mars OR CubeSat OR SmallSat "
    "OR \"pulsed power\" OR \"in-space\" OR \"on-orbit\" OR propulsion"
)


class GrantsGovAdapter(Adapter):
    source = "grants.gov"

    def __init__(self, rows_per_page: int = 100, max_pages: int = 5) -> None:
        self.rows_per_page = rows_per_page
        self.max_pages = max_pages

    def fetch(self) -> Iterator[tuple[str, dict]]:
        with httpx.Client(timeout=30.0) as client:
            offset = 0
            for _ in range(self.max_pages):
                body = {
                    "rows": self.rows_per_page,
                    "offset": offset,
                    "oppStatuses": "forecasted|posted",
                    "fundingCategories": "|".join(FUNDING_CATEGORIES),
                    "agencies": "|".join(AGENCY_CODES),
                    "keyword": KEYWORD_FILTER,
                    "sortBy": "openDate|desc",
                }
                r = client.post(SEARCH_URL, json=body)
                r.raise_for_status()
                data = (r.json() or {}).get("data", {}) or {}
                hits = data.get("oppHits") or []
                hit_count = int(data.get("hitCount") or 0)
                if not hits:
                    return
                for hit in hits:
                    nid = str(hit.get("id") or hit.get("number") or "")
                    if not nid:
                        continue
                    self._enrich_detail(client, nid, hit)
                    yield nid, hit
                offset += len(hits)
                if offset >= hit_count or len(hits) < self.rows_per_page:
                    return

    def _enrich_detail(self, client: httpx.Client, opp_id: str, hit: dict[str, Any]) -> None:
        """Pull the full synopsis via fetchOpportunity — Search2 only returns a summary.
        Silently skip on failure; the LLM will just see the thin summary."""
        try:
            r = client.post(FETCH_URL, json={"opportunityId": int(opp_id)}, timeout=30.0)
            if r.status_code != 200:
                return
            data = (r.json() or {}).get("data") or {}
            synopsis = data.get("synopsis") or {}
        except (ValueError, httpx.HTTPError):
            log.debug("grants.gov detail fetch failed for %s", opp_id, exc_info=True)
            return
        hit["_detail"] = {
            "synopsisDesc": synopsis.get("synopsisDesc") or "",
            "applicantTypes": synopsis.get("applicantTypes") or [],
            "applicantEligibilityDesc": synopsis.get("applicantEligibilityDesc") or "",
            "costSharing": synopsis.get("costSharing"),
            "agencyName": synopsis.get("agencyName"),
            "awardCeiling": synopsis.get("awardCeiling"),
            "awardFloor": synopsis.get("awardFloor"),
        }
        if not hit.get("agencyName") and synopsis.get("agencyName"):
            hit["agencyName"] = synopsis.get("agencyName")

    def normalize(self, notice_id: str, payload: dict[str, Any], content_hash: str) -> Notice | None:
        title = payload.get("title") or ""
        if not title:
            return None
        return Notice(
            source=self.source,
            notice_id=notice_id,
            content_hash=content_hash,
            title=title,
            agency=payload.get("agencyName") or payload.get("agencyCode"),
            description=_compose_description(payload),
            posted_date=payload.get("openDate"),
            response_deadline=payload.get("closeDate"),
            url=f"https://www.grants.gov/search-results-detail/{notice_id}",
            last_modified=payload.get("lastUpdatedDate"),
        )


def _compose_description(payload: dict[str, Any]) -> str:
    """Merge Search2 summary with fetchOpportunity detail so the LLM gets the
    full synopsis plus structured eligibility/cost-share hints."""
    detail = payload.get("_detail") or {}
    parts: list[str] = []
    syn_desc = detail.get("synopsisDesc") or ""
    if syn_desc:
        parts.append(syn_desc)
    elif payload.get("description"):
        parts.append(str(payload["description"]))

    eligibility_bits: list[str] = []
    types = detail.get("applicantTypes") or []
    if types:
        labels = [t.get("description") or t.get("id") for t in types if isinstance(t, dict)]
        eligibility_bits.append("Applicant types: " + "; ".join(str(x) for x in labels if x))
    elig_desc = detail.get("applicantEligibilityDesc") or ""
    if elig_desc:
        eligibility_bits.append("Additional eligibility: " + elig_desc)
    cost = detail.get("costSharing")
    if cost is not None:
        eligibility_bits.append(f"Cost sharing required: {bool(cost)}")
    if eligibility_bits:
        parts.append("\n".join(eligibility_bits))
    return "\n\n".join(parts).strip()
