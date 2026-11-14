# Smart Feed Filter — Product Requirements Document (Implementation Baseline)

**Status:** v0.3.0, MVP  
**Date:** 2026-11-11  
**Working name:** Smart Feed Filter  
**Scope:** v0.3.0, MVP, local single-user web app  
**Decision model:** `tev1:0.8b` via Ollama (original evaluation gate not met; accepted for the MVP)  
**Delivery:** local Python install or Docker Compose

---

## 0. Summary

Smart Feed Filter turns public RSS feeds into a short, category-grouped digest for one person. The user runs the app locally, seeds one of two profiles, and presses **Refresh**. The app fetches enabled public feeds, dedupes and stores new items, asks a local Ollama decision model to score each item against enabled categories, and renders the last 72 hours grouped by category and ranked inside each section.

The product is a filtering engine, not a summarizer. It does not fetch article bodies, connect to private accounts, or generate prose. Everything runs locally against a SQLite database and a local Ollama instance.

This version describes the implemented MVP. Planned editing and management surfaces are explicitly deferred in §5.7.

---

## 1. Problem

Professionals who still use RSS face two problems: too many items, and no way to express "what I care about this month" without writing code or maintaining keyword rules. Generic readers sort by recency and source. AI summarizers add reading work instead of removing it.

Smart Feed Filter removes the triage step. The user states interests once in seeded profiles, gets a ranked shortlist, and can refresh manually without an account, cloud service, or scheduler.

---

## 2. Users and jobs to be done

**Primary user:** a 35–50 year old professional (data scientist, developer, PM, marketer, analyst) who reads public feeds and wants a short daily list without accounts or cloud services.

**Jobs to be done:**
- When I have 15 minutes to catch up, help me find the few items that match my current work and hobbies, and ignore the rest.
- When a match appears, show me the category and match probability so I can trust or dismiss it.
- When a feed or the model fails, show me the problem and let me retry without losing my digest.
- As a local-first user, I want no account, no telemetry, and no private-service access.

**Implemented user stories:**
1. As a data scientist, I want ML, LLM, analytics, and data-engineering items grouped separately so I can skim by intent.
2. As an off-work reader, I want hobby items without work, politics, or health noise.
3. As a local-first user, I want to install with pip or run the stack with Docker Compose, with no account or telemetry.

**Deferred user stories:**
- As a tinkerer, I want to add a category from scratch and see what it matches.
- As a cautious user, I want a preview diff before a category edit rewrites the last 14 days.
- As a feed owner, I want to add, remove, or disable individual feeds from the UI.

---

## 3. Goals and non-goals

**Goals**
- One local process, one SQLite file, no accounts.
- Two seeded profiles (`ds.v1`, `fun.v1`) that work without setup.
- Category-level relevance with templated reason data and match probability.
- Manual refresh with persisted job state, progress, and retry.
- Clear, recoverable error states.
- Reproducible local checks (`ruff`, `unittest`) and a GitHub Actions CI workflow.
- Optional one-command local stack with Docker Compose, including Ollama.

**Non-goals for v0.3.0 MVP **
- No category editor, preview/apply flow, or retroactive re-score UI.
- No feed add/remove/toggle UI; the seeded catalog is read-only in the UI.
- Settings are limited to display preferences and the max-new-items override; no weight or threshold editing.
- The profile switcher is implemented.
- No email, calendar, social, or private-service integrations.
- No full-text article fetching, paywall bypass, or content rehosting.
- No accounts, multi-user support, cloud sync, or telemetry.
- No scheduler, push notifications, or background polling.
- No read/unread, save, or dismiss state; the digest is stateless.
- No job-search profile kind.
- No generated summaries or free-text model output; reasons are templated.
- No medical, health, or mental-health advice.
- No GitHub trending, Amazon, or Rotten Tomatoes sources.
- No CD/deployment pipeline; the app is local-only.
- No Docker build or Compose validation in CI.

---

## 4. Locked decisions

