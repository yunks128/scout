from __future__ import annotations

import logging

import click

from scout.adapters.doe_sc import DoeScAdapter
from scout.adapters.grants_gov import GrantsGovAdapter
from scout.adapters.sam_gov import SamGovAdapter
from scout.alerting.digest import write_digest
from scout.alerting.email import send_daily
from scout.pipeline import classify_unclassified
from scout.storage.db import DB
from scout.web.generate import build as build_site

ADAPTERS = {
    "grants.gov": GrantsGovAdapter,
    "sam.gov": SamGovAdapter,
    "doe.sc": DoeScAdapter,
}


@click.group()
@click.option("--verbose", "-v", is_flag=True)
def main(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )


@main.command()
@click.option("--source", "sources", multiple=True, type=click.Choice(list(ADAPTERS)))
def ingest(sources: tuple[str, ...]) -> None:
    """Fetch from all configured sources (or a subset) and store notices."""
    db = DB()
    chosen = sources or tuple(ADAPTERS.keys())
    for name in chosen:
        adapter = ADAPTERS[name]()
        total, new = adapter.run(db)
        click.echo(f"[{name}] total={total} new_or_amended={new}")


@main.command()
def classify() -> None:
    """Run lexical gate + LLM classifier on everything unclassified."""
    db = DB()
    seen, llm = classify_unclassified(db)
    click.echo(f"classified seen={seen} llm_calls={llm}")


@main.command()
@click.option("--include-archive", is_flag=True)
@click.option("--out", "out_dir", default="docs")
def digest(include_archive: bool, out_dir: str) -> None:
    """Render markdown digest of act-now and review lanes."""
    db = DB()
    path = write_digest(db, out_dir=out_dir, include_archive=include_archive)
    click.echo(str(path))


@main.command()
@click.option("--out", "out_dir", default="site")
def web(out_dir: str) -> None:
    """Generate the static site (index.html) from the current DB."""
    db = DB()
    path = build_site(db, out_dir=out_dir)
    click.echo(str(path))


@main.command()
@click.option("--always", is_flag=True, help="Send even when no act-now or review items.")
def email(always: bool) -> None:
    """Send today's digest to Buttondown subscribers. No-op without BUTTONDOWN_API_KEY."""
    db = DB()
    result = send_daily(db, only_if_changes=not always)
    if result.sent:
        click.echo(f"email sent id={result.email_id}")
    else:
        click.echo(f"email skipped: {result.reason}")


@main.command()
@click.option("--source", "sources", multiple=True, type=click.Choice(list(ADAPTERS)))
def run(sources: tuple[str, ...]) -> None:
    """Convenience: ingest + classify + digest + web in one call."""
    db = DB()
    chosen = sources or tuple(ADAPTERS.keys())
    for name in chosen:
        total, new = ADAPTERS[name]().run(db)
        click.echo(f"[{name}] total={total} new_or_amended={new}")
    seen, llm = classify_unclassified(db)
    click.echo(f"classified seen={seen} llm_calls={llm}")
    click.echo(str(write_digest(db)))
    click.echo(str(build_site(db)))
