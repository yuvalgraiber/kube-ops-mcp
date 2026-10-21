"""MCP server entry point: creates the app and registers tools, resources and prompts."""

import json
import sys
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from typing import Annotated, Any

from mcp.server.mcpserver import (
    AcceptedElicitation,
    Context,
    Elicit,
    ElicitationResult,
    MCPServer,
    Resolve,
)
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from kube_ops_mcp import diagnose, k8s, prometheus, safety
from kube_ops_mcp.config import Settings

try:
    settings = Settings.from_env()
except ValueError as exc:
    sys.exit(f"kube-ops-mcp: invalid configuration: {exc}")
k8s.use_context(settings.kube_context, settings.cluster_name)

mcp = MCPServer(
    name="kube-ops",
    version=version("kube-ops-mcp"),
    instructions=(
        "Kubernetes incident triage. For a failing workload call diagnose_workload first; "
        "use get_pod_status, get_recent_events, get_pod_logs and query_prometheus to dig "
        "deeper. Explain the most likely root cause with the evidence that supports it."
    ),
    middleware=[safety.SafetyMiddleware()],
)

Namespace = Annotated[str, Field(description="Kubernetes namespace, e.g. 'demo'.")]
DeploymentName = Annotated[str, Field(description="Deployment name, e.g. 'payment-service'.")]


def _read_only(title: str) -> ToolAnnotations:
    return ToolAnnotations(title=title, readOnlyHint=True, idempotentHint=True, openWorldHint=True)


# --- Read tools --------------------------------------------------------------------------


@mcp.tool(annotations=_read_only("Get pod status"))
def get_pod_status(
    namespace: Namespace,
    label_selector: Annotated[
        str | None,
        Field(
            description="Optional label selector to narrow the pods, e.g. 'app=payment-service'."
        ),
    ] = None,
) -> dict[str, Any]:
    """Show the health of every pod in a namespace, unhealthy pods first.

    For each pod: headline status as kubectl shows it (e.g. CrashLoopBackOff, OOMKilled),
    ready containers, restart count, age, and per-container state including the reason
    and exit code of the last termination.
    """
    try:
        k8s.validate_namespace(namespace)
        pods = k8s.list_pods(namespace, label_selector, settings.k8s_timeout_seconds)
    except (ValueError, k8s.KubernetesError) as exc:
        raise ToolError(str(exc)) from exc

    summaries = sorted(
        (k8s.summarize_pod(pod) for pod in pods), key=lambda p: (p["healthy"], p["name"])
    )
    result: dict[str, Any] = {
        "cluster": k8s.cluster_name(),
        "namespace": namespace,
        "pod_count": len(summaries),
        "unhealthy_count": sum(1 for p in summaries if not p["healthy"]),
        "pods": summaries,
    }
    if not summaries:
        result["note"] = "No pods found. Check the namespace name and label selector."
    return result