| Area | Decision |
|---|---|
| Runtime | Local, single-user, one process |
| Stack | Python 3.12, FastAPI, Jinja2 + HTMX, SQLite WAL |
| Model | Ollama `tev1:0.8b`; gate evaluated but not met; accepted for MVP |
| Profiles | `ds.v1` and `fun.v1` seeded from YAML; `ds.v1` active by default |
| Profile switching | Header switcher; `POST /profiles/{id}/activate` persists the active profile |
| Categories | Seeded only; no create/edit/delete UI |
| Category preview/rescore | Deferred; `RESCORE_DAYS` is reserved |
| Feeds | Seeded read-only catalog per profile; no add/remove/toggle UI |
| Classification input | Feed title + summary only |
| Classification output | Category probabilities; match when probability ≥ profile threshold |
| Threshold | Per profile; `ds.v1` 0.62, `fun.v1` 0.55; default 0.55 |
| Low confidence | Collapsed section; `hide_low_confidence` DB setting overridable in UI Settings |
| Display preferences | `hide_reason_text` DB setting overridable in UI Settings |
| Digest window | 72 hours |
| Initial backfill | 7 days on first successful fetch; later fetches use retention window |
| Refresh | Manual only; in-process job runner; at most one active job globally |
| New item cap | `MAX_NEW_ITEMS` environment default, overridable in UI Settings |
| Cache | Category-level, keyed by item, profile, category version, and model |
| Dedupe | Canonical URL or normalized title within the same feed |
| Retention | 30 days for items and jobs |
| Errors | Persisted job error plus manual retry; previous digest remains |
| Read state | None; digest is stateless |
| Access | Host bind on 127.0.0.1; container publishes loopback-only ports |
| Testing | `unittest` service tests plus FastAPI `TestClient` route tests |
| CI | GitHub Actions on push/PR: `ruff` + `unittest`; no container validation |
| Delivery | Local Python install or Docker Compose |
| Containers | Ollama `docker.io/alpine/ollama:0.40.15`; app image on `python:3.12-slim` |

---

## 5. Scope and user flows

### 5.1 First run

1. Create the SQLite database and apply schema version 1.
2. Load the seeded feed catalog and the two profile presets from `profiles/*.yaml`.
3. Set `ds.v1` as the active profile; both profiles start with their seeded feeds.
4. Seed display settings: `hide_low_confidence=0`, `hide_reason_text=0`.
5. Show an empty digest state: "No items in this window / Refresh to fetch more items from your sources."
6. On refresh, backfill 7 days, dedupe, classify up to the configured new-item cap (default 50), and render the digest.

### 5.2 Digest

- Window: items published in the last 72 hours; fall back to `fetched_at` when a feed has no date.
- Grouping: one section per enabled category that has at least one match.
- Sections are rendered as expanded `<details>` elements by default.
- Within a section: ranked by probability × weight × recency decay.
- Each item shows: title (links to the source), source, relative time, match probability, and any other matched category tags.
- An item matching multiple categories appears in each relevant section. This is intentional; no cross-section dedupe.
- The templated reason is computed in the digest payload but is not rendered for matched items in the current UI.
- A collapsed "Low confidence" section lists items with no category above threshold, ordered by highest probability. `hide_low_confidence` hides this section when enabled in the database.
- A pending notice appears when some items in the window have not been classified.
- A sources panel lists seeded feeds with enabled state, last status, and last fetched time.
- No read/unread, save, or dismiss controls.

### 5.3 Deferred: profile and category editing UI

The category editor, preview diff, atomic apply, and retroactive re-score flow are not implemented. `RESCORE_DAYS` is validated but unused. Category definitions change only by editing seed YAML before first run or by direct database changes.

### 5.4 Feeds

- Feeds are loaded from `profiles/catalog.yaml` at seed time.
- The sources panel is read-only: it shows title, enabled state, last status, and last fetched time.
- No UI exists to add, remove, enable, or disable a feed.
- Feed requests are validated against public HTTP(S) URLs and private/local networks before fetching.

### 5.5 Refresh and errors

- Refresh runs as an in-process job with progress polling.
- It fetches enabled feeds, parses, dedupes, stores new items, classifies up to the configured cap, and updates the digest.
- If more than the cap are pending, process the newest items and show "N unprocessed; refresh again."
- If Ollama is unreachable, the model is missing, or a request fails after retries, the job fails with an actionable error and a Retry button. The previous digest remains visible.
- Retry is available only for failed jobs and re-runs the refresh job; it classifies only items without valid classifications for the current profile versions and model.
- On server restart, queued/running/preview jobs are marked failed with `interrupted by server restart`.

### 5.6 Health

- `GET /healthz` returns `{"status": "ok", ...}` plus row counts for profiles, categories, feeds, items, classifications, and jobs.
- The Docker app healthcheck calls `/healthz` on `127.0.0.1`.

### 5.7 Deferred MVP limits

