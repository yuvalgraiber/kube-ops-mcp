"""End-to-end tests through an in-memory MCP client: tools, middleware and elicitation."""

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import mcp_types as types
import pytest
from mcp.client import Client

from kube_ops_mcp import server
from kube_ops_mcp.config import Settings
from tests.factories import RUNNING, container, make_event, make_pod

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_server_identity() -> None:
    assert server.mcp.name == "kube-ops"
    assert server.mcp.version
    assert "triage" in server.mcp.instructions


async def test_write_tools_are_not_registered_by_default() -> None:
    async with Client(server.mcp) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}

    assert {"get_pod_status", "get_pod_logs", "diagnose_workload"} <= names
    assert not names & {"restart_deployment", "scale_deployment"}


def test_write_tools_are_registered_when_flag_is_on() -> None:
    env = {**os.environ, "KUBE_OPS_ENABLE_WRITE": "true", "ALLOWED_NAMESPACES": "demo"}
    script = (
        "import asyncio\nfrom kube_ops_mcp.server import mcp\n"
        "print(sorted(t.name for t in asyncio.run(mcp.list_tools())))"
    )

    output = subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, check=True
    ).stdout

    assert "restart_deployment" in output
    assert "scale_deployment" in output


async def test_tool_output_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server.k8s, "read_logs", lambda *a: "login ok\npassword=hunter2\n")

    async with Client(server.mcp) as client:
        result = await client.call_tool("get_pod_logs", {"namespace": "demo", "pod": "web-1"})

    assert not result.is_error
    assert "hunter2" not in result.content[0].text
    assert "hunter2" not in str(result.structured_content)


async def test_large_output_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server.k8s, "read_logs", lambda *a: ("x" * 200 + "\n") * 500)

    async with Client(server.mcp) as client:
        result = await client.call_tool("get_pod_logs", {"namespace": "demo", "pod": "web-1"})

    assert "output truncated" in result.content[0].text


async def test_invalid_input_returns_tool_error_and_audit_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with Client(server.mcp) as client:
        with caplog.at_level("INFO", logger="kube_ops_mcp.audit"):
            result = await client.call_tool("get_pod_status", {"namespace": "Bad_NS"})

    assert result.is_error
    assert "Invalid namespace" in result.content[0].text
    audit = [r.msg for r in caplog.records if r.name == "kube_ops_mcp.audit"]
    assert audit[-1]["tool"] == "get_pod_status"
    assert audit[-1]["outcome"] == "error"


async def test_diagnose_reports_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_diagnose(settings: Settings, ns: str, dep: str, progress: Any) -> dict:
        await progress(0, "Reading deployment and pods")
        await progress(5, "Done")
        return {"findings": []}

    monkeypatch.setattr(server.diagnose, "diagnose_workload", fake_diagnose)
    updates: list[tuple[float, str | None]] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        updates.append((progress, message))

    async with Client(server.mcp) as client:
        await client.call_tool(
            "diagnose_workload",
            {"namespace": "demo", "deployment": "web"},
            progress_callback=on_progress,
        )

    assert updates == [(0, "Reading deployment and pods"), (5, "Done")]


async def test_get_pod_status_lists_unhealthy_pods_first(monkeypatch: pytest.MonkeyPatch) -> None:
    pods = [
        make_pod("a-healthy", "Running", [container(ready=True, state=RUNNING)]),
        make_pod("b-pending", "Pending", []),
    ]
    monkeypatch.setattr(server.k8s, "list_pods", lambda *a: pods)

    async with Client(server.mcp) as client:
        result = await client.call_tool("get_pod_status", {"namespace": "demo"})

    content = result.structured_content
    assert [p["name"] for p in content["pods"]] == ["b-pending", "a-healthy"]
    assert content["unhealthy_count"] == 1


async def test_get_pod_status_explains_an_empty_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server.k8s, "list_pods", lambda *a: [])

    async with Client(server.mcp) as client:
        result = await client.call_tool("get_pod_status", {"namespace": "demo"})

    assert "No pods found" in result.structured_content["note"]


async def test_get_recent_events_filters_by_object_name(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    events = [
        make_event("BackOff", "web-1", now - timedelta(minutes=1)),
        make_event("BackOff", "db-1", now - timedelta(minutes=1)),
        make_event("BackOff", "web-2", now - timedelta(hours=3)),
    ]
    monkeypatch.setattr(server.k8s, "list_warning_events", lambda *a: events)

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_recent_events", {"namespace": "demo", "object_name": "web"}
        )

    assert [e["object"] for e in result.structured_content["events"]] == ["Pod/web-1"]


async def test_kubernetes_failures_reach_the_model_as_tool_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unreachable(*args: Any) -> None:
        raise server.k8s.KubernetesError("Could not reach the Kubernetes API")

    monkeypatch.setattr(server.k8s, "list_pods", unreachable)

    async with Client(server.mcp) as client:
        result = await client.call_tool("get_pod_status", {"namespace": "demo"})

    assert result.is_error
    assert "Could not reach the Kubernetes API" in result.content[0].text


