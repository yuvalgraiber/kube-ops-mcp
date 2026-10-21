# Changelog

All notable changes to this project are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [semantic versioning](https://semver.org/).

## [0.1.0] - 2026-10-21

First release.

### Added

- Read tools: `diagnose_workload`, `get_pod_status`, `get_recent_events`, `get_pod_logs` and `query_prometheus`.
- `diagnose_workload` combines pod status, warning events, crash logs and CPU/memory against limits into ranked findings with evidence and a fix, and reports progress while it runs.
- Write tools `restart_deployment` and `scale_deployment`, off by default, limited to `ALLOWED_NAMESPACES` and confirmed by the user through MCP elicitation.
- `cluster://summary` resource, `incident_triage` prompt and a `/triage` command for Claude Code.
- Safety middleware on every tool call: secret redaction, a 25k character output cap and a JSON audit log on stderr.
- stdio transport for local use and Streamable HTTP for running in a cluster, with DNS-rebinding protection and a `/healthz` endpoint.
- Container image (`ghcr.io/yuvalgraiber/kube-ops-mcp`) for amd64 and arm64, with an SBOM and signed build provenance.
- Helm chart (`oci://ghcr.io/yuvalgraiber/charts/kube-ops-mcp`) with read-only RBAC, a hardened pod spec, a NetworkPolicy and a `helm test` hook.
- kind demo environment with two deliberately broken apps.

[0.1.0]: https://github.com/yuvalgraiber/kube-ops-mcp/releases/tag/v0.1.0
