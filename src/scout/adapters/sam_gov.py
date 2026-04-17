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
        naics_codes = ",".join(n["code"] for n in naics_psc()["naics"])
        with httpx.Client(timeout=30.0) as client:
            offset = 0
            while True:
                params = {
                    "api_key": self.api_key,
                    "postedFrom": start.strftime("%m/%d/%Y"),
                    "postedTo": end.strftime("%m/%d/%Y"),
                    "ncode": naics_codes,
                    "limit": self.page_size,
                    "offset": offset,
                }
                r = client.get(SEARCH_URL, params=params)
                if r.status_code == 429:
                    log.warning("SAM.gov rate limited; stopping pagination")
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
                    yield str(nid), hit
                total = data.get("totalRecords", 0)
                offset += len(hits)
                if offset >= total:
                    return

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
            description=payload.get("description"),
            posted_date=payload.get("postedDate"),
            response_deadline=payload.get("responseDeadLine"),
            naics=naics,
            psc=psc_val,
            url=payload.get("uiLink"),
            last_modified=payload.get("updatedDate"),
        )