async def test_cluster_summary_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    summary = {"kubernetes_version": "v1.37.0", "nodes": {"total": 1, "ready": 1}}
    monkeypatch.setattr(server.k8s, "cluster_summary", lambda timeout: summary)

    async with Client(server.mcp) as client:
        result = await client.read_resource("cluster://summary")

    assert json.loads(result.contents[0].text) == summary


async def test_incident_triage_prompt_names_the_workload() -> None:
    async with Client(server.mcp) as client:
        result = await client.get_prompt(
            "incident_triage", {"namespace": "demo", "workload": "payment-service"}
        )

    text = result.messages[0].content.text
    assert 'diagnose_workload(namespace="demo", deployment="payment-service")' in text
    assert "Do not change anything" in text


# --- Write tools ---------------------------------------------------------------------------


@pytest.fixture
def write_mode(monkeypatch: pytest.MonkeyPatch) -> Iterator[MagicMock]:
    """Enable writes for namespace 'demo' and replace the Kubernetes write calls with a mock."""
    monkeypatch.setattr(
        server,
        "settings",
        Settings(enable_write=True, allowed_namespaces=frozenset({"demo"}), max_replicas=5),
    )
    k8s_writes = MagicMock()
    k8s_writes.scale_deployment.return_value = 1
    monkeypatch.setattr(server.k8s, "restart_deployment", k8s_writes.restart_deployment)
    monkeypatch.setattr(server.k8s, "scale_deployment", k8s_writes.scale_deployment)
    server.mcp.add_tool(server.restart_deployment)
    server.mcp.add_tool(server.scale_deployment)
    yield k8s_writes
    server.mcp.remove_tool("restart_deployment")
    server.mcp.remove_tool("scale_deployment")


def answer(action: str, confirm: bool = True) -> Any:
    asked: list[str] = []

    async def callback(context: Any, params: types.ElicitRequestParams) -> types.ElicitResult:
        asked.append(params.message)
        content = {"confirm": confirm} if action == "accept" else None
        return types.ElicitResult(action=action, content=content)

    callback.asked = asked  # type: ignore[attr-defined]
    return callback


RESTART = {"namespace": "demo", "deployment": "web"}


async def test_restart_runs_after_user_confirms(write_mode: MagicMock) -> None:
    callback = answer("accept")

    async with Client(server.mcp, elicitation_callback=callback) as client:
        result = await client.call_tool("restart_deployment", RESTART)

    assert not result.is_error
    assert result.structured_content["approved_by"] == "user"
    assert "demo/web" in callback.asked[0]
    write_mode.restart_deployment.assert_called_once()


@pytest.mark.parametrize(("action", "confirm"), [("decline", True), ("accept", False)])
async def test_restart_does_nothing_when_user_says_no(
    write_mode: MagicMock, action: str, confirm: bool
) -> None:
    async with Client(server.mcp, elicitation_callback=answer(action, confirm)) as client:
        result = await client.call_tool("restart_deployment", RESTART)

    assert result.is_error
    write_mode.restart_deployment.assert_not_called()


async def test_restart_fails_closed_without_elicitation(write_mode: MagicMock) -> None:
    async with Client(server.mcp) as client:
        result = await client.call_tool("restart_deployment", RESTART)

    assert result.is_error
    assert "cannot ask you to confirm" in result.content[0].text
    write_mode.restart_deployment.assert_not_called()


async def test_namespace_outside_allow_list_is_refused_before_asking(
    write_mode: MagicMock,
) -> None:
    callback = answer("accept")

    async with Client(server.mcp, elicitation_callback=callback) as client:
        result = await client.call_tool(
            "restart_deployment", {"namespace": "kube-system", "deployment": "coredns"}
        )

    assert result.is_error
    assert "ALLOWED_NAMESPACES" in result.content[0].text
    assert callback.asked == []
    write_mode.restart_deployment.assert_not_called()


async def test_scale_within_range(write_mode: MagicMock) -> None:
    async with Client(server.mcp, elicitation_callback=answer("accept")) as client:
        result = await client.call_tool(
            "scale_deployment", {"namespace": "demo", "deployment": "web", "replicas": 3}
        )

    assert result.structured_content["previous_replicas"] == 1
    assert result.structured_content["replicas"] == 3
    write_mode.scale_deployment.assert_called_once_with("demo", "web", 3, pytest.approx(10.0))


@pytest.mark.parametrize("replicas", [0, 6])
async def test_scale_out_of_range_is_refused(write_mode: MagicMock, replicas: int) -> None:
    async with Client(server.mcp, elicitation_callback=answer("accept")) as client:
        result = await client.call_tool(
            "scale_deployment", {"namespace": "demo", "deployment": "web", "replicas": replicas}
        )

    assert result.is_error
    write_mode.scale_deployment.assert_not_called()
