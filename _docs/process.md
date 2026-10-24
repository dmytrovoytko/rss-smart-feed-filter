# Process — self-contained agent workflow

This file is the complete coordination process. It does not depend on another process document and can be copied into a new empty repository.

## 1. Purpose

This process keeps multi-agent work simple, auditable, and safe:

- one active task per agent;
- one combined claimed/in-progress state;
- one atomic filesystem lock per task;
- clear completion evidence;
- no need for external issue tracker.

## 2. Project rules

- `TODO.md` is the active task tracker.
- Read `TODO.md` before claiming any task.
- Work on one task at a time.
- Commit regularly; never push. @owner reviews and pushes.
- Do not add dependencies or install tools without owner approval.
- `TODO.md` is the public task tracker if the repository is public; it satisfies the same role as an external tracker.
- Do not use GitHub Issues, Jira, or another external tracker service.
- If a task needs a credential, a product decision, or another external resource, park it for `@owner` and ask the owner.

## 3. Roles

| ID | Role | Owns | May write | Must not |
|---|---|---|---|---|
| `@owner` | Product owner | Scope, secrets, merges, production approval | Final decisions and sign-off, spec, `TODO.md`, locks | Delegate production approval |
| `@pm` | Product manager | Spec, backlog, acceptance criteria, dispatch queue | Spec, `TODO.md` | App or server code |
| `@dev` | Software engineer | Implementation | App, server, CSS, tests, task field updates | Create new tasks, change scope or self-approve |
| `@qa` | QA engineer | Independent verification | Test plans, test-related task field updates, review comments, QA notes | Production fixes; authoring new tests or editing product code while verifying |

One agent may hold multiple roles, but a task cannot be marked complete by the implementer alone. A different agent verifies it, or `@owner` records explicit sign-off. `@qa` verification is read-only; authoring tests or editing product code is QA task work, not verification. If the verifier must edit files, park via the parking procedure first.

## 4. Status model

Use exactly four task states:

| State | Meaning |
|---|---|
| `[ ]` | Ready and unclaimed |
| `[~]` | Claimed and in progress |
| `[!]` | Parked — assigned to a named handler; only that handler may claim |
| `[x]` | Completed and verified |

Rules:

- `[~]` combines claimed and in-progress. There is no separate `[/]` state.
- Set `[~]` only after the task lock is acquired.
- Keep `[~]` until the task is `[x]` or `[!]`.
- `[!]` is claimable only by the named `Assignee` (@pm, @owner, or @qa for verification). All other agents must skip it.
- Do not add an `updated:` timestamp for progress. Use `Discussion:` comments instead.
- The lock is per task. It does not make concurrent edits to `TODO.md` safe. Keep one active agent session until the owner enables concurrency.

## Transition matrix

```
[ ] --claim (lock)--------------------------> [~]
[~] --complete + verify---------------------> [x]
[~] --park (verification | rework)----------> [!] Assignee: @qa (verification) or @pm (rework)
[~] --park (continuation | dispatch)--------> [!] Assignee: @pm
[~] --park (blocked | abandoned | question)-> [!] Assignee: @owner
[!] --Assignee claims (lock)----------------> [~]
[~] @pm triage: reopen----------------------> [ ]  (Role: @dev|@qa, clarified AC)
[~] @pm triage: split + re-verify-----------> [ ]  (Rationale names new IDs)
[~] @pm triage: escalate--------------------> [!] Assignee: @owner
[~] @qa verify: pass------------------------> [x]  (Verified by: @qa)
[~] @qa verify: fail------------------------> [!] Assignee: @pm (Reason: rework)
[~] @owner resolve: reopen------------------> [ ]
[~] @owner resolve: done--------------------> [x]
[~] @owner keep parked----------------------> [!] Assignee: @owner
[x] --@owner reopen-------------------------> [ ]
```

Handling a parked item uses the normal lock/claim flow: `[!]` → `[~]` (lock) → outcome. This avoids "parked but locked" ambiguity and reuses existing semantics.

## 5. Task format and tracker skeleton

Minimal task schema:

```markdown
- [ ] TASK-001: Short imperative title
  - Priority: High | Medium | Low
  - Acceptance criteria:
    - Clear, verifiable condition.
  - Assignee: @unassigned
  - Role: @dev | @qa | @pm   (optional; restricts which agents may claim)
  - Tags: #docs (optional)
  - Dependencies: TASK-000 (optional)
  - Subtasks: (optional)
    - [ ] 1. Phase or component
      - [ ] 1.1 Detailed step
  - Discussion: (optional)
    - YYYY-MM-DD HH:mm UTC @agent #tag: Comment text
  - Notes: may include file paths (repo-relative only) or context (optional)
```

