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
