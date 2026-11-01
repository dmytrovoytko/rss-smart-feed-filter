# TODO — Smart Feed Filter

> Active task tracker and evidence log. Read this file before claiming work.
>
> Coordination process: [`_docs/process.md`](_docs/process.md)
> Agent entrypoint: [`AGENTS.md`](AGENTS.md)
> Tracker URL: https://github.com/dmytrovoytko/rss-smart-feed-filter/blob/main/TODO.md

## Status

| State | Meaning |
|---|---|
| `[ ]` | Ready and unclaimed |
| `[~]` | Claimed and in progress |
| `[!]` | Parked — assigned to a named handler; only that handler may claim |
| `[x]` | Completed and verified |

## Completed

<!-- Move fully verified `[x]` tasks here after completing project milestones. Keep the original task ID and evidence. -->

- [x] TASK-001: Specification and seed profiles
  - Priority: High
  - Acceptance criteria:
    - Product spec documents the problem, goals and non-goals, data model, classification contract, ranking, and acceptance criteria.
    - `ds.v1` defines 8 work categories with descriptions, criteria, and weights; `fun.v1` defines 9 off-work categories.
    - Both profiles load as valid YAML and pass evaluation-harness validation.
  - Tags: #spec #profiles
  - Assignee: @dev
  - Completed: 2026-10-24 08:08 UTC
  - Verified by: @owner
  - Evidence: commit 486e507; spec.md; profiles/ds.v1.yaml; profiles/fun.v1.yaml.
  - Resolution: Authored the product specification and seeded both reading profiles used by the classifier and evaluation harness.

- [x] TASK-002: Review, fill, and adjust the AGENTS.md template according to bootstrap checklist
  - Priority: High
  - Acceptance criteria:
    - `AGENTS.md` exists at the repository root and is the agent entrypoint.
    - Every placeholder is replaced with project-specific information.
    - Commands are tested or clearly marked unavailable.
    - The repository map and documentation links match the repository.
    - Security and privacy rules match the project and contain no secrets or local paths.
  - Tags: #docs #setup
  - Assignee: @owner
  - Completed: 2026-10-24 17:35 UTC
  - Verified by: @owner, @qa
  - Evidence: AGENTS.md contains project-specific overview, architecture, constraints, commands, repository map, security rules, and documentation links. `.venv/bin/python -m unittest discover -s tests -v` passed 57 tests; `.venv/bin/ruff check .` passed.
  - Resolution: Finalized AGENTS.md against the bootstrap checklist; no placeholders remain, the repository map matches the repository, and commands are verified with the local `.venv`.
  - Dependencies: None
  - Subtasks:
    - [x] 1. Copy the AGENTS.md template into the repository.
    - [x] 2. Fill project overview, architecture, and constraints.
    - [x] 3. Fill and verify commands.
    - [x] 4. Fill the repository map and documentation links.
    - [x] 5. Review security/privacy rules and the bootstrap checklist.
    - [x] 6. Check and resolve all conflicts and inconsistencies

- [x] TASK-003: Review and adjust the process.md template
  - Priority: High
  - Acceptance criteria:
    - `_docs/process.md` exists and is self-contained.
    - It defines roles, the four task states (`[ ]`, `[~]`, `[!]`, `[x]`), the claim/lock protocol, verification, completion, parking, triage, escalation, and blocked/abandoned rules.
    - It contains no references to another process document and no template-only placeholders.
    - Its task schema matches this tracker.
    - Timestamp formats match `_docs/process.md` (see section "Task format and tracker skeleton").
    - Lock paths match `_docs/process.md` (see section "Claim with an atomic lock").
    - All relative links resolve.
  - Tags: #docs #process #setup
  - Assignee: @owner
  - Completed: 2026-10-24 17:35 UTC
  - Verified by: @owner, @qa
  - Evidence: `_docs/process.md` contains a self-contained process, task schema, lock protocol, and workflow rules; timestamp format (`YYYY-MM-DD HH:mm UTC`) and lock path (`.locks/<task-id>.lock/`) match this tracker and AGENTS.md.
  - Resolution: Finalized `_docs/process.md`; it is self-contained, matches the tracker schema, and has no template-only references.
  - Dependencies: None
  - Subtasks:
    - [x] 1. Copy the self-contained process template into `_docs/process.md`.
    - [x] 2. Verify roles, statuses, and the lock protocol.
    - [x] 3. Verify completion, parking, triage, escalation, and blocked/abandoned rules.
    - [x] 4. Verify the task schema and TODO.md skeleton.
    - [x] 5. Remove template-only references and validate links.
    - [x] 6. Check and resolve all conflicts and inconsistencies

