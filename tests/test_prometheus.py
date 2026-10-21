import json

import httpx
import pytest

from kube_ops_mcp import prometheus

URL = "http://prometheus.test"

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def transport_returning(status: int, body: dict, seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, content=json.dumps(body))

    return httpx.MockTransport(handler)


def success(result: list, result_type: str = "vector") -> dict:
    return {"status": "success", "data": {"resultType": result_type, "result": result}}


async def test_instant_query() -> None:
    seen: list[httpx.Request] = []
    series = [{"metric": {"pod": "web"}, "value": [1, "42"]}]

    result = await prometheus.query(
        URL, "up", timeout=5, transport=transport_returning(200, success(series), seen)
    )

    assert seen[0].url.path == "/api/v1/query"
    assert seen[0].url.params["query"] == "up"
    assert result["result_type"] == "vector"
    assert result["result"] == series


async def test_range_query_sets_bounded_step() -> None:
    seen: list[httpx.Request] = []

    await prometheus.query(
        URL,
        "up",
        range_minutes=60,
        timeout=5,
        transport=transport_returning(200, success([], "matrix"), seen),
    )

    params = seen[0].url.params
    assert seen[0].url.path == "/api/v1/query_range"
    assert params["step"] == "30"
    assert float(params["end"]) - float(params["start"]) == pytest.approx(3600)


async def test_many_series_are_truncated() -> None:
    series = [{"metric": {"i": str(i)}, "value": [1, "1"]} for i in range(80)]

    result = await prometheus.query(
        URL, "up", timeout=5, transport=transport_returning(200, success(series), [])
    )

    assert len(result["result"]) == prometheus.MAX_SERIES
    assert result["series_count"] == 80
    assert "truncated" in result


async def test_bad_promql_is_reported_clearly() -> None:
    body = {"status": "error", "errorType": "bad_data", "error": "parse error at char 4"}

    with pytest.raises(prometheus.PrometheusError, match="Invalid PromQL: parse error"):
        await prometheus.query(URL, "up{", timeout=5, transport=transport_returning(400, body, []))


async def test_connection_failure_is_actionable() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(prometheus.PrometheusError, match="PROMETHEUS_URL"):
        await prometheus.query(URL, "up", timeout=5, transport=httpx.MockTransport(refuse))


async def test_timeout_is_reported() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    with pytest.raises(prometheus.PrometheusError, match="did not respond within 5s"):
        await prometheus.query(URL, "up", timeout=5, transport=httpx.MockTransport(slow))


@pytest.mark.parametrize(
    ("promql", "range_minutes", "message"),
    [
        ("  ", None, "empty"),
        ("x" * (prometheus.MAX_QUERY_CHARS + 1), None, "longer than"),
        ("up", 0, "range_minutes"),
        ("up", prometheus.MAX_RANGE_MINUTES + 1, "range_minutes"),
    ],
)
def test_invalid_input_is_rejected(promql: str, range_minutes: int | None, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        prometheus.validate_query(promql, range_minutes)


async def test_scalar_result_counts_as_one_value() -> None:
    body = {"status": "success", "data": {"resultType": "scalar", "result": [1, "42"]}}

    result = await prometheus.query(
        URL, "scalar(up)", timeout=5, transport=transport_returning(200, body, [])
    )

    assert result["series_count"] == 1
    assert result["result"] == [1, "42"]
    assert "note" not in result
