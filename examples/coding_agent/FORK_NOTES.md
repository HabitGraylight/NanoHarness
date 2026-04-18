# Fork Notes — examples/coding_agent/

## Relationship to Root nanoharness

`examples/coding_agent/nanoharness/` is a **symlink** to `../../nanoharness`.

This means:
- Any change to the root `nanoharness/` is immediately reflected here.
- There is **no divergence** — both point to the same code on disk.
- Coding-agent-specific behavior lives entirely in `app/`, not in the kernel.

## Why a Symlink

The kernel has been refactored to be policy-free:
- `NanoEngine` does not depend on `PromptManager`, `MemoryManager`, or permission I/O.
- All app-level behavior (memory injection/persistence, prompt templates, approval flow)
  is wired in `app/builder.py` via hooks and component configuration.

This makes it safe to share the kernel — the example only needs to add its own
app layer (`app/`) without forking or modifying the kernel.

## App Layer Structure

```
app/
  builder.py       # Engine wiring (assembles kernel components + app policy)
  prompts.yaml     # Coding-agent-specific prompt templates
  tools.py         # Tool assembly (shell scripts + Python-native tools)
  permissions.py   # Permission policy (deny reset, confirm push)
  hooks.py         # Output hooks (step-by-step visibility)
```

## History

- **2026-04-18**: Initial setup with vendored copy of nanoharness
- **2026-04-18**: Replaced vendored copy with symlink after kernel refactoring
  removed all app-layer coupling from the engine
