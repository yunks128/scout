from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from typing import Any

import httpx
from selectolax.parser import HTMLParser

from scout.adapters.base import Adapter
from scout.storage.db import Notice

log = logging.getLogger(__name__)

LISTING_URL = "https://science.osti.gov/grants/FOAs/Open"

FOA_NUMBER_RE = re.compile(r"DE-FOA-\d{7}", re.IGNORECASE)


class DoeScAdapter(Adapter):
    """Scrape the DOE Office of Science open-FOA listing.

    The page structure is a table of open FOAs with title, number, dates, and a
    link to the detail page. We treat the FOA number as notice_id and the row
    HTML as the raw payload so downstream can reprocess if the scrape shape
    changes.
    """

    source = "doe.sc"

    def fetch(self) -> Iterator[tuple[str, dict]]:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            r = client.get(LISTING_URL, headers={"User-Agent": "scout/0.1 (+github.com/yunks128/scout)"})
            r.raise_for_status()
            tree = HTMLParser(r.text)
            for row in tree.css("table tr"):
                cells = row.css("td")
                if not cells:
                    continue
                text_blob = row.text(separator=" ", strip=True)
                link = row.css_first("a[href]")
                href = link.attributes.get("href") if link else None
                foa_match = FOA_NUMBER_RE.search(text_blob)
                if not foa_match:
                    continue
                notice_id = foa_match.group(0).upper()
                payload = {
                    "foa_number": notice_id,
                    "row_text": text_blob,
                    "detail_url": _absolute(href),
                    "cells": [c.text(separator=" ", strip=True) for c in cells],
                }
                yield notice_id, payload

    def normalize(self, notice_id: str, payload: dict[str, Any], content_hash: str) -> Notice | None:
        cells = payload.get("cells") or []
        title = ""
        dates: list[str] = []
        for c in cells:
            if c and c.upper() != notice_id and not title:
                title = c
            dates += _extract_dates(c)
        if not title:
            title = payload.get("row_text", "")[:200]
        response_deadline = dates[-1] if dates else None
        posted = dates[0] if dates else None
        return Notice(
            source=self.source,
            notice_id=notice_id,
            content_hash=content_hash,
            title=title,
            agency="DOE Office of Science",
            description=payload.get("row_text"),
            posted_date=posted,
            response_deadline=response_deadline,
            url=payload.get("detail_url") or LISTING_URL,
        )


def _absolute(href: str | None) -> str | None:
    if not href:
        return None
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return f"https://science.osti.gov{href}"
    return f"https://science.osti.gov/{href}"


_DATE_RE = re.compile(
    r"\b(?:"
    r"\d{1,2}/\d{1,2}/\d{2,4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}"
    r")\b",
    re.IGNORECASE,
)


def _extract_dates(text: str) -> list[str]:
    return _DATE_RE.findall(text or "")
