"""Kubernetes API access: client setup, input validation and pod summaries."""

import re
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Any

from kubernetes import client, config
from kubernetes.client import ApiException, V1ContainerStatus, V1Pod
from urllib3.exceptions import HTTPError

# RFC 1123 label: what Kubernetes accepts as a namespace name.
_DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_MAX_MESSAGE_CHARS = 300
# A container that terminated this recently is still "flapping", even if it is Running now.
RECENT_RESTART_WINDOW = timedelta(minutes=15)


class KubernetesError(Exception):
    """An anticipated Kubernetes failure with a message that is safe to show the model."""


def validate_namespace(namespace: str) -> str:
    if len(namespace) > 63 or not _DNS_LABEL.match(namespace):
        raise ValueError(
            f"Invalid namespace {namespace!r}: use lowercase letters, digits and '-', "
            "at most 63 characters."
        )
    return namespace


@cache
def core_v1() -> client.CoreV1Api:
    """Return a CoreV1Api client, preferring in-cluster credentials over kubeconfig."""
    try:
        config.load_incluster_config()
    except config.ConfigException:
        try:
            config.load_kube_config()
        except config.ConfigException as exc:
            raise KubernetesError(
                "No Kubernetes credentials found: not running in a cluster and no usable "
                "kubeconfig. Check KUBECONFIG or run `kubectl config current-context`."
            ) from exc
    return client.CoreV1Api()


def list_pods(namespace: str, label_selector: str | None, timeout: float) -> list[V1Pod]:
    try:
        response = core_v1().list_namespaced_pod(
            namespace,
            label_selector=label_selector or "",
            _request_timeout=timeout,
        )
    except ApiException as exc:
        raise KubernetesError(_describe_api_error(exc, f"pods in namespace {namespace!r}")) from exc
    except HTTPError as exc:
        raise KubernetesError(
            f"Could not reach the Kubernetes API within {timeout:g}s. "
            "Is the cluster running? Check with `kubectl cluster-info`."
        ) from exc
    return response.items


def _describe_api_error(exc: ApiException, what: str) -> str:
    if exc.status in (401, 403):
        return f"Access denied listing {what}: the credentials lack get/list permission (RBAC)."
    if exc.status == 400:
        return (
            f"Kubernetes rejected the request for {what}: {exc.reason}. Check the label selector."
        )
    return f"Kubernetes API error {exc.status} ({exc.reason}) while listing {what}."


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
