import pytest

from kube_ops_mcp.config import DEFAULT_PROMETHEUS_URL, DEFAULT_TIMEOUT_SECONDS, Settings


def test_defaults() -> None:
    settings = Settings.from_env({})

    assert settings.prometheus_url == DEFAULT_PROMETHEUS_URL
    assert settings.k8s_timeout_seconds == DEFAULT_TIMEOUT_SECONDS


def test_reads_environment() -> None:
    settings = Settings.from_env(
        {"PROMETHEUS_URL": "http://prom:9090/", "KUBE_OPS_K8S_TIMEOUT": "2.5"}
    )

    assert settings.prometheus_url == "http://prom:9090"
    assert settings.k8s_timeout_seconds == 2.5


@pytest.mark.parametrize("raw", ["abc", "0", "-1"])
def test_invalid_timeout_is_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match="KUBE_OPS_K8S_TIMEOUT"):
        Settings.from_env({"KUBE_OPS_K8S_TIMEOUT": raw})


def test_write_settings_from_environment() -> None:
    settings = Settings.from_env(
        {
            "KUBE_OPS_ENABLE_WRITE": "true",
            "ALLOWED_NAMESPACES": "demo, staging ,",
            "KUBE_OPS_MAX_REPLICAS": "4",
        }
    )

    assert settings.enable_write is True
    assert settings.allowed_namespaces == frozenset({"demo", "staging"})
    assert settings.max_replicas == 4
    assert settings.require_confirmation is True


def test_write_is_off_and_confirmation_on_by_default() -> None:
    settings = Settings.from_env({})

    assert settings.enable_write is False
    assert settings.allowed_namespaces == frozenset()
    assert settings.require_confirmation is True


def test_invalid_boolean_is_rejected() -> None:
    with pytest.raises(ValueError, match="KUBE_OPS_ENABLE_WRITE"):
        Settings.from_env({"KUBE_OPS_ENABLE_WRITE": "maybe"})


def test_http_transport_settings() -> None:
    settings = Settings.from_env(
        {
            "KUBE_OPS_TRANSPORT": "HTTP",
            "KUBE_OPS_HOST": "0.0.0.0",  # noqa: S104 - parsing test, nothing binds
            "KUBE_OPS_PORT": "9000",
            "KUBE_OPS_ALLOWED_HOSTS": "kube-ops-mcp:*, localhost:*",
            "KUBE_OPS_CONTEXT": "kind-demo",
        }
    )

    assert settings.transport == "http"
    assert settings.port == 9000
    assert settings.allowed_hosts == ("kube-ops-mcp:*", "localhost:*")
    assert settings.kube_context == "kind-demo"


def test_stdio_on_localhost_is_the_default() -> None:
    settings = Settings.from_env({})

    assert settings.transport == "stdio"
    assert settings.host == "127.0.0.1"
    assert settings.kube_context is None


def test_unknown_transport_is_rejected() -> None:
    with pytest.raises(ValueError, match="KUBE_OPS_TRANSPORT must be one of stdio, http"):
        Settings.from_env({"KUBE_OPS_TRANSPORT": "sse"})
