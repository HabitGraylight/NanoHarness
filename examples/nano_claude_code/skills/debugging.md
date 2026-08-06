---
name: debugging
description: "Systematically debug an issue or error"
trigger: "when something fails, crashes, or produces wrong results"
---

# Debugging

## Steps

1. **Reproduce** — Confirm the issue by running the failing command or test.
2. **Read the error** — Parse the traceback fully. Identify:
   - What failed (error type and message)
   - Where it failed (file, line, function)
   - What was expected vs. what happened
3. **Read the code** — Use `file_read` on the failing file, focusing on the relevant function.
4. **Form a hypothesis** — Based on the error and code, what's the most likely cause?
5. **Verify** — Add a diagnostic print/log or write a minimal reproduction test.
6. **Fix** — Make the smallest change that resolves the issue.
7. **Confirm** — Re-run the failing test/command to verify the fix.

## Guidelines

- Read the error message carefully before reading code — the error usually tells you what's wrong.
- Change one thing at a time. Don't shotgun-debug.
- If hypothesis A fails, form hypothesis B — don't keep trying A.
- When you find the fix, check if the same bug exists elsewhere.
