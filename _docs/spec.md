# Smart Feed Filter — Product Requirements Document

**Status:** Draft v0.1  
**Date:** 2026-10-23  
**Working name:** Smart Feed Filter  
**Scope:** v1, local single-user web app  
**Decision model:** `tev1:0.8b` via Ollama (fallback `tev1:4b` if the Phase 0 gate fails)

---

## 0. Summary

Smart Feed Filter turns public RSS feeds into a short, category-grouped digest for one person. The user defines a profile: a small set of categories, each with a name, description, criteria, and weight. A local decision model scores every feed item against those categories, and the app shows the last 72 hours grouped by category, ranked inside each category, with tags and a one-line reason.

The product is a filtering engine, not a summarizer. It does not fetch article bodies, connect to private accounts, or generate prose. Everything runs locally against a SQLite database and a local Ollama instance.

---

## 1. Problem

Professionals who still use RSS face two problems: too many items, and no way to express "what I care about this month" without writing code or maintaining keyword rules. Generic readers sort by recency and source. AI summarizers add reading work instead of removing it.

Smart Feed Filter removes the triage step. The user states interests once, gets a ranked shortlist, and can change those interests in minutes. Edits are previewed and applied retroactively, so the digest does not silently drift.

---

## 2. Users and jobs to be done

**Primary user:** a 35–50 year old professional (data scientist, developer, PM, marketer, analyst) who reads public feeds and wants a short daily list without accounts or cloud services.

**Jobs to be done:**
- When I have 15 minutes to catch up, help me find the few items that match my current work and hobbies, and ignore the rest.
- When my interests change, let me express that in plain language and see the effect before committing.
- When a match appears, tell me in one line why it was selected so I can trust or dismiss it.
- When the model or feed fails, show me the problem and let me retry without losing my digest.

**Primary user stories:**
1. As a data scientist, I want ML, LLM, analytics, and data-engineering items grouped separately so I can skim by intent.
2. As an off-work reader, I want hobby items without work, politics, or health noise.
3. As a tinkerer, I want to add a category from scratch and see what it matches.
4. As a cautious user, I want a preview diff before a category edit rewrites the last 14 days.
5. As a local-first user, I want no account, no telemetry, and no private-service access.

---

## 3. Goals and non-goals

**Goals**
- One local process, one SQLite file, no accounts.
- Two seeded profiles (`ds.v1`, `fun.v1`) that work without setup.
- Custom categories with descriptions and weights.
- Category-level relevance with a short templated reason.
- Retroactive edits with preview and atomic apply.
- A measurable model-quality gate before UI work.
- Clear, recoverable error states.

**Non-goals for v1**
- No email, calendar, social, or private-service integrations.
- No full-text article fetching, paywall bypass, or content rehosting.
- No accounts, multi-user support, cloud sync, or telemetry.
- No scheduler, push notifications, or background polling.
- No read/unread, save, or dismiss state; the digest is stateless.
- No job-search profile kind (designed, deferred).
- No generated summaries or free-text model output; reasons are templated.
- No medical, health, or mental-health advice; health-focused content is out of scope.
- No GitHub trending, Amazon, or Rotten Tomatoes sources in v1.

---

## 4. Locked product decisions

| Area | Decision |
|---|---|
| Runtime | Local, single-user, one process |
| Stack | Python, FastAPI, Jinja2 + HTMX, SQLite |
| Model | Ollama `tev1:0.8b`, fallback `tev1:4b` if gate fails |
| Profiles | One active profile at a time |
| Seeds | `ds.v1` (work) and `fun.v1` (off-work) |
| Categories | User can create from scratch; name, description, criteria, weight |
| Feeds | Seeded catalog plus user add/remove; separate list per profile |
| Classification input | Feed title + summary only |
| Classification output | Category tags, match probability, templated reason |
| Threshold | 0.55 default, per profile |
| Low confidence | Collapsed section by default; setting to hide |
| Digest window | 72 hours |
| Initial backfill | 7 days |
| Refresh | Manual only; no scheduler |
| New item cap | 50 new items classified per refresh |
| Category edits | Preview diff, then retroactive re-score of last 14 days |
| Cache | Category-level, keyed by item, category version, and model |
| Dedupe | Canonical URL plus normalized title |
| Retention | 30 days |
| Errors | Persisted error state with manual retry |
| Read state | None; digest is stateless |
| Access | Bind to 127.0.0.1, no auth |

---

