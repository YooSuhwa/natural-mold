"""Langfuse trace tags/metadata for a run (split from ``langfuse``; re-exported there).

I-08 (plan 8.9): every trace carries the run's own company and organization
(``moldy_tenant_id``/``moldy_org_id`` and a ``tenant:<id>`` tag) so the shared
platform project can be filtered per company; all identifiers come from the run's
config, i.e. from that one company.
"""

from __future__ import annotations

from typing import Any


def _run_scope(cfg: Any) -> tuple[str | None, str | None]:
    context = getattr(cfg, "org_context", None)
    tenant_id = getattr(context, "tenant_id", None)
    org_id = getattr(context, "org_id", None)
    return (str(tenant_id) if tenant_id else None, str(org_id) if org_id else None)


def _tags_for_run(cfg: Any, *, source: str) -> list[str]:
    tags = ["moldy", "agent-chat", f"source:{source}"]
    tenant_id, _org_id = _run_scope(cfg)
    if tenant_id:
        tags.append(f"tenant:{tenant_id}")
    agent_id = getattr(cfg, "agent_id", None)
    if agent_id:
        tags.append(f"agent:{agent_id}")
    return tags


def _metadata_for_run(cfg: Any, *, run_id: str, source: str) -> dict[str, Any]:
    user_id = getattr(cfg, "user_id", None)
    conversation_id = getattr(cfg, "thread_id", None)
    agent_id = getattr(cfg, "agent_id", None)
    agent_name = getattr(cfg, "agent_name", None)
    model_id = getattr(cfg, "model_id", None)
    checkpoint_id = getattr(cfg, "checkpoint_id", None)
    tenant_id, org_id = _run_scope(cfg)
    return {
        "langfuse_user_id": str(user_id) if user_id else None,
        "langfuse_session_id": str(conversation_id) if conversation_id else None,
        "langfuse_tags": _tags_for_run(cfg, source=source),
        "moldy_tenant_id": tenant_id,
        "moldy_org_id": org_id,
        "moldy_user_id": str(user_id) if user_id else None,
        "moldy_agent_id": str(agent_id) if agent_id else None,
        "moldy_agent_name": str(agent_name) if agent_name else None,
        "moldy_conversation_id": str(conversation_id) if conversation_id else None,
        "moldy_run_id": run_id,
        "moldy_model_id": str(model_id) if model_id else None,
        "moldy_checkpoint_id": str(checkpoint_id) if checkpoint_id else None,
        "moldy_route": (
            f"/agents/{agent_id}/conversations/{conversation_id}"
            if agent_id and conversation_id
            else None
        ),
        "moldy_source": source,
    }
