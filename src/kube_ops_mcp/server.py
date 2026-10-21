"""MCP server entry point: creates the app and registers tools, resources and prompts."""

from importlib.metadata import version
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from kube_ops_mcp import k8s, prometheus
from kube_ops_mcp.config import Settings

settings = Settings.from_env()

mcp = MCPServer(
    name="kube-ops",
    version=version("kube-ops-mcp"),
    instructions=(
        "Read-only Kubernetes incident triage. Use these tools to inspect pod status, "
        "events, logs and Prometheus metrics, then explain the most likely root cause."
    ),
)


def _read_only(title: str) -> ToolAnnotations:
    return ToolAnnotations(title=title, readOnlyHint=True, idempotentHint=True, openWorldHint=True)


@mcp.tool(annotations=_read_only("Get pod status"))
def get_pod_status(
    namespace: Annotated[str, Field(description="Kubernetes namespace, e.g. 'demo'.")],
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
    and exit code of the last termination. Start incident triage here.
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
        "namespace": namespace,
        "pod_count": len(summaries),
        "unhealthy_count": sum(1 for p in summaries if not p["healthy"]),
        "pods": summaries,
    }
    if not summaries:
        result["note"] = "No pods found. Check the namespace name and label selector."
    return result


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


def main() -> None:
    """Run the server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
