---
name: code-review
description: "Review code changes for quality, security, and correctness"
trigger: "when asked to review, audit, or evaluate code"
---

# Code Review

## Steps

1. Identify the changed files — use `git_diff` or ask the user which files to review.
2. Read each changed file with `file_read` — always read before commenting.
3. For each file, check:
   - **Logic errors**: Off-by-one, wrong condition, missing edge case
   - **Security**: Injection, exposed secrets, unsafe deserialization
   - **Performance**: N+1 queries, unnecessary copies, missing early returns
   - **Readability**: Naming, dead code, overly complex expressions
4. Summarize findings — reference specific line numbers.
5. Rate overall quality: excellent / good / needs-work / poor.

## Guidelines

- Always read the full file before commenting.
- Be specific — reference line numbers and suggest fixes.
- Don't just point out problems — propose solutions.
- Distinguish "must fix" from "nice to have" (use [MUST] / [SUGGEST] tags).
- If the change is large, focus on the most impactful issues first.
