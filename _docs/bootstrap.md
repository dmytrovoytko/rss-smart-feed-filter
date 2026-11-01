# New repository bootstrap

A new repository needs only these coordination files:

- `AGENTS.md` — an entrypoint that tells agents to read this process and `TODO.md`.
- `_docs/process.md` = coordination process.
- `TODO.md` — the active task tracker.
- `.gitignore` — includes `.locks/` so lock directories are never committed.

Optional: a product specification or design document. If one exists, it is the product source of truth. This process remains the coordination source of truth.

## Bootstrap checklist:

1. [ ] Fill every `[...]` placeholder (including working commands) in `AGENTS_templ.md` and copy it into `AGENTS.md`.
2. [ ] Copy the coordination process into `_docs/process.md`.
3. [ ] Create `TODO.md` using the tracker skeleton in that process.
4. [ ] Add `.locks/` to `.gitignore`.
5. [ ] Document external services and data handling.
6. [ ] Commit the initial files.
7. [ ] Do not start implementation until `TODO.md` has at least one testable task (a `[ ]` task with at least one acceptance criterion and no unmet dependencies).

If the process file or tracker is missing, ask `@owner` before proceeding.
