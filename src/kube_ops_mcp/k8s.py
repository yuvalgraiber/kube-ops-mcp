"""Kubernetes API access: client setup, input validation, reads, guarded writes and summaries."""

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Any

from kubernetes import client, config
from kubernetes.client import ApiException, CoreV1Event, V1ContainerStatus, V1Deployment, V1Pod
from urllib3.exceptions import HTTPError

# RFC 1123 label: namespaces, deployments, containers.
_DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
# RFC 1123 subdomain: pod names.
_DNS_SUBDOMAIN = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")
_MAX_MESSAGE_CHARS = 300
MAX_LOG_LINES = 500
_MAX_LOG_BYTES = 64 * 1024
# A container that terminated this recently is still "flapping", even if it is Running now.
RECENT_RESTART_WINDOW = timedelta(minutes=15)
RESTARTED_AT_ANNOTATION = "kubectl.kubernetes.io/restartedAt"


class KubernetesError(Exception):
    """An anticipated Kubernetes failure with a message that is safe to show the model."""


def validate_namespace(namespace: str) -> str:
    return _validate_name(namespace, "namespace", _DNS_LABEL, 63)


def validate_deployment(name: str) -> str:
    return _validate_name(name, "deployment name", _DNS_LABEL, 63)


def validate_container(name: str) -> str:
    return _validate_name(name, "container name", _DNS_LABEL, 63)


def validate_pod(name: str) -> str:
    return _validate_name(name, "pod name", _DNS_SUBDOMAIN, 253)


def _validate_name(value: str, kind: str, pattern: re.Pattern[str], max_len: int) -> str:
    if len(value) > max_len or not pattern.match(value):
        raise ValueError(
            f"Invalid {kind} {value!r}: use lowercase letters, digits and '-', "
            f"at most {max_len} characters."
        )
    return value


_context: str | None = None
_display_name: str | None = None


def use_context(context: str | None, display_name: str | None = None) -> None:
    """Pin the kubeconfig context (ignored inside a cluster) and the name to report."""
    global _context, _display_name
    _context, _display_name = context, display_name
    for cached in (_load_config, core_v1, apps_v1, version_api):
        cached.cache_clear()


def cluster_name() -> str:
    """The configured display name, else "in-cluster" or the kubeconfig context name."""
    return _display_name or _load_config()


@cache
def _load_config() -> str:
    """Load credentials once, preferring the in-cluster service account over kubeconfig."""
    try:
        config.load_incluster_config()
        return "in-cluster"
    except config.ConfigException:
        pass
    try:
        config.load_kube_config(context=_context)
        _, active = config.list_kube_config_contexts()
    except (config.ConfigException, ValueError, OSError) as exc:
        target = f"context {_context!r}" if _context else "the current context"
        raise KubernetesError(
            f"No Kubernetes credentials for {target}: not running in a cluster and no usable "
            "kubeconfig. Check KUBECONFIG, KUBE_OPS_CONTEXT or `kubectl config get-contexts`."
        ) from exc
    return _context or active["name"]


@cache
def core_v1() -> client.CoreV1Api:
    _load_config()
    return client.CoreV1Api()


@cache
def apps_v1() -> client.AppsV1Api:
    _load_config()
    return client.AppsV1Api()


@cache
def version_api() -> client.VersionApi:
    _load_config()
    return client.VersionApi()