@mcp.tool(annotations=_read_only("Get recent warning events"))
def get_recent_events(
    namespace: Namespace,
    minutes: Annotated[
        int, Field(ge=1, le=1440, description="How far back to look, in minutes (1-1440).")
    ] = 60,
    object_name: Annotated[
        str | None,
        Field(
            description=(
                "Optional name prefix to keep only events about one workload, e.g. "
                "'payment-service' matches its Deployment, ReplicaSets and Pods."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """List Warning events in a namespace, newest first.

    Events explain what Kubernetes itself saw: BackOff restarts, failed scheduling, failed
    probes, image pull errors, failed volume mounts. At most 50 events are returned.
    """
    try:
        k8s.validate_namespace(namespace)
        events = k8s.list_warning_events(namespace, settings.k8s_timeout_seconds)
    except (ValueError, k8s.KubernetesError) as exc:
        raise ToolError(str(exc)) from exc

    now = datetime.now(UTC)
    recent = k8s.recent_events(events, timedelta(minutes=minutes), now)
    if object_name:
        recent = [e for e in recent if e.involved_object.name.startswith(object_name)]
    summaries = [k8s.summarize_event(e, now) for e in recent]
    return {
        "namespace": namespace,
        "window_minutes": minutes,
        "event_count": len(summaries),
        "events": summaries[:50],
    }


@mcp.tool(annotations=_read_only("Get pod logs"))
def get_pod_logs(
    namespace: Namespace,
    pod: Annotated[str, Field(description="Pod name, as returned by get_pod_status.")],
    container: Annotated[
        str | None,
        Field(description="Container name. Required only when the pod has several containers."),
    ] = None,
    tail_lines: Annotated[
        int,
        Field(ge=1, le=k8s.MAX_LOG_LINES, description="Number of lines from the end (1-500)."),
    ] = 100,
    previous: Annotated[
        bool,
        Field(
            description=(
                "Read the logs of the previous, crashed container instead of the current one. "
                "Use this for CrashLoopBackOff, where the current container has just restarted."
            )
        ),
    ] = False,
) -> dict[str, Any]:
    """Read the last lines of a pod's container logs. Secrets in the output are redacted."""
    try:
        k8s.validate_namespace(namespace)
        k8s.validate_pod(pod)
        if container:
            k8s.validate_container(container)
        text = k8s.read_logs(
            namespace, pod, container, tail_lines, previous, settings.k8s_timeout_seconds
        )
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    except k8s.KubernetesError as exc:
        hint = (
            " The previous container may be gone already; try previous=false." if previous else ""
        )
        raise ToolError(f"{exc}{hint}") from exc

    lines = text.splitlines()
    return {
        "namespace": namespace,
        "pod": pod,
        "container": container,
        "previous": previous,
        "line_count": len(lines),
        "lines": lines,
    }


@mcp.tool(annotations=_read_only("Query Prometheus"))
async def query_prometheus(
    query: Annotated[
        str,
        Field(
            description=(
                "PromQL expression, e.g. 'container_memory_working_set_bytes{namespace=\"demo\"}'."
            )
        ),
    ],
    range_minutes: Annotated[
        int | None,
        Field(
            description=(
                "Omit for the current value (instant query). Set to look back that many "
                "minutes as a time series (range query, 1-1440); the step is chosen automatically."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """Run a PromQL query against Prometheus for CPU, memory, restart and other metrics.

    Useful queries: container_memory_working_set_bytes and kube_pod_container_resource_limits
    to compare memory use with limits, rate(container_cpu_usage_seconds_total[5m]) for CPU,
    and kube_pod_container_status_restarts_total for restarts. Results are capped at 50 series.
    """
    try:
        return await prometheus.query(
            settings.prometheus_url,
            query,
            range_minutes=range_minutes,
            timeout=settings.prometheus_timeout_seconds,
        )
    except (ValueError, prometheus.PrometheusError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=_read_only("Diagnose workload"))
async def diagnose_workload(
    namespace: Namespace, deployment: DeploymentName, ctx: Context
) -> dict[str, Any]:
    """Investigate a failing Deployment in one call and return ranked findings.

    Combines deployment and pod status, Warning events from the last hour, logs of the
    crashed containers, and CPU/memory usage against limits from Prometheus. Each finding
    has a severity, a summary, the evidence behind it and a suggested fix. Start here.
    """

    async def progress(step: float, message: str) -> None:
        await ctx.report_progress(step, diagnose.STEPS, message)

    try:
        k8s.validate_namespace(namespace)
        k8s.validate_deployment(deployment)
        result = await diagnose.diagnose_workload(settings, namespace, deployment, progress)
        return {"cluster": k8s.cluster_name(), **result}
    except (ValueError, k8s.KubernetesError) as exc:
        raise ToolError(str(exc)) from exc


# --- Write tools: registered only with KUBE_OPS_ENABLE_WRITE=true ------------------------


class Confirm(BaseModel):
    confirm: bool = Field(description="Approve this change to the cluster.")


class NoConfirmation(BaseModel):
    """The client cannot show a confirmation prompt (no elicitation support)."""


def _supports_elicitation(ctx: Context) -> bool:
    capabilities = ctx.client_capabilities
    elicitation = capabilities.elicitation if capabilities else None
    return elicitation is not None and (elicitation.form is not None or elicitation.url is None)


def _guard(namespace: str, deployment: str) -> None:
    """Validate names and enforce the write policy before the user is asked anything."""
    try:
        k8s.validate_namespace(namespace)
        k8s.validate_deployment(deployment)
        safety.check_write_allowed(settings, namespace)
    except (ValueError, safety.WriteDenied) as exc:
        raise ToolError(str(exc)) from exc


def _ask(ctx: Context, message: str) -> Elicit[Confirm] | NoConfirmation:
    return Elicit(message, Confirm) if _supports_elicitation(ctx) else NoConfirmation()


def confirm_restart(
    namespace: str, deployment: str, ctx: Context
) -> Elicit[Confirm] | NoConfirmation:
    _guard(namespace, deployment)
    return _ask(
        ctx,
        f"Restart deployment {namespace}/{deployment}? "
        "All of its pods will be replaced one by one (rolling restart).",
    )


def confirm_scale(
    namespace: str, deployment: str, replicas: int, ctx: Context
) -> Elicit[Confirm] | NoConfirmation:
    _guard(namespace, deployment)
    try:
        safety.check_replicas(settings, replicas)
    except safety.WriteDenied as exc:
        raise ToolError(str(exc)) from exc
    return _ask(ctx, f"Scale deployment {namespace}/{deployment} to {replicas} replicas?")


def _confirmed_by(confirmation: ElicitationResult[Any]) -> str:
    """Return who approved the change, or raise if nobody did."""
    if isinstance(confirmation, AcceptedElicitation):
        data = confirmation.data
        if isinstance(data, Confirm) and data.confirm:
            return "user"
        if isinstance(data, NoConfirmation):
            if settings.require_confirmation:
                raise ToolError(
                    "Not changed: this MCP client cannot ask you to confirm the change. Use a "
                    "client with elicitation support, or set KUBE_OPS_REQUIRE_CONFIRMATION=false."
                )
            return "policy (client cannot confirm; KUBE_OPS_REQUIRE_CONFIRMATION=false)"
    raise ToolError("Not changed: the change was not approved.")


def restart_deployment(
    namespace: Namespace,
    deployment: DeploymentName,
    confirmation: Annotated[ElicitationResult[Confirm], Resolve(confirm_restart)],
) -> dict[str, Any]:
    """Rolling-restart a Deployment, like `kubectl rollout restart`.

    Only works in namespaces listed in ALLOWED_NAMESPACES, and asks the user to confirm
    first. Use it when pods are stuck, never as a fix for a crash that will just repeat.
    """
    approved_by = _confirmed_by(confirmation)
    try:
        k8s.restart_deployment(
            namespace, deployment, settings.k8s_timeout_seconds, datetime.now(UTC)
        )
    except k8s.KubernetesError as exc:
        raise ToolError(str(exc)) from exc
    return {
        "namespace": namespace,
        "deployment": deployment,
        "restarted": True,
        "approved_by": approved_by,
    }


def scale_deployment(
    namespace: Namespace,
    deployment: DeploymentName,
    replicas: Annotated[int, Field(description="New replica count, at least 1.")],
    confirmation: Annotated[ElicitationResult[Confirm], Resolve(confirm_scale)],
) -> dict[str, Any]:
    """Change a Deployment's replica count within the allowed range (1 to KUBE_OPS_MAX_REPLICAS).

    Only works in namespaces listed in ALLOWED_NAMESPACES, and asks the user to confirm first.
    """
    approved_by = _confirmed_by(confirmation)
    try:
        previous = k8s.scale_deployment(
            namespace, deployment, replicas, settings.k8s_timeout_seconds
        )
    except k8s.KubernetesError as exc:
        raise ToolError(str(exc)) from exc
    return {
        "namespace": namespace,
        "deployment": deployment,
        "previous_replicas": previous,
        "replicas": replicas,
        "approved_by": approved_by,
    }


if settings.enable_write:
    mcp.add_tool(
        restart_deployment,
        annotations=ToolAnnotations(
            title="Restart deployment",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
        ),
    )
    mcp.add_tool(
        scale_deployment,
        annotations=ToolAnnotations(
            title="Scale deployment", readOnlyHint=False, destructiveHint=True, idempotentHint=True
        ),
    )


# --- Resources and prompts ---------------------------------------------------------------


@mcp.resource(
    "cluster://summary",
    name="cluster_summary",
    description="Kubernetes version, node readiness and namespace list.",
    mime_type="application/json",
)
def cluster_summary() -> str:
    try:
        return json.dumps(k8s.cluster_summary(settings.k8s_timeout_seconds), indent=2)
    except k8s.KubernetesError as exc:
        raise ResourceError(str(exc)) from exc


@mcp.prompt(title="Incident triage")
def incident_triage(
    namespace: Annotated[str, Field(description="Namespace of the failing workload.")],
    workload: Annotated[str, Field(description="Deployment name of the failing workload.")],
) -> str:
    """Step-by-step triage of a failing workload, in a fixed order."""
    return f"""Triage the deployment "{workload}" in namespace "{namespace}".

Work in this order and say what each step showed before moving on:
1. Status: diagnose_workload(namespace="{namespace}", deployment="{workload}") for the overview.
2. Events: get_recent_events for anything Kubernetes itself reported.
3. Logs: get_pod_logs for crashed containers, with previous=true for CrashLoopBackOff.
4. Metrics: query_prometheus to compare CPU and memory use with the limits.
5. Hypothesis: state the most likely root cause, the evidence for it, and the fix.

Do not change anything in the cluster unless the user explicitly asks."""


@mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
async def healthz(request: Request) -> PlainTextResponse:
    """Liveness/readiness probe for the HTTP transport."""
    return PlainTextResponse("ok")


def main() -> None:
    """Run the server over stdio (local client) or Streamable HTTP (in a cluster)."""
    safety.configure_audit_logging()
    try:
        cluster = k8s.cluster_name()
    except k8s.KubernetesError as exc:
        # Start anyway: every tool call returns this error until credentials work.
        cluster = f"unavailable: {exc}"
    safety.log_event(
        "startup",
        version=mcp.version,
        transport=settings.transport,
        cluster=cluster,
        write_tools=settings.enable_write,
        allowed_namespaces=sorted(settings.allowed_namespaces),
        prometheus_url=safety.redact_text(settings.prometheus_url),
    )
    if settings.transport == "stdio":
        mcp.run()
        return
    mcp.run(
        "streamable-http",
        host=settings.host,
        port=settings.port,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(settings.allowed_hosts),
            allowed_origins=[f"http://{host}" for host in settings.allowed_hosts],
        ),
    )


if __name__ == "__main__":
    main()
