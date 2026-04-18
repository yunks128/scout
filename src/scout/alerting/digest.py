from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scout.storage.db import DB

LANES = ["act-now", "review", "archive"]
LANE_HEADERS = {
    "act-now": "## Act now",
    "review": "## Review",
    "archive": "## Archive (summary only)",
}


def render(db: DB, include_archive: bool = False) -> str:
    lanes = LANES if include_archive else ["act-now", "review"]
    rows = db.digest_rows(lanes)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"# Scout digest — {today}", ""]
    if not rows:
        lines.append("_No classified opportunities in act-now or review lanes._")
        return "\n".join(lines) + "\n"

    counts = lane_counts(rows, lanes)
    lines.append(" · ".join(f"**{lane}**: {counts.get(lane, 0)}" for lane in lanes))
    lines.append("")
    lines.extend(render_cards(rows, lanes))
    return "\n".join(lines) + "\n"


def render_cards(rows, lanes) -> list[str]:
    """Just the per-lane card markdown, no top header or counter line.
    Used both by the file digest and by the email sender so they stay aligned."""
    out: list[str] = []
    for lane in lanes:
        subset = [r for r in rows if r["lane"] == lane]
        if not subset:
            continue
        out.append(LANE_HEADERS[lane])
        out.append("")
        for r in subset:
            out.extend(_format_row(r, verbose=(lane != "archive")))
            out.append("")
    return out


def lane_counts(rows, lanes) -> dict[str, int]:
    counts = {lane: 0 for lane in lanes}
    for r in rows:
        counts[r["lane"]] = counts.get(r["lane"], 0) + 1
    return counts


def _format_row(r, verbose: bool) -> list[str]:
    themes = json.loads(r["llm_themes"] or "[]")
    matches = json.loads(r["lexical_matches"] or "[]")
    header = f"### [{r['notice_id']}] {r['title']}"
    meta = [
        f"- **Source**: {r['source']}",
        f"- **Agency**: {r['agency'] or 'unknown'}",
        f"- **Posted**: {r['posted_date'] or '—'} · **Deadline**: {r['response_deadline'] or '—'}",
        f"- **URL**: {r['url'] or '—'}",
    ]
    if r["llm_relevance"] is not None:
        meta.append(
            f"- **Relevance**: {r['llm_relevance']}/10 · **FFRDC**: {r['ffrdc_eligible']} "
            f"· **Cost share**: {r['cost_share']} · **Foreign entity**: {r['foreign_entity']}"
        )
    if matches:
        meta.append(f"- **Lexical hits**: {', '.join(matches[:10])} (score {r['lexical_score']:.1f})")
    out = [header, *meta]
    if verbose:
        if themes:
            out.append(f"- **Themes**: {', '.join(themes)}")
        if r["llm_fit_notes"]:
            out.append(f"- **Fit**: {r['llm_fit_notes']}")
        if r["eligibility_quote"]:
            out.append(f"- **Eligibility quote**: > {r['eligibility_quote']}")
    return out


def write_digest(db: DB, out_dir: str | Path = "docs", include_archive: bool = False) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = out / f"digest_{today}.md"
    path.write_text(render(db, include_archive=include_archive))
    return path
