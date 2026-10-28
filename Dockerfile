# syntax=docker/dockerfile:1
# Multi-stage build: dependencies are resolved from uv.lock in a builder stage, and the
# runtime image holds only the virtualenv. Base images are pinned by digest.

FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

FROM python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2 AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first, so this layer is cached until uv.lock changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2
LABEL org.opencontainers.image.title="kube-ops-mcp" \
      org.opencontainers.image.description="MCP server for read-only Kubernetes incident triage" \
      org.opencontainers.image.source="https://github.com/yuvalgraiber/kube-ops-mcp" \
      org.opencontainers.image.licenses="MIT"
# Fixed non-root UID/GID so Kubernetes runAsNonRoot can verify it.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
COPY --from=builder --chown=root:root /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    KUBE_OPS_TRANSPORT=http \
    KUBE_OPS_HOST=0.0.0.0 \
    KUBE_OPS_PORT=8000
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["kube-ops-mcp"]
