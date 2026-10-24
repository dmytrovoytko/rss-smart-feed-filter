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
  - Assignee: @owner, @qa
  - Verified by: @owner
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
  - Verified by: @owner
  - Evidence: threshold results and predictions artifacts.
  - Resolution: Selected the 0.62 threshold after a five-point sweep and added prediction output for manual label comparison.

## Open backlog

## Open questions

<!-- Add owner decisions, blockers, or ambiguities here. -->

## Activity log

- [2026-10-24 08:08 UTC] @owner: Created this tracker from the TODO.md skeleton and added initial tasks.
- [2026-10-24 18:45 UTC] @owner: Completed and verified TASK-002 - TASK-006.
