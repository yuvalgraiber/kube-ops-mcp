from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from kubernetes.client import (
    V1ContainerStateTerminated,
    V1Deployment,
    V1DeploymentCondition,
    V1DeploymentSpec,
    V1DeploymentStatus,
    V1LabelSelector,
    V1PodTemplateSpec,
)

from kube_ops_mcp import diagnose, k8s, prometheus
from kube_ops_mcp.config import Settings
from tests.factories import RUNNING, container, make_deployment, make_event, make_pod

HEALTHY_STATUS = {"desired": 1, "ready": 1, "problem_conditions": []}


def pod(name: str, **container: Any) -> dict[str, Any]:
    c = {"name": "app", "ready": False, "restarts": 3, "state": "waiting", **container}
    return {"name": name, "healthy": False, "containers": [c]}


def test_oom_killed_finding_includes_limit_and_peak() -> None:
    pods = [pod("api-1", last_termination={"reason": "OOMKilled", "exit_code": 137})]
    usage = {"api-1/app": {"memory_limit_mib": 64, "memory_peak_mib": 63.9}}

    [finding] = diagnose.analyze(HEALTHY_STATUS, pods, [], {}, usage)

    assert finding["severity"] == "critical"
    assert "OOMKilled" in finding["summary"]
    assert "memory limit 64 MiB" in finding["evidence"]
    assert "limits.memory" in finding["fix"]


def test_app_error_finding_quotes_the_log_line() -> None:
    pods = [pod("pay-1", reason="CrashLoopBackOff", last_termination={"exit_code": 1})]
    logs = {"pay-1/app": ["starting", "FATAL: DATABASE_URL is not set"]}

    [finding] = diagnose.analyze(HEALTHY_STATUS, pods, [], logs, {})

    assert "exits with code 1" in finding["summary"]
    assert "log: FATAL: DATABASE_URL is not set" in finding["evidence"]


def test_image_pull_failure() -> None:
    pods = [pod("web-1", reason="ImagePullBackOff", message="manifest unknown")]

    [finding] = diagnose.analyze(HEALTHY_STATUS, pods, [], {}, {})

    assert "cannot be pulled" in finding["summary"]
    assert finding["evidence"] == ["manifest unknown"]


def test_missing_config_reference() -> None:
    pods = [pod("web-1", reason="CreateContainerConfigError", message='secret "db" not found')]

    [finding] = diagnose.analyze(HEALTHY_STATUS, pods, [], {}, {})

    assert "config references something missing" in finding["summary"]


def test_scheduling_failure_comes_from_events() -> None:
    events = [
        {
            "reason": "FailedScheduling",
            "message": "0/1 nodes: insufficient memory",
            "object": "Pod/web-1",
        }
    ]

    [finding] = diagnose.analyze(HEALTHY_STATUS, [], events, {}, {})

    assert "cannot be scheduled" in finding["summary"]


def test_same_problem_on_several_replicas_is_one_finding() -> None:
    oom = {"last_termination": {"reason": "OOMKilled", "exit_code": 137}}
    pods = [pod("api-1", **oom), pod("api-2", **oom)]

    [finding] = diagnose.analyze(HEALTHY_STATUS, pods, [], {}, {})

    assert finding["affected"] == ["api-1/app", "api-2/app"]


def test_findings_are_ranked_by_severity() -> None:
    events = [{"reason": "Unhealthy", "message": "probe failed", "object": "Pod/a-1"}]
    pods = [pod("a-1", last_termination={"reason": "OOMKilled", "exit_code": 137})]

    findings = diagnose.analyze(HEALTHY_STATUS, pods, events, {}, {})

    assert [f["severity"] for f in findings] == ["critical", "warning"]


def test_unready_replicas_without_a_known_cause() -> None:
    status = {"desired": 3, "ready": 1, "problem_conditions": []}

    [finding] = diagnose.analyze(status, [], [], {}, {})

    assert finding["summary"] == "Only 1 of 3 replicas are ready."


def test_healthy_workload_has_no_problems() -> None:
    [finding] = diagnose.analyze(HEALTHY_STATUS, [], [], {}, {})

    assert finding["severity"] == "info"


def test_error_line_prefers_the_latest_error() -> None:
    lines = ["boot", "error: first", "ok", "panic: second", ""]

    assert diagnose.error_line(lines) == "panic: second"
    assert diagnose.error_line(["just a line", ""]) == "just a line"
    assert diagnose.error_line([]) is None


def test_related_names_are_exact_and_include_replicasets() -> None:
    names = diagnose.related_names("web", ["web-7d9f8-abcde"])

    assert names == {"web", "web-7d9f8", "web-7d9f8-abcde"}
    assert "web-api-5c6d7-xyz12" not in names


