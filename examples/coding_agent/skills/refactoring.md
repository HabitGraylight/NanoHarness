---
name: refactoring
description: "Safely refactor code without changing behavior"
trigger: "when asked to restructure, clean up, or improve code structure"
---

# Refactoring

## Steps

1. **Understand existing behavior** — Read the code with `file_read`. Identify what it does.
2. **Find tests** — Use `search_code` to find existing tests for the code you're refactoring.
   - If no tests exist, write them first (use the test-writing skill).
3. **Run existing tests** — Confirm they pass before you start.
4. **Plan the refactor** — List specific changes: rename, extract, inline, move.
5. **Make one change at a time** — After each change, run tests.
6. **Final verification** — Run the full test suite.

## Guidelines

- If there are no tests, STOP — write tests first.
- Never change behavior during a refactor. If you find a bug, note it separately.
- Prefer small, reviewable commits over one big change.
- Rename variables/methods to reveal intent.
- Extract functions when a block does one identifiable thing.
- Delete dead code — don't comment it out.