Parked task:

```markdown
- [!] TASK-001: Short imperative title
  - Priority: High
  - Acceptance criteria:
    - Clear, verifiable condition.
  - Parked by: @dev
  - Parked at: YYYY-MM-DD HH:mm UTC
  - Assignee: @pm
  - Reason: verification | rework | continuation | dispatch | blocked | abandoned | question
  - State: what is done / what remains
  - Next step: one concrete action
  - Evidence: commits, tests, logs
  - Suggested role: @qa   (optional; role, never a person)
  - Discussion:
    - YYYY-MM-DD HH:mm UTC @dev #park: Parked for @pm — awaiting triage
```

Claimed task:

```markdown
- [~] TASK-001: Short imperative title
  - Priority: High
  - Acceptance criteria:
    - Clear, verifiable condition.
  - Assignee: @dev (claimed: YYYY-MM-DD HH:mm UTC)
```

Handler-claimed parked task (handler has lock, doing triage/verification):

```markdown
- [~] TASK-001: Short imperative title
  - Priority: High
  - Acceptance criteria:
    - Clear, verifiable condition.
  - Assignee: @pm (claimed: YYYY-MM-DD HH:mm UTC)
  - Handled by: @pm (YYYY-MM-DD HH:mm UTC)
  - Decision: reopened | split | escalated | verified | resolved
  - Rationale: what changed
  - Next step: action
```

Completed task:

```markdown
- [x] TASK-001: Short imperative title
  - Priority: High
  - Acceptance criteria:
    - Clear, verifiable condition.
  - Assignee: @dev (claimed: YYYY-MM-DD HH:mm UTC)
  - Completed: YYYY-MM-DD HH:mm UTC
  - Verified by: @qa
  - Evidence: commands run and results.
  - Resolution: One or two sentences describing the change.
```

Formatting rules:

- Do not wrap task IDs or field names in bold.
- Omit optional fields when they are unknown.
- Use UTC timestamps in the form `YYYY-MM-DD HH:mm UTC`.
- Append discussion comments; never delete another agent's comment.
- Use `#abandoned` for unexpected stops, `#blocker` for dependencies, `#question` for owner decisions, `#bug`/`#fix` for defects, `#qa` for verification, `#park` for parking, `#reopen` for reopening.
- The parent task is authoritative. Mark it `[x]` only when all required subtasks are `[x]` and verification is recorded.

Minimal `TODO.md` skeleton:

```markdown
# TODO

> Active task tracker and evidence log. Read this file before claiming work.
> Coordination process: [`_docs/process.md`](_docs/process.md)
> Agent entrypoint: [`AGENTS.md`](AGENTS.md)

## Status

| State | Meaning |
|---|---|
| `[ ]` | Ready and unclaimed |
| `[~]` | Claimed and in progress |
| `[!]` | Parked — assigned to a named handler; only that handler may claim |
| `[x]` | Completed and verified |

## Completed

<!-- Move fully verified `[x]` tasks here after completing project milestones. Keep the original task ID and evidence. -->

## Open backlog

<!-- Add TASK-### entries here. -->

## Open questions

<!-- Add owner decisions, blockers, or ambiguities here. -->

## Activity log

<!-- Append milestone and completion notes here. Format:
- [YYYY-MM-DD HH:mm UTC] @owner: Log record.
-->
```

## 6. Task IDs and creation

- Task IDs use the form `TASK-001`, `TASK-002`, and so on.
- IDs are never reused.
- `@pm` or `@owner` allocates new IDs.
- To create a task, read `TODO.md`, choose the next unused number, add the task under `## Open backlog`, and commit.
- Until the owner enables concurrent sessions, only one agent session may edit `TODO.md`.
- When concurrent sessions are enabled, task creation is a shared `TODO.md` edit. Serialize it with `.locks/TODO.lock/` (coordination lock; does not correspond to a task) using the same atomic `mkdir` protocol as task locks.

## 7. Claim with an atomic lock

The lock is a directory created with `mkdir`, which is atomic on a local filesystem. A plain marker file created with `>` is not atomic.

Lock path:

```text
.locks/<task-id>.lock/
```

The lock directory is the authoritative ownership record. `TODO.md` is the readable record.

### Claim sequence (worker claiming `[ ]`)

