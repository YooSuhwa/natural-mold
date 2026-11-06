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