def _call[T](what: str, timeout: float, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Call the Kubernetes API with a timeout and turn failures into actionable messages."""
    try:
        return fn(*args, _request_timeout=timeout, **kwargs)
    except ApiException as exc:
        raise KubernetesError(_describe_api_error(exc, what)) from exc
    except HTTPError as exc:
        raise KubernetesError(
            f"Could not reach the Kubernetes API within {timeout:g}s while {what}. "
            "Is the cluster running? Check with `kubectl cluster-info`."
        ) from exc


def _describe_api_error(exc: ApiException, what: str) -> str:
    if exc.status in (401, 403):
        return f"Access denied while {what}: the credentials lack permission (RBAC)."
    if exc.status == 404:
        return f"Not found while {what}. Check the names with get_pod_status."
    if exc.status == 400:
        reason = (exc.body or exc.reason or "bad request").strip()[:_MAX_MESSAGE_CHARS]
        return f"Kubernetes rejected the request while {what}: {reason}"
    return f"Kubernetes API error {exc.status} ({exc.reason}) while {what}."


# --- Reads -------------------------------------------------------------------------------


def list_pods(namespace: str, label_selector: str | None, timeout: float) -> list[V1Pod]:
    response = _call(
        f"listing pods in namespace {namespace!r}",
        timeout,
        core_v1().list_namespaced_pod,
        namespace,
        label_selector=label_selector or "",
    )
    return response.items


def list_warning_events(namespace: str, timeout: float) -> list[CoreV1Event]:
    response = _call(
        f"listing events in namespace {namespace!r}",
        timeout,
        core_v1().list_namespaced_event,
        namespace,
        field_selector="type=Warning",
    )
    return response.items


def read_logs(
    namespace: str, pod: str, container: str | None, tail_lines: int, previous: bool, timeout: float
) -> str:
    kwargs: dict[str, Any] = {"tail_lines": tail_lines, "limit_bytes": _MAX_LOG_BYTES}
    if container:
        kwargs["container"] = container
    if previous:
        kwargs["previous"] = True
    which = "previous " if previous else ""
    return _call(
        f"reading {which}logs of pod {namespace}/{pod}",
        timeout,
        core_v1().read_namespaced_pod_log,
        pod,
        namespace,
        **kwargs,
    )


def get_deployment(namespace: str, name: str, timeout: float) -> V1Deployment:
    return _call(
        f"reading deployment {namespace}/{name}",
        timeout,
        apps_v1().read_namespaced_deployment,
        name,
        namespace,
    )


def deployment_selector(deployment: V1Deployment) -> str:
    """Render the deployment's label selector, including matchExpressions, as a string."""
    selector = deployment.spec.selector
    if selector is None:
        raise KubernetesError("The deployment has no pod selector.")
    parts = [f"{k}={v}" for k, v in sorted((selector.match_labels or {}).items())]
    for expr in selector.match_expressions or []:
        values = ",".join(expr.values or [])
        match expr.operator:
            case "In":
                parts.append(f"{expr.key} in ({values})")
            case "NotIn":
                parts.append(f"{expr.key} notin ({values})")
            case "Exists":
                parts.append(expr.key)
            case "DoesNotExist":
                parts.append(f"!{expr.key}")
    if not parts:
        # An empty selector would match every pod in the namespace.
        raise KubernetesError("The deployment's pod selector is empty.")
    return ",".join(parts)


def cluster_summary(timeout: float) -> dict[str, Any]:
    nodes = _call("listing nodes", timeout, core_v1().list_node).items
    namespaces = _call("listing namespaces", timeout, core_v1().list_namespace).items
    version = _call("reading the server version", timeout, version_api().get_code)
    ready_nodes = sum(
        1
        for node in nodes
        for cond in node.status.conditions or []
        if cond.type == "Ready" and cond.status == "True"
    )
    return {
        "cluster": cluster_name(),
        "kubernetes_version": version.git_version,
        "nodes": {"total": len(nodes), "ready": ready_nodes},
        "namespaces": sorted(ns.metadata.name for ns in namespaces),
    }


# --- Writes (callers must enforce the write policy first) -------------------------------


def restart_deployment(namespace: str, name: str, timeout: float, now: datetime) -> None:
    """Trigger a rolling restart, exactly like `kubectl rollout restart`."""
    body = {
        "spec": {
            "template": {"metadata": {"annotations": {RESTARTED_AT_ANNOTATION: now.isoformat()}}}
        }
    }
    _call(
        f"restarting deployment {namespace}/{name}",
        timeout,
        apps_v1().patch_namespaced_deployment,
        name,
        namespace,
        body,
    )


def scale_deployment(namespace: str, name: str, replicas: int, timeout: float) -> int:
    """Set the replica count and return the previous one."""
    scale = _call(
        f"reading scale of deployment {namespace}/{name}",
        timeout,
        apps_v1().read_namespaced_deployment_scale,
        name,
        namespace,
    )
    _call(
        f"scaling deployment {namespace}/{name}",
        timeout,
        apps_v1().patch_namespaced_deployment_scale,
        name,
        namespace,
        {"spec": {"replicas": replicas}},
    )
    return scale.spec.replicas


# --- Summaries ---------------------------------------------------------------------------


def summarize_pod(pod: V1Pod, now: datetime | None = None) -> dict[str, Any]:
    """Reduce a V1Pod to the fields that matter for triage."""
    now = now or datetime.now(UTC)
    statuses = pod.status.container_statuses or []
    containers = [_summarize_container(cs) for cs in statuses]
    ready = sum(1 for cs in statuses if cs.ready)
    total = len(pod.spec.containers)
    status = _pod_status(pod)
    return {
        "name": pod.metadata.name,
        "status": status,
        "healthy": _is_healthy(pod.status.phase, ready, total)
        and not _restarted_recently(statuses, now),
        "ready": f"{ready}/{total}",
        "restarts": sum(cs.restart_count for cs in statuses),
        "age": _age(pod.metadata.creation_timestamp, now),
        "node": pod.spec.node_name,
        "containers": containers,
    }


def _summarize_container(cs: V1ContainerStatus) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "name": cs.name,
        "ready": cs.ready,
        "restarts": cs.restart_count,
        "state": "unknown",
    }
    state = cs.state
    if state and state.running:
        summary["state"] = "running"
    elif state and state.waiting:
        summary["state"] = "waiting"
        summary["reason"] = state.waiting.reason
        if state.waiting.message:
            summary["message"] = state.waiting.message[:_MAX_MESSAGE_CHARS]
    elif state and state.terminated:
        summary["state"] = "terminated"
        summary["reason"] = state.terminated.reason
        summary["exit_code"] = state.terminated.exit_code

    last = cs.last_state.terminated if cs.last_state else None
    if last:
        summary["last_termination"] = {
            "reason": last.reason,
            "exit_code": last.exit_code,
            "finished_at": last.finished_at.isoformat() if last.finished_at else None,
        }
    return summary


