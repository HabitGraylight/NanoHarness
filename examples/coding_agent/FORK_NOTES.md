# Fork Notes — examples/coding_agent/nanoharness/

This document tracks the relationship between the vendored `nanoharness/` in this
example and the root `nanoharness/` in the repository.

## Origin

- **Source**: Root `nanoharness/` at commit `e9d83af` (2026-04-18)
- **Copy date**: 2026-04-18
- **Method**: Direct file copy, no modifications at time of fork

## Divergence Log

| Date | File(s) | Change | Back-ported? |
|---|---|---|---|
| — | — | No divergence yet | — |

## Sync Rules

1. **Bug fixes** in root `nanoharness/` core components (`core/schema.py`, `core/engine.py`, `core/base.py`, `core/prompt.py`) should be synced here.
2. **Coding-agent-specific changes** (prompts, tool policy, hooks) live in `app/` — these are NOT forked code and don't need syncing.
3. **Component changes** in `components/` are evaluated case-by-case: if a change is generic, sync it; if it's coding-specific, keep it local.

## Files expected to diverge

These files are likely to accumulate coding-agent-specific behavior over time:

- `nanoharness/core/engine.py` — may add coding-specific loop strategies
- `nanoharness/components/tools/script_tools.py` — may change tool execution policy
- `nanoharness/components/permissions/rule_permission.py` — may add richer approval UX

## Files expected to stay close to root

These files are unlikely to need coding-agent-specific changes:

- `nanoharness/core/schema.py`
- `nanoharness/core/base.py`
- `nanoharness/core/prompt.py`
- `nanoharness/utils/`
