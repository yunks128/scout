from __future__ import annotations

import html
import json
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

from scout.storage.db import DB

LANES = ("act-now", "review", "archive")
LANE_LABEL = {"act-now": "Act now", "review": "Review", "archive": "Archive"}


def build(db: DB, out_dir: str | Path = "site") -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = _all_rows(db)
    counts = _counts(rows)
    generated = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d %H:%M %Z")
    sources = sorted({r["source"] for r in rows})
    html_doc = _render_index(rows, counts, generated, sources)
    index = out / "index.html"
    index.write_text(html_doc)
    return index


def _all_rows(db: DB) -> list[sqlite3.Row]:
    # Dedup on (source, notice_id) by taking the latest content_hash — this is
    # what makes an amendment visible as one card with updated info rather than
    # three historical snapshots of the same FOA.
    return db.latest_rows()


def _counts(rows: list[sqlite3.Row]) -> dict[str, int]:
    out = {lane: 0 for lane in LANES}
    for r in rows:
        out[r["lane"]] = out.get(r["lane"], 0) + 1
    return out


def _render_index(rows, counts, generated: str, sources: list[str]) -> str:
    cards_actnow = "\n".join(_card(r) for r in rows if r["lane"] == "act-now")
    cards_review = "\n".join(_card(r) for r in rows if r["lane"] == "review")
    archive_rows = "\n".join(_archive_row(r) for r in rows if r["lane"] == "archive")
    actnow_section = cards_actnow or '<p class="empty">No opportunities in this lane.</p>'
    review_section = cards_review or '<p class="empty">No opportunities in this lane.</p>'
    archive_section = (
        f"<details><summary>Show {counts['archive']} archived notices</summary>"
        f"<table class='archive'><thead><tr><th>ID</th><th>Source</th><th>Title</th>"
        f"<th>Deadline</th><th>Score</th></tr></thead><tbody>{archive_rows}</tbody></table></details>"
        if archive_rows
        else ""
    )
    sources_str = ", ".join(sources) or "(none yet)"
    return _PAGE.format(
        generated=html.escape(generated),
        sources=html.escape(sources_str),
        c_actnow=counts["act-now"],
        c_review=counts["review"],
        c_archive=counts["archive"],
        total=sum(counts.values()),
        actnow=actnow_section,
        review=review_section,
        archive=archive_section,
        subscribe=_subscribe_block(),
    )


def _subscribe_block() -> str:
    user = os.environ.get("SCOUT_BUTTONDOWN_USER", "").strip()
    if not user:
        return ""
    action = f"https://buttondown.email/api/emails/embed-subscribe/{html.escape(user)}"
    public_url = f"https://buttondown.email/{html.escape(user)}"
    return f"""
<section class="subscribe">
  <h2>Subscribe to the daily digest</h2>
  <p>Get this page's act-now and review lanes in your inbox each morning. Skips empty days.</p>
  <form action="{action}" method="post" target="popupwindow"
        onsubmit="window.open('{public_url}', 'popupwindow')" class="embeddable-buttondown-form">
    <input type="email" name="email" placeholder="you@example.com" required>
    <input type="hidden" value="1" name="embed">
    <button type="submit">Subscribe</button>
  </form>
  <p class="fineprint">Delivered via <a href="{public_url}" target="_blank" rel="noopener">Buttondown</a>. Unsubscribe any time from the email footer.</p>
</section>
""".strip()


def _card(r: sqlite3.Row) -> str:
    themes = json.loads(r["llm_themes"] or "[]")
    matches = json.loads(r["lexical_matches"] or "[]")
    theme_pills = "".join(f'<span class="pill">{html.escape(t)}</span>' for t in themes[:6])
    match_pills = "".join(f'<span class="pill pill-lex">{html.escape(m)}</span>' for m in matches[:8])
    ffrdc = r["ffrdc_eligible"] or "unclear"
    rel = r["llm_relevance"]
    rel_str = f"{rel}/10" if rel is not None else "—"
    deadline = r["response_deadline"] or "—"
    posted = r["posted_date"] or "—"
    agency = r["agency"] or "unknown"
    notes = html.escape(r["llm_fit_notes"] or "")
    quote = html.escape(r["eligibility_quote"] or "")
    url = r["url"] or "#"
    return f"""
<article class="card lane-{html.escape(r['lane'])}">
  <header>
    <span class="badge badge-ffrdc badge-{html.escape(ffrdc)}">FFRDC: {html.escape(ffrdc)}</span>
    <span class="badge badge-rel">Relevance {rel_str}</span>
    <span class="badge badge-source">{html.escape(r['source'])}</span>
  </header>
  <h3><a href="{html.escape(url)}" target="_blank" rel="noopener">{html.escape(r['title'])}</a></h3>
  <p class="meta">
    <strong>{html.escape(r['notice_id'])}</strong> · {html.escape(agency)}<br>
    Posted {html.escape(posted)} · <strong>Deadline {html.escape(deadline)}</strong>
  </p>
  {f'<p class="notes">{notes}</p>' if notes else ''}
  {f'<div class="pills">{theme_pills}</div>' if theme_pills else ''}
  {f'<div class="pills lex">{match_pills}</div>' if match_pills else ''}
  {f'<blockquote class="elig">{quote}</blockquote>' if quote else ''}
</article>
""".strip()