def test_deployment_status_reports_problem_conditions() -> None:
    dep = V1Deployment(
        spec=V1DeploymentSpec(replicas=2, selector=V1LabelSelector(), template=V1PodTemplateSpec()),
        status=V1DeploymentStatus(
            ready_replicas=1,
            conditions=[
                V1DeploymentCondition(type="Available", status="False", reason="MinimumReplicas"),
                V1DeploymentCondition(type="Progressing", status="True", reason="NewRSAvailable"),
            ],
        ),
    )

    status = diagnose.deployment_status(dep)

    assert status["desired"] == 2
    assert status["ready"] == 1
    assert [c["type"] for c in status["problem_conditions"]] == ["Available"]


def test_oom_without_metrics_says_why_usage_is_missing() -> None:
    pods = [pod("api-1", last_termination={"reason": "OOMKilled", "exit_code": 137})]

    [finding] = diagnose.analyze(HEALTHY_STATUS, pods, [], {}, {})

    assert any("not captured" in e for e in finding["evidence"])


# --- Orchestration with a fake cluster and Prometheus -------------------------------------


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def cluster(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A deployment 'web' with one OOMKilled pod, plus an event from an unrelated workload."""
    now = datetime.now(UTC)
    oom = V1ContainerStateTerminated(
        reason="OOMKilled", exit_code=137, finished_at=now - timedelta(minutes=1)
    )
    pod = make_pod(
        "web-7d9f8-abcde",
        "Running",
        [container(ready=False, restarts=4, state=RUNNING, last=oom)],
        created=now - timedelta(hours=1),
    )
    events = [
        make_event("BackOff", "web-7d9f8-abcde", now - timedelta(minutes=2)),
        make_event("BackOff", "web-api-5c6d7-xyz12", now - timedelta(minutes=2)),
    ]
    calls: dict[str, Any] = {"logs": []}

    def list_pods(namespace: str, selector: str, timeout: float) -> list[Any]:
        calls["selector"] = selector
        return [pod]

    def read_logs(*args: Any) -> str:
        calls["logs"].append(args)
        return "allocating\nkilled"

    monkeypatch.setattr(k8s, "get_deployment", lambda *a: make_deployment("web"))
    monkeypatch.setattr(k8s, "list_pods", list_pods)
    monkeypatch.setattr(k8s, "list_warning_events", lambda *a: events)
    monkeypatch.setattr(k8s, "read_logs", read_logs)
    return calls


def fake_prometheus(values: dict[str, float]) -> Any:
    async def query(base_url: str, promql: str, **kwargs: Any) -> dict[str, Any]:
        for needle, value in values.items():
            if needle in promql:
                metric = {"pod": "web-7d9f8-abcde", "container": "app"}
                return {"result": [{"metric": metric, "value": [0, str(value)]}]}
        return {"result": []}

    return query


@pytest.mark.anyio
async def test_diagnose_combines_every_signal(
    cluster: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        prometheus,
        "query",
        fake_prometheus({"working_set": 63 * 2**20, 'resource="memory"': 64 * 2**20}),
    )
    steps: list[str] = []

    async def progress(step: float, message: str) -> None:
        steps.append(message)

    result = await diagnose.diagnose_workload(Settings(), "demo", "web", progress)

    assert cluster["selector"] == "app=web"
    assert result["findings"][0]["summary"].endswith("(OOMKilled).")
    assert [e["object"] for e in result["warning_events"]] == ["Pod/web-7d9f8-abcde"]
    assert cluster["logs"][0][4] is True  # previous container, because it crashed
    usage = result["resource_usage"]["web-7d9f8-abcde/app"]
    assert usage["memory_limit_mib"] == 64
    assert usage["memory_peak_pct_of_limit"] == pytest.approx(98.4)
    assert steps[0] == "Reading deployment and pods" and steps[-1] == "Done"
    assert "unavailable" not in result


@pytest.mark.anyio
async def test_diagnose_still_answers_when_prometheus_is_down(
    cluster: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def down(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise prometheus.PrometheusError("Could not connect to Prometheus")

    async def progress(step: float, message: str) -> None:
        pass

    monkeypatch.setattr(prometheus, "query", down)

    result = await diagnose.diagnose_workload(Settings(), "demo", "web", progress)

    assert result["findings"][0]["severity"] == "critical"
    assert result["resource_usage"] == {}
    assert result["unavailable"] == ["metrics: Could not connect to Prometheus"]


@pytest.mark.anyio
async def test_diagnose_reports_unreadable_logs(
    cluster: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any) -> str:
        raise k8s.KubernetesError("Access denied (RBAC)")

    async def progress(step: float, message: str) -> None:
        pass

    monkeypatch.setattr(k8s, "read_logs", forbidden)
    monkeypatch.setattr(prometheus, "query", fake_prometheus({}))

    result = await diagnose.diagnose_workload(Settings(), "demo", "web", progress)

    assert result["logs"] == {}
    assert result["unavailable"] == ["logs web-7d9f8-abcde/app: Access denied (RBAC)"]
