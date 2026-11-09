"""Tool approval views and conversation grants (approval-modes-plan 3.3, 3.5, Q5).

* ``agent_tools``: the agent's tools by their run-time names and risk levels (the names the
  model calls them by, so a setting and a grant match the call).
* ``live`` / ``grant`` / ``revoke``: "allow for this conversation". Only the conversation's
  owner makes or revokes one (the routes gate the conversation), only for a tool the agent
  has and ``tool_modes.grantable`` allows, and every change is audited. Changing the agent's
  tool approval settings revokes its conversations' grants (``revoke_for_agent``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_policy import ToolApprovalPolicyV1, resolve_runtime_policy
from app.approvals.tool_modes import grantable
from app.authz.audit_actions import APPROVAL_GRANT_CREATED, APPROVAL_GRANT_REVOKED
from app.authz.org_context import OrgContext
from app.exceptions import AppError
from app.guardrails.tool_risk import ToolRiskPolicy, level_of
from app.models.conversation import Conversation
from app.models.conversation_tool_grant import ConversationToolGrant
from app.services import audit_service
from app.tools.risk import ToolRiskLevel, execute_in_skill_risk, risk_from_tool_config

FILESYSTEM_WRITE_TOOLS: Final = ("write_file", "edit_file")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def runtime_name(config: dict[str, Any]) -> str:
    """The name a run gives this tool (``tool_factory`` / ``mcp_tool_loader`` rules)."""
    from app.agent_runtime.tool_factory import _safe_tool_name, create_builtin_tool

    key = str(config.get("definition_key") or "")
    if key == "mcp":
        return str(config.get("mcp_tool_name") or config.get("name") or "mcp")
    if key.startswith("builtin:"):
        tool = create_builtin_tool(key)
        if tool is not None:
            return tool.name
    return _safe_tool_name(str(config.get("name") or key or "tool"))


async def agent_tools(
    db: AsyncSession, agent: Any, context: OrgContext
) -> list[tuple[str, ToolRiskLevel]]:
    """The root agent's tools a setting can name, with their risk levels."""
    from app.services.chat.runtime_context import build_agent_skills, build_tools_config

    found: dict[str, ToolRiskLevel] = {}
    configs = await build_tools_config(
        agent, context=context, db=db, conversation_id=None, read_only=True
    )
    for config in configs:
        found[runtime_name(config)] = risk_from_tool_config(config).risk_level
    if build_agent_skills(agent, context):
        found["execute_in_skill"] = execute_in_skill_risk().risk_level
    policy = resolve_runtime_policy(agent.runtime_policy).effective
    if policy.filesystem.mode == "artifact_write":
        for name in FILESYSTEM_WRITE_TOOLS:
            found.setdefault(name, level_of(name))
    return sorted(found.items())


async def list_live(db: AsyncSession, conversation_id: uuid.UUID) -> list[ConversationToolGrant]:
    rows = await db.execute(
        select(ConversationToolGrant)
        .where(
            ConversationToolGrant.conversation_id == conversation_id,
            ConversationToolGrant.revoked_at.is_(None),
        )
        .order_by(ConversationToolGrant.created_at, ConversationToolGrant.id)
    )
    return list(rows.scalars())


def settings_of(agent: Any) -> ToolApprovalPolicyV1 | None:
    return resolve_runtime_policy(agent.runtime_policy).effective.tool_approval


async def live(db: AsyncSession, conversation_id: uuid.UUID) -> frozenset[str]:
    rows = await db.execute(
        select(ConversationToolGrant.tool_name).where(
            ConversationToolGrant.conversation_id == conversation_id,
            ConversationToolGrant.revoked_at.is_(None),
        )
    )
    return frozenset(rows.scalars())


def not_grantable(tool: str) -> AppError:
    return AppError(
        "TOOL_GRANT_NOT_ALLOWED",
        "이 도구는 대화 동안 허용할 수 없습니다",
        status=422,
        details={"tool": tool},
    )


async def grant(
    db: AsyncSession,
    context: OrgContext,
    conversation: Conversation,
    tool: str,
    *,
    agent: Any,
    company: ToolRiskPolicy | None,
) -> ConversationToolGrant:
    """Allow ``tool`` for this conversation (idempotent while a live grant exists)."""
    levels = dict(await agent_tools(db, agent, context))
    level = levels.get(tool)
    if level is None or not grantable(tool, level, settings=settings_of(agent), company=company):
        raise not_grantable(tool)
    existing = await db.scalar(
        select(ConversationToolGrant).where(
            ConversationToolGrant.conversation_id == conversation.id,
            ConversationToolGrant.tool_name == tool,
            ConversationToolGrant.revoked_at.is_(None),
        )
    )
    if existing is not None:
        return existing
    row = ConversationToolGrant(
        conversation_id=conversation.id, tool_name=tool, granted_by=context.user_id
    )
    db.add(row)
    await db.flush()
    await _audit(db, context, conversation, APPROVAL_GRANT_CREATED, tool, level.value)
    return row


async def revoke(
    db: AsyncSession, context: OrgContext, conversation: Conversation, tool: str
) -> bool:
    result = await db.execute(
        update(ConversationToolGrant)
        .where(
            ConversationToolGrant.conversation_id == conversation.id,
            ConversationToolGrant.tool_name == tool,
            ConversationToolGrant.revoked_at.is_(None),
        )
        .values(revoked_at=_now(), revoked_reason="revoked")
        .execution_options(synchronize_session=False)
    )
    revoked = bool(result.rowcount)  # type: ignore[attr-defined]
    if revoked:
        await _audit(db, context, conversation, APPROVAL_GRANT_REVOKED, tool, None)
    return revoked


async def revoke_for_agent(db: AsyncSession, agent_id: uuid.UUID) -> int:
    """Every live grant in the agent's conversations (its tool approval settings changed)."""
    conversations = select(Conversation.id).where(Conversation.agent_id == agent_id)
    result = await db.execute(
        update(ConversationToolGrant)
        .where(
            ConversationToolGrant.conversation_id.in_(conversations),
            ConversationToolGrant.revoked_at.is_(None),
        )
        .values(revoked_at=_now(), revoked_reason="settings_changed")
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def _audit(
    db: AsyncSession,
    context: OrgContext,
    conversation: Conversation,
    action: str,
    tool: str,
    level: str | None,
) -> None:
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=context.user_id,
        owner_user_id=context.user_id,
        action=action,
        target_type="conversation",
        target_id=conversation.id,
        target_owner_user_id=conversation.user_id,
        outcome="success",
        metadata={
            "tool": tool,
            "risk_level": level,
            "agent_id": str(conversation.agent_id) if conversation.agent_id else None,
        },
    )


__all__ = [
    "Conversation",
    "agent_tools",
    "grant",
    "list_live",
    "live",
    "not_grantable",
    "revoke",
    "revoke_for_agent",
    "runtime_name",
    "settings_of",
]