def _archive_row(r: sqlite3.Row) -> str:
    rel = r["llm_relevance"]
    lex = r["lexical_score"] if r["lexical_score"] is not None else 0.0
    if rel is not None:
        score_str = f"{rel}/10"
    else:
        # No LLM score: item failed the lexical gate. Showing the raw lexical
        # score (even 0.0) is more honest than '—' and hints at why it archived.
        score_str = f'<span title="Lexical score — failed the gate, so no LLM call">{lex:.1f} <em>lex</em></span>'
    deadline = r["response_deadline"] or ""
    deadline_str = html.escape(deadline) if deadline else '<span class="muted">Rolling</span>'
    url = r["url"] or "#"
    title = html.escape(r["title"] or "")
    return (
        f"<tr><td><code>{html.escape(r['notice_id'])}</code></td>"
        f"<td>{html.escape(r['source'])}</td>"
        f'<td><a href="{html.escape(url)}" target="_blank" rel="noopener">{title}</a></td>'
        f"<td>{deadline_str}</td>"
        f"<td>{score_str}</td></tr>"
    )


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SCOUT — Solicitation &amp; Call Opportunity Understanding Tool</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http%3A//www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%2358a6ff'/%3E%3Ctext x='50%25' y='58%25' text-anchor='middle' dominant-baseline='middle' font-family='system-ui,sans-serif' font-size='20' font-weight='700' fill='%230d1117'%3ES%3C/text%3E%3C/svg%3E">
<style>
:root {{
  --bg: #0d1117;
  --panel: #161b22;
  --border: #30363d;
  --text: #e6edf3;
  --muted: #8b949e;
  --accent: #58a6ff;
  --warn: #d29922;
  --danger: #f85149;
  --ok: #3fb950;
  --chip: #1f2937;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.5;
}}
header.site {{
  padding: 2rem 1.25rem 1rem;
  border-bottom: 1px solid var(--border);
}}
header.site h1 {{ margin: 0; font-size: 1.75rem; letter-spacing: 0.12em; font-weight: 700; }}
header.site p.affiliation {{ margin: 0 0 .35rem; font-size: .7rem; font-weight: 600; letter-spacing: .14em; text-transform: uppercase; color: var(--muted); }}
header.site p.expansion {{ margin: .15rem 0 .25rem; color: var(--text); font-size: 1rem; font-weight: 500; }}
header.site p.sub {{ margin: .25rem 0 0; color: var(--muted); font-size: .9rem; }}
.counts {{ display: flex; flex-wrap: wrap; gap: 1rem; padding: 1rem 1.25rem; border-bottom: 1px solid var(--border); background: var(--panel); }}
.count {{ display: flex; flex-direction: column; }}
.count .num {{ font-size: 1.5rem; font-weight: 600; }}
.count .lbl {{ font-size: .75rem; text-transform: uppercase; color: var(--muted); letter-spacing: .05em; }}
.count.actnow .num {{ color: var(--danger); }}
.count.review .num {{ color: var(--warn); }}
.count.archive .num {{ color: var(--muted); }}
main {{ max-width: 1080px; margin: 0 auto; padding: 1.5rem 1.25rem 4rem; }}
section {{ margin-bottom: 2.5rem; }}
section h2 {{
  font-size: 1.1rem; text-transform: uppercase; letter-spacing: .08em;
  border-bottom: 1px solid var(--border); padding-bottom: .5rem; margin-bottom: 1rem;
}}
.card {{
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1.25rem;
  margin-bottom: 1rem;
}}
.card.lane-act-now {{ border-left: 4px solid var(--danger); }}
.card.lane-review {{ border-left: 4px solid var(--warn); }}
.card header {{ display: flex; flex-wrap: wrap; gap: .5rem; margin-bottom: .5rem; }}
.card h3 {{ margin: .25rem 0 .5rem; font-size: 1.05rem; font-weight: 600; line-height: 1.3; }}
.card h3 a {{ color: var(--accent); text-decoration: none; }}
.card h3 a:hover {{ text-decoration: underline; }}
.card .meta {{ color: var(--muted); font-size: .85rem; margin: .25rem 0 .75rem; }}
.card .notes {{ margin: .5rem 0; font-size: .95rem; }}
.card blockquote.elig {{
  margin: .75rem 0 0; padding: .5rem .75rem;
  border-left: 3px solid var(--border);
  color: var(--muted); font-size: .85rem; background: rgba(255,255,255,.02);
}}
.badge {{ font-size: .7rem; padding: .15rem .5rem; border-radius: 10px; background: var(--chip); border: 1px solid var(--border); }}
.badge-ffrdc.badge-yes {{ background: rgba(63,185,80,.15); color: var(--ok); border-color: var(--ok); }}
.badge-ffrdc.badge-as_partner {{ background: rgba(88,166,255,.15); color: var(--accent); border-color: var(--accent); }}
.badge-ffrdc.badge-no {{ background: rgba(248,81,73,.15); color: var(--danger); border-color: var(--danger); }}
.badge-ffrdc.badge-unclear {{ background: rgba(210,153,34,.15); color: var(--warn); border-color: var(--warn); }}
.badge-rel {{ color: var(--accent); }}
.badge-source {{ font-family: ui-monospace, monospace; }}
.pills {{ display: flex; flex-wrap: wrap; gap: .25rem; margin-top: .5rem; }}
.pill {{ font-size: .75rem; padding: .1rem .5rem; background: var(--chip); border-radius: 10px; color: var(--text); }}
.pill-lex {{ opacity: .7; font-family: ui-monospace, monospace; }}
.empty {{ color: var(--muted); font-style: italic; }}
details {{ margin-top: 1rem; }}
details summary {{ cursor: pointer; color: var(--muted); }}
table.archive {{ width: 100%; border-collapse: collapse; margin-top: 1rem; font-size: .85rem; }}
table.archive th, table.archive td {{ padding: .4rem .5rem; border-bottom: 1px solid var(--border); text-align: left; }}
table.archive th {{ color: var(--muted); font-weight: 500; text-transform: uppercase; letter-spacing: .05em; font-size: .7rem; }}
table.archive code {{ font-size: .8rem; color: var(--muted); }}
table.archive em {{ font-style: normal; color: var(--muted); font-size: .7rem; margin-left: .15rem; }}
.muted {{ color: var(--muted); font-style: italic; }}
footer {{ max-width: 1080px; margin: 0 auto; padding: 2rem 1.25rem; color: var(--muted); font-size: .8rem; border-top: 1px solid var(--border); }}
footer a {{ color: var(--accent); }}
footer p {{ margin: .5rem 0; }}
footer p.legal {{ font-size: .7rem; line-height: 1.4; opacity: .75; }}
.subscribe {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 1.25rem; margin-top: 1rem; }}
.subscribe h2 {{ border: 0; margin: 0 0 .5rem; font-size: 1rem; text-transform: uppercase; letter-spacing: .08em; }}
.subscribe p {{ margin: .25rem 0 .75rem; color: var(--muted); font-size: .9rem; }}
.subscribe form {{ display: flex; gap: .5rem; flex-wrap: wrap; }}
.subscribe input[type=email] {{ flex: 1; min-width: 220px; padding: .5rem .75rem; background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 6px; font-size: .95rem; }}
.subscribe button {{ padding: .5rem 1rem; background: var(--accent); color: var(--bg); border: 0; border-radius: 6px; font-weight: 600; cursor: pointer; }}
.subscribe button:hover {{ filter: brightness(1.1); }}
.subscribe .fineprint {{ font-size: .75rem; margin-top: .5rem; }}
</style>
</head>
<body>
<header class="site">
  <p class="affiliation">JPL · Section 345</p>
  <h1>SCOUT</h1>
  <p class="expansion">Solicitation &amp; Call Opportunity Understanding Tool</p>
  <p class="sub">Power systems funding opportunities, surfaced and ranked daily · generated {generated} · sources: {sources}</p>
