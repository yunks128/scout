from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterator

from scout.storage.db import DB, Notice

log = logging.getLogger(__name__)


class Adapter(ABC):
    source: str

    @abstractmethod
    def fetch(self) -> Iterator[tuple[str, dict]]:
        """Yield (notice_id, raw_payload) tuples."""

    @abstractmethod
    def normalize(self, notice_id: str, payload: dict, content_hash: str) -> Notice | None:
        """Turn raw payload into a canonical Notice. Return None to skip."""

    def run(self, db: DB) -> tuple[int, int]:
        """Fetch, persist raw, normalize, upsert. Returns (total, new_or_amended)."""
        total = 0
        new_count = 0
        for notice_id, payload in self.fetch():
            total += 1
            try:
                chash, is_new_raw = db.upsert_raw(self.source, notice_id, payload)
                notice = self.normalize(notice_id, payload, chash)
                if notice is None:
                    continue
                if db.upsert_notice(notice):
                    new_count += 1
            except Exception:
                log.exception("Adapter %s failed on notice_id=%s", self.source, notice_id)
        return total, new_count
