#!/usr/bin/env bash
# Create the local demo cluster, install Prometheus and deploy the broken apps.
set -euo pipefail
cd "$(dirname "$0")"

CLUSTER=kube-ops-demo
CHART_VERSION=92.11.0 # kube-prometheus-stack, pinned for reproducible installs

for tool in docker kind kubectl helm; do
  command -v "$tool" >/dev/null || { echo "Missing prerequisite: $tool" >&2; exit 1; }
done
docker info >/dev/null 2>&1 || { echo "Docker is not running" >&2; exit 1; }

if ! kind get clusters | grep -qx "$CLUSTER"; then
  kind create cluster --config kind-config.yaml
fi
kubectl config use-context "kind-$CLUSTER"

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null
helm repo update prometheus-community >/dev/null
helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
  --version "$CHART_VERSION" \
  --namespace monitoring --create-namespace \
  --values prometheus-values.yaml --wait --timeout 10m

kubectl apply -f broken-apps.yaml

echo
echo "Demo ready. Expose Prometheus on localhost:9090 with: make port-forward"
echo "Watch the failures: kubectl -n demo get pods -w"
