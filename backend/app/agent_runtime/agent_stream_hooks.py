"""HookContext/HookResult construction for agent stream and invoke runs."""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime
from typing import Any

from app.agent_runtime.runtime_config import AgentConfig
from app.hooks import HookContext, HookResult


def _hook_ctx_for_agent(cfg: AgentConfig) -> HookContext | None:
    """Build a ``HookContext`` for an ``agent_invoke`` call.

    Returns ``None`` when the caller didn't propagate a ``user_id`` (legacy
    tests that build ``AgentConfig`` directly). Hook dispatch is a no-op in
    that case so the runtime stays backward compatible.

    Correlation IDs (``agent_id`` / ``model_id`` / ``llm_credential_id``)
    are best-effort: a malformed UUID drops the field instead of crashing
    the request — these are trace metadata, not access-control gates.
    The user_id check above is the security boundary.
    """

    if not cfg.user_id:
        return None
    try:
        user_uuid = _uuid.UUID(str(cfg.user_id))
    except (TypeError, ValueError):
        return None

    def _opt(value: str | None) -> _uuid.UUID | None:
        if not value:
            return None
        try:
            return _uuid.UUID(str(value))
        except (TypeError, ValueError):
            return None

    metadata: dict[str, Any] = {
        "provider": cfg.provider,
        "model_name": cfg.model_name,
        "thread_id": cfg.thread_id,
        "identity_mode": cfg.identity_mode,
        "credential_subject_user_id": cfg.credential_subject_user_id,
        "agent_runtime_name": cfg.agent_runtime_name,
    }
    return HookContext(
        request_id=str(_uuid.uuid4()),
        kind="agent_invoke",
        user_id=user_uuid,
        started_at=datetime.now(UTC).replace(tzinfo=None),
        agent_id=_opt(cfg.agent_id),
        model_id=_opt(cfg.model_id),
        credential_id=_opt(cfg.llm_credential_id),
        metadata=metadata,
        tenant_id=cfg.org_context.tenant_id,
        org_id=cfg.org_context.org_id,
        billing_user_id=cfg.org_context.user_id,
    )


def _hook_result_from_usage(duration_ms: int, usage_sink: dict[str, Any]) -> HookResult:
    """Build a :class:`HookResult` from streaming-captured usage.

    Streaming surfaces ``prompt_tokens`` / ``completion_tokens`` /
    ``estimated_cost`` keys; the hook framework maps them to its own
    ``tokens_in`` / ``tokens_out`` / ``cost_usd`` field names.
    """

    prompt = usage_sink.get("prompt_tokens")
    completion = usage_sink.get("completion_tokens")
    cost = usage_sink.get("estimated_cost")
    return HookResult(
        duration_ms=duration_ms,
        tokens_in=int(prompt) if prompt is not None else None,
        tokens_out=int(completion) if completion is not None else None,
        cost_usd=float(cost) if cost is not None else None,
    )