## 5. Scope and user flows

### 5.1 First run

1. Create the SQLite database and run migrations.
2. Load the seeded feed catalog and the two profile presets.
3. Set `ds.v1` as the active profile; both profiles start with their seeded feeds.
4. Show a short empty state: "Refresh to build your first digest."
5. On refresh, backfill 7 days, dedupe, classify up to 50 new items, and render the digest.

### 5.2 Digest

- Window: items published in the last 72 hours; fall back to `fetched_at` when a feed has no date.
- Grouping: one section per category that has at least one match.
- Within a section: ranked by probability × weight × recency decay.
- Each item shows: title (links to the source), source, relative time, matched category tags, probability, and a one-line reason.
- An item matching multiple categories appears in each relevant section. This is intentional; no cross-section dedupe.
- A collapsed "Low confidence" section lists items with no category above threshold, ordered by highest probability. A setting hides this section entirely.
- No read/unread, save, or dismiss controls in v1.

### 5.3 Profile and category editing

- The profile editor lists categories with enabled toggle, weight, and order.
- Creating a category requires a name and description; criteria fields are prefilled from the description and remain editable.
- Editing a category opens a preview step:
  - Preview re-scores only the changed categories over items from the last 14 days.
  - It shows counts: items entering, leaving, and unchanged, plus up to five examples of each.
  - Nothing is written to the digest until the user confirms.
- Apply writes new classifications in one transaction and makes the new category version active.
- Cancel discards the preview.
- Weight changes do not require re-scoring. Threshold changes re-use cached probabilities and apply instantly.
- Disabling a category hides it from the digest and skips it in future classification requests.

### 5.4 Feeds

- The feed manager lists feeds for the active profile.
- Seeded feeds can be disabled or removed; user feeds can be added by URL.
- Accept only public `http`/`https` feed URLs. Reject `file:`, `localhost`, and private IP ranges.
- Feed fetch errors are isolated per feed and shown inline with the last status.
- A manual refresh fetches all enabled feeds for the active profile.

### 5.5 Refresh and errors

- Refresh runs as an in-process job with progress polling.
- It fetches feeds, parses, dedupes, stores up to 50 new items, classifies them, and updates the digest.
- If more than 50 new items are pending, process the 50 newest and show "N unprocessed; refresh again."
- If Ollama is unreachable or the model is missing, the job fails with an actionable banner (`ollama serve`, `ollama pull tev1:0.8b`) and a Retry button. The previous digest remains visible.
- Retry processes only items without valid classifications for the current profile versions.

---

## 6. Data model

SQLite with WAL. Timestamps stored as UTC ISO-8601 text.

### `profiles`
| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | e.g. `ds.v1` |
| `name` | TEXT | display name |
| `kind` | TEXT | `work` or `fun` |
| `threshold` | REAL | default 0.55 |
| `is_active` | INTEGER | at most one row is 1 |
| `created_at` | TEXT | |

### `categories`
Current, editable definition of a category.

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
Immutable snapshots for preview, rollback, and cache identity.

| Column | Type | Notes |
|---|---|---|
| `profile_id` | TEXT | |
| `category_id` | TEXT | |
| `version_hash` | TEXT | SHA-256 prefix |
| `snapshot_json` | TEXT | name, description, criteria, reason hint |
| `created_at` | TEXT | |
| PRIMARY KEY | (`profile_id`, `category_id`, `version_hash`) | |

`version_hash` is computed from the normalized classification-relevant fields: name, description, `criteria_true`, `criteria_false`, and `reason_hint`. Weight and threshold are excluded because they do not affect probabilities.

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

### `items`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `feed_id` | INTEGER FK | |
| `guid` | TEXT | feed-provided ID when present |
| `canonical_url` | TEXT | tracking parameters removed |
| `url_hash` | TEXT | SHA-256 of canonical URL |
| `title` | TEXT | plain text |
| `summary` | TEXT | plain text, max 700 chars |
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

The cache key is `(item_hash, profile_id, category_id, category_version, model)`. The profile is included to avoid collisions between identical category IDs in different profiles. Probabilities are stored once per category; threshold changes are applied at read time and never require a model call.

### `jobs`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `kind` | TEXT | `refresh` or `rescore` |
| `profile_id` | TEXT | |
| `status` | TEXT | queued, running, preview, done, failed |
| `processed` | INTEGER | |
| `total` | INTEGER | |
| `error` | TEXT | nullable |
| `result_json` | TEXT | preview diff or summary |
| `created_at` | TEXT | |
| `updated_at` | TEXT | |

