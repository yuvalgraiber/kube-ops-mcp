import json

import pytest

from kube_ops_mcp import safety
from kube_ops_mcp.config import Settings


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("Authorization: Bearer abcdef123456789", "abcdef123456789"),
        ("connecting with password=hunter2", "hunter2"),
        ('{"api_key": "sk-live-1234567890"}', "sk-live-1234567890"),
        ("DB_PASSWORD: s3cr3t!", "s3cr3t!"),
        ("postgres://admin:topsecret@db:5432/app", "topsecret"),
        ("token ghp_" + "a" * 36, "ghp_" + "a" * 36),
        ("key AKIAABCDEFGHIJKLMNOP used", "AKIAABCDEFGHIJKLMNOP"),
        ("jwt eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0NTY3.SflKxwRJSMeKKF2QT4", "SflKxwRJSMeKKF2QT4"),
        (
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----",
            "MIIEow",
        ),
    ],
)
def test_secrets_are_redacted(text: str, secret: str) -> None:
    redacted = safety.redact_text(text)

    assert secret not in redacted
    assert "REDACTED" in redacted


@pytest.mark.parametrize(
    "text",
    [
        "FATAL: required environment variable DATABASE_URL is not set",
        "GET /healthz 200 in 3ms",
        "http://prometheus:9090/api/v1/query",
    ],
)
def test_ordinary_text_is_untouched(text: str) -> None:
    assert safety.redact_text(text) == text


def test_secret_looking_keys_are_redacted_recursively() -> None:
    data = {"env": [{"name": "DB_PASSWORD", "password": "x"}], "replicas": 3}

    assert safety.redact(data) == {
        "env": [{"name": "DB_PASSWORD", "password": safety.REDACTED}],
        "replicas": 3,
    }


def test_small_results_are_not_capped() -> None:
    result = {"content": [{"type": "text", "text": "ok"}], "structuredContent": {"a": 1}}

    assert safety.cap_tool_result(result) is result


def test_large_results_are_capped() -> None:
    big = "x" * 50_000
    result = {"content": [{"type": "text", "text": big}], "structuredContent": {"text": big}}

    capped = safety.cap_tool_result(result, limit=1000)

    assert len(json.dumps(capped)) < 2000
    assert "output truncated" in capped["content"][0]["text"]
    assert capped["structuredContent"]["truncated"] is True


def test_writes_blocked_when_flag_off() -> None:
    settings = Settings(enable_write=False, allowed_namespaces=frozenset({"demo"}))

    with pytest.raises(safety.WriteDenied, match="KUBE_OPS_ENABLE_WRITE"):
        safety.check_write_allowed(settings, "demo")


def test_writes_blocked_outside_allowed_namespaces() -> None:
    settings = Settings(enable_write=True, allowed_namespaces=frozenset({"demo"}))

    with pytest.raises(safety.WriteDenied, match="ALLOWED_NAMESPACES"):
        safety.check_write_allowed(settings, "kube-system")


def test_writes_allowed_in_allowed_namespace() -> None:
    settings = Settings(enable_write=True, allowed_namespaces=frozenset({"demo"}))

    safety.check_write_allowed(settings, "demo")


@pytest.mark.parametrize("replicas", [0, -1, 11])
def test_replicas_out_of_range_are_rejected(replicas: int) -> None:
    with pytest.raises(safety.WriteDenied, match="between 1 and 10"):
        safety.check_replicas(Settings(max_replicas=10), replicas)


def test_audit_line_redacts_arguments(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO", logger="kube_ops_mcp.audit"):
        safety.audit("query_prometheus", {"query": "up", "token": "abc"}, "ok", 1.5, None)

    entry = caplog.records[-1].msg
    assert entry["tool"] == "query_prometheus"
    assert entry["arguments"] == {"query": "up", "token": safety.REDACTED}
    assert entry["outcome"] == "ok"
