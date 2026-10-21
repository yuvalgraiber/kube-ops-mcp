"""Prometheus HTTP API access: instant and range PromQL queries."""

import math
import time
from typing import Any

import httpx

MAX_QUERY_CHARS = 2000
MAX_RANGE_MINUTES = 24 * 60
MAX_SERIES = 50
# Aim for about this many points per series so range results stay small.
_TARGET_POINTS = 120
_MIN_STEP_SECONDS = 15


class PrometheusError(Exception):
    """An anticipated Prometheus failure with a message that is safe to show the model."""


def validate_query(promql: str, range_minutes: int | None) -> None:
    if not promql.strip():
        raise ValueError("The PromQL query is empty.")
    if len(promql) > MAX_QUERY_CHARS:
        raise ValueError(f"The PromQL query is longer than {MAX_QUERY_CHARS} characters.")
    if range_minutes is not None and not 1 <= range_minutes <= MAX_RANGE_MINUTES:
        raise ValueError(f"range_minutes must be between 1 and {MAX_RANGE_MINUTES}.")


def step_for(range_minutes: int) -> int:
    """Pick a step that keeps a range query near _TARGET_POINTS points per series."""
    return max(_MIN_STEP_SECONDS, math.ceil(range_minutes * 60 / _TARGET_POINTS))


async def query(
    base_url: str,
    promql: str,
    *,
    range_minutes: int | None = None,
    timeout: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Run an instant query, or a range query over the last `range_minutes` minutes."""
    validate_query(promql, range_minutes)
    if range_minutes is None:
        path, params = "/api/v1/query", {"query": promql}
    else:
        end = time.time()
        step = step_for(range_minutes)
        path = "/api/v1/query_range"
        params = {"query": promql, "start": end - range_minutes * 60, "end": end, "step": step}

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport) as http:
        try:
            response = await http.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise PrometheusError(
                f"Prometheus at {base_url} did not respond within {timeout:g}s."
            ) from exc
        except httpx.TransportError as exc:
            raise PrometheusError(
                f"Could not connect to Prometheus at {base_url}. Is it running and is "
                "PROMETHEUS_URL correct? For the local demo run `make port-forward`."
            ) from exc

    return _parse(response, promql)


def _parse(response: httpx.Response, promql: str) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        raise PrometheusError(
            f"Prometheus returned HTTP {response.status_code} with a non-JSON body."
        ) from None

    if body.get("status") != "success":
        error = body.get("error", "unknown error")
        if body.get("errorType") == "bad_data":
            raise PrometheusError(f"Invalid PromQL: {error}")
        raise PrometheusError(f"Prometheus query failed (HTTP {response.status_code}): {error}")

    data = body["data"]
    series = data["result"]
    # vector and matrix results are lists of series; scalar and string are one [time, value].
    is_series = data["resultType"] in ("vector", "matrix")
    result: dict[str, Any] = {
        "query": promql,
        "result_type": data["resultType"],
        "series_count": len(series) if is_series else 1,
        "result": series[:MAX_SERIES] if is_series else series,
    }
    if is_series and len(series) > MAX_SERIES:
        result["truncated"] = f"Showing {MAX_SERIES} of {len(series)} series; narrow the query."
    if result["series_count"] == 0:
        result["note"] = (
            "No series matched. Check label names and values; a container that crashes "
            "within seconds may never be scraped (default scrape interval is 30s)."
        )
    if body.get("warnings"):
        result["warnings"] = body["warnings"]
    return result