| Area | Prototype status |
|---|---|
| Category create/edit/delete UI | Deferred |
| Category preview/apply/cancel UI | Deferred |
| Retroactive re-score (`RESCORE_DAYS`) | Deferred; config only |
| Feed add/remove/toggle UI | Deferred |
| Settings page | Implemented for display preferences and max-new-items override |
| Profile switcher | Implemented in the header |
| Templated reason display | Implemented for matched and low-confidence items |
| `hide_reason_text` behavior | Implemented; hides match explanations |
| Docker/Compose validation in CI | Not planned |
| CD/deployment | Not applicable; local-only app |

---

## 6. Data model

SQLite with WAL, foreign keys, `busy_timeout=5000`, and `synchronous=NORMAL`. Timestamps are UTC ISO-8601 text with milliseconds and a trailing `Z`. Schema version is stored in `PRAGMA user_version` and is currently `1`; `init_db` applies the schema when the version is lower and refuses a newer version.

### `profiles`
| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | e.g. `ds.v1` |
| `name` | TEXT | display name |
| `kind` | TEXT | `work` or `fun` |
| `threshold` | REAL | default 0.55 |
| `is_active` | INTEGER | at most one row is 1 |
| `created_at` | TEXT | |

A unique partial index enforces at most one active profile.

### `categories`
Current seeded definition of a category.

| Column | Type | Notes |
|---|---|---|
| `profile_id` | TEXT FK | |
| `id` | TEXT | stable slug, unique per profile |
| `name` | TEXT | |
| `description` | TEXT | classifier definition |
| `criteria_true` | TEXT | positive criteria |
| `criteria_false` | TEXT | negative criteria |
| `reason_hint` | TEXT | short phrase used in the templated reason |
| `weight` | REAL | ranking multiplier |
| `enabled` | INTEGER | |
| `position` | INTEGER | display order |
| `version_hash` | TEXT | active version |
| PRIMARY KEY | (`profile_id`, `id`) | |

### `category_versions`
Immutable snapshots for cache identity and future rollback.

| Column | Type | Notes |
|---|---|---|
| `profile_id` | TEXT | |
| `category_id` | TEXT | |
| `version_hash` | TEXT | SHA-256 prefix, 16 hex characters |
| `snapshot_json` | TEXT | name, description, criteria, reason hint |
| `created_at` | TEXT | |
| PRIMARY KEY | (`profile_id`, `category_id`, `version_hash`) | |

`version_hash` is computed from normalized classification-relevant fields: name, description, `criteria_true`, `criteria_false`, and `reason_hint`. Weight and threshold are excluded.

### `feeds`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `profile_id` | TEXT FK | separate feed list per profile |
| `url` | TEXT | unique per profile |
| `title` | TEXT | |
| `enabled` | INTEGER | |
| `etag` | TEXT | conditional GET |
| `last_modified` | TEXT | |
| `last_fetched_at` | TEXT | |
| `last_status` | TEXT | success or short error |
| UNIQUE | (`profile_id`, `url`) | |

### `items`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `feed_id` | INTEGER FK | |
| `guid` | TEXT | feed-provided ID when present |
| `canonical_url` | TEXT | tracking parameters removed |
| `url_hash` | TEXT | SHA-256 of canonical URL |
| `title` | TEXT | plain text |
| `summary` | TEXT | plain text, max 700 chars, default empty |
| `published_at` | TEXT | nullable |
| `fetched_at` | TEXT | |
| `content_hash` | TEXT | normalized title hash |
| UNIQUE | (`feed_id`, `url_hash`) | |

### `classifications`
| Column | Type | Notes |
|---|---|---|
| `item_id` | INTEGER | |
| `profile_id` | TEXT | |
| `category_id` | TEXT | |
| `category_version` | TEXT | |
| `model` | TEXT | e.g. `tev1:0.8b` |
| `probability` | REAL | P(true) from `noul` |
| `created_at` | TEXT | |
| PRIMARY KEY | (`item_id`, `profile_id`, `category_id`, `category_version`, `model`) | |

A classification is usable only when its `created_at` is at or after the item's `fetched_at`, its category version matches the current category, its model matches the configured model, and the category and feed are enabled. Threshold changes are applied at read time.

### `jobs`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `kind` | TEXT | `refresh` or `rescore`; `rescore` is reserved |
| `profile_id` | TEXT | |
| `status` | TEXT | queued, running, preview, done, failed |
| `processed` | INTEGER | |
| `total` | INTEGER | |
| `error` | TEXT | nullable |
| `result_json` | TEXT | job summary JSON |
| `created_at` | TEXT | |
| `updated_at` | TEXT | |

