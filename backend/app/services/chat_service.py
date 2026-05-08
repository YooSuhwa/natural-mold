"""Chat service — conversations, messages, and agent context assembly.

Greenfield M5 rewrite. The legacy PREBUILT/CUSTOM/MCP branching has been
collapsed into a single resolution path: every tool row points at a registered
``ToolDefinition`` (``tool.definition_key``) plus an optional credential
(``tool.credential_id``). MCP server bindings are handled separately by the
caller via the new ``app.mcp.client`` module.

Helpers re-exported by this module are imported by the trigger executor and the
conversations router; their public shape (``get_agent_with_tools``,
``build_tools_config``, ``build_effective_prompt``, ``build_agent_skills``) is
preserved to keep those callers thin.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, selectinload

from app.credentials import service as credential_service
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.skill import AgentSkillLink
from app.models.token_usage import TokenUsage
from app.models.tool import AgentToolLink, Tool
from app.schemas.conversation import ConversationUpdate
from app.skills.runtime import build_skills_for_agent

logger = logging.getLogger(__name__)


__all__ = [
    "build_agent_skills",
    "build_effective_prompt",
    "build_tools_config",
    "clear_active_branch_override",
    "create_conversation",
    "delete_conversation",
    "get_agent_with_tools",
    "get_conversation",
    "get_owned_conversation",
    "get_owned_conversation_with_agent",
    "link_attachments_to_conversation",
    "list_conversations",
    "list_messages_from_checkpointer",
    "maybe_set_auto_title",
    "save_token_usage",
    "touch_conversation",
    "update_conversation",
]


# ---------------------------------------------------------------------------
# Conversations CRUD
# ---------------------------------------------------------------------------


async def list_conversations(db: AsyncSession, agent_id: uuid.UUID) -> list[Conversation]:
    result = await db.execute(
        select(Conversation)
        .where(Conversation.agent_id == agent_id)
        .order_by(Conversation.is_pinned.desc(), Conversation.updated_at.desc())
    )
    return list(result.scalars().all())


async def create_conversation(
    db: AsyncSession, agent_id: uuid.UUID, title: str | None = None
) -> Conversation:
    conv = Conversation(agent_id=agent_id, title=title or "새 대화")
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return conv


async def get_conversation(db: AsyncSession, conversation_id: uuid.UUID) -> Conversation | None:
    result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    return result.scalar_one_or_none()


async def get_owned_conversation(
    db: AsyncSession, conversation_id: uuid.UUID, user_id: uuid.UUID
) -> Conversation | None:
    """Conversation lookup gated by ownership through Agent.user_id.

    Single SELECT joining ``conversations -> agents`` so callers don't have to
    issue two queries (conversation, then agent ownership check). Returns
    ``None`` when the conversation doesn't exist *or* belongs to another user
    — callers should map both to ``conversation_not_found`` so existence
    isn't leaked via 403/404 differences.
    """
    result = await db.execute(
        select(Conversation)
        .join(Agent, Conversation.agent_id == Agent.id)
        .where(Conversation.id == conversation_id)
        .where(Agent.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def update_conversation(
    db: AsyncSession, conv: Conversation, data: ConversationUpdate
) -> Conversation:
    if data.title is not None:
        conv.title = data.title
    if data.is_pinned is not None:
        conv.is_pinned = data.is_pinned
    await db.commit()
    await db.refresh(conv)
    return conv


async def delete_conversation(db: AsyncSession, conv: Conversation) -> None:
    from app.agent_runtime.checkpointer import delete_thread

    await delete_thread(str(conv.id))
    await db.delete(conv)
    await db.commit()


async def list_messages_from_checkpointer(
    db: AsyncSession,
    conversation: Conversation,
    user_id: uuid.UUID | None = None,
    *,
    tree: Any = None,
) -> list:
    """Return persisted messages, attaching stable per-message timestamps.

    LangChain ``BaseMessage`` carries no timestamp metadata, so we keep an
    ``idx → ISO`` mapping in ``Conversation.message_timestamps``. The first
    time a message is exposed we stamp it with the current time; subsequent
    reads reuse the stored value so old messages don't drift on every fetch.

    M-CHAT1b: when the conversation has multiple branches we now walk the
    full checkpoint tree (not just the latest checkpoint) so each
    ``MessageResponse`` carries ``parent_id`` / ``branch_checkpoint_id`` /
    ``siblings`` for assistant-ui's BranchPicker. The legacy callers (and
    legacy tests) that expect a flat list are unaffected — for a thread with
    no branching this returns the same active linear list as before.

    When ``user_id`` is provided, each ``MessageResponse`` is hydrated with
    the caller's existing feedback rating (P0-1c) and any attachments linked
    by message id (P1-7).
    """

    from app.agent_runtime.message_utils import langchain_messages_to_response, parse_msg_id
    from app.models.message_attachment import MessageAttachment
    from app.models.message_feedback import MessageFeedback
    from app.schemas.conversation import MessageAttachmentBrief, MessageFeedbackBrief

    # P0-D: tree를 호출자가 미리 만들어 넘기면 build_message_tree 중복 호출
    # (= _collect_checkpoints + alist 전체 walk)을 피한다. 단독으로 부르면
    # 하위호환 유지를 위해 직접 build.
    if tree is None:
        from app.agent_runtime.checkpointer import get_checkpointer
        from app.services.thread_branch_service import build_message_tree

        checkpointer = get_checkpointer()
        tree = await build_message_tree(
            checkpointer,
            str(conversation.id),
            active_checkpoint_id=conversation.active_branch_checkpoint_id,
        )

    if not tree.nodes:
        return []

    messages = [node.message for node in tree.nodes]

    new_stored: dict[str, str] = dict(conversation.message_timestamps or {})
    timestamps: list[datetime] = []
    now = datetime.now(UTC).replace(tzinfo=None)
    changed = False

    for idx, msg in enumerate(messages):
        msg_uuid = parse_msg_id(getattr(msg, "id", None), conversation.id, idx)
        key = str(msg_uuid)
        iso = new_stored.get(key)
        if iso:
            ts = datetime.fromisoformat(iso)
        else:
            ts = now + timedelta(milliseconds=idx)
            new_stored[key] = ts.isoformat()
            changed = True
        timestamps.append(ts)

    if changed:
        await db.execute(
            update(Conversation)
            .where(Conversation.id == conversation.id)
            .values(message_timestamps=new_stored)
        )
        await db.commit()

    # W7-4 — conversation의 agent에 연결된 model 단가를 한 번 조회해 넘긴다.
    # 메시지마다 model이 다를 수 있으나(fallback chain) 단순화 — 95% 케이스인
    # default model 단가만 사용해 근사. 정확한 누적은 Daily Spend가 별도로 추적.
    cost_per_input, cost_per_output = await _resolve_agent_model_pricing(db, conversation)

    responses = langchain_messages_to_response(
        messages,
        conversation.id,
        timestamps=timestamps,
        cost_per_input_token=cost_per_input,
        cost_per_output_token=cost_per_output,
    )

    # Attach branch tree info — parent_id, siblings, branch_checkpoint_id.
    # We pre-compute msg id → response idx so parent/sibling lookups are O(1).
    raw_to_uuid: dict[str, uuid.UUID] = {}
    for idx, msg in enumerate(messages):
        raw = str(getattr(msg, "id", None) or f"synthetic-{idx}")
        raw_to_uuid[raw] = parse_msg_id(getattr(msg, "id", None), conversation.id, idx)

    # Pre-compute uuids for *every* sibling raw id we may reference (siblings
    # for the active node may live on non-active leaves whose raw ids don't
    # appear in ``raw_to_uuid`` yet — derive them with the same parse_msg_id
    # logic so the frontend ids are consistent).
    def _sibling_uuid(raw: str, idx: int) -> uuid.UUID:
        if raw in raw_to_uuid:
            return raw_to_uuid[raw]
        # Synthesize using the same fallback rule as the active chain.
        synth = None if raw.startswith("synthetic-") else raw
        return parse_msg_id(synth, conversation.id, idx)

    for idx, (resp, node) in enumerate(zip(responses, tree.nodes, strict=False)):
        resp.branch_checkpoint_id = node.introduced_by_checkpoint_id
        if node.parent_id:
            resp.parent_id = raw_to_uuid.get(node.parent_id)
        # Sibling map keyed by the raw langchain id.
        raw_id = str(getattr(node.message, "id", None) or f"synthetic-{idx}")
        sibling_entries = tree.branches_by_message.get(raw_id, [])
        resp.siblings = [
            _sibling_uuid(s.message_id, idx) for s in sibling_entries
        ]
        resp.sibling_checkpoint_ids = [s.checkpoint_id for s in sibling_entries]
        resp.branch_index = node.branch_index
        resp.branch_total = node.branch_total

    # Hydrate per-message feedback (current user) + attachments. Wrapped in
    # broad try/except so a missing migration (m27/m28 not yet applied) or
    # any other query glitch degrades gracefully — the message list still
    # renders, just without the feedback/attachment metadata.
    feedback_by_msg: dict[str, str] = {}
    attachments_by_msg: dict[str, list[MessageAttachmentBrief]] = {}

    if user_id is not None:
        try:
            result = await db.execute(
                select(MessageFeedback).where(
                    MessageFeedback.user_id == user_id,
                    MessageFeedback.conversation_id == conversation.id,
                )
            )
            for fb in result.scalars().all():
                feedback_by_msg[fb.message_id] = fb.rating
        except Exception:  # noqa: BLE001 — non-critical hydration
            logger.warning(
                "feedback hydrate failed for conversation %s — skipping",
                conversation.id,
                exc_info=True,
            )

    try:
        attach_result = await db.execute(
            select(MessageAttachment).where(
                MessageAttachment.conversation_id == conversation.id,
                MessageAttachment.message_id.is_not(None),
            )
        )
        for att in attach_result.scalars().all():
            if att.message_id is None:
                continue
            attachments_by_msg.setdefault(att.message_id, []).append(
                MessageAttachmentBrief.model_validate(att)
            )
    except Exception:  # noqa: BLE001 — non-critical hydration
        logger.warning(
            "attachment hydrate failed for conversation %s — skipping",
            conversation.id,
            exc_info=True,
        )

    for resp in responses:
        mid = str(resp.id)
        rating = feedback_by_msg.get(mid)
        if rating:
            resp.feedback = MessageFeedbackBrief(rating=rating)
        atts = attachments_by_msg.get(mid)
        if atts:
            resp.attachments = atts

    return responses


async def maybe_set_auto_title(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    content: str,
) -> None:
    title = content.strip().replace("\n", " ")
    if len(title) > 40:
        title = title[:37] + "..."
    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id, Conversation.title == "새 대화")
        .values(title=title)
    )
    await db.commit()


async def _resolve_agent_model_pricing(
    db: AsyncSession, conversation: Conversation
) -> tuple[float | None, float | None]:
    """W7-4 — conversation.agent.model의 ``cost_per_*_token`` 단가를 조회.

    Decimal → float 변환. Agent/Model row가 사라졌거나 단가가 NULL이면
    ``(None, None)``. 호출자(``langchain_messages_to_response``)는 None을
    받으면 ``estimated_cost``를 채우지 않는다.
    """
    from sqlalchemy import select as _select

    from app.models.agent import Agent
    from app.models.model import Model

    result = await db.execute(
        _select(Model.cost_per_input_token, Model.cost_per_output_token)
        .join(Agent, Agent.model_id == Model.id)
        .where(Agent.id == conversation.agent_id)
        .limit(1)
    )
    row = result.first()
    if row is None:
        return None, None
    cost_in, cost_out = row
    return (
        float(cost_in) if cost_in is not None else None,
        float(cost_out) if cost_out is not None else None,
    )


async def save_token_usage(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    agent_id: uuid.UUID,
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    estimated_cost: float | None = None,
) -> TokenUsage:
    usage = TokenUsage(
        conversation_id=conversation_id,
        agent_id=agent_id,
        model_name=model_name,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        estimated_cost=estimated_cost,
    )
    db.add(usage)
    await db.commit()
    return usage


async def link_attachments_to_conversation(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    attachment_ids: list[uuid.UUID],
) -> None:
    """Stamp orphan ``MessageAttachment`` rows with their conversation id.

    ``message_id`` stays null at send time (LangGraph hands the id back
    inside the SSE stream); the frontend currently keys previews on the
    upload id directly, so leaving it null doesn't block rendering.
    """

    if not attachment_ids:
        return
    from app.models.message_attachment import MessageAttachment

    await db.execute(
        update(MessageAttachment)
        .where(
            MessageAttachment.id.in_(attachment_ids),
            MessageAttachment.user_id == user_id,
        )
        .values(conversation_id=conversation_id)
    )
    await db.commit()


async def touch_conversation(db: AsyncSession, conversation_id: uuid.UUID) -> None:
    """Bump ``conversation.updated_at`` to anchor message-list timestamps."""

    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(updated_at=datetime.now(UTC).replace(tzinfo=None))
    )
    await db.commit()


async def clear_active_branch_override(
    db: AsyncSession, conversation_id: uuid.UUID
) -> None:
    """Reset ``active_branch_checkpoint_id`` so the next list call falls back
    to the newest leaf — used after edit/regenerate where the new branch is
    the most recent and should automatically become active."""

    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(active_branch_checkpoint_id=None)
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Agent context assembly (single-path, greenfield)
# ---------------------------------------------------------------------------


async def get_owned_conversation_with_agent(
    db: AsyncSession, conversation_id: uuid.UUID, user_id: uuid.UUID
) -> Conversation | None:
    """Single SELECT joining ``conversations ⨝ agents on user_id`` + agent
    runtime eager-loads (model / llm_credential / tool_links / mcp_tool_links
    / skill_links). 결과 ``conv.agent`` 는 별도 query 없이 hydrated.

    ``_resolve_agent_context`` 의 conv lookup + ``get_agent_with_tools`` 두
    round-trip 을 하나로 축소 (W3-out 트랙 종료 retrospective MED follow-up).
    runtime relations 의 selectin chain 자체는 동일하게 발사되므로 SELECT
    수는 (2 + N) → (1 + N) — N=5 (model, llm_credential, tool_links, mcp_tool
    _links, skill_links) 기준 약 14% 절감.

    ``Model.default_credential`` 관계는 eager-load 하지 않는다 — 런타임
    (``credential_resolution``) 이 ``model.default_credential_id`` (FK 컬럼)
    만 읽고 tier 2 fallback 시에만 ``credential_service.get_for_user`` 로
    별도 fetch 한다. 이전에는 ``selectinload(Model.default_credential)`` 로
    매 chat 요청마다 +1 SELECT 가 발사됐으나 결과를 아무도 읽지 않아 dead
    round-trip 이었음.

    Returns ``None`` when the conversation doesn't exist *or* belongs to
    another user — caller should map both to a single 404 (rules/security.md
    enumeration oracle, ``get_owned_conversation`` 와 동일 contract).
    """
    result = await db.execute(
        select(Conversation)
        .join(Agent, Conversation.agent_id == Agent.id)
        .where(Conversation.id == conversation_id, Agent.user_id == user_id)
        .options(
            contains_eager(Conversation.agent).options(
                selectinload(Agent.model),
                selectinload(Agent.llm_credential),
                selectinload(Agent.tool_links)
                .selectinload(AgentToolLink.tool)
                .selectinload(Tool.credential),
                selectinload(Agent.mcp_tool_links)
                .selectinload(AgentMcpToolLink.mcp_tool)
                .selectinload(McpTool.server)
                .selectinload(McpServer.credential),
                selectinload(Agent.skill_links).selectinload(AgentSkillLink.skill),
            )
        )
    )
    return result.unique().scalar_one_or_none()


async def get_agent_with_tools(
    db: AsyncSession, agent_id: uuid.UUID, user_id: uuid.UUID
) -> Agent | None:
    """Load agent with everything needed by the runtime in one round-trip.

    Eager-loads:
    - ``Agent.model`` (no provider join — ``llm_providers`` is retired)
    - ``Agent.tool_links → tool → credential`` (single FK path)
    - ``Agent.skill_links → skill``

    The legacy per-user "default connection map" prefetch is gone: every tool
    row owns its own ``credential_id``. The trigger executor and conversations
    router both call this helper, so prefetch is consistent across the two
    callers (closing the M11 ``trigger_executor.py`` prefetch-skew bug).
    """

    result = await db.execute(
        select(Agent)
        .where(Agent.id == agent_id, Agent.user_id == user_id)
        .options(
            selectinload(Agent.model),
            selectinload(Agent.llm_credential),
            selectinload(Agent.tool_links)
            .selectinload(AgentToolLink.tool)
            .selectinload(Tool.credential),
            # MCP tool link → mcp_tool → server (server carries transport,
            # url, headers, and credential needed to actually invoke).
            selectinload(Agent.mcp_tool_links)
            .selectinload(AgentMcpToolLink.mcp_tool)
            .selectinload(McpTool.server)
            .selectinload(McpServer.credential),
            selectinload(Agent.skill_links).selectinload(AgentSkillLink.skill),
        )
    )
    return result.scalar_one_or_none()


def build_effective_prompt(agent: Agent) -> str:
    """Build system prompt — skill bodies are injected by deepagents middleware."""

    return agent.system_prompt


def build_agent_skills(agent: Agent) -> list[dict[str, Any]]:
    """Forward the agent's skill links to the runtime descriptor list."""

    return build_skills_for_agent(agent.skill_links)


