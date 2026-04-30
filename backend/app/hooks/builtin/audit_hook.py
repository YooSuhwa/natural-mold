"""Audit hook — writes ``credential_audit_log`` rows for credentialed calls.

Only fires when the in-flight call has a ``credential_id`` so non-credentialed
helpers (DuckDuckGo, web scraper, current_datetime) don't pollute the audit
table. Each event records the hook kind plus minimal metadata
(duration / token counts / error) so the audit trail stays small.
"""

from __future__ import annotations

import logging

from app.database import async_session
from app.hooks.base import CustomHook, HookContext, HookResult

logger = logging.getLogger(__name__)


class AuditHook(CustomHook):
    name = "audit_hook"
    enabled = True

    # ``invoke`` is the canonical action recorded against the credential when a
    # runtime call uses its data. The pre/post split is captured in
    # ``log_metadata.phase``.
    _ACTION = "invoke"

    async def _write(
        self,
        ctx: HookContext,
        *,
        phase: str,
        result: HookResult | None = None,
        error: Exception | None = None,
    ) -> None:
        if ctx.credential_id is None:
            return
        # Lazy import to avoid pulling SA deps into hooks/__init__.
        from app.credentials import service as credential_service

        metadata: dict[str, object] = {
            "phase": phase,
            "kind": ctx.kind,
            "request_id": ctx.request_id,
        }
        if ctx.agent_id is not None:
            metadata["agent_id"] = str(ctx.agent_id)
        if ctx.tool_id is not None:
            metadata["tool_id"] = str(ctx.tool_id)
        if ctx.mcp_server_id is not None:
            metadata["mcp_server_id"] = str(ctx.mcp_server_id)
        if ctx.model_id is not None:
            metadata["model_id"] = str(ctx.model_id)
        if result is not None:
            metadata["duration_ms"] = result.duration_ms
            if result.tokens_in is not None:
                metadata["tokens_in"] = result.tokens_in
            if result.tokens_out is not None:
                metadata["tokens_out"] = result.tokens_out
            if result.cost_usd is not None:
                metadata["cost_usd"] = result.cost_usd
        if ctx.metadata:
            # Merge but don't let user metadata clobber control fields.
            for key, value in ctx.metadata.items():
                metadata.setdefault(f"meta_{key}", value)

        try:
            async with async_session() as db:
                await credential_service.write_audit_log(
                    db,
                    credential_id=ctx.credential_id,
                    actor_user_id=ctx.user_id,
                    action=self._ACTION,
                    source="runtime",
                    error=str(error) if error else None,
                    metadata=metadata,
                )
                await db.commit()
        except Exception:  # noqa: BLE001 — failure isolation
            logger.warning(
                "audit hook write failed (kind=%s phase=%s)", ctx.kind, phase, exc_info=True
            )

    async def async_pre_call_hook(self, ctx: HookContext) -> None:
        # Skip the pre row to keep the audit table small — post/failure carry
        # the lifecycle. Tests rely on this behaviour to count rows.
        return None

    async def async_post_call_hook(self, ctx: HookContext, result: HookResult) -> None:
        await self._write(ctx, phase="post", result=result)

    async def async_failure_hook(self, ctx: HookContext, error: Exception) -> None:
        await self._write(ctx, phase="failure", error=error)


__all__ = ["AuditHook"]