### `settings`
Key/value store for `hide_low_confidence`, `active_profile_id`, and UI preferences.

---

## 7. Classification contract

### 7.1 Backend

- Endpoint: `POST {OLLAMA_BASE_URL}/v1/systemone`
- Default model: `tev1:0.8b`
- No API key. Ollama is expected on localhost.
- One request per item, one `noul` question per enabled category.
- v1 UI cap: 16 categories. Hard limit: 64 questions per request.

### 7.2 Input

`state` is the feed title and summary only:

```
{title}

{summary}
```

Truncate to 1,200 characters on a word boundary. No article fetching. No HTML.

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
- A category is a match when probability ≥ profile threshold (default 0.55).
- If no category meets the threshold, the item has no labels and appears in Low confidence.
- There is no `none` category. Absence of a match is represented by an empty label set.
- `confidence` from the API is concentration, not correctness. The UI shows the match probability, not "confidence."

### 7.4 Reasons

Reasons are templated, never generated:

```
Matched {category.name}: {category.reason_hint} (p={probability:.2f})
```

Example: `Matched LLMs & Generative AI: covers LLMs, agents, prompting, RAG, or generative-AI releases (p=0.82)`.

### 7.5 Failure behavior

- If the model is unreachable, the model is not pulled, or a request fails after retries, the job fails.
- The previous digest stays visible.
- The UI shows the error, the exact remediation command, and a Retry button.
- A retry classifies only items missing a valid classification for the active category versions and model.
- No partial digest is rendered from a failed job.

### 7.6 Model and version changes

- Changing `TEV_MODEL` creates new cache entries; switching back reuses prior results.
- Editing classification-relevant category fields creates a new `version_hash`.
- Old classifications are retained for rollback and audit.
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

- Canonical URL: lowercase host, remove fragments, remove `utm_*`, `fbclid`, `gclid`, `mc_cid`, and `ref`, strip trailing slash.
- Title key: lowercase, collapse whitespace, strip punctuation.
- An item is a duplicate if the canonical URL matches within the feed, or if the title key matches another item from the same feed.
- Duplicate feed entries do not create new items; the newest metadata wins.

### 8.3 Low confidence

- Contains items with no label at or above threshold.
- Sorted by the highest probability across categories.
- Collapsed by default.
- Hidden entirely when `hide_low_confidence` is enabled.
- Items with no labels do not appear in category sections.

### 8.4 Retention

- Delete items older than 30 days and their classifications during refresh.
- Jobs older than 30 days are deleted.
- No user-facing export in v1.

---

## 9. Architecture

### 9.1 Components

- **Web app:** FastAPI serving Jinja templates and HTMX fragments.
- **Database:** SQLite in WAL mode, one file under the app data directory.
- **Feed service:** `feedparser` for RSS/Atom, `requests` for conditional GETs, per-feed timeouts and error isolation.
- **Classifier:** Ollama `/v1/systemone`, one request per item, sequential by default.
- **Job runner:** one in-process worker thread plus a jobs table. No Celery, Redis, or scheduler.
- **Profiles:** YAML seeds under `profiles/`, imported into SQLite on first run.
- **Eval:** standalone harness under `eval/`, not part of the web app.

### 9.2 Suggested layout

```
app/
  main.py
  db.py
  models.py
  routes/
  services/
    feeds.py
    classify.py
    rank.py
    jobs.py
  templates/
profiles/
  ds.v1.yaml
  fun.v1.yaml
  catalog.yaml
eval/
  items.jsonl
  harness.py
spec.md
```

### 9.3 Routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Redirect to active profile digest |
| GET | `/profiles/{id}/digest` | Digest with 72-hour sections |
| GET | `/profiles/{id}/categories` | Category list and editor |
| POST | `/profiles/{id}/categories` | Create or update a category |
| POST | `/profiles/{id}/categories/{cid}/preview` | Build preview diff |
| POST | `/profiles/{id}/categories/{cid}/apply` | Apply preview atomically |
| GET | `/profiles/{id}/feeds` | Feed manager |
| POST | `/profiles/{id}/feeds` | Add a feed |
| POST | `/profiles/{id}/feeds/{fid}/toggle` | Enable or disable |
| POST | `/profiles/{id}/refresh` | Start refresh job |
| GET | `/jobs/{id}` | Job progress fragment |
| GET | `/settings` | Settings |
| POST | `/settings` | Update settings |

