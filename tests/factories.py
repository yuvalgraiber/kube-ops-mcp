"""Builders for Kubernetes API objects used across the tests."""

from datetime import UTC, datetime, timedelta

from kubernetes.client import (
    CoreV1Event,
    V1Container,
    V1ContainerState,
    V1ContainerStateRunning,
    V1ContainerStateTerminated,
    V1ContainerStatus,
    V1Deployment,
    V1DeploymentSpec,
    V1DeploymentStatus,
    V1LabelSelector,
    V1ObjectMeta,
    V1ObjectReference,
    V1Pod,
    V1PodSpec,
    V1PodStatus,
    V1PodTemplateSpec,
)

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
RUNNING = V1ContainerState(running=V1ContainerStateRunning())


def make_pod(
    name: str,
    phase: str,
    statuses: list[V1ContainerStatus],
    *,
    init_statuses: list[V1ContainerStatus] | None = None,
    reason: str | None = None,
    created: datetime = NOW - timedelta(minutes=5),
) -> V1Pod:
    return V1Pod(
        metadata=V1ObjectMeta(name=name, creation_timestamp=created),
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


def make_event(reason: str, name: str, seen: datetime, kind: str = "Pod") -> CoreV1Event:
    return CoreV1Event(
        metadata=V1ObjectMeta(name=f"{name}.1"),
        involved_object=V1ObjectReference(kind=kind, name=name),
        reason=reason,
        message=f"{reason} happened",
        last_timestamp=seen,
        count=2,
    )


def make_deployment(name: str, replicas: int = 1, ready: int = 0) -> V1Deployment:
    return V1Deployment(
        metadata=V1ObjectMeta(name=name),
        spec=V1DeploymentSpec(
            replicas=replicas,
            selector=V1LabelSelector(match_labels={"app": name}),
            template=V1PodTemplateSpec(),
        ),
        status=V1DeploymentStatus(ready_replicas=ready),
    )