### `settings`
Key/value store. Current keys:
- `active_profile_id`
- `hide_low_confidence`
- `hide_reason_text` (reserved; no effect)

---

## 7. Classification contract

### 7.1 Backend

- Endpoint: `POST {OLLAMA_BASE_URL}/v1/systemone`
- Preflight: `GET {OLLAMA_BASE_URL}/api/version` and `GET {OLLAMA_BASE_URL}/api/tags`
- Default model: `tev1:0.8b`
- No API key. Ollama is expected on localhost or the Compose service.
- One request per item, containing questions only for enabled categories that do not yet have a valid classification.
- Hard limit: 64 questions per request. Seeded profiles use 8 (`ds.v1`) and 9 (`fun.v1`) categories.
- Requests are sequential.
- Client timeout: 120 seconds per request; 2 retries (3 attempts total).
- Retryable failures: network exceptions, HTTP 429/500/502/503/504, invalid response bodies. Backoff is 1.5s then 3.0s.
- HTTP 404 raises a model-missing error; other HTTP 4xx responses fail immediately.
- `keep_alive` is `30m`.

### 7.2 Input

`state` is the feed title and summary only:

```
{title}

{summary}
```

Truncate to 1,200 characters on a word boundary. No article fetching. No HTML. Summaries are stored at up to 700 characters.

Each category becomes:

```json
{
  "ml-research": {
    "type": "noul",
    "instructions": "Does this item focus on ML Methods & Research? <description>",
    "criteria": {
      "true": "<criteria_true>",
      "false": "<criteria_false>"
    }
  }
}
```

The state is data, never instructions. The backend uses the model's decision prompt and returns only probabilities.

### 7.3 Output

- `answers[category_id].noul` is the probability that the category applies.
- The client accepts a boolean, number, or mapping using `noul`, `probability`, or `score`.
- Probabilities must be finite and are clamped to `[0.0, 1.0]`.
- A category is a match when probability ≥ profile threshold.
- If no category meets the threshold, the item has no labels and appears in Low confidence.
- There is no `none` category.
- The UI shows match probability, not "confidence."

### 7.4 Reasons

Reasons are templated, never generated:

```
Matched {category.name}: {category.reason_hint} (p={probability:.2f})
```

The digest payload includes this string for matched items, but the current template does not render it for matched items. This display is deferred. Low-confidence items show "Best match: {category} (p=...)" in the UI.

### 7.5 Failure behavior

- If the model is unreachable, the model is not pulled, or a request fails after retries, the job fails.
- The previous digest stays visible.
- The job panel shows the error and a Retry button.
- Retry is allowed only for failed jobs. The retry re-runs refresh and classification for the profile.
- Classification rows are inserted at the end of a classification run, so a failed run does not write partial category scores.
- Feed fetches may still have stored items and statuses before a later classification failure.

### 7.6 Model and version changes

- Changing `TEV_MODEL` creates new cache entries; switching back reuses prior results.
- Editing classification-relevant category fields creates a new `version_hash`.
- Old classifications are retained.
- Weight edits do not invalidate the cache. Threshold edits re-filter existing probabilities.

---

## 8. Ranking and digest rules

### 8.1 Score

For each item and category:

```
recency = max(0.25, 0.5 ** (age_hours / 36))
score   = probability × category.weight × recency
```

- `age_hours` is measured from `published_at`, falling back to `fetched_at`.
- Only items inside the 72-hour window are considered.
- Sort descending inside each category section.
- Use probability for display and score for ordering.

### 8.2 Dedupe

- Canonical URL: lowercase host, remove fragments, remove `utm_*`, `fbclid`, `gclid`, `mc_cid`, and `ref`, drop default ports, sort query parameters, strip trailing slash.
- Title key: NFKC normalize, lowercase, remove punctuation, collapse whitespace.
- An item is a duplicate if the canonical URL or title key matches an existing item from the same feed.
- If a duplicate has a newer published date, its metadata is updated; otherwise it is counted as a duplicate and ignored.
- No cross-feed dedupe.

### 8.3 Low confidence

- Contains items with no label at or above threshold.
- Sorted by the highest probability across categories.
- Collapsed by default.
- Hidden entirely when `hide_low_confidence` is enabled in the database.
- Items with no labels do not appear in category sections.

### 8.4 Retention

- Delete items older than 30 days (by `COALESCE(published_at, fetched_at)`) and their classifications during refresh.
- Delete jobs older than 30 days.
- No user-facing export.

