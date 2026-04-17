from __future__ import annotations

import logging

import click

from scout.adapters.doe_sc import DoeScAdapter
from scout.adapters.grants_gov import GrantsGovAdapter
from scout.adapters.sam_gov import SamGovAdapter
from scout.alerting.digest import write_digest
from scout.pipeline import classify_unclassified
from scout.storage.db import DB

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
@click.option("--source", "sources", multiple=True, type=click.Choice(list(ADAPTERS)))
def run(sources: tuple[str, ...]) -> None:
    """Convenience: ingest + classify + digest in one call."""
    db = DB()
    chosen = sources or tuple(ADAPTERS.keys())
    for name in chosen:
        total, new = ADAPTERS[name]().run(db)
        click.echo(f"[{name}] total={total} new_or_amended={new}")
    seen, llm = classify_unclassified(db)
    click.echo(f"classified seen={seen} llm_calls={llm}")
    path = write_digest(db)
    click.echo(str(path))
