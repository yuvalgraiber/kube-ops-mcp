#!/usr/bin/env bash
# Create the local demo cluster, install Prometheus and deploy the broken apps.
set -euo pipefail
cd "$(dirname "$0")"

CLUSTER=kube-ops-demo

if ! kind get clusters | grep -qx "$CLUSTER"; then
  kind create cluster --config kind-config.yaml
fi
kubectl config use-context "kind-$CLUSTER"

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null
helm repo update prometheus-community >/dev/null
helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace \
  --values prometheus-values.yaml --wait --timeout 10m

kubectl apply -f broken-apps.yaml

echo
echo "Demo ready. Prometheus: kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090"
echo "Watch the failures: kubectl -n demo get pods -w"