---

## 9. Architecture

### 9.1 Components

- **Web app:** FastAPI serving Jinja templates and HTMX fragments.
- **Database:** SQLite in WAL mode, one file under the app data directory.
- **Feed service:** `feedparser` for RSS/Atom, `requests` for conditional GETs, per-feed timeouts and error isolation.
- **Classifier:** Ollama `/v1/systemone`, one request per item, sequential.
- **Job runner:** one in-process worker thread plus a jobs table.
- **Profiles:** YAML seeds under `profiles/`, imported into SQLite on first run.
- **Eval:** standalone harness under `eval/`, not part of the web app.
- **Containers:** `Dockerfile` and `docker-compose.yml` for app + Ollama.
- **CI:** GitHub Actions workflow under `.github/workflows/ci.yml`.

### 9.2 Repository layout

```
.github/
  workflows/
    ci.yml
_docs/
app/
  __main__.py
  config.py
  db.py
  logging_config.py
  main.py
  schema.sql
  seed.py
  timeutil.py
  versioning.py
  routes/
    pages.py
  services/
    classify.py
    digest.py
    feeds.py
    jobs.py
    preferences.py
  static/
  templates/
    base.html
    digest.html
    partials/job.html
eval/
  items.jsonl
  harness.py
profiles/
  catalog.yaml
  ds.v1.yaml
  fun.v1.yaml
tests/
  test_classify.py
  test_digest.py
  test_feeds.py
  test_foundation.py
  test_jobs.py
  test_routes.py
.dockerignore
Dockerfile
docker-compose.yml
pyproject.toml
requirements.txt
```

### 9.3 Routes

Implemented routes:

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Redirect to active profile digest |
| GET | `/profiles/{id}/digest` | Digest with 72-hour sections |
| POST | `/profiles/{id}/activate` | Persist the active profile and redirect to its digest |
| POST | `/profiles/{id}/refresh` | Enqueue refresh job; redirect or return HTMX panel |
| GET | `/jobs/{id}` | Job progress fragment |
| POST | `/jobs/{id}/retry` | Retry a failed job |
| GET | `/settings` | Settings page |
| POST | `/settings` | Update display preferences and max-new-items override |
| GET | `/healthz` | Health and table counts |
| GET | `/favicon.ico` | Favicon |
| GET | `/static/{path}` | CSS, HTMX, favicon |

Deferred routes: category editing, preview/apply, and feed manager.

### 9.4 Configuration

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Local Ollama endpoint |
| `TEV_MODEL` | `tev1:0.8b` | Decision model |
| `DB_PATH` | `./data/feedfilter.db` | SQLite database file |
| `LOG_PATH` | `./data/feedfilter.log` | Log file |
| `LOG_LEVEL` | `INFO` | Log verbosity |
| `HOST` | `127.0.0.1` | Bind address |
| `PORT` | `8000` | HTTP port |
| `MAX_NEW_ITEMS` | `50` | Environment default; UI Settings can override it |
| `RETENTION_DAYS` | `30` | Item and job retention |
| `WINDOW_HOURS` | `72` | Digest window |
| `RESCORE_DAYS` | `14` | Reserved; no rescore flow |
| `THRESHOLD` | `0.55` | Default threshold for new profiles |
| `PROFILES_DIR` | repository `profiles/` | Seed profile directory |

Docker Compose values:

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_BASE_URL_DOCKER` | `http://ollama:11434` | App-to-Ollama endpoint inside Compose |
| `OLLAMA_PUBLISH_HOST` | `127.0.0.1` | Host interface for the published Ollama port |

- Behavioral settings from `.env` are passed through: `TEV_MODEL`, `PORT`, `LOG_LEVEL`, `MAX_NEW_ITEMS`, `RETENTION_DAYS`, `WINDOW_HOURS`, `RESCORE_DAYS`, `THRESHOLD`, `DB_PATH`, `LOG_PATH`.
- App `HOST` is forced to `0.0.0.0` inside the container; the host port is published on `127.0.0.1`.
- Default relative `DB_PATH`/`LOG_PATH` resolve under `/app` to `/app/data/...`; custom absolute paths require a matching volume mount.
- The container uses `OLLAMA_BASE_URL_DOCKER` instead of the local `OLLAMA_BASE_URL`.
- Set `OLLAMA_BASE_URL_DOCKER=http://host.docker.internal:11434` to use a host Ollama; `extra_hosts` maps `host.docker.internal` to `host-gateway`.

