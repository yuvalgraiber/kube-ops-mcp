"""diagnose_workload: gather status, events, logs and metrics in one call, then explain them.

Data gathering is async glue; the analysis (`analyze` and helpers) is pure so it can be
unit-tested without a cluster.
"""

import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import anyio.to_thread
from kubernetes.client import V1Deployment, V1DeploymentStatus

from kube_ops_mcp import k8s, prometheus
from kube_ops_mcp.config import Settings

EVENT_WINDOW = timedelta(hours=1)
MAX_EVENTS = 20
MAX_PODS_WITH_LOGS = 3
# A Deployment can have hundreds of replicas; show enough to reason about, not all of them.
MAX_PODS_SHOWN = 15
MAX_UNHEALTHY_SHOWN = 10
MAX_AFFECTED_SHOWN = 10
LOG_TAIL_LINES = 30
STEPS = 5

_ERROR_LINE = re.compile(
    r"(?i)\b(fatal|error|exception|panic|traceback|failed|cannot|could not|refused|denied"
    r"|not set|missing|no such)\b"
)
_IMAGE_REASONS = {"ImagePullBackOff", "ErrImagePull", "InvalidImageName"}
_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}

Progress = Callable[[float, str], Awaitable[None]]


async def diagnose_workload(
    settings: Settings, namespace: str, deployment: str, progress: Progress
) -> dict[str, Any]:
    timeout = settings.k8s_timeout_seconds
    unavailable: list[str] = []

    await progress(0, "Reading deployment and pods")
    dep = await _thread(k8s.get_deployment, namespace, deployment, timeout)
    pods = await _thread(k8s.list_pods, namespace, k8s.deployment_selector(dep), timeout)
    now = datetime.now(UTC)
    pod_summaries = sorted(
        (k8s.summarize_pod(p, now) for p in pods), key=lambda p: (p["healthy"], p["name"])
    )

    await progress(1, "Reading warning events")
    events = await _thread(k8s.list_warning_events, namespace, timeout)
    names = related_names(deployment, [p["name"] for p in pod_summaries])
    event_summaries = [
        k8s.summarize_event(e, now)
        for e in k8s.recent_events(events, EVENT_WINDOW, now)
        if (e.involved_object.name or "") in names
    ][:MAX_EVENTS]

    await progress(2, "Reading logs of unhealthy pods")
    logs: dict[str, list[str]] = {}
    for pod in [p for p in pod_summaries if not p["healthy"]][:MAX_PODS_WITH_LOGS]:
        for container in pod["containers"]:
            crashed = "last_termination" in container
            # Skip healthy containers and ones that never started (e.g. image pull errors).
            if not crashed and (container["ready"] or container["state"] == "waiting"):
                continue
            key = f"{pod['name']}/{container['name']}"
            try:
                text = await _crash_logs(namespace, pod["name"], container, timeout)
            except k8s.KubernetesError as exc:
                unavailable.append(f"logs {key}: {exc}")
                continue
            logs[key] = text.splitlines()

    await progress(3, "Querying Prometheus for CPU and memory")
    try:
        usage = await resource_usage(settings, namespace, deployment)
    except (prometheus.PrometheusError, ValueError) as exc:
        usage = {}
        unavailable.append(f"metrics: {exc}")

    await progress(4, "Analyzing")
    status = deployment_status(dep)
    findings = analyze(status, pod_summaries, event_summaries, logs, usage)
    await progress(STEPS, "Done")

    shown, omitted = k8s.limit_pods(
        pod_summaries, max_pods=MAX_PODS_SHOWN, max_unhealthy=MAX_UNHEALTHY_SHOWN
    )
    shown_names = {p["name"] for p in shown}
    result: dict[str, Any] = {
        "namespace": namespace,
        "deployment": deployment,
        "findings": findings,
        "deployment_status": status,
        "pod_count": len(pod_summaries),
        "pods": shown,
        "warning_events": event_summaries,
        "logs": logs,
        "resource_usage": {
            key: value for key, value in usage.items() if key.split("/")[0] in shown_names
        },
    }
    if omitted:
        result["pods_note"] = f"Showing {len(shown)} of {len(pod_summaries)} pods, unhealthy first."
    if unavailable:
        result["unavailable"] = unavailable
    return result


async def _crash_logs(namespace: str, pod: str, container: dict[str, Any], timeout: float) -> str:
    """Logs of the run that crashed.

    A container that is terminated right now still holds its own crash logs. One that has
    already restarted needs the previous instance, which the kubelet may have cleaned up,
    so fall back to the current logs.
    """
    name = container["name"]
    if container["state"] == "terminated" or "last_termination" not in container:
        return await _thread(k8s.read_logs, namespace, pod, name, LOG_TAIL_LINES, False, timeout)
    try:
        return await _thread(k8s.read_logs, namespace, pod, name, LOG_TAIL_LINES, True, timeout)
    except k8s.KubernetesError:
        return await _thread(k8s.read_logs, namespace, pod, name, LOG_TAIL_LINES, False, timeout)


