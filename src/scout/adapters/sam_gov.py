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

SEARCH_URL = "https://api.sam.gov/prod/opportunities/v2/search"


class SamGovAdapter(Adapter):
    source = "sam.gov"

    # Narrow NAICS set to the highest-signal Section 345 codes. SI-NONFED role
    # has a tight daily quota; pick space-vehicle + R&D + engineering services
    # as the three that cover most 345-relevant SAM.gov postings.
    CORE_NAICS = ["336414", "541715", "541330"]

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
        with httpx.Client(timeout=30.0) as client:
            for ncode in self.CORE_NAICS:
                stop, hits = self._paginate(client, start, end, ncode)
                yield from hits
                if stop:
                    log.warning("SAM.gov quota exhausted; stopping all ingestion")
                    return

    def _paginate(
        self, client: httpx.Client, start, end, ncode: str
    ) -> tuple[bool, list[tuple[str, dict]]]:
        """Return (hard_stop, results). hard_stop=True on 429 so the outer loop bails."""
        out: list[tuple[str, dict]] = []
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
                log.warning("SAM.gov 429 on NAICS %s", ncode)
                return True, out
            if r.status_code == 404:
                return False, out
            r.raise_for_status()
            data = r.json()
            hits = data.get("opportunitiesData") or []
            if not hits:
                return False, out
            for hit in hits:
                nid = hit.get("noticeId") or hit.get("solicitationNumber")
                if not nid:
                    continue
                self._enrich_description(client, hit)
                out.append((str(nid), hit))
            total = data.get("totalRecords", 0)
            offset += len(hits)
            if offset >= total:
                return False, out

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