### 9.5 Dependencies

Runtime: `fastapi`, `uvicorn`, `jinja2`, `python-multipart`, `feedparser`, `requests`, `pyyaml`, `pydantic`, `python-dateutil`.

Development: `ruff`, `httpx` (FastAPI `TestClient`), Python's built-in `unittest`.

Optional delivery: Docker and Docker Compose. CI: GitHub Actions.

The model runs outside the app in Ollama. No model weights are bundled.

### 9.6 Delivery, CI, and containerization

**Local delivery**

```bash
pip install -e '.[dev]'
feed-filter
```

**Testing and CI**

```bash
python -m unittest discover -s tests -v
ruff check .
```

- GitHub Actions runs on every push and pull request.
- Workflow: Python 3.12, pip cache keyed by `pyproject.toml`, `pip install -e '.[dev]'`, `ruff check .`, `python -m unittest discover -s tests -v`.
- Permissions: `contents: read`.
- CI does not build the Docker image, run Compose, start Ollama, or execute the eval harness.
- No CD/deployment job exists.

**Containerization**

- `Dockerfile`: `python:3.12-slim`, non-root `appuser` UID 10001, runtime dependencies, editable app install, `/app/data`, `EXPOSE 8000`, `/healthz` healthcheck that follows `PORT`, `feed-filter` entrypoint.
- Python 3.12 is mature and receives security updates; the base image tag is intentionally not pinned.
- Ollama images are pinned to `docker.io/alpine/ollama:0.40.15` (tested; requires 0.35.1 or newer).
- Compose services:
  - `ollama`: server, model volume, healthcheck via `ollama list`.
  - `ollama-pull`: one-shot pull of `TEV_MODEL`, runs after Ollama is healthy.
  - `app`: starts after Ollama is healthy and the model pull completes.
- Compose requires `.env` for the app via `env_file`.
- Compose passes `TEV_MODEL`, `PORT`, `LOG_LEVEL`, `MAX_NEW_ITEMS`, `RETENTION_DAYS`, `WINDOW_HOURS`, `RESCORE_DAYS`, `THRESHOLD`, `DB_PATH`, and `LOG_PATH` from `.env`.
- App `HOST` is forced to `0.0.0.0` inside the container; the app container uses `OLLAMA_BASE_URL_DOCKER`, which defaults to `http://ollama:11434`.
- Host ports:
  - App: `127.0.0.1:${PORT:-8000}`
  - Ollama: `${OLLAMA_PUBLISH_HOST:-127.0.0.1}:11434`
- Named volumes: `ollama-models`, `feedfilter-data`.
- A failed model pull prevents app startup by design.

---

## 10. Non-functional requirements

- **Local only:** host bind is loopback; container host ports are loopback-only. The container itself binds `0.0.0.0` to accept mapped traffic.
- **Privacy:** no accounts, no private services, no telemetry. Feed content is public and stored only as title, summary, URL, and timestamps.
- **Security:** escape all feed text in templates; strip HTML; allow only public HTTP(S) feed URLs and reject private/local address ranges and credentials; no script execution from feed content; container runs as non-root.
- **Performance:** original target was 50 items under 3 minutes on an 8-core CPU. The recorded eval p95 is 10.55 s/item, so this target is not demonstrated for the prototype.
- **Reliability:** feed errors are isolated; model errors fail the job explicitly; job state is persisted; digest reads are transactional.
- **Cost:** zero inference cost; local model only.
- **Retention:** 30 days for items and jobs.
- **Logging:** rotating local log (5 MB × 3 backups) plus stream handler; no full summaries logged by default.
- **Accessibility:** skip link, semantic headings, keyboard-operable details/summary, visible focus, sufficient contrast.
- **Testing:** unit/service tests for parsing, dedupe, ranking, cache keys, classification, and job runner; FastAPI `TestClient` route integration tests with a temporary SQLite database; no Ollama call in route tests.
- **Delivery:** local Python install or Docker Compose; no cloud dependency.

---

## 11. Evaluation and model gate

### 11.1 Eval set

- `eval/items.jsonl`: 100 items, 50 per profile.
- Each item: `id`, `profile`, `source`, `title`, `summary`, `url`, `published`, `labels`, `label_status: manual`.
- Labels are multi-label and use the profile category IDs.
- Items with no relevant category have an empty label list.
- Composition: 8 `ds.v1` categories and 9 `fun.v1` categories.
- Ground truth was labeled from title + summary only, matching the model input.
- Treat the set as a fixed evaluation set; do not tune the threshold on it.

