-- Scout storage schema.
-- Raw payloads are stored verbatim so normalization is reprocessable.
-- Amendment detection keys on (source, notice_id, content_hash).

CREATE TABLE IF NOT EXISTS raw_notices (
    source        TEXT    NOT NULL,
    notice_id     TEXT    NOT NULL,
    content_hash  TEXT    NOT NULL,
    fetched_at    TEXT    NOT NULL,
    payload_json  TEXT    NOT NULL,
    PRIMARY KEY (source, notice_id, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_raw_fetched_at ON raw_notices(fetched_at);

CREATE TABLE IF NOT EXISTS notices (
    source             TEXT    NOT NULL,
    notice_id          TEXT    NOT NULL,
    content_hash       TEXT    NOT NULL,
    agency             TEXT,
    title              TEXT    NOT NULL,
    description        TEXT,
    posted_date        TEXT,
    response_deadline  TEXT,
    loi_deadline       TEXT,
    preapp_deadline    TEXT,
    naics              TEXT,
    psc                TEXT,
    url                TEXT,
    last_modified      TEXT,
    first_seen_at      TEXT    NOT NULL,
    last_seen_at       TEXT    NOT NULL,
    is_amendment       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source, notice_id, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_notices_deadline ON notices(response_deadline);
CREATE INDEX IF NOT EXISTS idx_notices_posted ON notices(posted_date);

-- Classification output. One row per (source, notice_id, content_hash) — new
-- rows are produced whenever an amendment creates a new content_hash.
CREATE TABLE IF NOT EXISTS classifications (
    source            TEXT    NOT NULL,
    notice_id         TEXT    NOT NULL,
    content_hash      TEXT    NOT NULL,
    lexical_score     REAL    NOT NULL,
    lexical_matches   TEXT,
    llm_relevance     INTEGER,
    llm_themes        TEXT,
    llm_fit_notes     TEXT,
    ffrdc_eligible    TEXT,
    cost_share        TEXT,
    foreign_entity    TEXT,
    eligibility_quote TEXT,
    lane              TEXT    NOT NULL,
    classified_at     TEXT    NOT NULL,
    PRIMARY KEY (source, notice_id, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_class_lane ON classifications(lane);

-- One row per (notice-version, lane, channel) that has been delivered. Lane is
-- part of the key so a reclassification (e.g. review → act-now) without a
-- content_hash change re-alerts.
CREATE TABLE IF NOT EXISTS alerts_sent (
    source        TEXT    NOT NULL,
    notice_id     TEXT    NOT NULL,
    content_hash  TEXT    NOT NULL,
    lane          TEXT    NOT NULL,
    channel       TEXT    NOT NULL,
    sent_at       TEXT    NOT NULL,
    PRIMARY KEY (source, notice_id, content_hash, lane, channel)
);
