"""Browser preflight for the request headers the frontend sends (app.main CORS)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.config import settings


@pytest.mark.parametrize("header", ["if-match", "x-csrf-token", "content-type"])
async def test_a_cross_origin_write_with_the_frontend_headers_is_allowed(
    client: AsyncClient, header: str
) -> None:
    origin = settings.cors_origins_list[0]

    response = await client.options(
        "/api/tenants/00000000-0000-0000-0000-000000000000/guardrails",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": header,
        },
    )

    assert response.status_code == 200, response.text
    assert response.headers["access-control-allow-origin"] == origin
    allowed = response.headers["access-control-allow-headers"].lower()
    assert header in allowed


_AG_UI_RUNS = "/api/conversations/00000000-0000-0000-0000-000000000001/ag-ui/runs"


@pytest.mark.parametrize(
    ("path", "method", "request_headers"),
    [
        (_AG_UI_RUNS, "POST", {"content-type", "x-csrf-token"}),
        (f"{_AG_UI_RUNS}/client-id/stream", "GET", {"last-event-id"}),
    ],
)
async def test_ag_ui_cross_origin_submit_and_reconnect_preflight(
    client: AsyncClient, path: str, method: str, request_headers: set[str]
) -> None:
    origin = settings.cors_origins_list[0]
    response = await client.options(
        path,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": ", ".join(sorted(request_headers)),
        },
    )

    assert response.status_code == 200, response.text
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"
    allowed = {
        key.strip().lower() for key in response.headers["access-control-allow-headers"].split(",")
    }
    assert request_headers <= allowed


async def test_ag_ui_exposes_run_and_queue_receipts_to_the_app_origin(
    raw_client: AsyncClient,
) -> None:
    origin = settings.cors_origins_list[0]
    response = await raw_client.post(_AG_UI_RUNS, headers={"Origin": origin}, json={})

    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"
    exposed = {
        name.strip().lower()
        for name in response.headers["access-control-expose-headers"].split(",")
    }
    assert {
        "x-run-id",
        "x-moldy-run-id",
        "x-moldy-message-id",
        "x-moldy-input-id",
        "x-moldy-input-status",
        "x-moldy-input-revision",
        "x-moldy-input-position",
        "x-stream-protocol",
    } <= exposed