### 11.2 Harness

`python eval/harness.py --model tev1:0.8b --gate --out eval/results-0.8b.json`

- Validates profiles and labels (`--dry-run` performs no model calls).
- Calls Ollama once per item with one `noul` question per enabled category.
- Applies threshold 0.62.
- Reports per-category precision, recall, and F1; macro-F1; micro-F1; precision@10 per profile; Brier score; p50/p95 latency; and harness RSS.
- Exits non-zero when the gate fails or a request fails.

### 11.3 Gate result

Original gate targets:

| Metric | Threshold |
|---|---|
| Macro-F1 | ≥ 0.75 |
| Precision@10 | ≥ 0.70 |
| p95 latency per item | ≤ 2.0 s |
| Request failures | 0 |

Recorded `tev1:0.8b` result:

- `ds.v1`: macro-F1 ≈ 0.595; precision@10 = 1.00
- `fun.v1`: macro-F1 ≈ 0.867; precision@10 = 1.00
- Overall p95 latency ≈ 10.55 s
- Gate passed: **false** (ds macro-F1 below 0.75; p95 above 2.0 s)
- A threshold-0.62 run records the same gate failure.
- No `tev1:4b` results are recorded, and the fallback was not adopted.

**Decision:** the MVP continues with `tev1:0.8b` despite the gate failure. The quality and latency gaps are documented risks, not a release gate for this MVP.

### 11.4 Known model limitations

- `tev1:0.8b` is an 812 MB experimental decision model. Public benchmark accuracy is about 63.5% versus 73.3% for the 4B model, and custom taxonomies are out of distribution.
- Practical context is about 2,000 tokens. Keep state under 1,200 characters and categories under 64 in requests.
- Probabilities are model preferences, not calibrated confidence.
- Categories with overlapping definitions (analytics vs experimentation; LLM research vs applied LLM) produce the most errors; the criteria fields exist to separate them.
- The recorded latency exceeds the original per-item target.

---

## 12. Milestones

| Phase | Work | Status |
|---|---|---|
| 0 | Ollama upgrade/model pull, eval harness, 100-item labels, gate run | Done; gate not passed; waiver recorded |
| 1 | SQLite schema, seed catalog, feed fetch/parse | Done |
| 2 | Classifier client, category cache, error state, retry | Done |
| 3 | Category editor, preview diff, apply, 14-day re-score | Deferred |
| 4 | Digest grouped by category, ranking, low-confidence section, reasons | Done; last-refreshed metadata and button progress added |
| 5 | Feed manager, settings, dedupe, retention | Dedupe, retention, settings, and profile switcher done; feed manager deferred |
| 6 | Tests, fixtures, logging, accessibility pass, docs, CI | Done |
| 7 | Hardening, containerization, delivery docs | Done |

---

## 13. Acceptance criteria

Implemented criteria:

- First run creates the database, seeds both profiles and feed catalogs, and sets one active profile.
- Refresh backfills 7 days on first successful fetch, respects the configured new-item cap, and produces a 72-hour digest.
- Category sections show title, source, relative time, match probability, and additional matched-category tags.
- The digest shows the last successful refresh time; active jobs show percentage progress in the refresh button.
- Low-confidence items are collapsed and can be hidden with the `hide_low_confidence` setting.
- The header switches profiles and persists the active profile.
- The Settings page controls `max_new_items`, `hide_low_confidence`, and `hide_reason_text`.
- Ollama downtime produces a failed job with an error and a working retry button; the previous digest remains visible.
- Duplicate feed entries do not create duplicate digest items.
- No private services, accounts, telemetry, or full-text fetching are present.
- `ruff check .` and `python -m unittest discover -s tests -v` pass locally and in GitHub Actions on push/PR.
- Route integration tests cover first-run redirect, digest, refresh enqueue, HTMX job panel, job fragment, retry, `/healthz`, and favicon against a temporary database without Ollama.
- `docker compose up --build` starts Ollama, pulls `TEV_MODEL`, and serves the app on `127.0.0.1:8000`; only loopback host ports are published.
- The container healthcheck uses `/healthz`.
- The model gate result is recorded; the gate did not pass and the prototype waiver is documented.

Deferred criteria:

- Category editing with preview counts/examples and atomic apply.
- Weight/threshold edits without model calls through a settings UI.
- Feed add/remove/toggle UI.
- Profile creation and editing.
- `python eval/harness.py --gate` passing with the chosen model.

