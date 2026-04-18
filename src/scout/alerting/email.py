from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from scout.alerting.digest import lane_counts, render_cards
from scout.storage.db import DB

log = logging.getLogger(__name__)

BUTTONDOWN_API = "https://api.buttondown.email/v1/emails"
DASHBOARD_URL = "https://yunks128.github.io/scout/"

EMAIL_LANES = ["act-now", "review"]


@dataclass
class SendResult:
    sent: bool
    reason: str
    email_id: str | None = None


def send_daily(db: DB, only_if_changes: bool = True) -> SendResult:
    """Send today's digest to all Buttondown subscribers.

    Returns SendResult(sent=False, reason=...) when we intentionally skip
    (no API key, no lane items, etc.) — callers should not treat that as an error.
    """
    api_key = os.environ.get("BUTTONDOWN_API_KEY")
    if not api_key:
        return SendResult(sent=False, reason="BUTTONDOWN_API_KEY not set")

    rows = db.digest_rows(EMAIL_LANES)
    if only_if_changes and not rows:
        return SendResult(sent=False, reason="no act-now or review items today")

    counts = lane_counts(rows, EMAIL_LANES)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    subject = _subject(counts, today)
    body_markdown = _compose_body(rows, counts, today)

    payload = {
        "subject": subject,
        "body": body_markdown,
        "email_type": "public",
        # Buttondown's /v1/emails defaults to 'draft', which silently parks the
        # email in the dashboard. 'about_to_send' queues it for immediate
        # delivery to confirmed subscribers.
        "status": "about_to_send",
    }
    headers = {
        "Authorization": f"Token {api_key}",
        # Required once per API key to acknowledge that status='about_to_send'
        # will actually deliver email rather than sit as a draft.
        "X-Buttondown-Live-Dangerously": "true",
    }
    with httpx.Client(timeout=30.0) as client:
        r = client.post(BUTTONDOWN_API, json=payload, headers=headers)
        if r.status_code == 401:
            return SendResult(sent=False, reason="Buttondown auth rejected (check API key)")
        if r.status_code >= 400:
            log.error("Buttondown %s: %s", r.status_code, r.text[:1000])
            r.raise_for_status()
        data = r.json()
    return SendResult(sent=True, reason="ok", email_id=data.get("id"))


def _subject(counts: dict[str, int], today: str) -> str:
    """Action-first subject — what the reader needs to do today."""
    a, r = counts.get("act-now", 0), counts.get("review", 0)
    if a and r:
        return f"SCOUT · {a} to act on, {r} to review · {today}"
    if a:
        return f"SCOUT · {a} to act on · {today}"
    return f"SCOUT · {r} to review · {today}"


def _compose_body(rows, counts: dict[str, int], today: str) -> str:
    """Masthead + lane legend + card content.

    Note we do not include the 'SCOUT' title twice — Buttondown already renders
    the subject above the body, so we lead with the tagline and counters.
    """
    a, r = counts.get("act-now", 0), counts.get("review", 0)
    lines = [
        "**Power systems funding opportunities, surfaced and ranked daily.**",
        "",
        f"_{today} · {a} act-now · {r} review · [live dashboard →]({DASHBOARD_URL})_",
        "",
        "---",
        "",
        "🔴 **Act now** — high fit, FFRDC-eligible, deadline within 30 days.  ",
        "🟡 **Review** — worth a human look; eligibility often needs confirming from the FOA text.  ",
        "⚫ _Archive — filtered out by portfolio policy, not shown here._",
        "",
        "---",
        "",
    ]
    lines.extend(render_cards(rows, EMAIL_LANES))
    lines.extend(
        [
            "",
            "---",
            "",
            f"Filtered for JPL's power-systems portfolio · [full archive and active list →]({DASHBOARD_URL})",
        ]
    )
    return "\n".join(lines) + "\n"