- [x] TASK-004: Evaluation harness and labeled dataset
  - Priority: High
  - Acceptance criteria:
    - `eval/harness.py` validates profiles and labels and supports `--dry-run` and `--gate`.
    - The harness computes per-category precision, recall, and F1; macro/micro-F1; precision@10; Brier score; and latency.
    - `eval/items.jsonl` provides 100 labeled items, 50 per profile.
    - Baseline `eval/results-0.8b.json` is recorded.
  - Tags: #eval
  - Assignee: @dev
  - Completed: 2026-10-24 08:08 UTC
  - Verified by: @owner
  - Evidence: commit 486e507; eval/harness.py; eval/items.jsonl; eval/results-0.8b.json.
  - Resolution: Built the model-quality gate and a 100-item labeled evaluation set, then recorded the first `tev1:0.8b` baseline.

- [x] TASK-005: Category criteria tuning v1–v4
  - Priority: Medium
  - Acceptance criteria:
    - Category `description`, `criteria_true`, `criteria_false`, and `reason_hint` are iterated for `ds.v1` and `fun.v1`.
    - Harness results are re-run and recorded after each iteration.
    - Overlapping categories (ML methods vs LLM apps vs experimentation) are separated by explicit criteria.
  - Tags: #eval #profiles
  - Assignee: @dev
  - Completed: 2026-10-24 16:41 UTC
  - Verified by: @owner
  - Evidence: profile criteria and harness/result updates.
  - Resolution: Tuned the category taxonomy in four iterations to reduce overlap and improve classification quality.

- [x] TASK-006: Threshold selection and prediction comparison
  - Priority: Medium
  - Acceptance criteria:
    - Thresholds 0.50, 0.55, 0.60, 0.65, and 0.70 are evaluated; 0.62 is selected for `ds.v1`.
    - The harness emits per-item predictions for label comparison.
    - `eval/results-0.8b-threshold-0.62.json` and `eval/predictions-0.8b-threshold-0.62.json` are recorded.
  - Tags: #eval #threshold
  - Assignee: @dev
  - Completed: 2026-10-24 18:43 UTC
  - Verified by: @owner
  - Evidence: threshold results and predictions artifacts.
  - Resolution: Selected the 0.62 threshold after a five-point sweep and added prediction output for manual label comparison.

- [x] TASK-007: Config, DB schema, logging, packaging
  - Priority: High
  - Acceptance criteria:
    - `app/config.py` loads environment/`.env` values and validates settings.
    - `app/db.py` and `app/schema.sql` initialize SQLite with WAL and schema versioning.
    - `app/logging_config.py` provides rotating file and stream logs.
    - `pyproject.toml`, `requirements.txt`, and `.env.example` make the app installable and runnable.
  - Tags: #backend
  - Assignee: @dev
  - Completed: 2026-10-25 18:31 UTC
  - Verified by: @owner
  - Evidence: app/config.py; app/db.py; app/schema.sql; app/logging_config.py; app/timeutil.py; app/versioning.py; .env.example; pyproject.toml; requirements.txt.
  - Resolution: Established the application foundation: validated configuration, SQLite schema, logging, and packaging.

- [x] TASK-008: Database seeding and feed catalog
  - Priority: High
  - Acceptance criteria:
    - `app/seed.py` imports profile presets and validates public HTTP(S) feed URLs.
    - `profiles/catalog.yaml` supplies seeded feeds for `ds.v1` and `fun.v1`.
    - First run seeds both profiles and sets one active profile.
  - Tags: #backend #seed
  - Assignee: @dev
  - Completed: 2026-10-25 18:31 UTC
  - Verified by: @owner
  - Evidence: app/seed.py; profiles/catalog.yaml.
  - Resolution: Added first-run database seeding and the per-profile feed catalog.

- [x] TASK-009: Feed ingestion and classification services
  - Priority: High
  - Acceptance criteria:
    - `app/services/feeds.py` fetches, parses, and dedupes feeds with per-feed error isolation.
    - `app/services/classify.py` calls Ollama `/v1/systemone` per item and category and caches results by category version and model.
    - Model and feed failures surface actionable errors.
  - Tags: #backend #feeds #classify
  - Assignee: @dev
  - Completed: 2026-10-25 18:31 UTC
  - Verified by: @owner
  - Evidence: app/services/feeds.py; app/services/classify.py.
  - Resolution: Implemented the ingestion and classification pipeline, including dedupe, caching, and error handling.

- [x] TASK-010: Digest, ranking, preferences, and job runner
  - Priority: High
  - Acceptance criteria:
    - `app/services/digest.py` builds the 72-hour digest grouped by category and ranked by probability × weight × recency decay.
    - `app/services/jobs.py` runs refresh and rescore jobs with progress and retry.
    - `app/services/preferences.py` manages settings such as `hide_low_confidence` and the active profile.
  - Tags: #backend #digest #jobs
  - Assignee: @dev
  - Completed: 2026-10-25 18:31 UTC
  - Verified by: @owner
  - Evidence: app/services/digest.py; app/services/jobs.py; app/services/preferences.py.
  - Resolution: Added digest ranking, the job runner, and preference handling.

