"""Runtime settings, read from environment variables (12-factor style)."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

DEFAULT_PROMETHEUS_URL = "http://localhost:9090"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_REPLICAS = 10


@dataclass(frozen=True)
class Settings:
    prometheus_url: str = DEFAULT_PROMETHEUS_URL
    k8s_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    prometheus_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    # Write tools are only registered when this is true.
    enable_write: bool = False
    # Write tools only act on these namespaces. Empty means nowhere.
    allowed_namespaces: frozenset[str] = field(default_factory=frozenset)
    max_replicas: int = DEFAULT_MAX_REPLICAS
    # Fail closed: refuse writes when the client cannot ask the user to confirm.
    require_confirmation: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        return cls(
            prometheus_url=env.get("PROMETHEUS_URL", DEFAULT_PROMETHEUS_URL).rstrip("/"),
            k8s_timeout_seconds=_positive_float(env, "KUBE_OPS_K8S_TIMEOUT"),
            prometheus_timeout_seconds=_positive_float(env, "KUBE_OPS_PROMETHEUS_TIMEOUT"),
            enable_write=_bool(env, "KUBE_OPS_ENABLE_WRITE", default=False),
            allowed_namespaces=frozenset(
                ns.strip() for ns in env.get("ALLOWED_NAMESPACES", "").split(",") if ns.strip()
            ),
            max_replicas=_positive_int(env, "KUBE_OPS_MAX_REPLICAS", DEFAULT_MAX_REPLICAS),
            require_confirmation=_bool(env, "KUBE_OPS_REQUIRE_CONFIRMATION", default=True),
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


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number, got {raw!r}") from None
    if value < 1:
        raise ValueError(f"{name} must be at least 1, got {raw!r}")
    return value


def _bool(env: Mapping[str, str], name: str, *, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in ("true", "1", "yes"):
        return True
    if value in ("false", "0", "no"):
        return False
    raise ValueError(f"{name} must be true or false, got {raw!r}")
