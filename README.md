# Scout

Early-warning system for power and energy funding opportunities. Monitors SAM.gov, Grants.gov, and DOE Office of Science for FOAs and contract notices; filters through a lexical gate and LLM classifier; surfaces a daily digest with FFRDC eligibility flags.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # fill in ANTHROPIC_API_KEY and SAM_GOV_API_KEY

scout ingest     # pull latest from all sources
scout classify   # run lexical gate + LLM relevance pass
scout digest     # write docs/digest_YYYY-MM-DD.md
```

## SAM.gov API key

Register at https://open.gsa.gov/api/get-opportunities-public-api/ — the public opportunities API requires a per-user key but is free. Default quota ~1,000 req/day.

## Scope (MVP)

Sources in v0.1: Grants.gov Search2, SAM.gov Opportunities, DOE Office of Science FOAs. Planned: ARPA-E eXCHANGE, NSPIRES, NSF, utility/state sources.

Monitoring only — Scout does not draft responsiveness or capture statements. It surfaces opportunities with strategic-fit notes and eligibility flags; humans decide pursue/no-pursue.

## Architecture

Five stages, each swappable:

1. **Ingestion** — one adapter per source writes raw payloads to `raw_notices`
2. **Normalization** — canonical schema keyed by `(source, notice_id)` with amendment hash
3. **Relevance** — cheap lexical gate (keywords + NAICS/PSC) then Haiku classifier for gate-passers
4. **Eligibility** — FFRDC yes/no/partner/unclear, cost share, foreign entity restrictions
5. **Alerting** — three lanes (act-now / review / archive) as markdown digest

## Layout

```
src/scout/
  adapters/       # grants_gov.py, sam_gov.py, doe_sc.py
  pipeline/       # normalize.py, lexical_gate.py, llm_classify.py, eligibility.py
  storage/        # schema.sql, db.py
  alerting/       # digest.py
  config.py       # loads config/*.yaml
  cli.py          # scout ingest | classify | digest | backfill
config/
  keywords.yaml
  naics_psc.yaml
  portfolio.yaml  # strategic-fit context fed to the LLM
```
