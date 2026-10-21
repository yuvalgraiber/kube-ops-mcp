"""End-to-end check against a deployed server.

    python scripts/smoke_test.py <mcp-url> <expected-cluster-name>

CI runs it after installing the Helm chart into a kind cluster with the demo apps.
CI has no Prometheus, so this also checks that diagnosis degrades cleanly without it.
"""

import asyncio
import sys

from mcp.client import Client

READ_TOOLS = {
    "diagnose_workload",
    "get_pod_logs",
    "get_pod_status",
    "get_recent_events",
    "query_prometheus",
}


def check(condition: bool, message: str) -> None:
    if not condition:
        sys.exit(f"FAIL: {message}")
    print(f"ok: {message}")


async def main(url: str, cluster: str) -> None:
    async with Client(url) as client:
        tools = {tool.name for tool in (await client.list_tools()).tools}
        check(tools == READ_TOOLS, f"only read tools are registered ({sorted(tools)})")

        status = (
            await client.call_tool("get_pod_status", {"namespace": "demo"})
        ).structured_content
        check(status["cluster"] == cluster, f"results name the cluster ({cluster})")
        check(status["unhealthy_count"] >= 2, "both broken demo apps are reported unhealthy")

        result = await client.call_tool(
            "diagnose_workload", {"namespace": "demo", "deployment": "payment-service"}
        )
        check(not result.is_error, "diagnose_workload succeeds")
        top = result.structured_content["findings"][0]
        check("exits with code 1" in top["summary"], f"root cause found: {top['summary']}")
        check(
            any("DATABASE_URL" in line for line in top["evidence"]),
            "the crash log line is quoted as evidence",
        )
        content = result.structured_content
        metrics_down = any(u.startswith("metrics:") for u in content.get("unavailable", []))
        check(
            bool(content["resource_usage"]) or metrics_down, "metrics present or reported missing"
        )

        denied = await client.call_tool("get_pod_status", {"namespace": "../kube-system"})
        check(denied.is_error, "invalid input is rejected")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
