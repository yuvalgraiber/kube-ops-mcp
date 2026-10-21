#!/usr/bin/env bash
# Delete the local demo cluster.
set -euo pipefail
kind delete cluster --name kube-ops-demo
