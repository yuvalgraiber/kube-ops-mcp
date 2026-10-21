"""Safety layer: secret redaction, output caps, audit logging and write-policy guards.

`SafetyMiddleware` wraps every `tools/call`, so no tool can return output that skips
redaction or the size cap, and every call leaves exactly one audit line.
"""

import json
import logging
import re
import sys
import time
from datetime import UTC, datetime
from typing import Any

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from pydantic import BaseModel

from kube_ops_mcp.config import Settings

MAX_OUTPUT_CHARS = 25_000
REDACTED = "[REDACTED]"

_SECRET_KEY = re.compile(
    r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key"
    r"|credential|authorization|cookie)"
)

# Ordered: specific token formats first, then generic key=value / key: value pairs.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        "[REDACTED PRIVATE KEY]",
    ),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), rf"\1 {REDACTED}"),
    (re.compile(r"\beyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}"), "[REDACTED JWT]"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[REDACTED AWS KEY]"),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_\w{30,})"), "[REDACTED GITHUB TOKEN]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "[REDACTED SLACK TOKEN]"),
    # Credentials inside URLs: scheme://user:password@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@/]+@"), rf"\1{REDACTED}@"),
    # password=..., "token": "...", API_KEY: ...
    (
        re.compile(
            r"(?i)\b([\w.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key"
            r"|private[_-]?key|credentials?)[\w.-]*)"
            r"(\"?\s*[=:]\s*\"?)([^\s\"',;&]+)"
        ),
        rf"\1\2{REDACTED}",
    ),
]

audit_logger = logging.getLogger("kube_ops_mcp.audit")


# --- Redaction and output caps -----------------------------------------------------------


def redact_text(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact(value: Any) -> Any:
    """Recursively redact secrets in strings, and the whole value of secret-looking keys."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            key: REDACTED
            if isinstance(key, str) and _SECRET_KEY.search(key) and isinstance(item, str)
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def cap_tool_result(result: dict[str, Any], limit: int = MAX_OUTPUT_CHARS) -> dict[str, Any]:
    """Shrink a CallToolResult payload that would flood the model's context."""
    size = len(json.dumps(result, default=str))
    if size <= limit:
        return result
    texts = "\n".join(
        c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"
    )
    note = (
        f"[output truncated: {size} characters exceeds the {limit} limit; "
        "narrow the request, e.g. fewer log lines or a label selector]"
    )
    capped = {**result, "content": [{"type": "text", "text": f"{texts[: limit // 2]}\n{note}"}]}
    if "structuredContent" in capped:
        capped["structuredContent"] = {"truncated": True, "note": note}
    return capped


# --- Write policy ------------------------------------------------------------------------


class WriteDenied(Exception):
    """A write was refused by policy. The message says how to allow it."""


def check_write_allowed(settings: Settings, namespace: str) -> None:
    if not settings.enable_write:
        raise WriteDenied(
            "Write tools are disabled. Set KUBE_OPS_ENABLE_WRITE=true to enable them."
        )
    if namespace not in settings.allowed_namespaces:
        allowed = ", ".join(sorted(settings.allowed_namespaces)) or "none"
        raise WriteDenied(
            f"Namespace {namespace!r} is not in ALLOWED_NAMESPACES (currently: {allowed})."
        )


def check_replicas(settings: Settings, replicas: int) -> None:
    if not 1 <= replicas <= settings.max_replicas:
        raise WriteDenied(
            f"replicas must be between 1 and {settings.max_replicas} (KUBE_OPS_MAX_REPLICAS). "
            "Scaling to 0 is not allowed because it takes the workload down."
        )


# --- Audit log ---------------------------------------------------------------------------


class _JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(record.msg, default=str)


def configure_audit_logging() -> None:
    """Send audit lines to stderr as JSON. stdout is reserved for the MCP stdio transport."""
    if audit_logger.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonLineFormatter())
    audit_logger.addHandler(handler)
    audit_logger.setLevel(logging.INFO)
    audit_logger.propagate = False


def log_event(event: str, **fields: Any) -> None:
    audit_logger.info(
        {"ts": datetime.now(UTC).isoformat(timespec="milliseconds"), "event": event, **fields}
    )


def audit(tool: str, arguments: Any, outcome: str, duration_ms: float, error: str | None) -> None:
    entry: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "event": "tool_call",
        "tool": tool,
        "arguments": redact(arguments),
        "outcome": outcome,
        "duration_ms": round(duration_ms, 1),
    }
    if error:
        entry["error"] = redact_text(error)[:500]
    audit_logger.info(entry)


# --- Middleware --------------------------------------------------------------------------


class SafetyMiddleware:
    """Redact, cap and audit every tool call."""

    async def __call__(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        if ctx.method != "tools/call":
            return await call_next(ctx)

        params = ctx.params or {}
        tool = str(params.get("name", "?"))
        arguments = params.get("arguments") or {}
        started = time.monotonic()
        try:
            result = await call_next(ctx)
        except Exception as exc:
            audit(tool, arguments, "protocol_error", _ms_since(started), str(exc))
            raise

        # The handler result is normally the serialized wire dict; accept a model too.
        if isinstance(result, BaseModel):
            payload = result.model_dump(by_alias=True, mode="json", exclude_none=True)
        elif isinstance(result, dict):
            payload = result
        else:
            audit(tool, arguments, "ok", _ms_since(started), None)
            return result

        if payload.get("resultType") == "input_required":
            # Waiting for the user (e.g. a write confirmation); the tool has not run yet.
            audit(tool, arguments, "awaiting_input", _ms_since(started), None)
            return result

        payload = cap_tool_result(redact(payload))
        is_error = bool(payload.get("isError"))
        error = _first_text(payload) if is_error else None
        audit(tool, arguments, "error" if is_error else "ok", _ms_since(started), error)
        return payload


def _first_text(payload: dict[str, Any]) -> str | None:
    for item in payload.get("content", []):
        if item.get("type") == "text":
            return item.get("text")
    return None


def _ms_since(started: float) -> float:
    return (time.monotonic() - started) * 1000
