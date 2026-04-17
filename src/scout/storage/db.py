from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def content_hash(payload: dict) -> str:
    normalized = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(normalized).hexdigest()[:16]


@dataclass
class Notice:
    source: str
    notice_id: str
    content_hash: str
    title: str
    agency: str | None = None
    description: str | None = None
    posted_date: str | None = None
    response_deadline: str | None = None
    loi_deadline: str | None = None
    preapp_deadline: str | None = None
    naics: str | None = None
    psc: str | None = None
    url: str | None = None
    last_modified: str | None = None


@dataclass
class Classification:
    source: str
    notice_id: str
    content_hash: str
    lexical_score: float
    lexical_matches: list[str]
    llm_relevance: int | None
    llm_themes: list[str]
    llm_fit_notes: str | None
    ffrdc_eligible: str | None
    cost_share: str | None
    foreign_entity: str | None
    eligibility_quote: str | None
    lane: str


class DB:
    def __init__(self, path: str | Path | None = None) -> None:
        resolved = path or os.environ.get("SCOUT_DB_PATH", "data/scout.db")
        self.path = Path(resolved)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        schema = files("scout.storage").joinpath("schema.sql").read_text()
        with self.connect() as conn:
            conn.executescript(schema)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert_raw(self, source: str, notice_id: str, payload: dict) -> tuple[str, bool]:
        """Store raw payload. Returns (content_hash, is_new)."""
        chash = content_hash(payload)
        with self.connect() as conn:
            cur = conn.execute(
                "SELECT 1 FROM raw_notices WHERE source=? AND notice_id=? AND content_hash=?",
                (source, notice_id, chash),
            )
            if cur.fetchone() is not None:
                return chash, False
            conn.execute(
                "INSERT INTO raw_notices(source, notice_id, content_hash, fetched_at, payload_json) "
                "VALUES(?,?,?,?,?)",
                (source, notice_id, chash, _now(), json.dumps(payload, default=str)),
            )
        return chash, True

    def upsert_notice(self, notice: Notice) -> bool:
        """Insert or update normalized notice. Returns True if this content_hash is new
        for this (source, notice_id) — which means it's either a first sighting or an
        amendment. Caller uses this to decide whether to reclassify."""
        now = _now()
        with self.connect() as conn:
            prior = conn.execute(
                "SELECT COUNT(*) FROM notices WHERE source=? AND notice_id=?",
                (notice.source, notice.notice_id),
            ).fetchone()[0]
            existing = conn.execute(
                "SELECT 1 FROM notices WHERE source=? AND notice_id=? AND content_hash=?",
                (notice.source, notice.notice_id, notice.content_hash),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE notices SET last_seen_at=? "
                    "WHERE source=? AND notice_id=? AND content_hash=?",
                    (now, notice.source, notice.notice_id, notice.content_hash),
                )
                return False
            is_amendment = 1 if prior > 0 else 0
            data = asdict(notice)
            data["first_seen_at"] = now
            data["last_seen_at"] = now
            data["is_amendment"] = is_amendment
            cols = ",".join(data.keys())
            qs = ",".join(["?"] * len(data))
            conn.execute(f"INSERT INTO notices({cols}) VALUES({qs})", tuple(data.values()))
        return True

    def unclassified(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    "SELECT n.* FROM notices n "
                    "LEFT JOIN classifications c USING(source, notice_id, content_hash) "
                    "WHERE c.notice_id IS NULL"
                )
            )

    def save_classification(self, c: Classification) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO classifications("
                " source, notice_id, content_hash, lexical_score, lexical_matches,"
                " llm_relevance, llm_themes, llm_fit_notes, ffrdc_eligible, cost_share,"
                " foreign_entity, eligibility_quote, lane, classified_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    c.source, c.notice_id, c.content_hash, c.lexical_score,
                    json.dumps(c.lexical_matches), c.llm_relevance,
                    json.dumps(c.llm_themes), c.llm_fit_notes, c.ffrdc_eligible,
                    c.cost_share, c.foreign_entity, c.eligibility_quote, c.lane,
                    _now(),
                ),
            )

    def digest_rows(self, lanes: Iterable[str]) -> list[sqlite3.Row]:
        placeholders = ",".join(["?"] * len(list(lanes)))
        lanes = list(lanes)
        with self.connect() as conn:
            return list(
                conn.execute(
                    f"SELECT n.*, c.* FROM notices n "
                    f"JOIN classifications c USING(source, notice_id, content_hash) "
                    f"WHERE c.lane IN ({placeholders}) "
                    f"ORDER BY CASE c.lane "
                    f"  WHEN 'act-now' THEN 0 WHEN 'review' THEN 1 ELSE 2 END, "
                    f"n.response_deadline ASC",
                    lanes,
                )
            )
