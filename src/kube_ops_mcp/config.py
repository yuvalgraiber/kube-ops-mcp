"""Runtime settings, read from environment variables (12-factor style)."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

DEFAULT_PROMETHEUS_URL = "http://localhost:9090"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_REPLICAS = 10
DEFAULT_PORT = 8000
TRANSPORTS = ("stdio", "http")


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
    # Pin a kubeconfig context so a local run never silently targets the wrong cluster.
    kube_context: str | None = None
    # Name to report for the cluster, e.g. "prod-eu". Defaults to the context or "in-cluster".
    cluster_name: str | None = None
    # "stdio" for a local client, "http" (Streamable HTTP) when running in a cluster.
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    # Host headers accepted over HTTP (DNS rebinding protection), e.g. "localhost:*".
    allowed_hosts: tuple[str, ...] = ("localhost:*", "127.0.0.1:*")

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        return cls(
            prometheus_url=env.get("PROMETHEUS_URL", DEFAULT_PROMETHEUS_URL).rstrip("/"),
            k8s_timeout_seconds=_positive_float(env, "KUBE_OPS_K8S_TIMEOUT"),
            prometheus_timeout_seconds=_positive_float(env, "KUBE_OPS_PROMETHEUS_TIMEOUT"),
            enable_write=_bool(env, "KUBE_OPS_ENABLE_WRITE", default=False),
            allowed_namespaces=frozenset(_csv(env, "ALLOWED_NAMESPACES")),
            max_replicas=_positive_int(env, "KUBE_OPS_MAX_REPLICAS", DEFAULT_MAX_REPLICAS),
            require_confirmation=_bool(env, "KUBE_OPS_REQUIRE_CONFIRMATION", default=True),
            kube_context=env.get("KUBE_OPS_CONTEXT") or None,
            cluster_name=env.get("KUBE_OPS_CLUSTER_NAME") or None,
            transport=_choice(env, "KUBE_OPS_TRANSPORT", TRANSPORTS),
            host=env.get("KUBE_OPS_HOST", "127.0.0.1"),
            port=_positive_int(env, "KUBE_OPS_PORT", DEFAULT_PORT),
            allowed_hosts=_csv(env, "KUBE_OPS_ALLOWED_HOSTS") or ("localhost:*", "127.0.0.1:*"),
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


def _choice(env: Mapping[str, str], name: str, choices: tuple[str, ...]) -> str:
    value = env.get(name, choices[0]).strip().lower()
    if value not in choices:
        raise ValueError(f"{name} must be one of {', '.join(choices)}, got {value!r}")
    return value


def _csv(env: Mapping[str, str], name: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in env.get(name, "").split(",") if item.strip())
