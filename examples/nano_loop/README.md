# NanoLoop

NanoLoop is a self-contained Loop Engineering example built above the
NanoHarness kernel. NanoHarness governs one agent run; NanoLoop repeatedly
creates fresh runs, verifies their artifacts, persists evidence, and stops at a
bounded terminal state or human gate.

## Execution model

```text
manual trigger
    -> prepare isolated Git worktree
    -> create a fresh NanoEngine
    -> worker edits files inside the worktree
    -> run trusted acceptance commands
       -> pass -> WAITING_HUMAN or COMPLETED
       -> fail -> feed evidence into the next iteration
       -> budget/threshold reached -> BLOCKED or BUDGET_EXHAUSTED
```

The worker's own success claim is never sufficient. Only configured verifier
evidence can advance the loop to completion.

## Quick start

From the repository root:

```bash
pip install -e ".[openai]"
export DEEPSEEK_API_KEY="sk-..."

cd examples/nano_loop
python main.py run configs/loops/local_fix.yaml \
  --repo ../.. \
  --task "Add a regression test for the evaluator query propagation"
```

The command prints a run ID, worktree path, branch, status, and stop reason.
The default example waits for human approval after verification because commit,
push, and merge are configured as gates.

```bash
python main.py list
python main.py status <run-id>
python main.py resume <run-id>
python main.py approve <run-id>
python main.py reject <run-id> --reason "The diff is too broad"
```

`approve` records that the evidence was accepted. Version one deliberately does
not execute the gated Git or external action.

The worktree and `nanoloop/<run-id>` branch are retained after the loop stops so
the human can inspect, commit, or discard the result. NanoLoop never removes a
workspace containing uncommitted agent work automatically.

## Loop specification

```yaml
name: local-code-repair

worker:
  type: nano_engine
  model: deepseek-chat
  max_steps: 20

workspace:
  type: git_worktree
  base_ref: HEAD

verify:
  commands:
    - PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -q
    - git diff --check

budget:
  max_iterations: 3
  max_wall_seconds: 1800
  max_consecutive_failures: 3

gates:
  require_human: [commit, push, merge]
```

Verifier commands are trusted application configuration, not model output.
An empty command list fails closed.

## Structure

```text
app/
  schema.py       LoopSpec, LoopState, evidence, terminal states
  runner.py       persistent outer-loop state machine
  worker.py       fresh NanoEngine per iteration
  verifier.py     structured command evidence
  policy.py       retry, failure, time, and iteration budgets
  workspace.py    Git worktree isolation
  gates.py        explicit human approval boundary
  store.py        atomic JSON state persistence
  tools.py        workspace-confined file tools
  context.py      OpenAI-compatible tool-call context
  adapters.py     DeepSeek/OpenAI-compatible LLM adapter
configs/loops/    reusable loop specifications
sandbox/          runtime states, engine traces, and worktrees (gitignored)
tests/            unit and real-Git system tests
nanoharness       symlink to the shared kernel
```

## Safety boundary

- Each iteration receives a newly built NanoEngine.
- Worker tools expose files only; they do not expose shell, Git, push, or merge.
- File paths and globs are confined to the selected workspace; `.git` is denied.
- Acceptance commands come only from trusted YAML configuration.
- Every loop has iteration, wall-clock, and consecutive-failure limits.
- Runtime state is saved before and after worker and verifier phases.
- Irreversible follow-up actions remain behind a human gate.

This is an application example, not a production multi-host scheduler. A
production deployment should add distributed leases, authentication, external
secret management, cost accounting, and sandboxed command execution.

## Tests

```bash
cd examples/nano_loop
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v
```
