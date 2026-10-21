from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from kubernetes.client import (
    ApiException,
    CoreV1Event,
    V1Container,
    V1ContainerState,
    V1ContainerStateRunning,
    V1ContainerStateTerminated,
    V1ContainerStateWaiting,
    V1ContainerStatus,
    V1ObjectMeta,
    V1ObjectReference,
    V1Pod,
    V1PodSpec,
    V1PodStatus,
)
from urllib3.exceptions import MaxRetryError

from kube_ops_mcp import k8s

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def make_pod(
    name: str,
    phase: str,
    statuses: list[V1ContainerStatus],
    *,
    init_statuses: list[V1ContainerStatus] | None = None,
    reason: str | None = None,
) -> V1Pod:
    return V1Pod(
        metadata=V1ObjectMeta(name=name, creation_timestamp=NOW - timedelta(minutes=5)),
        spec=V1PodSpec(containers=[V1Container(name="app")] * max(1, len(statuses))),
        status=V1PodStatus(
            phase=phase,
            reason=reason,
            container_statuses=statuses,
            init_container_statuses=init_statuses,
        ),
    )


def container(
    *,
    ready: bool,
    restarts: int = 0,
    state: V1ContainerState,
    last: V1ContainerStateTerminated | None = None,
) -> V1ContainerStatus:
    return V1ContainerStatus(
        name="app",
        image="example",
        image_id="",
        ready=ready,
        restart_count=restarts,
        state=state,
        last_state=V1ContainerState(terminated=last) if last else None,
    )


RUNNING = V1ContainerState(running=V1ContainerStateRunning())


def test_healthy_pod() -> None:
    pod = make_pod("frontend", "Running", [container(ready=True, state=RUNNING)])

    summary = k8s.summarize_pod(pod, NOW)

    assert summary["status"] == "Running"
    assert summary["healthy"] is True
    assert summary["ready"] == "1/1"
    assert summary["age"] == "5m"


def test_crash_loop_reports_reason_and_exit_code() -> None:
    waiting = V1ContainerState(
        waiting=V1ContainerStateWaiting(reason="CrashLoopBackOff", message="back-off 40s")
    )
    last = V1ContainerStateTerminated(exit_code=1, reason="Error", finished_at=NOW)
    pod = make_pod(
        "payment", "Running", [container(ready=False, restarts=4, state=waiting, last=last)]
    )

    summary = k8s.summarize_pod(pod, NOW)

    assert summary["status"] == "CrashLoopBackOff"
    assert summary["healthy"] is False
    assert summary["restarts"] == 4
    assert summary["containers"][0]["last_termination"]["exit_code"] == 1


def test_oom_killed_pod_that_is_running_again_is_still_unhealthy() -> None:
    last = V1ContainerStateTerminated(
        exit_code=137, reason="OOMKilled", finished_at=NOW - timedelta(minutes=1)
    )
    pod = make_pod(
        "recommendation", "Running", [container(ready=True, restarts=3, state=RUNNING, last=last)]
    )

    summary = k8s.summarize_pod(pod, NOW)

    assert summary["healthy"] is False
    assert summary["containers"][0]["last_termination"]["reason"] == "OOMKilled"


def test_old_restart_does_not_mark_pod_unhealthy() -> None:
    last = V1ContainerStateTerminated(
        exit_code=137, reason="OOMKilled", finished_at=NOW - timedelta(hours=3)
    )
    pod = make_pod(
        "stable", "Running", [container(ready=True, restarts=1, state=RUNNING, last=last)]
    )

    assert k8s.summarize_pod(pod, NOW)["healthy"] is True


def test_init_container_failure_is_the_headline_status() -> None:
    init_waiting = V1ContainerState(waiting=V1ContainerStateWaiting(reason="CrashLoopBackOff"))
    pod = make_pod(
        "migrate",
        "Pending",
        [],
        init_statuses=[container(ready=False, state=init_waiting)],
    )

    assert k8s.summarize_pod(pod, NOW)["status"] == "Init:CrashLoopBackOff"


def test_evicted_pod_uses_pod_reason() -> None:
    pod = make_pod("evicted", "Failed", [], reason="Evicted")

    summary = k8s.summarize_pod(pod, NOW)

    assert summary["status"] == "Evicted"
    assert summary["healthy"] is False


@pytest.mark.parametrize("namespace", ["demo", "kube-system", "a" * 63])
def test_valid_namespaces(namespace: str) -> None:
    assert k8s.validate_namespace(namespace) == namespace


@pytest.mark.parametrize("namespace", ["", "Demo", "-demo", "demo_ns", "a" * 64, "../etc"])
def test_invalid_namespaces(namespace: str) -> None:
    with pytest.raises(ValueError, match="Invalid namespace"):
        k8s.validate_namespace(namespace)


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock = MagicMock()
    monkeypatch.setattr(k8s, "core_v1", lambda: mock)
    return mock


def test_list_pods_passes_selector_and_timeout(api: MagicMock) -> None:
    api.list_namespaced_pod.return_value.items = []

    k8s.list_pods("demo", "app=web", timeout=3)

    api.list_namespaced_pod.assert_called_once_with(
        "demo", label_selector="app=web", _request_timeout=3
    )


def test_forbidden_becomes_rbac_message(api: MagicMock) -> None:
    api.list_namespaced_pod.side_effect = ApiException(status=403, reason="Forbidden")

    with pytest.raises(k8s.KubernetesError, match="RBAC"):
        k8s.list_pods("demo", None, timeout=3)


def test_unreachable_cluster_becomes_actionable_message(api: MagicMock) -> None:
    api.list_namespaced_pod.side_effect = MaxRetryError(MagicMock(), "/api")

    with pytest.raises(k8s.KubernetesError, match="kubectl cluster-info"):
        k8s.list_pods("demo", None, timeout=3)


def make_event(reason: str, name: str, seen: datetime) -> CoreV1Event:
    return CoreV1Event(
        metadata=V1ObjectMeta(name=f"{name}.1"),
        involved_object=V1ObjectReference(kind="Pod", name=name),
        reason=reason,
        message=f"{reason} happened",
        last_timestamp=seen,
        count=2,
    )


def test_recent_events_filters_by_window_and_sorts_newest_first() -> None:
    old = make_event("BackOff", "web-1", NOW - timedelta(hours=2))
    newer = make_event("BackOff", "web-2", NOW - timedelta(minutes=1))
    older = make_event("Unhealthy", "web-3", NOW - timedelta(minutes=30))

    recent = k8s.recent_events([old, older, newer], timedelta(hours=1), NOW)

    assert [e.involved_object.name for e in recent] == ["web-2", "web-3"]


def test_summarize_event() -> None:
    event = make_event("BackOff", "web-1", NOW - timedelta(minutes=3))

    assert k8s.summarize_event(event, NOW) == {
        "reason": "BackOff",
        "object": "Pod/web-1",
        "message": "BackOff happened",
        "count": 2,
        "last_seen": "3m",
    }


def test_not_found_becomes_actionable_message(api: MagicMock) -> None:
    api.list_namespaced_pod.side_effect = ApiException(status=404, reason="Not Found")

    with pytest.raises(k8s.KubernetesError, match="Not found"):
        k8s.list_pods("demo", None, timeout=3)
