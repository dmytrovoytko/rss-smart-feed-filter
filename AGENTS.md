# AGENTS.md — Agent entrypoint

> This file is the first thing an agent reads.
> Coordination process: `_docs/process.md`
> Active task tracker: `TODO.md`

## 1. Project overview

- Project name: Smart Feed Filter
- One-line purpose: turns public RSS feeds into a short, category-grouped digest for one person, running entirely locally with a decision model via Ollama
- Primary users: data scientists, developers, product managers, marketers, analysts, and off‑work readers who want a short daily list without accounts or cloud services
- Current state: prototype
- Main constraint: privacy + no cloud calls
- What success looks like: a 72‑hour RSS digest grouped by category with ranked items and templated reasons; zero inference cost; local model only

## 2. Architecture and constraints

- Stack: Python 3.12, FastAPI, Jinja2 + HTMX, uvicorn
- Build system: setuptools>=77
- Package manager: pip
- Deployment target: 127.0.0.1:8000 (local only)
- Data storage: SQLite (single file under app data directory)
- External services: Ollama `POST /v1/systemone` (local model); no other external APIs
- Offline/PWA requirements: none
- Browser/platform support: 127.0.0.1 (loopback), modern desktop browsers on localhost
- Hard constraints: loopback-only bind, no auth, no telemetry, no article fetching, title+summary only, 72‑hour window, 50 items per refresh

Do not add dependencies, frameworks, services, or build tools without explicit `@owner` approval.

## 3. Commands

```sh
# Install
pip install -e '.[dev]'

# Run locally
feed-filter

# Run tests
python -m unittest discover -s tests -v

# Lint
ruff check .

# Typecheck
# no typechecker configured

# Build
# no extra steps for now

# Deploy
# not applicable (local-only); start with `feed-filter`
```

If a command is unavailable, record the limitation in the relevant task instead of claiming it passed.

## 4. Repository map

| Path | Purpose |
|---|---|
| `AGENTS.md` | This entrypoint: project context, commands, constraints, and workflow. |
| `_docs/process.md` | Coordination process: roles, statuses, claiming, locks, verification, and completion. |
| `TODO.md` | Active task tracker. |
| `.locks/` | Ephemeral task locks; task locks live in `<task-id>.lock/` subdirectories (e.g. `.locks/TASK-001.lock/`). Never commit. |
| `app` | Application source folder. |
| `profiles/` | Feed profiles folder. |
| `tests/` | Automated tests folder. |
| `eval/` | Evaluation scripts and results folder. |
| `_docs/spec.md` | Product specification. |
| `_docs` | Product or technical documentation. |
| `README.md` | Main front door manual of the project. |
| `pyproject.toml` | Unified Python configuration file for the project. |
| `requirements.txt` | Pinned project dependencies. |
| `data/` | Local feedfilter.db and log storage. Never commit. |
| `.env.example` | Template of project environment variables. |
| `.gitignore` | Files or directories to intentionally prevent in Git repo. |

## 5. Agent workflow

Orientation only; the authoritative rules are in `_docs/process.md`.

1. Read `AGENTS.md`, `_docs/process.md`, and `TODO.md` before doing work.
2. Tasks have exactly four states: `[ ]` ready, `[~]` claimed, `[!]` parked, `[x]` done. See "Status model" in `_docs/process.md`.
3. Follow the "Quick checklist" in `_docs/process.md` for claiming, parking, verifying, and completing work.
4. If you stop at a role boundary, park to `[!]` with an `Assignee` and `Reason` (see "Parking").

## 6. Security and privacy

- Never commit secrets, tokens, credentials, `.env` files, or private URLs.
- If API keys are ever used, keep them in env/approved store; never browser, logs, or commits.
- Treat external input, generated content, URLs, and model output as untrusted. Validate and escape before use.
- Do not add analytics, tracking, third-party beacons, or background data collection by default.
- Do not collect, log, or transmit personal data unless the product spec explicitly requires it and the owner approves.
- Keep network calls explicit and minimal. Document every external service in section 2.
- Do not commit generated artifacts, local caches, lock directories, session logs, or agent transcripts.
- `TODO.md` is public unless stated otherwise. Do not put secrets, personal data, or local filesystem paths in it.

## 7. Documentation links

- Coordination process: [`_docs/process.md`](_docs/process.md)
- Definition of done: see `_docs/process.md` "Definition of done"
- Task tracker: [`TODO.md`](TODO.md)
- Product specification, architecture notes: `_docs/spec.md` 
- Runbook: none
- Bootstrap checklist: `_docs/bootstrap.md`

If this file, the process, and the specification disagree, do not silently choose one, stop and ask `@owner`.
