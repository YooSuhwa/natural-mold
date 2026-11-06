"""The backend's signed call reaches a real MCP runtime and comes back verified (plan §7.3).

Skipped unless ``MCP_RUNTIME_TEST_URL`` and ``MCP_RUNTIME_TEST_SECRET`` are
set to a running runtime (``docker compose up mcp-runtime`` or
``python -m moldy_mcp_runtime`` from mcp-runtime/).
"""

from __future__ import annotations

import os

import pytest

from app.config import settings
from app.mcp_runtime import probe

pytestmark = [
    pytest.mark.skipif(not os.environ.get("MCP_RUNTIME_TEST_URL"), reason="needs a runtime"),
]


@pytest.fixture(autouse=True)
def _runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mcp_runtime_url", os.environ["MCP_RUNTIME_TEST_URL"])
    monkeypatch.setattr(settings, "mcp_runtime_token_secret", os.environ["MCP_RUNTIME_TEST_SECRET"])


async def test_the_signed_call_is_verified() -> None:
    health = await probe.check_runtime()

    assert (health.configured, health.ok) == (True, True), health.detail
    assert health.detail == "signed call verified"


async def test_a_wrong_secret_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mcp_runtime_token_secret", "w" * 40)

    health = await probe.check_runtime()

    assert health.configured and not health.ok
    assert "signed call failed" in health.detail
