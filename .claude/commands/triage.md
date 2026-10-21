---
description: Triage a failing Kubernetes workload with the kube-ops MCP server
argument-hint: <deployment> [namespace]
allowed-tools: mcp__kube-ops__diagnose_workload, mcp__kube-ops__get_pod_status, mcp__kube-ops__get_recent_events, mcp__kube-ops__get_pod_logs, mcp__kube-ops__query_prometheus
---

Triage a failing Kubernetes deployment using only the kube-ops tools.

Arguments: `$ARGUMENTS` (format `<deployment> [namespace]`; the namespace defaults to `demo`).

1. Call `diagnose_workload` for the overview and ranked findings.
2. Check `get_recent_events` and `get_pod_logs` (with `previous=true` for crashed containers) to confirm the top finding.
3. Use `query_prometheus` to compare CPU and memory use with the limits when resources might be involved.

Reply with:
- **Root cause**: one sentence.
- **Evidence**: the exact status, exit code, log line, event or metric that proves it.
- **Fix**: the concrete change (manifest field, value or command).

Do not change anything in the cluster. If a restart or scale would help, suggest it and let me decide.
