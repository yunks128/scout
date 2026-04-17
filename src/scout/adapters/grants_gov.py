from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import httpx

from scout.adapters.base import Adapter
from scout.storage.db import Notice

log = logging.getLogger(__name__)

SEARCH_URL = "https://api.grants.gov/v1/api/search2"

# Grants.gov funding category codes that cover power/energy broadly.
FUNDING_CATEGORIES = ["ENG", "ST", "EN"]

# Agencies that actually post energy R&D work.
AGENCY_CODES = ["DOE", "DOD", "NSF", "DARPA", "USDA-NIFA", "NASA"]


class GrantsGovAdapter(Adapter):
    source = "grants.gov"

    def __init__(self, rows_per_page: int = 100, max_pages: int = 5) -> None:
        self.rows_per_page = rows_per_page
        self.max_pages = max_pages

    def fetch(self) -> Iterator[tuple[str, dict]]:
        with httpx.Client(timeout=30.0) as client:
            for page in range(self.max_pages):
                body = {
                    "rows": self.rows_per_page,
                    "offset": page * self.rows_per_page,
                    "oppStatuses": "forecasted|posted",
                    "fundingCategories": "|".join(FUNDING_CATEGORIES),
                    "agencies": "|".join(AGENCY_CODES),
                    "sortBy": "openDate|desc",
                }
                r = client.post(SEARCH_URL, json=body)
                r.raise_for_status()
                data = r.json()
                hits = data.get("data", {}).get("oppHits") or []
                if not hits:
                    return
                for hit in hits:
                    nid = str(hit.get("id") or hit.get("number") or "")
                    if not nid:
                        continue
                    yield nid, hit
                if len(hits) < self.rows_per_page:
                    return

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
            description=payload.get("description"),
            posted_date=payload.get("openDate"),
            response_deadline=payload.get("closeDate"),
            url=f"https://www.grants.gov/search-results-detail/{notice_id}",
            last_modified=payload.get("lastUpdatedDate"),
        )
