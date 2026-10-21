from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from kubernetes.client import (
    ApiException,
    V1ContainerState,
    V1ContainerStateTerminated,
    V1ContainerStateWaiting,
    V1Deployment,
    V1DeploymentSpec,
    V1LabelSelector,
    V1LabelSelectorRequirement,
    V1ObjectMeta,
    V1Pod,
    V1PodTemplateSpec,
)
from urllib3.exceptions import MaxRetryError

from kube_ops_mcp import k8s
from tests.factories import NOW, RUNNING, container, make_event, make_pod


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


def deployment_with(selector: V1LabelSelector) -> V1Deployment:
    return V1Deployment(spec=V1DeploymentSpec(selector=selector, template=V1PodTemplateSpec()))


def test_selector_includes_match_expressions() -> None:
    selector = V1LabelSelector(
        match_labels={"app": "web"},
        match_expressions=[
            V1LabelSelectorRequirement(key="tier", operator="In", values=["a", "b"]),
            V1LabelSelectorRequirement(key="canary", operator="DoesNotExist"),
        ],
    )

    assert k8s.deployment_selector(deployment_with(selector)) == "app=web,tier in (a,b),!canary"


def test_empty_selector_is_refused_instead_of_matching_every_pod() -> None:
    with pytest.raises(k8s.KubernetesError, match="empty"):
        k8s.deployment_selector(deployment_with(V1LabelSelector()))


def test_read_previous_logs_of_one_container(api: MagicMock) -> None:
    api.read_namespaced_pod_log.return_value = "line 1\nline 2"

    text = k8s.read_logs("demo", "web-1", "app", tail_lines=20, previous=True, timeout=3)

    assert text == "line 1\nline 2"
    api.read_namespaced_pod_log.assert_called_once_with(
        "web-1",
        "demo",
        tail_lines=20,
        limit_bytes=64 * 1024,
        container="app",
        previous=True,
        _request_timeout=3,
    )


def test_only_warning_events_are_requested(api: MagicMock) -> None:
    api.list_namespaced_event.return_value.items = []

    k8s.list_warning_events("demo", timeout=3)

    api.list_namespaced_event.assert_called_once_with(
        "demo", field_selector="type=Warning", _request_timeout=3
    )


def test_bad_request_includes_the_api_reason(api: MagicMock) -> None:
    api.list_namespaced_pod.side_effect = ApiException(status=400, reason="Bad Request")
    api.list_namespaced_pod.side_effect.body = "unable to parse requirement: bad selector"

    with pytest.raises(k8s.KubernetesError, match="bad selector"):
        k8s.list_pods("demo", "app==", timeout=3)


def test_missing_credentials_are_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_config(**kwargs: object) -> None:
        raise k8s.config.ConfigException("none")

    monkeypatch.setattr(k8s.config, "load_incluster_config", no_config)
    monkeypatch.setattr(k8s.config, "load_kube_config", no_config)
    k8s._load_config.cache_clear()
    try:
        with pytest.raises(k8s.KubernetesError, match="No Kubernetes credentials"):
            k8s._load_config()
    finally:
        k8s._load_config.cache_clear()


@pytest.fixture
def apps(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock = MagicMock()
    monkeypatch.setattr(k8s, "apps_v1", lambda: mock)
    return mock


def test_restart_patches_the_restarted_at_annotation(apps: MagicMock) -> None:
    k8s.restart_deployment("demo", "web", timeout=3, now=NOW)

    name, namespace, body = apps.patch_namespaced_deployment.call_args.args
    annotations = body["spec"]["template"]["metadata"]["annotations"]
    assert (name, namespace) == ("web", "demo")
    assert annotations == {k8s.RESTARTED_AT_ANNOTATION: NOW.isoformat()}


def test_scale_sets_replicas_and_returns_previous_count(apps: MagicMock) -> None:
    apps.read_namespaced_deployment_scale.return_value.spec.replicas = 2

    previous = k8s.scale_deployment("demo", "web", 4, timeout=3)

    assert previous == 2
    apps.patch_namespaced_deployment_scale.assert_called_once_with(
        "web", "demo", {"spec": {"replicas": 4}}, _request_timeout=3
    )


def test_cluster_summary(api: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    ready = MagicMock(type="Ready", status="True")
    not_ready = MagicMock(type="Ready", status="False")
    api.list_node.return_value.items = [
        MagicMock(status=MagicMock(conditions=[ready])),
        MagicMock(status=MagicMock(conditions=[not_ready])),
    ]
    namespaces = [MagicMock(), MagicMock()]
    namespaces[0].metadata.name, namespaces[1].metadata.name = "kube-system", "demo"
    api.list_namespace.return_value.items = namespaces
    version = MagicMock()
    version.get_code.return_value.git_version = "v1.37.0"
    monkeypatch.setattr(k8s, "version_api", lambda: version)
    monkeypatch.setattr(k8s, "cluster_name", lambda: "kind-demo")

    assert k8s.cluster_summary(timeout=3) == {
        "cluster": "kind-demo",
        "kubernetes_version": "v1.37.0",
        "nodes": {"total": 2, "ready": 1},
        "namespaces": ["demo", "kube-system"],
    }


def test_pinned_context_is_used_and_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def not_in_cluster() -> None:
        raise k8s.config.ConfigException("not in a pod")

    loaded: dict[str, object] = {}
    monkeypatch.setattr(k8s.config, "load_incluster_config", not_in_cluster)
    monkeypatch.setattr(k8s.config, "load_kube_config", lambda **kw: loaded.update(kw))
    monkeypatch.setattr(
        k8s.config, "list_kube_config_contexts", lambda: ([], {"name": "kind-demo"})
    )
    try:
        k8s.use_context("staging")
        assert k8s.cluster_name() == "staging"
        assert loaded == {"context": "staging"}

        k8s.use_context(None)
        assert k8s.cluster_name() == "kind-demo"
    finally:
        k8s.use_context(None)


def test_in_cluster_credentials_win(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(k8s.config, "load_incluster_config", lambda: None)
    try:
        k8s.use_context("ignored")
        assert k8s.cluster_name() == "in-cluster"
    finally:
        k8s.use_context(None)


def test_display_name_overrides_the_detected_cluster(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(k8s.config, "load_incluster_config", lambda: None)
    try:
        k8s.use_context(None, "prod-eu")
        assert k8s.cluster_name() == "prod-eu"
    finally:
        k8s.use_context(None)


def test_limit_pods_puts_unhealthy_first_and_shortens_healthy() -> None:
    summaries = [
        {
            "name": "bad",
            "healthy": False,
            "status": "Error",
            "ready": "0/1",
            "restarts": 3,
            "age": "1h",
            "node": "n",
            "containers": [],
        },
        *[
            {
                "name": f"ok-{i}",
                "healthy": True,
                "status": "Running",
                "ready": "1/1",
                "restarts": 0,
                "age": "1h",
                "node": "n",
                "containers": [],
            }
            for i in range(10)
        ],
    ]

    shown, omitted = k8s.limit_pods(summaries, max_pods=4, max_unhealthy=2)

    assert [p["name"] for p in shown] == ["bad", "ok-0", "ok-1", "ok-2"]
    assert "containers" in shown[0] and "containers" not in shown[1]
    assert omitted == 7


def test_pod_without_status_yet_does_not_crash() -> None:
    pod = V1Pod(metadata=V1ObjectMeta(name="just-created"))

    summary = k8s.summarize_pod(pod, NOW)

    assert summary["status"] == "Unknown"
    assert summary["healthy"] is False
    assert summary["ready"] == "0/0"
