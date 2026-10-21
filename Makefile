# Common tasks. Run `make help` to list them.
.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash

INSPECTOR_VERSION := 2.9.0
PROMETHEUS_SVC := svc/monitoring-kube-prometheus-prometheus
IMAGE := kube-ops-mcp:dev
CHART := deploy/helm/kube-ops-mcp
RELEASE := kube-ops-mcp
RELEASE_NS := kube-ops

.PHONY: help install lint typecheck fmt test check run inspector demo-up demo-down port-forward \
	image helm-lint deploy undeploy port-forward-mcp demo-gif

help: ## Show this help
	@grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies from the lock file
	uv sync --locked

lint: ## Check code style and security rules
	uv run ruff check .
	uv run ruff format --check .

typecheck: ## Static type check with mypy
	uv run mypy

helm-lint: ## Lint the Helm chart in read-only and write mode
	helm lint $(CHART) --strict
	helm lint $(CHART) --strict --set write.enabled=true --set 'write.allowedNamespaces={demo}'

fmt: ## Auto-format the code
	uv run ruff check --fix .
	uv run ruff format .

test: ## Run unit tests (no cluster needed)
	uv run pytest

check: lint typecheck helm-lint test ## Run everything CI runs

run: ## Run the MCP server over stdio
	uv run kube-ops-mcp

inspector: ## Open MCP Inspector against the server
	npx -y @modelcontextprotocol/inspector@$(INSPECTOR_VERSION) uv run kube-ops-mcp

demo-up: ## Create the kind demo cluster with Prometheus and broken apps
	demo/setup.sh

demo-down: ## Delete the kind demo cluster
	demo/teardown.sh

port-forward: ## Expose the demo Prometheus on localhost:9090
	kubectl -n monitoring port-forward $(PROMETHEUS_SVC) 9090:9090

image: ## Build the container image
	docker build -t $(IMAGE) .

deploy: image ## Build, load into kind and install the chart in the demo cluster
	kind load docker-image $(IMAGE) --name kube-ops-demo
	helm upgrade --install $(RELEASE) $(CHART) -n $(RELEASE_NS) --create-namespace \
		--set image.repository=kube-ops-mcp --set image.tag=dev --set image.pullPolicy=Never \
		--set clusterName=kind-kube-ops-demo \
		--wait --timeout 2m
	helm test $(RELEASE) -n $(RELEASE_NS)

undeploy: ## Remove the chart from the demo cluster
	helm uninstall $(RELEASE) -n $(RELEASE_NS)

port-forward-mcp: ## Expose the in-cluster MCP server on localhost:8000
	kubectl -n $(RELEASE_NS) port-forward svc/$(RELEASE) 8000:8000

demo-gif: ## Re-record docs/demo.gif (needs the demo cluster, port-forward and vhs)
	vhs docs/demo.tape