async def build_tools_config(
    agent: Agent,
    *,
    db: AsyncSession | None = None,
    conversation_id: str | None = None,
) -> list[dict[str, Any]]:
    """Build the runtime tools_config list for an agent.

    The shape is intentionally minimal — every entry exposes the registry
    ``definition_key`` (so the executor knows which runner to instantiate),
    the user-supplied ``parameters``, and an optional decrypted
    ``credentials`` dict.

    The legacy 4-way ``ToolType`` branch + per-user default connection map +
    cross-tenant ownership gates are gone. ``Tool.credential_id`` is the only
    auth source; ownership is enforced by ``user_id`` filters on the writes.

    MCP tools are not represented as ``Tool`` rows under the greenfield model
    (they live in ``mcp_servers``/``mcp_tools``). Until ``agent_mcp_servers``
    link binding ships, this function only emits regular tool entries; MCP
    bindings will come in via a separate config list. See
    ``app/mcp/client.py`` and the M5 follow-up note in ``progress.txt``.
    """

    configs: list[dict[str, Any]] = []
    for link in agent.tool_links:
        tool = link.tool
        if tool is None or not tool.enabled:
            continue

        credentials: dict[str, Any] | None = None
        credential = getattr(tool, "credential", None)
        if credential is not None:
            try:
                credentials = await credential_service.decrypt_with_external(
                    credential.data_encrypted
                )
            except Exception:  # noqa: BLE001 — surface as missing creds, never crash chat
                logger.exception(
                    "credential decryption failed for tool %s (credential %s)",
                    tool.id,
                    credential.id,
                )
                credentials = None

        configs.append(
            {
                "tool_id": str(tool.id),
                "definition_key": tool.definition_key,
                "name": tool.name,
                "description": tool.description,
                "parameters": dict(tool.parameters or {}),
                "credentials": credentials,
                "credential_id": (
                    str(tool.credential_id) if tool.credential_id else None
                ),
                # Hook-framework correlation — wire down to ``tool_factory``.
                "user_id": str(agent.user_id),
                "agent_id": str(agent.id),
            }
        )

    # MCP tool bindings — emit in the executor's mcp_server_url shape so
    # ``_build_mcp_tools`` instantiates them. m25 added the link table that
    # makes this possible (previously a m5 follow-up).
    for mcp_link in agent.mcp_tool_links:
        mcp_tool = mcp_link.mcp_tool
        if mcp_tool is None or not mcp_tool.enabled:
            continue
        server = mcp_tool.server
        if server is None or not server.url:
            continue

        mcp_credentials: dict[str, Any] | None = None
        if server.credential is not None:
            try:
                mcp_credentials = await credential_service.decrypt_with_external(
                    server.credential.data_encrypted
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "credential decryption failed for mcp server %s", server.id
                )

        configs.append(
            {
                "tool_id": f"mcp:{mcp_tool.id}",
                "definition_key": "mcp",
                "name": mcp_tool.name,
                "description": mcp_tool.description,
                "parameters": {},
                # _build_mcp_tools branches on these keys (see executor.py
                # ``mcp_server_url``).
                "mcp_server_url": server.url,
                "mcp_tool_name": mcp_tool.name,
                "mcp_transport_headers": dict(server.headers or {}),
                "credentials": mcp_credentials,
                "user_id": str(agent.user_id),
                "agent_id": str(agent.id),
            }
        )

    return configs
