# Changelog

All notable changes to this project are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [semantic versioning](https://semver.org/).

## [0.1.3] - 2026-10-26

### Added

- `diagnose_workload` shows each container's image, command, arguments, resource requests and limits, and the names of its environment variables (never their values). The model can now see, for example, that a container is told to allocate 150M against a 64Mi limit, or that a required variable isn't set at all.

### Changed

- `/triage` answers only from what the kube-ops tools return and no longer reads local files or runs shell commands, since in a real incident the repository may not match what's running.
- The demo shows both broken apps.

## [0.1.2] - 2026-10-22

### Fixed

- `get_pod_status` and `diagnose_workload` stay useful on namespaces with hundreds of pods. A 300-pod namespace used to hit the output cap and lose the whole result; unhealthy pods now come first in full and healthy ones are shortened.
- When a container instance is already gone, the kubelet can return its error message as the log body. That text is now reported as an error instead of being shown as the app's log, and `diagnose_workload` falls back to the current logs.
- Pods, nodes and deployments with missing optional fields (for example a pod with no status yet) no longer cause errors.
- Events without an object name, and scalar PromQL results, are handled.

### Added

- CI runs on Python 3.12, 3.13 and 3.14, and type-checks the code with mypy.
- Contributing guide, code of conduct, and issue and pull request templates.
- A shorter demo recording.

## [0.1.1] - 2026-10-21

### Added

- Signed build provenance for the container image, now that the repository is public.
- A README for the Helm chart, shown by `helm show readme`, with the values reference.
- README sections for every setup: the demo, your own cluster without cloning (`uvx` from a release tag), a team install from the published chart, other MCP clients, enabling writes and troubleshooting.

### Changed

- CI uses astral-sh/setup-uv 10.3.0.

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

[0.1.3]: https://github.com/yuvalgraiber/kube-ops-mcp/releases/tag/v0.1.3
[0.1.2]: https://github.com/yuvalgraiber/kube-ops-mcp/releases/tag/v0.1.2
[0.1.1]: https://github.com/yuvalgraiber/kube-ops-mcp/releases/tag/v0.1.1
[0.1.0]: https://github.com/yuvalgraiber/kube-ops-mcp/releases/tag/v0.1.0
