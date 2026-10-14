# kube-ops-mcp

An [MCP](https://modelcontextprotocol.io) server that lets AI assistants like Claude Code triage Kubernetes incidents. It is **read-only by default**.

Ask *"Why is payment-service failing in the demo namespace?"* and the assistant checks pod status, events, logs and Prometheus metrics to find the cause.

> **Status:** work in progress.

## Planned tools

| Tool | Purpose | Access |
|---|---|---|
| `get_pod_status` | Phase, restarts, readiness and last termination reason per pod | read |
| `get_recent_events` | Warning events from the last hour | read |
| `get_pod_logs` | Last N log lines, including the previous container | read |
| `query_prometheus` | Instant or range PromQL queries | read |
| `diagnose_workload` | Combined state, events, logs and CPU/memory vs. limits | read |
| `restart_deployment` | Rollout restart of an allow-listed deployment | write, off by default |
| `scale_deployment` | Change replicas within a bounded range | write, off by default |

## Security model

- Read-only RBAC (`get`/`list`), with no access to `secrets`
- Write tools exist only when `KUBE_OPS_ENABLE_WRITE=true`, and work only in allow-listed namespaces
- Values that look like secrets are redacted from all output
- Output size is capped per call
- Every tool call is written to a JSON audit log

## License

[MIT](LICENSE)