async def _thread(fn: Callable[..., Any], *args: Any) -> Any:
    """Run a blocking Kubernetes client call without blocking the event loop."""
    return await anyio.to_thread.run_sync(partial(fn, *args))


def related_names(deployment: str, pod_names: list[str]) -> set[str]:
    """The deployment, its pods, and their ReplicaSets (pod name minus the last segment).

    Exact names, not a prefix match, so diagnosing "web" ignores events of "web-api".
    """
    names = {deployment, *pod_names}
    names.update(name.rsplit("-", 1)[0] for name in pod_names if "-" in name)
    return names


def deployment_status(dep: V1Deployment) -> dict[str, Any]:
    status = dep.status or V1DeploymentStatus()
    problems = [
        {"type": c.type, "reason": c.reason, "message": c.message}
        for c in status.conditions or []
        if c.status != "True" or c.reason == "ProgressDeadlineExceeded"
    ]
    return {
        "desired": dep.spec.replicas if dep.spec else None,
        "ready": status.ready_replicas or 0,
        "available": status.available_replicas or 0,
        "updated": status.updated_replicas or 0,
        "problem_conditions": problems,
        "containers": container_specs(dep),
    }


def container_specs(dep: V1Deployment) -> list[dict[str, Any]]:
    """What each container is told to run: image, command, args, resources, env var names.

    Env var values are left out on purpose; they often hold credentials. The names are
    enough to spot a missing variable.
    """
    template = dep.spec.template if dep.spec else None
    containers = template.spec.containers if template and template.spec else []
    specs = []
    for c in containers:
        resources = c.resources
        specs.append(
            {
                "name": c.name,
                "image": c.image,
                "command": _clip(c.command),
                "args": _clip(c.args),
                "requests": dict(resources.requests or {}) if resources else {},
                "limits": dict(resources.limits or {}) if resources else {},
                "env_names": [e.name for e in c.env or []],
            }
        )
    return specs


def _clip(items: list[str] | None, max_items: int = 20, max_chars: int = 200) -> list[str]:
    return [item[:max_chars] for item in (items or [])[:max_items]]


async def resource_usage(
    settings: Settings, namespace: str, deployment: str
) -> dict[str, dict[str, float]]:
    """Peak memory (15m) and CPU (5m rate) per container, next to their limits."""
    # Deployment pods are named <deployment>-<replicaset hash>-<pod hash>.
    selector = f'namespace="{namespace}",pod=~"{deployment}-[a-z0-9]+-[a-z0-9]+"'
    containers = f'{selector},container!="",container!="POD"'
    by = "max by (pod, container)"
    limits = "kube_pod_container_resource_limits"
    queries = {
        "memory_peak_mib": (
            f"{by} (max_over_time(container_memory_working_set_bytes{{{containers}}}[15m]))",
            2**20,
        ),
        "memory_limit_mib": (f'{by} ({limits}{{{selector},resource="memory"}})', 2**20),
        "cpu_cores": (f"{by} (rate(container_cpu_usage_seconds_total{{{containers}}}[5m]))", 1),
        "cpu_limit_cores": (f'{by} ({limits}{{{selector},resource="cpu"}})', 1),
    }
    usage: dict[str, dict[str, float]] = {}
    for field, (promql, divisor) in queries.items():
        result = await prometheus.query(
            settings.prometheus_url, promql, timeout=settings.prometheus_timeout_seconds
        )
        for series in result["result"]:
            labels = series["metric"]
            key = f"{labels.get('pod')}/{labels.get('container')}"
            usage.setdefault(key, {})[field] = round(float(series["value"][1]) / divisor, 3)
    for values in usage.values():
        if values.get("memory_peak_mib") and values.get("memory_limit_mib"):
            values["memory_peak_pct_of_limit"] = round(
                100 * values["memory_peak_mib"] / values["memory_limit_mib"], 1
            )
    return usage


# --- Analysis (pure) ---------------------------------------------------------------------


