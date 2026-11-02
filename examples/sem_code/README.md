# Inspect local code with Sem

Install [Sem](https://github.com/Ataraxy-Labs/sem) and put `sem` on PATH, then
install NanoHarness with `pip install -e ".[mcp]"` from this repository.

```bash
python examples/sem_code/inspect_repo.py /absolute/path/to/git/repo parse_config
```

This opt-in example discovers Sem's tools and calls `sem_find` through the
existing MCP extension. It needs neither a model key nor a hosted MCP service.
The extension manager closes the subprocess on exit. A missing binary, invalid
repository or MCP failure follows the existing extension error behavior.

To use this in an agent, pass the same `context.tools` to NanoEngine while the
extension manager is open. Tool results then become visible to your model
provider. Sem may populate its local index; cloud uploads are disabled here.