### 9.4 Configuration

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | local model endpoint |
| `TEV_MODEL` | `tev1:0.8b` | decision model |
| `DB_PATH` | `./data/feedfilter.db` | SQLite file |
| `HOST` | `127.0.0.1` | bind address |
| `PORT` | `8000` | |
| `MAX_NEW_ITEMS` | `50` | classification cap per refresh |
| `RETENTION_DAYS` | `30` | |
| `WINDOW_HOURS` | `72` | digest window |
| `RESCORE_DAYS` | `14` | retroactive preview window |
| `THRESHOLD` | `0.55` | default for new profiles |

### 9.5 Dependencies

`fastapi`, `uvicorn`, `jinja2`, `python-multipart`, `feedparser`, `requests`, `pyyaml`, `pydantic`, `python-dateutil`; Python's built-in `unittest` and `ruff` for development.

The model runs outside the app in Ollama. No model weights are bundled.

---

## 10. Non-functional requirements

- **Local only:** bind to loopback, no auth, no outbound calls except public feeds.
- **Privacy:** no accounts, no private services, no telemetry. Feed content is public and stored only as title, summary, URL, and timestamps.
- **Security:** escape all feed text in templates; strip HTML; allow only public HTTP(S) feed URLs and reject private IP ranges; no script execution from feed content.
- **Performance:** manual refresh of 50 items should finish under 3 minutes on an 8-core CPU with `tev1:0.8b`; page responses under 200 ms excluding refresh.
- **Reliability:** feed errors are isolated; model errors fail the job explicitly; digest writes are transactional.
- **Cost:** zero inference cost; local model only.
- **Retention:** 30 days.
- **Logging:** local rotating log; do not log full summaries by default.
- **Accessibility:** keyboard-operable editor, visible focus, sufficient contrast.
- **Testing:** unit tests for parsing, dedupe, ranking, and cache keys; fixture feeds for integration; the eval harness for model quality.

---

## 11. Evaluation and model gate

### 11.1 Eval set

- `eval/items.jsonl`: 100 items, 50 per profile.
- Each item: `id`, `profile`, `source`, `title`, `summary`, `url`, `published`, `labels`, `label_status: manual`.
- Labels are multi-label and use the profile category IDs.
- Items with no relevant category have an empty label list.
- Current composition: 8 `ds.v1` categories and 9 `fun.v1` categories, with 2–19 positive labels per category and 3–4 no-label items per profile.
- Ground truth was labeled from title + summary only, matching the model input.
- Do not tune the threshold on this set. Treat it as a fixed gate.

### 11.2 Harness

`python eval/harness.py --model tev1:0.8b --gate --out eval/results-0.8b.json`

- Validates profiles and labels (`--dry-run` performs no model calls).
- Calls Ollama once per item with one `noul` question per enabled category.
- Applies threshold 0.55.
- Reports per-category precision, recall, and F1; macro-F1; micro-F1; precision@10 per profile; Brier score; p50/p95 latency; and harness RSS.
- Exits non-zero when the gate fails or a request fails.

### 11.3 Gate

Phase 0 passes only if, for both profiles:

| Metric | Threshold |
|---|---|
| Macro-F1 | ≥ 0.75 |
| Precision@10 | ≥ 0.70 |
| p95 latency per item | ≤ 2.0 s |
| Request failures | 0 |

Procedure:

1. Pull `tev1:0.8b` (about 812 MB) and run it against the full 100-item set.
2. If it passes, make `tev1:0.8b` the default and proceed to UI work.
3. If it fails, run the identical harness against `tev1:4b`. The 4B model needs about 4.6 GB of free RAM and about 4.5 GB of free disk; close other applications before testing.
4. If 4B passes, set `TEV_MODEL=tev1:4b` as the default and document the memory requirement.
5. If both fail, revise the taxonomy and criteria first (fewer, sharper categories; less overlap), then rerun. Do not add UI features to compensate for weak classification.
6. Record the model, Ollama version, date, and metrics in the results file.

A 100-item set gives noisy estimates. The gate is a floor, not proof. Expand to 200 items after v1 if category-level errors remain.

### 11.4 Known model limitations