1. Re-read `TODO.md`.
2. Create the lock directory with `mkdir`.
3. Write the owner record inside the lock directory.
4. Re-read `TODO.md` again and confirm the task is still `[ ]`.
5. Change the task to `[~]`, set `Assignee`, and add `(claimed: timestamp)`.
6. Keep the lock until the task is completed, parked, or abandoned.

Example:

```sh
task_id=TASK-003
mkdir -p .locks

if ! mkdir ".locks/$task_id.lock" 2>/dev/null; then
  echo "Task is already locked: .locks/$task_id.lock"
  cat ".locks/$task_id.lock/owner" 2>/dev/null || true
  exit 1
fi

printf 'agent=%s\ntime=%s\npid=%s\nhost=%s\n' \
  "@dev" \
  "$(date -u '+%Y-%m-%d %H:%M UTC')" \
  "$$" \
  "$(hostname)" \
  > ".locks/$task_id.lock/owner"
```

If any step fails before the task is `[~]`, remove the lock you just created and stop.

### Handler claim sequence (handler claiming `[!]`)

A handler claims a parked task assigned to their role. The `Assignee` field names the handler role (`@pm`, `@qa`, or `@owner`).

1. Re-read `TODO.md` and locate a `[!]` task whose `Assignee` equals your role.
2. Create the lock directory with `mkdir`.
3. Write the owner record inside the lock directory.
4. Re-read `TODO.md` again and confirm the task is still `[!]` with the same `Assignee`.
5. Change the task to `[~]` (retain `Assignee`), add `Handled by: @<role> (YYYY-MM-DD HH:mm UTC)`.
6. Do the work under the lock (triage, verify, resolve).
7. Resolve per the transition matrix; append the handler record; release the lock.

If any step fails before the task is `[~]`, remove the lock you just created and stop.

### Conflict rules

- Lock exists: do not claim. Use the owner record to see who has it.
- A brief lock + `[ ]` during a claim, or lock + `[x]`/`[!]` during finalization, is normal. Do not interfere. If it persists, notify `@owner`.
- `TODO.md` says `[~]` but no lock exists: stop and notify `@owner` about it. This is never normal; `@owner` repairs it.
- Lock exists more than 300 seconds but the task is `[x]` or `[!]`: treat it as a stale lock. Do not claim; notify `@owner` if it persists.
- `[!]` with a lock: a handler is actively handling it. Normal if brief; if stale, `@owner` cleans it.
- `[!]` with no `Assignee` or unknown handler: invalid; `@owner` repairs it (sets `Assignee: @owner`).
- Never delete a lock you did not create.
- Never steal a stale lock. Only `@owner` may remove one after checking that no session is active.

### Stale lock recovery

`@owner` only:

1. Confirm no active session owns the lock.
2. If the task is `[~]`, change it to `[!]`, add `(abandoned: timestamp)`, `Assignee: @owner`, and add an `#abandoned` comment with the last known state.
3. If the task is already `[x]` or `[!]`, leave the state unchanged.
4. Remove the stale lock.

```sh
cat ".locks/TASK-003.lock/owner"
# Confirm no active session owns this PID/host.
rm -rf -- ".locks/TASK-003.lock"
```

Lock directories are ephemeral. Never commit `.locks/`.

## 8. Work, parking, triage, verification, and completion

### Work

- Keep `[~]` for the whole task.
- Use `Discussion:` comments for progress, decisions, and findings.
- Do not change task state for progress.

### Parking

When a task reaches a clean boundary and needs a different handler, the current owner parks it instead of handing off.

1. Keep `[~]` until the moment of parking.
2. Change `[~]` → `[!]`.
3. Add these fields:
   ```
   - Parked by: @dev
   - Parked at: YYYY-MM-DD HH:mm UTC
   - Assignee: @qa | @pm | @owner
   - Reason: verification | rework | continuation | dispatch | blocked | abandoned | question
   - State: what is done / what remains
   - Next step: one concrete action
   - Evidence: commits, tests, logs
   - Suggested role: @qa   (optional; role, never a person)
   ```
4. Add a `Discussion:` comment with what passed/failed and what remains.
5. Remove the lock and stop. Do not keep editing; do not set `[ ]`; do not name a person — only a handler role.

If the next step needs a decision, credential, or external dependency, use `Reason: blocked` (assigned to `@owner`). Abandoned/crash recovery also parks to `@owner` with `#abandoned`.

### Triage (`@pm` handles `[!] Assignee: @pm`)

