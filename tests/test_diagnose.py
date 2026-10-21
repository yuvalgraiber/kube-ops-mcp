from typing import Any

from kubernetes.client import (
    V1Deployment,
    V1DeploymentCondition,
    V1DeploymentSpec,
    V1DeploymentStatus,
    V1LabelSelector,
    V1PodTemplateSpec,
)

from kube_ops_mcp import diagnose

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


def test_belongs_to_matches_deployment_replicasets_and_pods() -> None:
    assert diagnose._belongs_to("web", "web")
    assert diagnose._belongs_to("web-7d9f8-abcde", "web")
    assert not diagnose._belongs_to("webhook", "web")


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