def _pod_status(pod: V1Pod) -> str:
    """Return the same headline status `kubectl get pods` would show."""
    if pod.metadata.deletion_timestamp:
        return "Terminating"
    if pod.status.reason:  # e.g. Evicted
        return pod.status.reason
    for cs in pod.status.init_container_statuses or []:
        if cs.state and cs.state.waiting and cs.state.waiting.reason:
            return f"Init:{cs.state.waiting.reason}"
        if cs.state and cs.state.terminated and cs.state.terminated.exit_code != 0:
            return f"Init:{cs.state.terminated.reason or 'Error'}"
    for cs in pod.status.container_statuses or []:
        if cs.state and cs.state.waiting and cs.state.waiting.reason:
            return cs.state.waiting.reason
        if cs.state and cs.state.terminated and cs.state.terminated.reason:
            return cs.state.terminated.reason
    return pod.status.phase or "Unknown"


def _is_healthy(phase: str | None, ready: int, total: int) -> bool:
    return phase == "Succeeded" or (phase == "Running" and ready == total)


def _restarted_recently(statuses: list[V1ContainerStatus], now: datetime) -> bool:
    for cs in statuses:
        last = cs.last_state.terminated if cs.last_state else None
        if last and last.finished_at and now - last.finished_at < RECENT_RESTART_WINDOW:
            return True
    return False


def _age(created: datetime | None, now: datetime) -> str | None:
    if created is None:
        return None
    seconds = max(0, int((now - created).total_seconds()))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def limit_pods(
    summaries: list[dict[str, Any]], max_pods: int, max_unhealthy: int
) -> tuple[list[dict[str, Any]], int]:
    """Keep the output small on big namespaces without hiding what matters.

    Unhealthy pods come first with full detail; healthy ones are shortened to one line.
    Returns the pods to show and how many were left out.
    """
    unhealthy = [p for p in summaries if not p["healthy"]][:max_unhealthy]
    room = max(0, max_pods - len(unhealthy))
    healthy = [
        {key: p[key] for key in ("name", "status", "ready", "restarts", "age")}
        for p in summaries
        if p["healthy"]
    ][:room]
    shown = unhealthy + healthy
    return shown, len(summaries) - len(shown)


def summarize_event(event: CoreV1Event, now: datetime) -> dict[str, Any]:
    obj = event.involved_object
    return {
        "reason": event.reason,
        "object": f"{obj.kind}/{obj.name}",
        "message": (event.message or "").strip()[:_MAX_MESSAGE_CHARS],
        "count": event.count or 1,
        "last_seen": _age(_event_time(event), now),
    }


def recent_events(events: list[CoreV1Event], window: timedelta, now: datetime) -> list[CoreV1Event]:
    """Keep events seen within `window`, newest first."""
    recent = [e for e in events if (t := _event_time(e)) is not None and now - t <= window]
    return sorted(recent, key=_event_time, reverse=True)


def _event_time(event: CoreV1Event) -> datetime | None:
    return (
        event.last_timestamp
        or event.event_time
        or event.first_timestamp
        or (event.metadata.creation_timestamp if event.metadata else None)
    )
