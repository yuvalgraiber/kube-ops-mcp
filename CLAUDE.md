# kube-ops-mcp

MCP server for read-only Kubernetes incident triage. Python 3.12, uv, MCP Python SDK 2.x, the official `kubernetes` client and httpx for Prometheus.

## Commands

- `make check` runs ruff, the format check, helm lint and pytest. Run it before every commit; CI runs the same steps.
- `make demo-up` / `make demo-down` create and delete the kind demo cluster. `make port-forward` exposes its Prometheus on :9090.
- `make deploy` builds the image, loads it into kind, installs the chart and runs `helm test`.
- To check a tool against the live demo cluster: `npx -y @modelcontextprotocol/inspector@2.9.0 --cli uv run kube-ops-mcp --method tools/call --tool-name get_pod_status --tool-arg namespace=demo`. Put `-e KEY=VALUE` after the server command.

## Layout

- `src/kube_ops_mcp/server.py`: tools, resource, prompt, transport selection
- `k8s.py`: all Kubernetes API calls go through `_call`, which adds the timeout and turns errors into messages the model can act on
- `diagnose.py`: `diagnose_workload`. Keep the analysis functions pure so they stay unit-testable
- `safety.py`: `SafetyMiddleware` (redaction, output cap, audit log) and the write-policy checks
- `config.py`: settings from environment variables
- `deploy/helm/kube-ops-mcp`: the chart. `demo/`: the kind cluster and broken apps

## Rules

- Never give the server access to Secrets or ConfigMaps, in RBAC or in code.
- Write tools are registered only when `KUBE_OPS_ENABLE_WRITE=true`, act only in `ALLOWED_NAMESPACES`, and need user confirmation through elicitation. Keep it fail closed.
- Every external call needs a timeout, and every expected failure should raise `ToolError` with a message that says what to do next.
- Tool docstrings are what the model reads to choose a tool. Keep them short, concrete and accurate.
- stdout belongs to the stdio transport. Logs and audit lines go to stderr.
- Use demo data only: no real cluster names, IPs or configs in code, tests or docs.

## Tests

- No live cluster in pytest. Mock `k8s` functions and use `httpx.MockTransport` for Prometheus. Builders for pods, events and deployments are in `tests/factories.py`.
- Server tests use the SDK's in-memory client, `mcp.client.Client(server.mcp)`, so the middleware and elicitation run for real.
- Tests must pass without a kubeconfig (`KUBECONFIG=/nonexistent uv run pytest`), the way they run in CI.

## SDK notes

- In SDK 2.x `FastMCP` is called `MCPServer` (`from mcp.server.mcpserver import MCPServer`). Check the installed package before trusting v1 examples.
- `ServerMiddleware` gets the serialized wire dict (camelCase keys) from `call_next`, not a model.
- Client logging is deprecated in the 2026-07-28 spec. Claude Code doesn't declare the sampling capability.

## Commits

Conventional commits (`feat:`, `fix:`, `test:`, `build:`, `ci:`, `docs:`), one concern per commit, and tests passing at every commit.
