# Inspect local code with Sem

This optional integration recipe connects the existing MCP extension to [Sem](https://github.com/Ataraxy-Labs/sem) to find a named entity in a local Git checkout. It is not a complete agent and does not change default servers or dependencies.

Install a trusted [Sem 0.29.0 release](https://github.com/Ataraxy-Labs/sem/releases/tag/v0.29.0), or a compatible version, and put `sem` on PATH. Check `sem --version`: GNU Parallel also installs a binary named `sem`. Then run these commands from the NanoHarness repository root:

```bash
pip install -e ".[mcp]"
python recipes/sem_code/inspect_repo.py . NanoEngine
# Or inspect another Git checkout:
python recipes/sem_code/inspect_repo.py /absolute/path/to/git/repo parse_config
```

The recipe discovers Sem's tools and calls `sem_find` through the tool registry. It needs neither a model key nor a hosted MCP service. The repository must be an existing directory; Sem validates that it is a Git checkout. Startup errors fail immediately rather than leaving an empty tool registry, and MCP requests have a 60-second timeout. The extension manager closes the connection on success or failure.

## Local execution and agent reuse

The server's working directory and `SEM_REPO` both point to the explicitly selected checkout. For Sem 0.29.0, `SEM_LOCAL=1` disables cloud routing and `SEM_MCP_NO_SHARED=1` starts a dedicated stdio session rather than reusing a shared daemon with different settings. Sem may create local index/cache files. These settings are not an OS sandbox or a network firewall; only run a trusted binary against repositories you intend to inspect.

To use this in an agent, pass the same `context.tools` to NanoEngine while the extension manager is open. The extension registers all advertised tools, although this recipe calls only `sem_find`; apply tool policy and approval boundaries before exposing the full registry, since tools such as `sem_check` can execute project commands. Tool results, including any source excerpts, become visible to the configured model provider when called by the agent.
