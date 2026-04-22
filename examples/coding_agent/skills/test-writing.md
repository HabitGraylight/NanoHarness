---
name: test-writing
description: "Write unit tests for a module or function"
trigger: "when asked to write tests, add test coverage, or verify correctness"
---

# Test Writing

## Steps

1. Identify the target module/function — read it with `file_read`.
2. Understand inputs, outputs, and edge cases.
3. Check if a test file already exists — use `file_find` or `search_code`.
4. Write tests covering:
   - **Happy path**: Normal inputs produce expected outputs
   - **Edge cases**: Empty input, boundary values, None/null
   - **Error cases**: Invalid input raises expected exceptions
5. Run the tests with `shell_exec` to verify they pass.
6. If tests fail, read the error output, fix, and re-run.

## Guidelines

- One test per behavior — don't bundle multiple assertions.
- Use descriptive test names: `test_<function>_<scenario>_<expected>`.
- Prefer the project's existing test framework (pytest, unittest, etc.).
- Don't mock what you don't own — mock external APIs, not internal helpers.
- Keep tests independent — no ordering dependencies.
