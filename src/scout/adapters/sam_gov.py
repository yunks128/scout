from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import httpx

from scout.adapters.base import Adapter
from scout.config import naics_psc
from scout.storage.db import Notice

log = logging.getLogger(__name__)

SEARCH_URL = "https://api.sam.gov/opportunities/v2/search"


class SamGovAdapter(Adapter):
    source = "sam.gov"

    def __init__(self, lookback_days: int = 30, page_size: int = 100) -> None:
        self.lookback_days = lookback_days
        self.page_size = page_size
        self.api_key = os.environ.get("SAM_GOV_API_KEY")

    def fetch(self) -> Iterator[tuple[str, dict]]:
        if not self.api_key:
            log.warning("SAM_GOV_API_KEY not set; skipping SAM.gov ingestion")
            return
        end = datetime.utcnow()
        start = end - timedelta(days=self.lookback_days)
        # SAM.gov accepts one NAICS code per request; we iterate codes rather than
        # passing a comma-separated list (not officially supported in v2).
        naics_codes = [n["code"] for n in naics_psc()["naics"]]
        with httpx.Client(timeout=30.0) as client:
            for ncode in naics_codes:
                yield from self._paginate(client, start, end, ncode)

    def _paginate(self, client: httpx.Client, start, end, ncode: str) -> Iterator[tuple[str, dict]]:
        offset = 0
        while True:
            params = {
                "api_key": self.api_key,
                "postedFrom": start.strftime("%m/%d/%Y"),
                "postedTo": end.strftime("%m/%d/%Y"),
                "ncode": ncode,
                "limit": self.page_size,
                "offset": offset,
            }
            r = client.get(SEARCH_URL, params=params)
            if r.status_code == 429:
                log.warning("SAM.gov rate limited on NAICS %s; moving on", ncode)
                return
            if r.status_code == 404:
                return
            r.raise_for_status()
            data = r.json()
            hits = data.get("opportunitiesData") or []
            if not hits:
                return
            for hit in hits:
                nid = hit.get("noticeId") or hit.get("solicitationNumber")
                if not nid:
                    continue
                self._enrich_description(client, hit)
                yield str(nid), hit
            total = data.get("totalRecords", 0)
            offset += len(hits)
            if offset >= total:
                return

    def _enrich_description(self, client: httpx.Client, hit: dict[str, Any]) -> None:
        """SAM.gov returns a link in `description`, not the text. Fetch the text
        once and put it in `description_text` so downstream doesn't re-fetch."""
        desc = hit.get("description")
        if not isinstance(desc, str) or not desc.startswith("http"):
            return
        try:
            r = client.get(desc, params={"api_key": self.api_key})
            if r.status_code == 200:
                # Response is a JSON object with a `description` field, or plain text.
                ctype = r.headers.get("content-type", "")
                if "json" in ctype:
                    body = r.json()
                    hit["description_text"] = body.get("description") if isinstance(body, dict) else str(body)
                else:
                    hit["description_text"] = r.text
        except Exception:
            log.debug("Description fetch failed for %s", hit.get("noticeId"), exc_info=True)

    def normalize(self, notice_id: str, payload: dict[str, Any], content_hash: str) -> Notice | None:
        title = payload.get("title") or ""
        if not title:
            return None
        naics = payload.get("naicsCode") or ""
        psc_val = payload.get("classificationCode") or ""
        return Notice(
            source=self.source,
            notice_id=notice_id,
            content_hash=content_hash,
            title=title,
            agency=payload.get("department") or payload.get("fullParentPathName"),
            description=payload.get("description_text") or payload.get("description"),
            posted_date=payload.get("postedDate"),
            response_deadline=payload.get("responseDeadLine"),
            naics=naics,
            psc=psc_val,
            url=payload.get("uiLink"),
            last_modified=payload.get("updatedDate"),
        )