- [x] TASK-011: FastAPI application and routes
  - Priority: High
  - Acceptance criteria:
    - `app/main.py` creates the app, initializes the database, seeds data, and mounts templates and static files.
    - `app/routes/pages.py` serves the digest, refresh, job fragments, retry, and health endpoints.
    - The app starts locally via the `feed-filter` entry point.
  - Tags: #backend #routes
  - Assignee: @dev
  - Completed: 2026-10-25 18:31 UTC
  - Verified by: @owner
  - Evidence: app/main.py; app/routes/pages.py; app/__main__.py.
  - Resolution: Wired the FastAPI application and HTTP routes for digest, refresh, jobs, retry, and health checks.

- [x] TASK-012: Jinja/HTMX templates and static UI
  - Priority: High
  - Acceptance criteria:
    - `base.html`, `digest.html`, and `partials/job.html` render digest and job state.
    - `app/static/app.css`, `htmx.min.js`, and the favicon support the HTMX UI.
    - Refresh progress polls via HTMX and renders without a full-page reload.
  - Tags: #ui
  - Assignee: @dev
  - Completed: 2026-10-25 18:31 UTC
  - Verified by: @owner
  - Evidence: app/templates/base.html; app/templates/digest.html; app/templates/partials/job.html; app/static/app.css; app/static/htmx.min.js; app/static/favicon.png.
  - Resolution: Delivered the Jinja/HTMX interface for the digest, job progress, and retry flow.

- [x] TASK-013: Lint fixes
  - Priority: Low
  - Acceptance criteria:
    - `ruff check .` passes.
  - Tags: #lint #cleanup
  - Assignee: @dev
  - Completed: 2026-10-25 18:45 UTC
  - Verified by: @owner
  - Evidence: app/routes/pages.py; eval/harness.py; `ruff check .` passes.
  - Resolution: Resolved lint findings.

- [x] TASK-014: Automated test suite
  - Priority: High
  - Acceptance criteria:
    - `tests/` covers classification, digest, feeds, foundation, and jobs.
    - The suite runs with `python -m unittest discover -s tests -v`.
  - Tags: #tests
  - Assignee: @dev
  - Completed: 2026-10-25 19:32 UTC
  - Verified by: @owner
  - Evidence: tests/test_classify.py; tests/test_digest.py; tests/test_feeds.py; tests/test_foundation.py; tests/test_jobs.py; 57 tests pass.
  - Resolution: Added the regression suite covering the core services and data layer.

- [x] TASK-015: README and screenshots
  - Priority: Medium
  - Acceptance criteria:
    - README documents the workflow, architecture.
    - Screenshots of the categorized digest are stored under `_docs/screenshots/`.
  - Tags: #docs
  - Assignee: @owner
  - Completed: 2026-10-25 19:58 UTC
  - Verified by: @owner
  - Evidence: README.md; _docs/screenshots/.
  - Resolution: Documented workflow and architecture, added UI screenshots.

- [x] TASK-016: Naming and wording fixes
  - Priority: Medium
  - Acceptance criteria:
    - Project naming is consistent across `_docs/spec.md`, `_docs/bootstrap.md`, and `pyproject.toml`.
    - `pyproject.toml` `readme` points to an existing file.
    - The verification section documents that some tasks may require multiple verifiers.
  - Tags: #docs #cleanup
  - Assignee: @owner
  - Completed: 2026-11-01 13:12 UTC
  - Verified by: @owner
  - Evidence: _docs/bootstrap.md; _docs/process.md; _docs/spec.md; pyproject.toml.
  - Resolution: Aligned project naming, fixed the bootstrap wording, documented multi-verifier tasks, and corrected the package readme path to `README.md`.

- [x] TASK-017: README quickstart and configuration
  - Priority: Medium
  - Acceptance criteria:
    - README documents Quickstart and Configuration.
    - README repository tree and profile link match the repository.
    - Model-gate wording matches the recorded evaluation results.
  - Tags: #docs
  - Assignee: @owner
  - Completed: 2026-11-01 13:13 UTC
  - Verified by: @owner
  - Evidence: README.md.
  - Resolution: Added Quickstart/Configuration, corrected the spec path and profile link, and reworded the gate result as falling short of the original targets.

## Open backlog

## Open questions

<!-- Add owner decisions, blockers, or ambiguities here. -->

## Activity log

- [2026-10-24 08:08 UTC] @owner: Created this tracker from the TODO.md skeleton and added initial tasks.
- [2026-10-24 18:45 UTC] @owner: Completed and verified TASK-002 - TASK-006.
- [2026-10-25 18:35 UTC] @owner: Completed and verified TASK-007 - TASK-012.
- [2026-10-25 20:15 UTC] @owner: Completed and verified TASK-013 - TASK-015.
- [2026-11-01 13:13 UTC] @owner: Completed and verified TASK-016 - TASK-017.
