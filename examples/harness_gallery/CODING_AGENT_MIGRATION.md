# Coding Agent disposition

`examples/coding_agent` is not redundant legacy code today. It is the existing,
well-tested NanoClaudeCode implementation and currently owns 435 regression
tests for its REPL, managed context, prompt assembly, resilient provider,
coding evaluator, permission UI, and workspace tools.

It should not coexist with a second copied NanoClaudeCode implementation.
Instead:

1. M5.1 keeps it unchanged as the behavioral baseline while Gallery contracts
   and Profiles are established.
2. M5.2 moves modules shared by NanoClaudeCode and NanoCodex into the Gallery
   shared layer only when both hosts actually use them.
3. Claude-specific REPL, session context, interactive approval, and prompt
   behavior remain a NanoClaudeCode host rather than becoming kernel policy.
4. `examples/coding_agent/main.py` then becomes a thin compatibility launcher
   for the Gallery NanoClaudeCode entry point.
5. Compatibility facades and the old directory may be removed only after its
   435 tests have equivalent coverage at the new entry point and documentation
   links have migrated.

This sequence preserves a working example and prevents speculative shared
abstractions. The final Gallery should contain one NanoClaudeCode
implementation, not two differently named copies.
