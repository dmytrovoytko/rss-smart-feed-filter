PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS profiles (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('work', 'fun')),
    threshold   REAL NOT NULL DEFAULT 0.55,
    is_active   INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    created_at  TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_profiles_single_active
    ON profiles (is_active) WHERE is_active = 1;

CREATE TABLE IF NOT EXISTS categories (
    profile_id       TEXT NOT NULL,
    id               TEXT NOT NULL,
    name             TEXT NOT NULL,
    description      TEXT NOT NULL,
    criteria_true    TEXT NOT NULL,
    criteria_false   TEXT NOT NULL,
    reason_hint      TEXT NOT NULL,
    weight           REAL NOT NULL DEFAULT 1.0,
    enabled          INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    position         INTEGER NOT NULL DEFAULT 0,
    version_hash     TEXT NOT NULL,
    PRIMARY KEY (profile_id, id),
    FOREIGN KEY (profile_id) REFERENCES profiles (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_categories_profile_position
    ON categories (profile_id, enabled, position);

CREATE TABLE IF NOT EXISTS category_versions (
    profile_id      TEXT NOT NULL,
    category_id     TEXT NOT NULL,
    version_hash    TEXT NOT NULL,
    snapshot_json   TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (profile_id, category_id, version_hash),
    FOREIGN KEY (profile_id) REFERENCES profiles (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS feeds (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id       TEXT NOT NULL,
    url              TEXT NOT NULL,
    title            TEXT NOT NULL,
    enabled          INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    etag             TEXT,
    last_modified    TEXT,
    last_fetched_at  TEXT,
    last_status      TEXT,
    UNIQUE (profile_id, url),
    FOREIGN KEY (profile_id) REFERENCES profiles (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_feeds_profile_enabled
    ON feeds (profile_id, enabled);

CREATE TABLE IF NOT EXISTS items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id         INTEGER NOT NULL,
    guid            TEXT,
    canonical_url   TEXT NOT NULL,
    url_hash        TEXT NOT NULL,
    title           TEXT NOT NULL,
    summary         TEXT NOT NULL DEFAULT '',
    published_at    TEXT,
    fetched_at      TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    UNIQUE (feed_id, url_hash),
    FOREIGN KEY (feed_id) REFERENCES feeds (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_items_feed_published
    ON items (feed_id, published_at);
CREATE INDEX IF NOT EXISTS idx_items_published
    ON items (published_at);
CREATE INDEX IF NOT EXISTS idx_items_fetched
    ON items (fetched_at);
CREATE INDEX IF NOT EXISTS idx_items_feed_content
    ON items (feed_id, content_hash);

CREATE TABLE IF NOT EXISTS classifications (
    item_id          INTEGER NOT NULL,
    profile_id       TEXT NOT NULL,
    category_id      TEXT NOT NULL,
    category_version TEXT NOT NULL,
    model            TEXT NOT NULL,
    probability      REAL NOT NULL,
    created_at       TEXT NOT NULL,
    PRIMARY KEY (item_id, profile_id, category_id, category_version, model),
    FOREIGN KEY (item_id) REFERENCES items (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_classifications_profile_item
    ON classifications (profile_id, item_id);
CREATE INDEX IF NOT EXISTS idx_classifications_profile_category
    ON classifications (profile_id, category_id, category_version, model);

CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL CHECK (kind IN ('refresh', 'rescore')),
    mode         TEXT NOT NULL DEFAULT 'refresh',
    profile_id   TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('queued', 'running', 'preview', 'done', 'failed')),
    phase        TEXT,
    processed    INTEGER NOT NULL DEFAULT 0,
    total        INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    result_json  TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    FOREIGN KEY (profile_id) REFERENCES profiles (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_jobs_created
    ON jobs (created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_profile_status
    ON jobs (profile_id, status, created_at);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