`@pm` is the dispatcher. `@pm` triages the `[!] Assignee: @pm` queue before claiming new `[ ]` work (oldest first - lowest TASK-### ).

1. Scan `[!]` tasks where `Assignee: @pm`, claim oldest first (task state changes to `[~]`).
2. Read `Reason`, `State`, `Next step`, `Evidence`.
3. Make exactly one decision:
   - **Reopen to `[ ]`** — add/clarify acceptance criteria, set `Role: @dev|@qa` if needed, add `Decision: reopened` and `Next step:` with clarified action.
   - **Split** — create new `[ ]` task(s) with IDs, then rescope (reduce) the original task and mark as `[ ]` with `Rationale: split into TASK-0NN... and re-verify original` and `Role: @qa`.
   - **Escalate** — If `@pm` cannot resolve a parked item, set `Assignee: @owner`, add `Escalated by: @pm (YYYY-MM-DD HH:mm UTC)`, change task state to `[!]`, add the exact decision needed. `@owner` then resolves it per the owner rules (reopen to `[ ]`, complete as `[x]`, or keep parked with reason).
4. Record:
   ```
   - Triaged by: @pm (YYYY-MM-DD HH:mm UTC)
   - Decision: reopened | split | escalated
   - Rationale: what changed
   - Next step: action
   ```
5. Release the lock.

### Verification

- `[x]` requires `Verified by: @qa` or `Verified by: @owner`.
- The verifier must not be the sole implementer.
- If the same agent holds both `@dev` and `@qa`, `@owner` must sign off explicitly.
- Verification is read-only by default. If the verifier must edit files, park the task to `[!] Assignee: @pm` with `Reason: rework`.
- If verification fails, add a `#bug` discussion comment with details. The task is parked to `[!]` for triage `Assignee: @pm` with `Reason: rework`.
- If the only available verifier is the implementer, park to `[!] Assignee: @owner` instead.
- If verification passes, set `[x]` with `Verified by: @qa` and completion evidence.

### Completion

1. Confirm acceptance criteria are met.
2. Confirm required tests/checks pass, or record the environment limitation.
3. Confirm independent verification or owner sign-off.
4. Update `TODO.md` to `[x]`.
5. Add `Completed`, `Verified by`, `Evidence`, and `Resolution`.
6. Remove the task lock only after the `TODO.md` update is complete.

If the process stops after the `TODO.md` update but before lock removal, the task is complete and the lock is stale; `@owner` cleans it.

### Blocked and abandoned

A task that cannot proceed without a decision, credential, external resource, or was abandoned is parked to `@owner`:

1. Change `[~]` → `[!]`.
2. Set `Assignee: @owner`.
3. Set `Reason: blocked | abandoned | question`.
4. Add `(blocked: timestamp)` or `(abandoned: timestamp)` to `Assignee`.
5. Add a `#blocker`, `#question`, or `#abandoned` discussion comment naming the exact decision or resource needed.
6. Release the lock.

Only `@owner` may reopen it to `[ ]` after resolving the blocker and removing stale fields. Keep the discussion history.

If the cause is a concrete blocker, also add `#blocker` or `#question`. This rule covers crashes, lost context, and agents that stop without a clean park. Only `@owner` may abandon another agent's task.

`@owner` may reassign any unavailable handler's `[!]` item, recording `Reassigned by: @owner`.

## 9. Definition of done

A task is fully complete only when:

- acceptance criteria are met;
- required tests/checks pass, or the limitation is documented;
- relevant documentation is updated;
- the task record has `Completed`, `Verified by`, `Evidence`, and `Resolution`;
- verification is independent or owner-approved;
- no `[!]` entry remains for the task; all parking/triage/verification history is present;
- task state is `[x]` and the lock is removed, or its cleanup is pending.

## 10. Quick checklist

- [ ] Read `TODO.md`.
- [ ] Scan `[ ]` tasks matching your `Role:`; then open `[ ]` tasks.
- [ ] If you hold a handler role, triage your `[!]` queue before new work.
- [ ] Create `.locks/<task-id>.lock/`.
- [ ] Confirm task is still `[ ]` (or `[!]` with your role).
- [ ] Set task to `[~]` and record assignee/time.
- [ ] Do the work; keep `[~]`.
- [ ] If hitting a role boundary or blocker, park to `[!]` with `Assignee` and `Reason`.
- [ ] Get independent verification or owner sign-off.
- [ ] Set task to `[x]` and add completion evidence.
- [ ] Remove `.locks/<task-id>.lock/`.
