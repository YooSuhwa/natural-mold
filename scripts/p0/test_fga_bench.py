# ruff: noqa: S101 - assertions are the pytest contract in this test module
"""Focused boundary regressions for the artificial OpenFGA benchmark."""

from __future__ import annotations

import hashlib
import json

import anyio
import httpx
import pytest
from fga_bench import bench, tuples
from pydantic import ValidationError


def test_check_fails_when_allowed_is_missing() -> None:
    # Given: an HTTP 200 Check response without an authorization decision.
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    async def scenario() -> None:
        async with httpx.AsyncClient(
            base_url="http://fga.test", transport=httpx.MockTransport(respond)
        ) as client:
            # When / Then: the benchmark must reject the missing field.
            with pytest.raises(ValidationError):
                await bench(client, "store", "model", [("other-user", "agent")])

    anyio.run(scenario)


def test_check_fails_when_http_status_is_not_200() -> None:
    # Given: a server error, which cannot become an authorization denial.
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"allowed": False})

    async def scenario() -> None:
        async with httpx.AsyncClient(
            base_url="http://fga.test", transport=httpx.MockTransport(respond)
        ) as client:
            # When / Then: reject the HTTP failure at the first Check.
            with pytest.raises(httpx.HTTPStatusError):
                await bench(client, "store", "model", [("other-user", "agent")])

    anyio.run(scenario)


@pytest.mark.parametrize(
    ("current", "count", "digest"),
    [
        (False, 131309, "71e580d47d21fa7e999a52c3063bc314d9de23e5106330b7c94cbb96faac8047"),
        (True, 151309, "3ccd9c9fc6e89f567b4e2051a0623a50dbadbf667fce22f1f0eecda519437eda"),
    ],
)
def test_tuple_workload_matches_supplied_input(current: bool, count: int, digest: str) -> None:
    # Given: the fixed supplied artificial workload.
    # When: generating either model's tuple sequence.
    generated, bad = tuples(current)
    # Then: preserve count, ordering, seeded assignments, and misgrants exactly.
    assert len(generated) == count
    assert hashlib.sha256(json.dumps(generated, sort_keys=True).encode()).hexdigest() == digest
    assert len(bad) == 100
    assert hashlib.sha256(json.dumps(bad).encode()).hexdigest() == (
        "df4c73361509d85aed45bd99acec35f58f96b85e67f778fb7b9c742d0f88bffb"
    )


@pytest.mark.parametrize(
    "body",
    [
        "{}",
        '{"allowed":"false"}',
        '{"allowed":0}',
        '{"allowed":false,"error":{"code":"internal_error"}}',
    ],
)
def test_check_decision_requires_a_boolean(body: str) -> None:
    from fga_bench_types import CheckReply

    # Given: malformed decision payloads.
    # When / Then: parsing must reject missing and coerced authorization decisions.
    with pytest.raises(ValidationError):
        CheckReply.model_validate_json(body)


@pytest.mark.parametrize(
    "body",
    [
        "{}",
        '{"result":{"0":{}}}',
        '{"result":{"0":{"allowed":false,"error":{"code":"internal_error"}}}}',
    ],
)
def test_batch_fails_when_a_decision_is_missing(body: str) -> None:
    from fga_bench_client import batch_check

    # Given: an incomplete BatchCheck success response.
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    async def scenario() -> None:
        async with httpx.AsyncClient(
            base_url="http://fga.test", transport=httpx.MockTransport(respond)
        ) as client:
            # When / Then: every requested decision is mandatory.
            with pytest.raises(ValidationError):
                await batch_check(client, "store", "model", "user:owner", ["agent"])

    anyio.run(scenario)


def test_batch_fails_when_correlations_do_not_match() -> None:
    from fga_bench_client import batch_check

    # Given: a valid decision belongs to a different request.
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"wrong": {"allowed": False}}})

    async def scenario() -> None:
        async with httpx.AsyncClient(
            base_url="http://fga.test", transport=httpx.MockTransport(respond)
        ) as client:
            # When / Then: fail rather than count the incomplete BatchCheck as success.
            with pytest.raises(httpx.DecodingError):
                await batch_check(client, "store", "model", "user:owner", ["agent"])

    anyio.run(scenario)


@pytest.mark.parametrize("status", [201, 503])
def test_list_fails_when_http_status_is_not_200(status: int) -> None:
    from fga_bench_client import list_objects

    # Given: a non-200 response that contains an otherwise valid objects field.
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"objects": []})

    async def scenario() -> None:
        async with httpx.AsyncClient(
            base_url="http://fga.test", transport=httpx.MockTransport(respond)
        ) as client:
            # When / Then: reject the failed ListObjects measurement.
            with pytest.raises(httpx.HTTPStatusError):
                await list_objects(client, "store", "model", "user:owner", "can_run")

    anyio.run(scenario)


@pytest.mark.parametrize(
    ("cross_allowed", "check_p95", "batch_p95", "expected_failures"),
    [(0, 20.0, 60.0, 0), (1, 1.0, 1.0, 1), (0, 20.001, 1.0, 3), (0, 1.0, 60.001, 1)],
)
def test_current_gate_enforces_unrounded_security_and_latency_limits(
    cross_allowed: int,
    check_p95: float,
    batch_p95: float,
    expected_failures: int,
) -> None:
    from fga_bench_types import (
        AllowedSummary,
        BenchResult,
        ObjectsSummary,
        acceptance_failures,
        summ,
    )

    # Given: typed measurements at or just above the contract's exact boundaries.
    checks = summ([check_p95])
    objects = ObjectsSummary(
        **checks.model_dump(), p95_raw=checks.p95_raw, avg_results=0.0, max_results=0
    )
    result = BenchResult(
        check_user_can_run=AllowedSummary(**checks.model_dump(), p95_raw=checks.p95_raw, allowed=0),
        check_agent_can_use_credential=checks,
        cross_tenant_misgrant_allowed=f"{cross_allowed}/100",
        cross_tenant_allowed_count=cross_allowed,
        batchcheck_50=summ([batch_p95]),
        listobjects_can_run=objects,
        listobjects_can_edit=objects,
        check_concurrency8=checks,
    )
    # When: applying CURRENT acceptance.
    failures = acceptance_failures(result)
    # Then: security and unrounded latency failures determine a nonzero CLI result.
    assert len(failures) == expected_failures