---

## 14. Risks and open questions

**Risks**
- `tev1:0.8b` does not meet the original quality gate on `ds.v1` (macro-F1 ≈ 0.595) and exceeds the latency target (p95 ≈ 10.55 s/item).
- CPU inference may remain slow if other workloads compete for cores.
- RSS summaries vary in length and quality; some feeds provide title only.
- Overlapping categories produce systematic false positives.
- Feed URLs and formats break; per-feed isolation and visible status mitigate this.
- Prompt injection in feed text is possible. The model has no tools, but hostile text can bias a decision; low-confidence handling is the mitigation.
- Category and feed management are not available in the UI; settings are limited to display preferences and the new-item cap.
- Compose requires `.env`; model selection depends on `TEV_MODEL` being present there. Custom absolute `DB_PATH`/`LOG_PATH` values need a matching volume mount.
- A failed model pull blocks app startup by design.
- CI does not validate the Docker image, Compose stack, or live Ollama behavior.
- The Python base image is intentionally unpinned; base-image drift is possible but limited to security patches.
- Named-volume ownership can require attention when running as non-root.
- No CD exists; deployment is local-only.

**Open questions**
- Whether to implement the deferred category/feed/profile-editing UI.
- Whether to adopt `tev1:4b`, revisit the taxonomy, or accept the 0.8b quality/latency tradeoff.
- Whether to extend Settings to category weights and profile thresholds.
- Whether to add Docker/Compose validation to CI.
- Whether Docker Compose or the Python install is the primary delivery path.
- Whether to support GPU acceleration in Ollama.
- Whether `RESCORE_DAYS` and `hide_reason_text` should remain reserved or be removed.

---

## Appendix A — Seeded profiles

Files: `profiles/ds.v1.yaml`, `profiles/fun.v1.yaml`.

`ds.v1` categories: ML Methods & Research; LLMs & Generative AI; MLOps & Deployment; Experimentation & Causal Inference; Data Engineering & Pipelines; DS Tooling & Libraries; Analytics & Visualization; Responsible AI & Evaluation.

`fun.v1` categories: Cooking & BBQ; Books, Film & Music; DIY & Woodworking; Cycling & Outdoors; Games & Tabletop; Travel & Day Trips; Gadgets & Gear; Science & Space; Local Culture & Events.

The fun profile excludes work/industry news, politics, celebrity gossip, and health or medical advice.

## Appendix B — Seed feed catalog

`ds.v1`: arXiv cs.LG, arXiv stat.ML, KDnuggets, MIT News AI, Simon Willison, Google AI Blog, Analytics Vidhya, dbt Blog, Data Engineering Weekly, Netflix Tech Blog, Databricks Blog, Occam's Razor.

`fun.v1`: Smitten Kitchen, Bon Appétit, Literary Hub, IndieWire, Pitchfork, CyclingTips (Velo), Bikepacking.com, Dicebreaker, Polygon, Make, Atlas Obscura, The Verge, ScienceDaily.

Health-focused feeds are excluded from the seed catalog. Users cannot add feeds in the current UI.

## Appendix C — Decision request example

```json
{
  "model": "tev1:0.8b",
  "state": "OpenAI Decisions API Tutorial for Building Decision Agents\n\nImagine building an AI agent that has to decide whether a customer complaint needs human intervention...",
  "questions": {
    "llm-genai": {
      "type": "noul",
      "instructions": "Does this item focus on LLMs & Generative AI? Large language models and generative AI: prompting, agents, retrieval, fine-tuning, evaluation, model releases, and LLM applications.",
      "criteria": {
        "true": "The item is substantially about large language models, generative AI, agents, prompting, RAG, or LLM applications and releases.",
        "false": "The item is about classical machine learning, analytics, infrastructure, or general software engineering without a generative-AI focus."
      }
    }
  },
  "keep_alive": "30m"
}
```

Response:

```json
{
  "answers": {
    "llm-genai": {
      "noul": 0.83
    }
  }
}
```

If the model is unavailable, the app must show the error and restore the last successful digest.

## Appendix D — Commands

```bash
# Install
pip install -e '.[dev]'

# Run locally
feed-filter

# Test and lint
python -m unittest discover -s tests -v
ruff check .

# Eval
python eval/harness.py --dry-run
python eval/harness.py --model tev1:0.8b --gate --out eval/results-0.8b.json

# Container stack
docker compose up --build
```
