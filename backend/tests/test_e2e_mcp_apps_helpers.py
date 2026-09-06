from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.routers.e2e_mcp_apps_helpers import _require_loopback_fixture


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8765/mcp",
        "http://example.test:8765/mcp",
        "http://user:pass@127.0.0.1:8765/mcp",
        "http://127.0.0.1:8765/other",
        "http://127.0.0.1:8765/mcp?target=other",
        "http://127.0.0.1:not-a-port/mcp",
    ],
)
def test_e2e_mcp_apps_fixture_rejects_non_loopback_or_ambiguous_urls(url: str) -> None:
    with pytest.raises(HTTPException) as rejected:
        _require_loopback_fixture(url)
    assert rejected.value.status_code == 422


def test_e2e_mcp_apps_fixture_accepts_explicit_loopback_mcp_url() -> None:
    _require_loopback_fixture("http://127.0.0.1:8765/mcp")