def analyze(
    status: dict[str, Any],
    pods: list[dict[str, Any]],
    events: list[dict[str, Any]],
    logs: dict[str, list[str]],
    usage: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    """Turn raw signals into ranked findings, merged across replicas with the same problem."""
    merged: dict[str, dict[str, Any]] = {}

    def add(severity: str, summary: str, where: str, evidence: list[str], fix: str) -> None:
        finding = merged.setdefault(
            summary,
            {
                "severity": severity,
                "summary": summary,
                "affected": [],
                "evidence": evidence,
                "fix": fix,
            },
        )
        finding["affected"].append(where)

    for pod in pods:
        for c in pod["containers"]:
            _container_findings(add, pod, c, logs, usage)
    _event_findings(add, events)

    if not merged and status["ready"] < (status["desired"] or 0):
        add(
            "warning",
            f"Only {status['ready']} of {status['desired']} replicas are ready.",
            "deployment",
            [f"{c['type']}: {c['reason']}" for c in status["problem_conditions"]],
            "Check the deployment conditions and warning events.",
        )
    if not merged:
        return [
            {"severity": "info", "summary": "No problems found.", "affected": [], "evidence": []}
        ]
    for finding in merged.values():
        affected = finding["affected"]
        if len(affected) > MAX_AFFECTED_SHOWN:
            extra = len(affected) - MAX_AFFECTED_SHOWN
            finding["affected"] = [*affected[:MAX_AFFECTED_SHOWN], f"and {extra} more"]
    return sorted(merged.values(), key=lambda f: _SEVERITY_ORDER[f["severity"]])


def _container_findings(
    add: Callable[..., None],
    pod: dict[str, Any],
    c: dict[str, Any],
    logs: dict[str, list[str]],
    usage: dict[str, dict[str, float]],
) -> None:
    key = f"{pod['name']}/{c['name']}"
    last = c.get("last_termination") or {}
    reasons = {c.get("reason"), last.get("reason")}
    exit_code = last.get("exit_code", c.get("exit_code"))
    restarts = f"{c['restarts']} restarts"

    if "OOMKilled" in reasons:
        evidence = [f"last termination OOMKilled (exit code {exit_code})", restarts]
        metrics = usage.get(key, {})
        if "memory_limit_mib" in metrics:
            evidence.append(f"memory limit {metrics['memory_limit_mib']:g} MiB")
        if "memory_peak_mib" in metrics:
            evidence.append(f"peak memory seen by Prometheus {metrics['memory_peak_mib']:g} MiB")
        else:
            evidence.append(
                "peak memory not captured: the container dies faster than Prometheus scrapes it"
            )
        add(
            "critical",
            f"Container '{c['name']}' is killed for using more memory than its limit (OOMKilled).",
            key,
            evidence,
            "Raise resources.limits.memory, or find out why the app needs more memory "
            "than expected.",
        )
    elif reasons & _IMAGE_REASONS:
        add(
            "critical",
            f"Container '{c['name']}' image cannot be pulled ({c.get('reason')}).",
            key,
            [c.get("message", "")],
            "Check the image name and tag, and registry credentials (imagePullSecrets).",
        )
    elif "CreateContainerConfigError" in reasons:
        add(
            "critical",
            f"Container '{c['name']}' cannot start: its config references something missing.",
            key,
            [c.get("message", "")],
            "Create the missing ConfigMap/Secret or key, or fix the reference in the pod spec.",
        )
    elif exit_code not in (None, 0):
        evidence = [f"exit code {exit_code}", restarts]
        if line := error_line(logs.get(key, [])):
            evidence.append(f"log: {line}")
        add(
            "critical",
            f"Container '{c['name']}' exits with code {exit_code} soon after starting (app error).",
            key,
            evidence,
            "Fix the error shown in the log; a missing environment variable, bad config or an "
            "unreachable dependency is the usual cause.",
        )


def _event_findings(add: Callable[..., None], events: list[dict[str, Any]]) -> None:
    for event in events:
        reason, message, obj = event["reason"], event["message"], event["object"]
        if reason == "FailedScheduling":
            add(
                "critical",
                "Pods cannot be scheduled on any node.",
                obj,
                [message],
                "Lower resource requests, add capacity, or fix node selectors/taints.",
            )
        elif reason == "Unhealthy":
            add(
                "warning",
                "A liveness or readiness probe is failing.",
                obj,
                [message],
                "Check the probe path, port and timing against what the app really serves.",
            )
        elif reason in ("FailedMount", "FailedAttachVolume"):
            add(
                "critical",
                "A volume cannot be mounted.",
                obj,
                [message],
                "Check that the referenced PVC, ConfigMap or Secret exists.",
            )


def error_line(lines: list[str]) -> str | None:
    """The most recent line that looks like an error, else the last non-empty line."""
    for line in reversed(lines):
        if _ERROR_LINE.search(line):
            return line.strip()[:300]
    for line in reversed(lines):
        if line.strip():
            return line.strip()[:300]
    return None
