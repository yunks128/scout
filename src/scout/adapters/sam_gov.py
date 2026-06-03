from __future__ import annotations

import io
import logging
import os
import zipfile
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import httpx

from scout.adapters.base import Adapter
from scout.config import naics_psc
from scout.storage.db import Notice

log = logging.getLogger(__name__)

SEARCH_URL = "https://api.sam.gov/prod/opportunities/v2/search"

# Maximum bytes to download per attachment (5 MB).
_ATTACH_MAX_BYTES = 5 * 1024 * 1024
# Maximum total chars stored across all attachments for one notice.
_ATTACH_TOTAL_CAP = 200_000


class SamGovAdapter(Adapter):
    source = "sam.gov"

    # Narrow NAICS set to the highest-signal Section 345 codes. SI-NONFED role
    # has a tight daily quota; pick space-vehicle + R&D + engineering services
    # as the three that cover most 345-relevant SAM.gov postings.
    CORE_NAICS = ["336414", "541715", "541330"]

    # Agencies that publish Proposer's Days and pre-BAA notices as Special
    # Notices or Presolicitations. These notices frequently have no NAICS code,
    # so NAICS-filtered queries miss them entirely.
    PREREQ_AGENCIES = ["DARPA", "AFOSR", "ONR", "ARL"]

    # SAM.gov ptype codes for pre-solicitation signals.
    # s = Special Notice (Proposer's Days, industry days, pre-BAA)
    # p = Presolicitation
    PREREQ_PTYPES = ["s", "p"]

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
                stop, hits = self._paginate(client, start, end, ncode=ncode)
                yield from hits
                if stop:
                    log.warning("SAM.gov quota exhausted; stopping all ingestion")
                    return

            # Second pass: scan for Special Notices and Presolicitations from
            # high-value agencies. These pre-BAA signals lack NAICS codes and
            # would be invisible to the NAICS-only queries above.
            for agency in self.PREREQ_AGENCIES:
                for ptype in self.PREREQ_PTYPES:
                    stop, hits = self._paginate(
                        client, start, end, ptype=ptype, keyword=agency
                    )
                    yield from hits
                    if stop:
                        log.warning("SAM.gov quota exhausted; stopping all ingestion")
                        return

    def _paginate(
        self,
        client: httpx.Client,
        start,
        end,
        ncode: str | None = None,
        ptype: str | None = None,
        keyword: str | None = None,
    ) -> tuple[bool, list[tuple[str, dict]]]:
        """Return (hard_stop, results). hard_stop=True on 429 so the outer loop bails."""
        out: list[tuple[str, dict]] = []
        offset = 0
        while True:
            params: dict[str, Any] = {
                "api_key": self.api_key,
                "postedFrom": start.strftime("%m/%d/%Y"),
                "postedTo": end.strftime("%m/%d/%Y"),
                "limit": self.page_size,
                "offset": offset,
            }
            if ncode:
                params["ncode"] = ncode
            if ptype:
                params["ptype"] = ptype
            if keyword:
                params["keyword"] = keyword
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
                self._enrich(client, hit)
                out.append((str(nid), hit))
            total = data.get("totalRecords", 0)
            offset += len(hits)
            if offset >= total:
                return False, out

    # ------------------------------------------------------------------
    # Enrichment: description text + attachment text
    # ------------------------------------------------------------------

    def _enrich(self, client: httpx.Client, hit: dict[str, Any]) -> None:
        """Populate hit['description_text'] with notice body + attachment text."""
        self._fetch_description(client, hit)
        self._fetch_attachments(client, hit)

    def _fetch_description(self, client: httpx.Client, hit: dict[str, Any]) -> None:
        """Resolve the SAM.gov noticedesc URL in hit['description'] to plain text."""
        desc = hit.get("description")
        if not isinstance(desc, str) or not desc.startswith("http"):
            return
        try:
            r = client.get(desc, params={"api_key": self.api_key})
            if r.status_code != 200:
                log.info(
                    "noticedesc fetch returned %s for %s", r.status_code, hit.get("noticeId")
                )
                return
            ctype = r.headers.get("content-type", "")
            if "json" in ctype:
                body = r.json()
                text = body.get("description") if isinstance(body, dict) else str(body)
            else:
                text = r.text
            if text:
                hit["description_text"] = text
        except Exception:
            log.info(
                "noticedesc fetch failed for %s", hit.get("noticeId"), exc_info=True
            )

    def _fetch_attachments(self, client: httpx.Client, hit: dict[str, Any]) -> None:
        """Download resourceLinks and append extracted text to hit['description_text']."""
        links: list[str] = hit.get("resourceLinks") or []
        if not links:
            return

        parts: list[str] = []
        total_chars = 0

        for url in links:
            if total_chars >= _ATTACH_TOTAL_CAP:
                break
            try:
                r = client.get(
                    url,
                    params={"api_key": self.api_key},
                    follow_redirects=True,
                    timeout=60.0,
                )
                if r.status_code != 200:
                    log.debug("Attachment fetch returned %s for %s", r.status_code, url)
                    continue
                if len(r.content) > _ATTACH_MAX_BYTES:
                    log.debug("Skipping oversized attachment at %s (%d bytes)", url, len(r.content))
                    continue

                filename = _filename_from_headers(r.headers)
                ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

                if ext == "pdf":
                    text = _extract_pdf_text(r.content)
                elif ext == "docx":
                    text = _extract_docx_text(r.content)
                elif ext in ("txt", "html", "htm"):
                    text = r.content.decode("utf-8", errors="replace")
                else:
                    log.debug("Skipping unsupported attachment type .%s (%s)", ext, filename)
                    continue

                text = text.strip()
                if not text:
                    continue

                label = f"[ATTACHMENT: {filename}]" if filename else "[ATTACHMENT]"
                parts.append(f"{label}\n{text}")
                total_chars += len(text)
                log.info("Extracted %d chars from attachment %s", len(text), filename)

            except Exception:
                log.info("Failed to fetch/parse attachment %s", url, exc_info=True)

        if not parts:
            return

        existing = hit.get("description_text") or ""
        separator = "\n\n" if existing else ""
        hit["description_text"] = existing + separator + "\n\n".join(parts)

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


# ------------------------------------------------------------------
# Text extraction helpers
# ------------------------------------------------------------------

def _filename_from_headers(headers: httpx.Headers) -> str:
    cd = headers.get("content-disposition", "")
    if "filename=" in cd:
        return cd.split("filename=")[-1].strip().strip('"').strip("'")
    return ""


def _extract_pdf_text(content: bytes) -> str:
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        log.debug("pypdf extraction failed", exc_info=True)
        return ""


def _extract_docx_text(content: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            xml_bytes = z.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        texts = [elem.text for elem in root.iter(f"{{{W}}}t") if elem.text]
        return " ".join(texts)
    except Exception:
        log.debug("docx extraction failed", exc_info=True)
        return ""
