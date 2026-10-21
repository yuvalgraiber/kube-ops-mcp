# Security

kube-ops-mcp gives an AI model a view into a Kubernetes cluster, so security issues matter here more than in most side projects.

## Reporting a problem

Please don't open a public issue for a vulnerability. Use GitHub's private reporting instead: **Security → Report a vulnerability** on this repository. I'll reply within a few days.

Things I'd especially like to hear about:

- a way to make a tool return a secret that the redaction misses
- a way to run a write tool without `KUBE_OPS_ENABLE_WRITE`, outside `ALLOWED_NAMESPACES`, or without confirmation
- RBAC in the Helm chart that grants more than the README says

## What the server assumes

- The model is an untrusted caller. Inputs are validated, writes are gated, and outputs are redacted and capped.
- The HTTP endpoint has no authentication of its own. Keep the Service as ClusterIP and reach it through `kubectl port-forward` or from pods the NetworkPolicy allows. Don't put it behind a public Ingress.
- In local (stdio) mode the server has your kubeconfig permissions. Use `KUBE_OPS_CONTEXT` to pin a context, and prefer a read-only kubeconfig user.
