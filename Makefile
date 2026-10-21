# Common tasks. Run `make help` to list them.
.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash

INSPECTOR_VERSION := 2.9.0
PROMETHEUS_SVC := svc/monitoring-kube-prometheus-prometheus

.PHONY: help install lint fmt test check run inspector demo-up demo-down port-forward

help: ## Show this help
	@grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies from the lock file
	uv sync --locked

lint: ## Check code style and security rules
	uv run ruff check .
	uv run ruff format --check .

fmt: ## Auto-format the code
	uv run ruff check --fix .
	uv run ruff format .

test: ## Run unit tests (no cluster needed)
	uv run pytest

check: lint test ## Run everything CI runs

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
