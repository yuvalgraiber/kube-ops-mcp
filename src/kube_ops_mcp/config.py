"""Runtime settings, read from environment variables (12-factor style)."""

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_PROMETHEUS_URL = "http://localhost:9090"
DEFAULT_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class Settings:
    prometheus_url: str = DEFAULT_PROMETHEUS_URL
    k8s_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    prometheus_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        return cls(
            prometheus_url=env.get("PROMETHEUS_URL", DEFAULT_PROMETHEUS_URL).rstrip("/"),
            k8s_timeout_seconds=_positive_float(env, "KUBE_OPS_K8S_TIMEOUT"),
            prometheus_timeout_seconds=_positive_float(env, "KUBE_OPS_PROMETHEUS_TIMEOUT"),
        )


def _positive_float(env: Mapping[str, str], name: str) -> float:
    raw = env.get(name)
    if raw is None:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number of seconds, got {raw!r}") from None
    if value <= 0:
        raise ValueError(f"{name} must be greater than 0, got {raw!r}")
    return value
