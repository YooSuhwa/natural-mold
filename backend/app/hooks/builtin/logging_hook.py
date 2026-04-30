"""Structured logging hook — emits an INFO line per pre/post/failure event.

Useful as a baseline observability tool: every cross-cutting call shows up in
``app.hooks.builtin.logging_hook`` log output with the key fields. Production
deployments can swap this out for a dedicated tracer (OpenTelemetry, etc.).
"""

from __future__ import annotations

import logging

from app.hooks.base import CustomHook, HookContext, HookResult

logger = logging.getLogger(__name__)


class LoggingHook(CustomHook):
    name = "logging_hook"
    enabled = True

    async def async_pre_call_hook(self, ctx: HookContext) -> None:
        logger.info(
            "hook.pre kind=%s req=%s user=%s agent=%s tool=%s mcp=%s model=%s cred=%s",
            ctx.kind,
            ctx.request_id,
            ctx.user_id,
            ctx.agent_id,
            ctx.tool_id,
            ctx.mcp_server_id,
            ctx.model_id,
            ctx.credential_id,
        )

    async def async_post_call_hook(self, ctx: HookContext, result: HookResult) -> None:
        logger.info(
            "hook.post kind=%s req=%s duration_ms=%s tokens_in=%s tokens_out=%s cost=%s",
            ctx.kind,
            ctx.request_id,
            result.duration_ms,
            result.tokens_in,
            result.tokens_out,
            result.cost_usd,
        )

    async def async_failure_hook(self, ctx: HookContext, error: Exception) -> None:
        logger.warning(
            "hook.failure kind=%s req=%s error=%r",
            ctx.kind,
            ctx.request_id,
            error,
        )


__all__ = ["LoggingHook"]
