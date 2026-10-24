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

## Open backlog

- [!] TASK-002: Review, fill, and adjust the AGENTS.md template according to bootstrap checklist
  - Priority: High
  - Acceptance criteria:
    - `AGENTS.md` exists at the repository root and is the agent entrypoint.
    - Every placeholder is replaced with project-specific information.
    - Commands are tested or clearly marked unavailable.
    - The repository map and documentation links match the repository.
    - Security and privacy rules match the project and contain no secrets or local paths.
  - Tags: #docs #setup
  - Parked by: @owner
  - Parked at: 2026-10-24 08:08 UTC
  - Assignee: @owner
  - Reason: rework
  - State: draft is done / finalization remains
  - Next step: finalize AGENTS.md, verify against acceptance criteria, and close TASK-002
  - Evidence: AGENTS.md content aligned with project overview, architecture, constraints, commands, repository map, security rules, and documentation links.
  - Suggested role: @owner
  - Dependencies: None
  - Subtasks:
    - [x] 1. Copy the AGENTS.md template into the repository.
    - [x] 2. Fill project overview, architecture, and constraints.
    - [x] 3. Fill and verify commands.
    - [x] 4. Fill the repository map and documentation links.
    - [x] 5. Review security/privacy rules and the bootstrap checklist.
    - [ ] 6. Check and resolve all conflicts and inconsistencies
  - Notes: Start from the self-contained AGENTS.md template. Do not leave placeholder text, template-only instructions, secrets, or local filesystem paths.

- [!] TASK-003: Review and adjust the process.md template
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
  - Parked by: @owner
  - Parked at: 2026-10-24 08:08 UTC
  - Assignee: @owner
  - Reason: rework
  - State: draft is done / finalization remains
  - Next step: finalize `_docs/process.md`, verify against acceptance criteria, and close TASK-003
  - Evidence: `_docs/process.md` contains self-contained process, task schema, lock, and workflow rules.
  - Suggested role: @owner
  - Dependencies: None
  - Subtasks:
    - [x] 1. Copy the self-contained process template into `_docs/process.md`.
    - [x] 2. Verify roles, statuses, and the lock protocol.
    - [x] 3. Verify completion, parking, triage, escalation, and blocked/abandoned rules.
    - [x] 4. Verify the task schema and TODO.md skeleton.
    - [x] 5. Remove template-only references and validate links.
    - [ ] 6. Check and resolve all conflicts and inconsistencies
  - Notes: The process must work in a new empty repository without depending on another process file.

## Open questions

<!-- Add owner decisions, blockers, or ambiguities here. -->

## Activity log

- [2026-10-24 08:08 UTC] @owner: Created this tracker from the TODO.md skeleton and added initial tasks.
