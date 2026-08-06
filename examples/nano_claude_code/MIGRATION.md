# Coding Agent disposition

`examples/nano_claude_code` is the migrated, well-tested NanoClaudeCode
implementation and currently owns 435 regression
tests for its REPL, managed context, prompt assembly, resilient provider,
coding evaluator, permission UI, and workspace tools.

It should not coexist with a second copied NanoClaudeCode implementation.
Instead:

1. M5.1 kept it unchanged as the behavioral baseline while Profile contracts
   were established.
2. M5.2 moves modules shared by NanoClaudeCode and NanoCodex into the public
   shared layer only when both hosts actually use them.
3. Claude-specific REPL, session context, interactive approval, and prompt
   behavior remain a NanoClaudeCode host rather than becoming kernel policy.
4. `examples/nano_claude_code/main.py` remains the real provider-backed entry;
   `profile_demo.py` provides the deterministic contract run.
5. Compatibility facades and the old directory may be removed only after its
   435 tests have equivalent coverage at the new entry point and documentation
   links have migrated.

This sequence preserves a working example and prevents speculative shared
abstractions. There is one NanoClaudeCode implementation, not a Gallery copy.

## Staged assembly

The NanoClaudeCode Profile now declares two explicit extension phases:

1. Bootstrap installs Memory, Skills, Background, Scheduler, Tasks, Worktrees,
   and Team services.
2. The host binds Prompt/Context/ResilientLLM and publishes the derived runtime
   services and capabilities.
3. Runtime installs Subagent, whose dependencies are now genuinely available.
4. The application binds the final NanoEngine.

`app.assembly.StagedAssembler` validates complete, non-overlapping phase
membership, preserves the dependency order calculated by `HarnessBuilder`,
verifies the host binder satisfied the Profile declarations, and closes all
installed resources if any phase fails. The protocol now lives in
`nanoharness.profiles`; app-specific binders remain with their examples.