</header>
<div class="counts">
  <div class="count actnow"><span class="num">{c_actnow}</span><span class="lbl">Act now</span></div>
  <div class="count review"><span class="num">{c_review}</span><span class="lbl">Review</span></div>
  <div class="count archive"><span class="num">{c_archive}</span><span class="lbl">Archive</span></div>
  <div class="count total"><span class="num">{total}</span><span class="lbl">Total classified</span></div>
</div>
<main>
  <section>
    <h2>Act now</h2>
    {actnow}
  </section>
  <section>
    <h2>Review</h2>
    {review}
  </section>
  <section>
    <h2>Archive</h2>
    {archive}
  </section>
  {subscribe}
</main>
<footer>
  <p>SCOUT · Monitoring SAM.gov, Grants.gov, and DOE Office of Science.</p>
  <p class="legal">
    Copyright 2026, by the California Institute of Technology. ALL RIGHTS RESERVED.
    United States Government Sponsorship acknowledged. Any commercial use must be
    negotiated with the Office of Technology Transfer at the California Institute
    of Technology.
  </p>
  <p class="legal">
    This software may be subject to U.S. export control laws. By accepting this
    software, the user agrees to comply with all applicable U.S. export laws and
    regulations. User has the responsibility to obtain export licenses, or other
    export authority as may be required before exporting such information to
    foreign countries or providing access to foreign persons.
  </p>
</footer>
</body>
</html>
"""