- `tev1:0.8b` is an 812 MB experimental decision model. Public benchmark accuracy is about 63.5% versus 73.3% for the 4B model, and custom taxonomies are out of distribution.
- Practical context is about 2,000 tokens. Keep state under 1,200 characters and categories under 16 in v1.
- Probabilities are model preferences, not calibrated confidence.
- Categories with overlapping definitions (analytics vs experimentation; LLM research vs applied LLM) will produce the most errors; the criteria fields exist to separate them.

---

## 12. Milestones

| Phase | Work | Exit condition |
|---|---|---|
| 0 | Ollama upgrade and model pull, eval harness, 100-item labels, gate run | Gate passes for one model or taxonomy is revised |
| 1 | SQLite schema, migrations, seed catalog, feed fetch and parse | Refresh stores items and reports per-feed status |
| 2 | Classifier client, category cache, error state, retry | One item classifies end to end; failures are actionable |
| 3 | Category editor, preview diff, apply and rollback, 14-day re-score | Edit changes digest only after confirmation |
| 4 | Digest grouped by category, ranking, low-confidence section, reasons | 72-hour digest renders correctly |
| 5 | Feed manager, settings, dedupe, retention | User can add/remove feeds; duplicates are suppressed |
| 6 | Tests, fixtures, logging, accessibility pass, docs | Test suite green; manual refresh works from empty DB |
| 7 | Buffer and hardening | v1 acceptance checklist complete |

Phase 0 is a hard gate. Do not start Phase 1 UI work until a model passes or the taxonomy is revised and retested.

---

## 13. Acceptance criteria

- First run creates the database, seeds both profiles and feed catalogs, and sets one active profile.
- Refresh backfills 7 days, respects the 50-new-item cap, and produces a 72-hour digest.
- Every shown item has at least one tag and a one-line templated reason.
- Low-confidence items are collapsed and can be hidden.
- Editing a category shows preview counts and examples before apply.
- Apply re-scores the last 14 days only and is atomic; cancel leaves the digest unchanged.
- Weight edits reorder results without model calls; threshold edits re-filter without model calls.
- Ollama downtime produces an error banner with the correct command and a working retry.
- Duplicate feed entries do not create duplicate digest items.
- No private services, accounts, telemetry, or full-text fetching are present.
- `python eval/harness.py --dry-run` validates the eval set.
- `python eval/harness.py --gate` passes with the chosen model before release.

---

## 14. Risks and open questions

**Risks**
- `tev1:0.8b` accuracy may not clear the gate; 4B may be required, raising memory use to ~4.6 GB.
- CPU inference may exceed the 2 s/item latency target if other workloads compete for cores.
- RSS summaries vary in length and quality; some feeds provide title only and will not be usable.
- Overlapping categories will produce systematic false positives.
- Feed URLs and formats break; per-feed isolation and visible status are required.
- Prompt injection in feed text is possible. The model has no tools, but hostile text can still bias a decision; low-confidence handling is the mitigation.

**Open questions**
- Final product name.
- Whether 4B becomes the shipped default after the gate.
- Whether to expose `TEV_MODEL` in the UI or keep it environment-only.
- Whether 30-day retention is useful when the digest is stateless.
- Whether to add a second eval set for categories created from scratch.

---

## Appendix A — Seeded profiles

Files: `profiles/ds.v1.yaml`, `profiles/fun.v1.yaml`.

`ds.v1` categories: ML Methods & Research; LLMs & Generative AI; MLOps & Deployment; Experimentation & Causal Inference; Data Engineering & Pipelines; DS Tooling & Libraries; Analytics & Visualization; Responsible AI & Evaluation.

`fun.v1` categories: Cooking & BBQ; Books, Film & Music; DIY & Woodworking; Cycling & Outdoors; Games & Tabletop; Travel & Day Trips; Gadgets & Gear; Science & Space; Local Culture & Events.

The fun profile excludes work/industry news, politics, celebrity gossip, and health or medical advice.

## Appendix B — Seed feed catalog

`ds.v1`: arXiv cs.LG, arXiv stat.ML, KDnuggets, MIT News AI, Simon Willison, Google AI Blog, Analytics Vidhya, dbt Blog, Data Engineering Weekly, Netflix Tech Blog, Databricks Blog, Occam's Razor.

`fun.v1`: Smitten Kitchen, Bon Appétit, Literary Hub, IndieWire, Pitchfork, CyclingTips, Bikepacking.com, Dicebreaker, Polygon, Make, Atlas Obscura, The Verge, ScienceDaily.

Health-focused feeds are excluded from the seed catalog. Users may add public feeds, subject to URL validation.

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
