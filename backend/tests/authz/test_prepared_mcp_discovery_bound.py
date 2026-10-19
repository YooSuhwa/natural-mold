"""Selected-MCP discovery under the materialization locks has a small configured bound.

Discovery runs inside the canonical materialization transaction, so archive,
suspension and stop wait at most ``prepared_mcp_discovery_total_timeout_seconds``.
The post-marker MCP call timeout is a separate seam and keeps its prior value.
"""

from __future__ import annotations

import pytest

from app.agent_runtime.prepared_run_inputs import MaterializerSeams
from app.config import settings

MAX_LOCKED_DISCOVERY_SECONDS = 5.0


def test_default_discovery_bound_is_small_and_configured() -> None:
    seams = MaterializerSeams()

    assert seams.mcp_total_timeout == settings.prepared_mcp_discovery_total_timeout_seconds
    assert seams.mcp_per_server_timeout == settings.prepared_mcp_discovery_server_timeout_seconds
    assert 0 < seams.mcp_per_server_timeout <= seams.mcp_total_timeout
    assert seams.mcp_total_timeout <= MAX_LOCKED_DISCOVERY_SECONDS
    assert seams.mcp_call_timeout == 10.0  # post-marker call bound unchanged


def test_discovery_bound_follows_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "prepared_mcp_discovery_total_timeout_seconds", 1.5)
    monkeypatch.setattr(settings, "prepared_mcp_discovery_server_timeout_seconds", 1.0)

    seams = MaterializerSeams()

    assert (seams.mcp_per_server_timeout, seams.mcp_total_timeout) == (1.0, 1.5)
