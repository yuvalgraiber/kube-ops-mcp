# kube-ops-mcp Helm chart

Runs [kube-ops-mcp](https://github.com/yuvalgraiber/kube-ops-mcp) inside a cluster with a read-only ServiceAccount, so MCP clients can triage workloads without using personal credentials.

```bash
helm install kube-ops-mcp oci://ghcr.io/yuvalgraiber/charts/kube-ops-mcp --version 0.1.3 \
  -n kube-ops --create-namespace --set clusterName=prod-eu
helm test kube-ops-mcp -n kube-ops
```

Connect with `kubectl -n kube-ops port-forward svc/kube-ops-mcp 8000:8000`, then point your client at `http://localhost:8000/mcp`.

## What it creates

- A Deployment running as UID 10001 with a read-only root filesystem, no capabilities and `RuntimeDefault` seccomp
- A ClusterRole with `get`/`list` on pods, pod logs, events, namespaces, nodes, deployments and replicasets (no Secrets or ConfigMaps)
- With `write.enabled=true`: a Role in each `write.allowedNamespaces` entry that can only `get` and `patch` deployments
- A ClusterIP Service and a NetworkPolicy that only admits pods labelled `kube-ops-mcp-client: "true"`

## Values

| Value | Default | |
|---|---|---|
| `clusterName` | `""` | Name reported in results; empty means `in-cluster` |
| `prometheus.url` | `http://prometheus-operated.monitoring.svc:9090` | Prometheus HTTP API |
| `write.enabled` | `false` | Register the restart and scale tools |
| `write.allowedNamespaces` | `[]` | Namespaces the write tools may change (required when enabled) |
| `write.maxReplicas` | `10` | Upper bound for scaling |
| `write.requireConfirmation` | `true` | Refuse writes when the client can't ask the user |
| `timeoutSeconds.kubernetes`, `timeoutSeconds.prometheus` | `10` | Timeouts in seconds |
| `extraAllowedHosts` | `[]` | Extra Host headers to accept, e.g. an internal DNS name |
| `networkPolicy.enabled` | `true` | Restrict ingress to labelled client pods |
| `networkPolicy.clientPodLabels` | `kube-ops-mcp-client: "true"` | Labels a client pod needs |
| `image.repository`, `image.tag` | `ghcr.io/yuvalgraiber/kube-ops-mcp`, chart appVersion | Container image |
| `resources` | 50m / 128Mi requests, 256Mi memory limit | No CPU limit on purpose |

The HTTP endpoint has no authentication of its own. Keep the Service internal; don't put it behind a public Ingress.
