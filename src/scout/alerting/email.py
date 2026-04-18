from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from scout.alerting.digest import render as render_digest
from scout.storage.db import DB

log = logging.getLogger(__name__)

BUTTONDOWN_API = "https://api.buttondown.email/v1/emails"


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

    rows = db.digest_rows(["act-now", "review"])
    if only_if_changes and not rows:
        return SendResult(sent=False, reason="no act-now or review items today")

    counts = {"act-now": 0, "review": 0}
    for r in rows:
        counts[r["lane"]] = counts.get(r["lane"], 0) + 1

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    subject = (
        f"SCOUT — {today} digest · {counts['act-now']} act-now · {counts['review']} review"
    )
    body_markdown = render_digest(db, include_archive=False)

    payload = {
        "subject": subject,
        "body": body_markdown,
        "email_type": "public",
    }
    headers = {"Authorization": f"Token {api_key}"}
    with httpx.Client(timeout=30.0) as client:
        r = client.post(BUTTONDOWN_API, json=payload, headers=headers)
        if r.status_code == 401:
            return SendResult(sent=False, reason="Buttondown auth rejected (check API key)")
        r.raise_for_status()
        data = r.json()
    return SendResult(sent=True, reason="ok", email_id=data.get("id"))
