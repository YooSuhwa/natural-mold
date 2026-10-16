"""Bounded MCP preparation before the materialization marker (parent Task 10, I13).

Called by prepared-run materialization while the canonical gateway locks and
transaction are held. Exactly one uncached discovery per selected server runs
under a per-server timeout plus one total AnyIO deadline; every failure,
timeout, cancellation or transient-session cleanup error aborts with a typed
``mcp_preparation_failed`` (no stub tools). Transient discovery sessions are
closed before this returns. The retained tools keep only their connection
config: each later call opens a fresh session under a dispatch permit and is
bounded by ``call_timeout`` (independent of the discovery budget). No
discovery may happen after the marker.
"""

from __future__ import annotations

import anyio

from app.agent_runtime.mcp_strict_loader import (
    DiscoverTools,
    McpStrictLoadError,
    default_discover,
    load_selected_mcp_tools_strict,
)
from app.agent_runtime.prepared_mcp_resources import PreparedMcpResources, freeze_tool

PREPARATION_FAILED = "mcp_preparation_failed"


class McpPreparationError(RuntimeError):
    code = PREPARATION_FAILED

    def __init__(self, reason: str) -> None:
        super().__init__(f"{PREPARATION_FAILED}:{reason}")
        self.reason = reason


async def prepare_mcp_tools(
    mcp_configs: list[dict],
    *,
    per_server_timeout: float,
    total_timeout: float,
    call_timeout: float | None = None,
    discover: DiscoverTools = default_discover,
) -> PreparedMcpResources:
    if not mcp_configs:
        return PreparedMcpResources(tools=(), discovery_count=0)
    count = 0

    async def bounded(key: str, config: dict, interceptors: list) -> list:
        nonlocal count
        count += 1
        with anyio.fail_after(per_server_timeout):
            return await discover(key, config, interceptors)

    try:
        with anyio.fail_after(total_timeout):
            loaded = await load_selected_mcp_tools_strict(
                mcp_configs,
                discover=bounded,
                call_timeout_seconds=call_timeout or per_server_timeout,
            )
        tools = tuple(freeze_tool(item) for item in loaded)
    except TimeoutError as error:
        raise McpPreparationError("timeout") from error
    except McpStrictLoadError as error:
        cause = error.__cause__
        reason = "timeout" if isinstance(cause, TimeoutError) else error.reason
        raise McpPreparationError(reason) from error
    except Exception as error:  # noqa: BLE001 - any other failure is a typed abort
        raise McpPreparationError(f"internal:{type(error).__name__}") from error
    return PreparedMcpResources(tools=tools, discovery_count=count)
