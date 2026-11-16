CREATE TABLE IF NOT EXISTS traces (
    trace_id     TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    name         TEXT NOT NULL,
    profile_id   TEXT,
    job_id       INTEGER,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    duration_ms  REAL,
    status       TEXT NOT NULL DEFAULT 'running',
    error        TEXT
);

CREATE INDEX IF NOT EXISTS idx_traces_started
    ON traces (started_at DESC, trace_id);
CREATE INDEX IF NOT EXISTS idx_traces_kind_status
    ON traces (kind, status, started_at DESC);

CREATE TABLE IF NOT EXISTS spans (
    span_id          TEXT PRIMARY KEY,
    trace_id         TEXT NOT NULL,
    parent_span_id   TEXT,
    name             TEXT NOT NULL,
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    duration_ms      REAL,
    status           TEXT NOT NULL DEFAULT 'ok',
    attributes_json  TEXT,
    error            TEXT
);

CREATE INDEX IF NOT EXISTS idx_spans_trace
    ON spans (trace_id, started_at);

CREATE TABLE IF NOT EXISTS metrics (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id     TEXT,
    name         TEXT NOT NULL,
    value        REAL NOT NULL,
    unit         TEXT,
    labels_json  TEXT,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_metrics_trace
    ON metrics (trace_id, created_at);
CREATE INDEX IF NOT EXISTS idx_metrics_name_created
    ON metrics (name, created_at DESC);

CREATE TABLE IF NOT EXISTS logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id     TEXT,
    level        TEXT NOT NULL,
    event        TEXT NOT NULL,
    fields_json  TEXT,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_logs_trace
    ON logs (trace_id, created_at);
CREATE INDEX IF NOT EXISTS idx_logs_created
    ON logs (created_at DESC);

CREATE TABLE IF NOT EXISTS alerts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id       TEXT,
    code             TEXT NOT NULL,
    severity         TEXT NOT NULL,
    message          TEXT NOT NULL,
    trace_id         TEXT,
    created_at       TEXT NOT NULL,
    acknowledged_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_alerts_profile_created
    ON alerts (profile_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_open
    ON alerts (acknowledged_at, created_at DESC);
