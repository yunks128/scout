from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from typing import Any

import httpx
from selectolax.parser import HTMLParser, Node

from scout.adapters.base import Adapter
from scout.storage.db import Notice

log = logging.getLogger(__name__)

LISTING_URL = "https://science.osti.gov/grants/FOAs/Open"

FOA_NUMBER_RE = re.compile(r"DE-FOA-\d{7}", re.IGNORECASE)


class DoeScAdapter(Adapter):
    """Scrape the DOE Office of Science open-FOA listing.

    Each FOA is rendered as <article class="article_preview funding_preview">
    with labeled CSS classes for title, announcement number, post date, close
    date, and notes. We key notice_id on the announcement number.
    """

    source = "doe.sc"

    def fetch(self) -> Iterator[tuple[str, dict]]:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            r = client.get(LISTING_URL, headers={"User-Agent": "scout/0.1 (+github.com/yunks128/scout)"})
            r.raise_for_status()
            tree = HTMLParser(r.text)
            for art in tree.css("article.funding_preview"):
                payload = _parse_article(art)
                notice_id = payload.get("foa_number")
                if not notice_id:
                    continue
                yield notice_id, payload

    def normalize(self, notice_id: str, payload: dict[str, Any], content_hash: str) -> Notice | None:
        title = payload.get("title") or notice_id
        description = _description_text(payload)
        return Notice(
            source=self.source,
            notice_id=notice_id,
            content_hash=content_hash,
            title=title,
            agency="DOE Office of Science",
            description=description,
            posted_date=payload.get("post_date"),
            response_deadline=payload.get("close_date"),
            url=payload.get("detail_url") or LISTING_URL,
            last_modified=payload.get("updated"),
        )


def _parse_article(art: Node) -> dict[str, Any]:
    title_link = art.css_first("h3.title a")
    title = (title_link.text(strip=True) if title_link else "") or ""
    detail_href = title_link.attributes.get("href") if title_link else None

    ann_block = art.css_first("div.announcement_number")
    ann_text = ann_block.text(separator=" ", strip=True) if ann_block else ""
    foa_match = FOA_NUMBER_RE.search(ann_text)
    foa_number = foa_match.group(0).upper() if foa_match else None
    updated_match = re.search(r"updated\s+([A-Za-z]+\s+\d+,\s+\d{4})", ann_text)
    updated = updated_match.group(1) if updated_match else None

    post_date = _after_label(art, "div.funding_postdate")
    close_date = _after_label(art, "div.funding_closedate")
    fiscal_year = _after_label(art, "div.funding_fiscalyear")

    notes_block = art.css_first("div.funding_notes")
    notes_text = notes_block.text(separator="\n", strip=True) if notes_block else ""

    return {
        "foa_number": foa_number,
        "title": title,
        "detail_url": _absolute(detail_href),
        "post_date": post_date,
        "close_date": close_date,
        "fiscal_year": fiscal_year,
        "updated": updated,
        "notes": notes_text,
    }


_LABEL_RE = re.compile(r"^\s*(?:Announcement Number|Fiscal Year|Post Date|Close Date|Modification Date)\s*:\s*", re.I)


def _after_label(art: Node, selector: str) -> str | None:
    block = art.css_first(selector)
    if not block:
        return None
    full = block.text(separator=" ", strip=True)
    stripped = _LABEL_RE.sub("", full).strip()
    return stripped or None


def _description_text(payload: dict[str, Any]) -> str:
    parts = [payload.get("title") or "", payload.get("notes") or ""]
    return "\n\n".join(p for p in parts if p).strip() or None  # type: ignore[return-value]


def _absolute(href: str | None) -> str | None:
    if not href:
        return None
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return f"https://science.osti.gov{href}"
    return f"https://science.osti.gov/{href}"
