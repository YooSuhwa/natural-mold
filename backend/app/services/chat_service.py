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

import base64
import json
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, assert_never

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, contains_eager, selectinload

from app.agent_runtime.identity import AgentRunIdentity
from app.agent_runtime.protocol_redaction import redact_protocol_data
from app.credentials import service as credential_service
from app.exceptions import ValidationError
from app.mcp.auth import resolve_mcp_auth
from app.mcp.client import build_headers
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.message_event import MessageEvent
from app.models.skill import AgentSkillLink
from app.models.token_usage import TokenUsage
from app.models.tool import AgentToolLink, Tool
from app.schemas.conversation import ConversationSort, ConversationUpdate, MessageResponse
from app.skills.runtime import build_skills_for_agent

logger = logging.getLogger(__name__)


__all__ = [
    "build_agent_skills",
    "build_effective_prompt",
    "conversation_title_from_content",
    "build_tools_config",
    "clear_active_branch_override",
    "collect_conversation_secret_values",
    "create_conversation",
    "delete_conversation",
    "gc_orphan_draft_conversations",
    "get_agent_with_tools",
    "get_conversation",
    "get_owned_conversation",
    "get_owned_conversation_with_agent",
    "get_owned_ui_conversation_with_agent",
    "is_agent_owned_by_user",
    "link_attachments_to_conversation",
    "list_conversations",
    "list_conversations_page",
    "list_global_conversations_page",
    "list_messages_from_checkpointer",
    "mark_conversation_read",
    "maybe_set_auto_title",
    "promote_draft_conversation",
    "save_token_usage",
    "touch_conversation",
    "trigger_blocked_tools_for_agent_tree",
    "update_conversation",
]

_HITL_METADATA_KEYS = {
    "approval_id",
    "allowed_decisions",
    "hitl_interrupt_id",
    "hitl_action_index",
    "hitl_total_actions",
}


def conversation_title_from_content(content: str) -> str:
    title = content.strip().replace("\n", " ")
    if not title:
        return "새 대화"
    if len(title) > 40:
        return title[:37] + "..."
    return title


def _review_config_for_action(
    action: dict[str, Any],
    review_configs: list[dict[str, Any]],
    index: int,
) -> dict[str, Any]:
    if index < len(review_configs):
        return review_configs[index]
    action_name = action.get("name")
    for config in review_configs:
        if config.get("action_name") == action_name:
            return config
    return {"action_name": action_name, "allowed_decisions": ["approve", "reject"]}


def _hitl_metadata_for_action(
    interrupt_id: str,
    review_config: dict[str, Any],
    index: int,
    total_actions: int,
) -> dict[str, Any]:
    approval_id = f"{interrupt_id}:{index}"
    return {
        "approval_id": approval_id,
        "allowed_decisions": review_config.get("allowed_decisions") or ["approve", "reject"],
        "hitl_interrupt_id": interrupt_id,
        "hitl_action_index": index,
        "hitl_total_actions": total_actions,
    }


def _is_ask_user_respond_only(action: dict[str, Any], review_config: dict[str, Any]) -> bool:
    allowed = review_config.get("allowed_decisions") or []
    return action.get("name") == "ask_user" and allowed == ["respond"]


def _standard_interrupt_to_tool_calls(payload: dict[str, Any]) -> list[dict[str, Any]]:
    action_requests = payload.get("action_requests")
    review_configs = payload.get("review_configs")
    if not isinstance(action_requests, list) or not isinstance(review_configs, list):
        return []

    interrupt_id = payload.get("interrupt_id")
    if not isinstance(interrupt_id, str):
        interrupt_id = ""

    tool_calls: list[dict[str, Any]] = []
    total_actions = len(action_requests)
    for index, raw_action in enumerate(action_requests):
        if not isinstance(raw_action, dict):
            continue
        action_name = raw_action.get("name")
        if not isinstance(action_name, str) or not action_name:
            continue
        raw_args = raw_action.get("args")
        action_args = raw_args if isinstance(raw_args, dict) else {}
        review_config = _review_config_for_action(raw_action, review_configs, index)
        metadata = _hitl_metadata_for_action(interrupt_id, review_config, index, total_actions)
        if _is_ask_user_respond_only(raw_action, review_config):
            tool_calls.append(
                {
                    "id": metadata["approval_id"],
                    "name": "ask_user",
                    "args": {**action_args, **metadata},
                }
            )
            continue

        args = {
            "tool_name": action_name,
            "tool_args": action_args,
            **metadata,
        }
        description = raw_action.get("description")
        if isinstance(description, str) and description:
            args["description"] = description
        tool_calls.append(
            {
                "id": metadata["approval_id"],
                "name": "request_approval",
                "args": args,
            }
        )
    return tool_calls


def _strip_hitl_metadata(args: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in args.items() if key not in _HITL_METADATA_KEYS}


def _args_fingerprint(args: dict[str, Any]) -> str:
    return json.dumps(_strip_hitl_metadata(args), sort_keys=True, ensure_ascii=False, default=str)


def _equivalent_tool_args(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return _args_fingerprint(left) == _args_fingerprint(right)


def _contains_tool_args(superset: dict[str, Any], subset: dict[str, Any]) -> bool:
    cleaned_superset = _strip_hitl_metadata(superset)
    cleaned_subset = _strip_hitl_metadata(subset)
    for key, value in cleaned_subset.items():
        if json.dumps(cleaned_superset.get(key), sort_keys=True, default=str) != json.dumps(
            value,
            sort_keys=True,
            default=str,
        ):
            return False
    return True


def _tool_call_name(tool_call: dict[str, Any]) -> str | None:
    name = tool_call.get("name") or tool_call.get("tool_name")
    if isinstance(name, str) and name:
        return name
    function = tool_call.get("function")
    if isinstance(function, dict):
        fn_name = function.get("name")
        if isinstance(fn_name, str) and fn_name:
            return fn_name
    return None


def _tool_call_args(tool_call: dict[str, Any]) -> dict[str, Any]:
    args = tool_call.get("args")
    if isinstance(args, dict):
        return args
    params = tool_call.get("parameters")
    if isinstance(params, dict):
        return params
    function = tool_call.get("function")
    if isinstance(function, dict):
        arguments = function.get("arguments")
        if isinstance(arguments, dict):
            return arguments
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
    return {}


def _approval_target(
    synthetic: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    if synthetic.get("name") != "request_approval":
        return None
    args = synthetic.get("args")
    if not isinstance(args, dict):
        return None
    tool_name = args.get("tool_name")
    tool_args = args.get("tool_args")
    if not isinstance(tool_name, str) or not tool_name:
        return None
    return tool_name, tool_args if isinstance(tool_args, dict) else {}


def _with_replacement_id(
    synthetic: dict[str, Any],
    existing: dict[str, Any],
) -> dict[str, Any]:
    synthetic_id = synthetic.get("id")
    existing_id = existing.get("id")
    fallback_id = existing_id if isinstance(existing_id, str) and existing_id else synthetic_id
    if not isinstance(fallback_id, str) or not fallback_id:
        return synthetic
    args = synthetic.get("args")
    return {
        **synthetic,
        "id": fallback_id,
        "args": {**args, "approval_id": fallback_id} if isinstance(args, dict) else args,
    }


def _merge_interrupt_tool_calls(
    tool_calls: list[dict[str, Any]],
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    merged = [dict(call) for call in tool_calls]
    synthetic_tool_calls = _standard_interrupt_to_tool_calls(payload)
    replaced_indices: set[int] = set()

    for synthetic in synthetic_tool_calls:
        was_merged = False

        if synthetic.get("name") == "ask_user":
            synthetic_args = synthetic.get("args")
            if isinstance(synthetic_args, dict):
                for index in range(len(merged) - 1, -1, -1):
                    existing = merged[index]
                    existing_args = _tool_call_args(existing)
                    if _tool_call_name(existing) == "ask_user" and _equivalent_tool_args(
                        existing_args,
                        synthetic_args,
                    ):
                        merged[index] = {
                            **existing,
                            "args": {**existing_args, **synthetic_args},
                        }
                        was_merged = True
                        break

        target = _approval_target(synthetic)
        if not was_merged and target is not None:
            target_name, target_args = target
            for index in range(len(merged) - 1, -1, -1):
                if index in replaced_indices:
                    continue
                existing = merged[index]
                existing_args = _tool_call_args(existing)
                if _tool_call_name(existing) != target_name:
                    continue
                if (
                    _equivalent_tool_args(existing_args, target_args)
                    or _contains_tool_args(target_args, existing_args)
                    or _contains_tool_args(existing_args, target_args)
                    or not existing_args
                    or not target_args
                ):
                    merged[index] = _with_replacement_id(synthetic, existing)
                    replaced_indices.add(index)
                    was_merged = True
                    break

        if not was_merged:
            merged.append(synthetic)

    return merged


def _latest_interrupt_payload(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        data = _interrupt_payload_data(event)
        if data is None:
            continue
        action_requests = data.get("action_requests")
        if isinstance(action_requests, list) and action_requests:
            return data
    return None


def _interrupt_payload_data(event: dict[str, Any]) -> dict[str, Any] | None:
    if event.get("event") == "interrupt":
        data = event.get("data")
        return data if isinstance(data, dict) else None

    if event.get("method") != "input.requested":
        return None

    data = event.get("data")
    if not isinstance(data, dict):
        params = event.get("params")
        if not isinstance(params, dict):
            return None
        params_data = params.get("data")
        data = params_data if isinstance(params_data, dict) else {}

    if _has_action_requests(data):
        return data

    payload = data.get("payload")
    if not isinstance(payload, dict) or not _has_action_requests(payload):
        return None

    merged = dict(payload)
    interrupt_id = data.get("interrupt_id") or data.get("id")
    namespace = data.get("namespace") or data.get("ns")
    if interrupt_id is not None and "interrupt_id" not in merged:
        merged["interrupt_id"] = interrupt_id
    if namespace is not None and "namespace" not in merged:
        merged["namespace"] = namespace
    return merged


def _has_action_requests(data: dict[str, Any]) -> bool:
    action_requests = data.get("action_requests")
    return isinstance(action_requests, list) and bool(action_requests)


def _completed_tool_call_ids(responses: list[MessageResponse]) -> set[str]:
    return {
        response.tool_call_id
        for response in responses
        if response.role == "tool" and response.tool_call_id
    }


def _interrupt_action_matches_tool_call(
    action: dict[str, Any],
    tool_call: dict[str, Any],
) -> bool:
    action_args = action.get("args")
    target_args = action_args if isinstance(action_args, dict) else {}
    existing_args = _tool_call_args(tool_call)
    if _tool_call_name(tool_call) != action.get("name"):
        return False
    return (
        _equivalent_tool_args(existing_args, target_args)
        or _contains_tool_args(target_args, existing_args)
        or _contains_tool_args(existing_args, target_args)
        or not existing_args
        or not target_args
    )


def _all_interrupt_actions_completed(
    payload: dict[str, Any],
    tool_calls: list[dict[str, Any]],
    completed_tool_call_ids: set[str],
) -> bool:
    action_requests = payload.get("action_requests")
    if not isinstance(action_requests, list) or not action_requests:
        return False
    if not completed_tool_call_ids:
        return False

    for action in action_requests:
        if not isinstance(action, dict):
            return False
        action_completed = any(
            str(tool_call.get("id") or "") in completed_tool_call_ids
            and _interrupt_action_matches_tool_call(action, tool_call)
            for tool_call in tool_calls
        )
        if not action_completed:
            return False
    return True


def _interrupt_payload_targets_tool_calls(
    payload: dict[str, Any],
    tool_calls: list[dict[str, Any]],
) -> bool:
    action_requests = payload.get("action_requests")
    if not isinstance(action_requests, list) or not action_requests:
        return False
    return any(
        isinstance(action, dict)
        and any(_interrupt_action_matches_tool_call(action, tool_call) for tool_call in tool_calls)
        for action in action_requests
    )


async def _hydrate_pending_interrupt_tool_calls(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    responses: list[MessageResponse],
) -> None:
    """Restore persisted HITL approval cards from append-only trace chunks."""

    response_by_id = {
        str(response.id): response
        for response in responses
        if response.role == "assistant" and response.tool_calls
    }
    if not response_by_id:
        return

    from app.models.message_event import MessageEvent
    from app.services import trace_storage

    completed_tool_call_ids = _completed_tool_call_ids(responses)
    result = await db.execute(
        select(MessageEvent)
        .where(MessageEvent.conversation_id == conversation_id)
        .order_by(MessageEvent.created_at)
    )
    for record in result.scalars().all():
        linked_ids = record.linked_message_ids or []
        target_responses = [response_by_id[mid] for mid in linked_ids if mid in response_by_id]
        if not target_responses:
            continue
        events = await trace_storage.load_events(db, record)
        payload = _latest_interrupt_payload(events)
        if payload is None:
            continue
        for response in target_responses:
            if not _interrupt_payload_targets_tool_calls(payload, response.tool_calls or []):
                continue
            if _all_interrupt_actions_completed(
                payload,
                response.tool_calls or [],
                completed_tool_call_ids,
            ):
                continue
            response.tool_calls = _merge_interrupt_tool_calls(response.tool_calls or [], payload)


async def collect_conversation_secret_values(
    db: AsyncSession,
    conversation: Conversation,
) -> set[str]:
    """ADR-021 C2 — gather the conversation agent's plaintext secrets.

    Read/poll endpoints (GET /messages, GET /threads/{id}/state, share-link
    render) run *outside* any agent run, so the run-scoped redaction ContextVar
    is never set. This is the single LIGHTWEIGHT collector those paths share to
    rebuild the eager secret set (LLM ``api_key`` + each tool config's plaintext
    ``credentials`` + MCP transport headers + base_url/url userinfo) and pass it
    explicitly to ``redact_protocol_data``.

    Deliberately does NOT use ``resolve_agent_context``: that additionally
    assembles every sub-agent config + resolves run identities, which is full
    run-prep cost on a polling endpoint (ADR-021 review #1). One ``select(Agent)``
    with the runtime eager-load chain + ``build_tools_config`` is the correct
    weight here. Best-effort: a missing agent / model / credential degrades to
    heuristics-only rather than failing the read.
    """

    from app.agent_runtime.credential_resolution import resolve_llm_api_key_for_agent
    from app.agent_runtime.run_secrets import collect_secret_values, collect_url_userinfo
    from app.services.conversation_stream_service import resolve_fallback_chain

    secrets: set[str] = set()
    # GET /messages (and share-link render) run outside any agent run, and the
    # routers load the conversation WITHOUT eager-loading ``agent`` (the runtime
    # selectin chain is too costly for this hot read path). Touching
    # ``conversation.agent`` here would emit a lazy load with no greenlet context
    # (sqlalchemy MissingGreenlet), so fetch the agent + runtime relations
    # explicitly. ``agent_id`` is a plain column already loaded on the
    # conversation row, so reading it triggers no IO.
    agent_id = getattr(conversation, "agent_id", None)
    if agent_id is None:
        return secrets
    try:
        result = await db.execute(
            select(Agent).where(Agent.id == agent_id).options(*_agent_runtime_load_options())
        )
        agent = result.unique().scalar_one_or_none()
    except Exception:  # noqa: BLE001 — read must not fail on secret-collect
        logger.debug("secret-collect agent load failed for %s", conversation.id, exc_info=True)
        return secrets
    if agent is None:
        return secrets

    # Each source is isolated: a failure in ONE (e.g. resolve_llm_api_key_for_agent
    # raises for an agent whose LLM credential was deleted) must NOT abort the
    # others, or tool/MCP/base_url secrets would silently go uncollected →
    # under-masking (ADR-021 re-review #1).
    try:
        api_key = await resolve_llm_api_key_for_agent(db, agent)
        if api_key:
            secrets |= collect_secret_values(api_key)
    except Exception:  # noqa: BLE001
        logger.debug("secret-collect api_key skipped for %s", conversation.id, exc_info=True)

    model = getattr(agent, "model", None)
    if model is not None:
        collect_url_userinfo(getattr(model, "base_url", None), secrets)

    # Fallback models can carry credentials in their ``base_url`` userinfo too —
    # the live run masks these (collect_cfg_secret_values walks the chain), so a
    # read/poll path must match. One bounded ``select(Model) where id in (...)``.
    try:
        fallback_chain = await resolve_fallback_chain(db, agent.model_fallback_list)
        for entry in fallback_chain or []:
            if isinstance(entry, dict):
                collect_url_userinfo(entry.get("base_url"), secrets)
    except Exception:  # noqa: BLE001
        logger.debug("secret-collect fallback chain skipped for %s", conversation.id, exc_info=True)

    try:
        tools_config = await build_tools_config(agent, db=db, conversation_id=str(conversation.id))
        for tool_config in tools_config or []:
            if not isinstance(tool_config, dict):
                continue
            secrets |= collect_secret_values(tool_config.get("credentials"))
            secrets |= collect_secret_values(tool_config.get("mcp_transport_headers"))
            collect_url_userinfo(tool_config.get("url"), secrets)
    except Exception:  # noqa: BLE001
        logger.debug("secret-collect tools_config skipped for %s", conversation.id, exc_info=True)

    return secrets


def _redact_response_tool_calls(
    responses: list[MessageResponse],
    *,
    secret_values: Sequence[str] | None = None,
) -> None:
    # ADR-021 C2 — this runs in a GET /messages request (no active run /
    # ContextVar), so the run's secrets are passed explicitly by the caller.
    for response in responses:
        if not response.tool_calls:
            continue
        redacted = redact_protocol_data(
            "messages", response.tool_calls, secret_values=secret_values
        )
        if isinstance(redacted, list) and all(isinstance(item, dict) for item in redacted):
            response.tool_calls = redacted


# ---------------------------------------------------------------------------
# Conversations CRUD
# ---------------------------------------------------------------------------


async def list_conversations(db: AsyncSession, agent_id: uuid.UUID) -> list[Conversation]:
    result = await db.execute(
        select(Conversation)
        .where(Conversation.agent_id == agent_id, Conversation.source == "ui")
        .order_by(Conversation.is_pinned.desc(), Conversation.updated_at.desc())
    )
    return list(result.scalars().all())


async def is_agent_owned_by_user(db: AsyncSession, agent_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    result = await db.execute(
        select(Agent.id).where(Agent.id == agent_id, Agent.user_id == user_id).limit(1)
    )
    return result.scalar_one_or_none() is not None


ConversationCursorScope = Literal["agent", "global"]


@dataclass(frozen=True, slots=True)
class ConversationPageCursor:
    scope: ConversationCursorScope
    sort: ConversationSort
    timestamp: datetime
    id: uuid.UUID
    is_pinned: bool | None = None


def _escape_like(term: str) -> str:
    """LIKE 메타문자(``\\``, ``%``, ``_``)를 리터럴로 이스케이프한다 (escape="\\\\"와 짝)."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _conversation_sort_column(sort: ConversationSort) -> InstrumentedAttribute[datetime]:
    match sort:
        case "updated":
            return Conversation.updated_at
        case "created":
            return Conversation.created_at
        case unreachable:
            assert_never(unreachable)


def _conversation_sort_value(conversation: Conversation, sort: ConversationSort) -> datetime:
    match sort:
        case "updated":
            return conversation.updated_at
        case "created":
            return conversation.created_at
        case unreachable:
            assert_never(unreachable)


def _encode_conversation_cursor(
    conversation: Conversation,
    *,
    scope: ConversationCursorScope,
    sort: ConversationSort,
) -> str:
    payload = {
        "scope": scope,
        "sort": sort,
        "timestamp": _conversation_sort_value(conversation, sort).isoformat(),
        "id": str(conversation.id),
    }
    if scope == "agent":
        payload["is_pinned"] = bool(conversation.is_pinned)
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_conversation_cursor(
    cursor: str,
    *,
    expected_scope: ConversationCursorScope,
    expected_sort: ConversationSort,
) -> ConversationPageCursor:
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        scope = payload["scope"]
        sort = payload["sort"]
        if scope != expected_scope or sort != expected_sort:
            raise ValueError("conversation cursor scope or sort mismatch")
        is_pinned = bool(payload["is_pinned"]) if scope == "agent" else None
        timestamp = datetime.fromisoformat(str(payload["timestamp"]))
        if timestamp.tzinfo is not None:
            # DB 컬럼은 naive UTC — aware 커서는 UTC로 환산 후 naive로 정규화
            timestamp = timestamp.astimezone(UTC).replace(tzinfo=None)
        return ConversationPageCursor(
            scope=expected_scope,
            sort=expected_sort,
            timestamp=timestamp,
            id=uuid.UUID(str(payload["id"])),
            is_pinned=is_pinned,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid conversation cursor") from exc


async def list_conversations_page(
    db: AsyncSession,
    agent_id: uuid.UUID,
    *,
    limit: int,
    cursor: str | None = None,
    q: str | None = None,
    sort: ConversationSort = "updated",
) -> tuple[list[Conversation], str | None, bool]:
    timestamp_column = _conversation_sort_column(sort)
    query = select(Conversation).where(
        Conversation.agent_id == agent_id,
        Conversation.source == "ui",
    )

    search = (q or "").strip()
    if search:
        query = query.where(
            func.lower(func.coalesce(Conversation.title, "")).like(
                f"%{_escape_like(search.lower())}%", escape="\\"
            )
        )

    if cursor:
        page_cursor = _decode_conversation_cursor(
            cursor,
            expected_scope="agent",
            expected_sort=sort,
        )
        if page_cursor.is_pinned is None:
            raise ValueError("agent conversation cursor missing pin state")
        same_bucket_after = and_(
            Conversation.is_pinned == page_cursor.is_pinned,
            or_(
                timestamp_column < page_cursor.timestamp,
                and_(timestamp_column == page_cursor.timestamp, Conversation.id < page_cursor.id),
            ),
        )
        if page_cursor.is_pinned:
            query = query.where(or_(Conversation.is_pinned.is_(False), same_bucket_after))
        else:
            query = query.where(same_bucket_after)

    result = await db.execute(
        query.order_by(
            Conversation.is_pinned.desc(),
            timestamp_column.desc(),
            Conversation.id.desc(),
        ).limit(limit + 1)
    )
    rows = list(result.scalars().all())
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = (
        _encode_conversation_cursor(items[-1], scope="agent", sort=sort)
        if has_more and items
        else None
    )
    return items, next_cursor, has_more


async def list_global_conversations_page(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    limit: int,
    cursor: str | None = None,
    q: str | None = None,
    sort: ConversationSort = "updated",
) -> tuple[list[Conversation], str | None, bool]:
    timestamp_column = _conversation_sort_column(sort)
    query = (
        select(Conversation)
        .join(Agent, Conversation.agent_id == Agent.id)
        .where(Agent.user_id == user_id, Conversation.source == "ui")
        .options(contains_eager(Conversation.agent))
    )

    search = (q or "").strip()
    if search:
        query = query.where(
            func.lower(func.coalesce(Conversation.title, "")).like(
                f"%{_escape_like(search.lower())}%", escape="\\"
            )
        )

    if cursor:
        page_cursor = _decode_conversation_cursor(
            cursor,
            expected_scope="global",
            expected_sort=sort,
        )
        query = query.where(
            or_(
                timestamp_column < page_cursor.timestamp,
                and_(timestamp_column == page_cursor.timestamp, Conversation.id < page_cursor.id),
            )
        )

    result = await db.execute(
        query.order_by(timestamp_column.desc(), Conversation.id.desc()).limit(limit + 1)
    )
    rows = list(result.unique().scalars().all())
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = (
        _encode_conversation_cursor(items[-1], scope="global", sort=sort)
        if has_more and items
        else None
    )
    return items, next_cursor, has_more


async def create_conversation(
    db: AsyncSession,
    agent_id: uuid.UUID,
    title: str | None = None,
    *,
    source: str = "ui",
) -> Conversation:
    conv = Conversation(agent_id=agent_id, title=title or "새 대화", source=source)
    db.add(conv)
    await db.flush()
    await db.refresh(conv)
    return conv


async def promote_draft_conversation(
    db: AsyncSession,
    conv: Conversation,
    *,
    title_from_content: str | None = None,
) -> Conversation:
    if conv.source != "draft":
        return conv
    conv.source = "ui"
    if title_from_content:
        conv.title = conversation_title_from_content(title_from_content)
    await db.flush()
    await db.refresh(conv)
    return conv


async def gc_orphan_draft_conversations(db: AsyncSession, *, retention_hours: int) -> int:
    """Delete abandoned, message-less draft conversations past the cutoff.

    A draft (``source == "draft"``) is created by ``POST
    .../conversations/draft`` and only flips to ``"ui"`` via
    :func:`promote_draft_conversation` when the user sends a first message.
    A draft abandoned before sending is invisible to the UI (the list filters
    ``source == "ui"``) and never deleted, so empty drafts accumulate.

    This removes rows that are **both**:

    * still ``source == "draft"`` (never promoted — promotion is the
      first-message signal, so a non-draft row is never touched), AND
    * message-less: no ``message_events`` turn row exists for the
      conversation (the ORM-visible proxy for a recorded assistant turn).

    The age check uses ``created_at`` (naive UTC, matching the column) so a
    just-opened draft the user is still typing into is never collected. Child
    rows (message_events, attachments, runs, share links, ...) are all
    ``ON DELETE CASCADE``, so the row delete is self-contained. Commits the
    transaction so the cron caller doesn't have to manage one. Returns the
    number of drafts deleted.
    """

    # Reject (rather than clamp) a non-positive retention. ``retention_hours == 0``
    # sets ``cutoff = now`` and would delete a draft the user opened moments ago (still
    # typing), so a mis-set ``0`` must surface loudly as a config error instead of
    # silently destroying live drafts or silently substituting a value the operator
    # never chose.
    if retention_hours <= 0:
        raise ValueError(f"retention_hours must be >= 1, got {retention_hours}")

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=retention_hours)
    has_event = (
        select(MessageEvent.id).where(MessageEvent.conversation_id == Conversation.id).exists()
    )
    result = await db.execute(
        delete(Conversation).where(
            Conversation.source == "draft",
            Conversation.created_at < cutoff,
            ~has_event,
        )
    )
    await db.commit()
    deleted = int(getattr(result, "rowcount", 0) or 0)
    if deleted:
        logger.info(
            "Draft conversation GC: deleted %d orphan draft(s) older than %s",
            deleted,
            cutoff.isoformat(),
        )
    return deleted


def _unlink_paths(paths: Sequence[str]) -> None:
    """Best-effort delete of stored upload files (runs off the event loop)."""

    from pathlib import Path

    for raw in paths:
        try:
            Path(raw).unlink(missing_ok=True)
        except OSError:
            logger.warning("orphan attachment file delete failed: %s", raw, exc_info=True)


async def gc_orphan_attachments(db: AsyncSession, *, retention_hours: int) -> int:
    """Delete never-sent uploads (orphan ``message_attachments``) past the cutoff.

    ``POST /api/uploads`` creates a row with ``message_id IS NULL``; it is
    stamped with the user's message id at turn finalize (M1). A row whose
    ``message_id`` is still NULL after ``retention_hours`` was uploaded but
    never sent (composer abandoned) — invisible to every read path and never
    cleaned up, so both the DB row and its on-disk blob accumulate.

    Removes rows that are **both** ``message_id IS NULL`` AND older than the
    cutoff. The on-disk file is unlinked first (best-effort, off the event
    loop) so a delete failure can't strand bytes after the row is gone.
    Commits so the cron caller doesn't manage a transaction. Returns the
    number of orphan uploads deleted.
    """

    import asyncio

    from app.models.message_attachment import MessageAttachment

    # Reject (not clamp) a non-positive retention — ``0`` sets ``cutoff = now``
    # and would reap an upload the user just staged but hasn't sent yet. A
    # mis-set value must surface as a config error, not silently destroy data.
    if retention_hours <= 0:
        raise ValueError(f"retention_hours must be >= 1, got {retention_hours}")

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=retention_hours)
    result = await db.execute(
        select(MessageAttachment).where(
            MessageAttachment.message_id.is_(None),
            MessageAttachment.created_at < cutoff,
        )
    )
    orphans = list(result.scalars().all())
    if not orphans:
        return 0

    await asyncio.to_thread(_unlink_paths, [att.storage_path for att in orphans])

    ids = [att.id for att in orphans]
    await db.execute(delete(MessageAttachment).where(MessageAttachment.id.in_(ids)))
    await db.commit()
    logger.info(
        "Orphan attachment GC: deleted %d never-sent upload(s) older than %s",
        len(ids),
        cutoff.isoformat(),
    )
    return len(ids)


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


async def get_owned_ui_conversation_with_agent(
    db: AsyncSession, conversation_id: uuid.UUID, user_id: uuid.UUID
) -> Conversation | None:
    result = await db.execute(
        select(Conversation)
        .join(Agent, Conversation.agent_id == Agent.id)
        .where(
            Conversation.id == conversation_id,
            Conversation.source == "ui",
            Agent.user_id == user_id,
        )
        .options(contains_eager(Conversation.agent))
    )
    return result.unique().scalar_one_or_none()


async def update_conversation(
    db: AsyncSession, conv: Conversation, data: ConversationUpdate
) -> Conversation:
    if data.title is not None:
        conv.title = data.title
    if data.is_pinned is not None:
        conv.is_pinned = data.is_pinned
    await db.flush()
    await db.refresh(conv)
    return conv


async def mark_conversation_read(db: AsyncSession, conv: Conversation) -> Conversation:
    conv.unread_count = 0
    conv.last_read_at = datetime.now(UTC).replace(tzinfo=None)
    await db.flush()
    await db.refresh(conv)
    return conv


async def delete_conversation(db: AsyncSession, conv: Conversation) -> None:
    from app.agent_runtime.checkpointer import delete_thread

    await delete_thread(str(conv.id))
    await db.delete(conv)
    await db.flush()


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

    stored_timestamps: dict[str, str] = dict(conversation.message_timestamps or {})
    timestamps: list[datetime] = []
    fallback_base = conversation.created_at

    for idx, msg in enumerate(messages):
        msg_uuid = parse_msg_id(getattr(msg, "id", None), conversation.id, idx)
        key = str(msg_uuid)
        iso = stored_timestamps.get(key)
        ts = datetime.fromisoformat(iso) if iso else fallback_base + timedelta(milliseconds=idx)
        timestamps.append(ts)

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
        resp.siblings = [_sibling_uuid(s.message_id, idx) for s in sibling_entries]
        resp.sibling_checkpoint_ids = [s.checkpoint_id for s in sibling_entries]
        resp.branch_index = node.branch_index
        resp.branch_total = node.branch_total

    if user_id is not None:
        await _hydrate_pending_interrupt_tool_calls(
            db,
            conversation_id=conversation.id,
            responses=responses,
        )
    secrets = tuple(await collect_conversation_secret_values(db, conversation))
    _redact_response_tool_calls(responses, secret_values=secrets)

    # Hydrate per-message feedback (current user) + attachments/artifacts. Wrapped in
    # broad try/except so a missing migration (m27/m28 not yet applied) or
    # any other query glitch degrades gracefully — the message list still
    # renders, just without the side-channel metadata.
    feedback_by_msg: dict[str, str] = {}
    attachments_by_msg: dict[str, list[MessageAttachmentBrief]] = {}
    artifacts_by_msg: dict[str, list[Any]] = {}

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

    if user_id is not None:
        try:
            from app.services.artifact_service import list_conversation_artifacts_by_message_id

            artifacts_by_msg = await list_conversation_artifacts_by_message_id(
                db,
                user_id=user_id,
                conversation_id=conversation.id,
            )
        except Exception:  # noqa: BLE001 — non-critical hydration
            logger.warning(
                "artifact hydrate failed for conversation %s — skipping",
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
        artifacts = artifacts_by_msg.get(mid)
        if artifacts:
            resp.artifacts = artifacts

    return responses


async def maybe_set_auto_title(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    content: str,
) -> None:
    title = conversation_title_from_content(content)
    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id, Conversation.title == "새 대화")
        .values(title=title)
    )
    await db.flush()


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

    ``message_id`` stays null at send time — LangGraph assigns the user
    HumanMessage id only inside the run. It is backfilled at turn finalize by
    :func:`link_attachments_to_message` (M1) so reads can echo the attachment on
    the right user bubble.
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
    await db.flush()


async def resolve_turn_user_message_id(
    db: AsyncSession,
    conversation: Conversation,
    *,
    tree: Any = None,
) -> str | None:
    """Resolve THIS turn's user message id as the read path will compute it (M1).

    A sent upload's ``message_attachments.message_id`` must equal the id that
    :func:`list_messages_from_checkpointer` will later key attachment hydration
    on — i.e. ``str(parse_msg_id(msg.id, conversation.id, idx))`` for the user
    message. We reproduce **the exact same tree walk and enumeration** that the
    read path uses (``messages = [node.message for node in tree.nodes]`` →
    ``enumerate``), then take the **last** ``human`` message: the assistant's
    reply never appends a HumanMessage, so the last human in the active chain is
    always the message the user just sent. This holds across multi-turn /
    branch / HiTL-interrupt because the active chain is rebuilt each time.

    ``msg_id_sink`` carries **AI** message ids only (streaming only sinks
    ``ai``/``AIMessageChunk``), so the user id is never available there — it
    must be derived from the post-run checkpoint, never assumed at ``idx=0``.

    Returns the id as a string, or ``None`` if there is no user message (e.g.
    an empty/garbage checkpoint). ``db`` is unused today but kept in the
    signature so a future pricing/identity lookup needn't change call sites.
    """

    from app.agent_runtime.message_utils import parse_msg_id

    if tree is None:
        from app.agent_runtime.checkpointer import get_checkpointer
        from app.services.thread_branch_service import build_message_tree

        tree = await build_message_tree(
            get_checkpointer(),
            str(conversation.id),
            active_checkpoint_id=conversation.active_branch_checkpoint_id,
        )

    if not tree.nodes:
        return None

    messages = [node.message for node in tree.nodes]
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if getattr(msg, "type", None) == "human":
            return str(parse_msg_id(getattr(msg, "id", None), conversation.id, idx))
    return None


async def link_attachments_to_message(
    db: AsyncSession,
    *,
    attachment_ids: list[uuid.UUID],
    message_id: str,
) -> int:
    """Backfill ``message_attachments.message_id`` for this send's uploads (M1).

    Only rows whose ``message_id`` is still NULL are stamped, so a stale orphan
    from an earlier turn whose finalize failed can't be mis-attached to this
    turn's user message (cross-send mis-link guard). Returns the rows updated.
    """

    if not attachment_ids:
        return 0
    from app.models.message_attachment import MessageAttachment

    result = await db.execute(
        update(MessageAttachment)
        .where(
            MessageAttachment.id.in_(attachment_ids),
            MessageAttachment.message_id.is_(None),
        )
        .values(message_id=message_id)
    )
    await db.flush()
    return int(getattr(result, "rowcount", 0) or 0)


async def touch_conversation(db: AsyncSession, conversation_id: uuid.UUID) -> None:
    """Bump ``conversation.updated_at`` to anchor message-list timestamps."""

    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(
            updated_at=datetime.now(UTC).replace(tzinfo=None),
            last_activity_source="user",
        )
    )
    await db.flush()


async def clear_active_branch_override(db: AsyncSession, conversation_id: uuid.UUID) -> None:
    """Reset ``active_branch_checkpoint_id`` so the next list call falls back
    to the newest leaf — used after edit/regenerate where the new branch is
    the most recent and should automatically become active."""

    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(active_branch_checkpoint_id=None)
    )
    await db.flush()


# ---------------------------------------------------------------------------
# Agent context assembly (single-path, greenfield)
# ---------------------------------------------------------------------------


def _agent_runtime_load_options() -> list[Any]:
    child_agent = AgentSubAgentLink.sub_agent
    return [
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
        selectinload(Agent.sub_agent_links)
        .joinedload(child_agent)
        .options(
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
        ),
    ]


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

    ``Model.default_credential`` 관계는 의도적으로 chain 에서 제외한다 —
    ``credential_resolution`` 이 FK (``default_credential_id``) 만 읽고 tier 2
    fallback 시 ownership 검증을 위해 ``credential_service.get_for_user`` 로
    별도 fetch 하므로, eager-load 결과는 사용처가 없다.

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
                *_agent_runtime_load_options(),
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
        .options(*_agent_runtime_load_options())
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
    identity: AgentRunIdentity | None = None,
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
    credential_cache: dict[uuid.UUID, dict[str, Any] | None] = {}
    credential_subject_user_id = (
        identity.credential_subject_user_id if identity is not None else agent.user_id
    )
    runtime_actor_user_id = (
        identity.caller_user_id or identity.agent_owner_user_id
        if identity is not None
        else agent.user_id
    )

    async def decrypt_cached(credential: Any) -> dict[str, Any] | None:
        if (
            credential.user_id != credential_subject_user_id
            or bool(getattr(credential, "is_system", False)) is True
        ):
            raise ValidationError(
                "CREDENTIAL_SUBJECT_MISMATCH",
                "credential is not available for this agent run identity",
            )
        cached = credential_cache.get(credential.id)
        if credential.id in credential_cache:
            return cached
        try:
            cached = await credential_service.decrypt_with_external(credential.data_encrypted)
        except Exception:  # noqa: BLE001 — surface as missing creds, never crash chat
            logger.exception(
                "credential decryption failed for credential %s",
                credential.id,
            )
            cached = None
        credential_cache[credential.id] = cached
        return cached

    for link in agent.tool_links:
        tool = link.tool
        if tool is None or not tool.enabled:
            continue

        credentials: dict[str, Any] | None = None
        credential = getattr(tool, "credential", None)
        if credential is not None:
            credentials = await decrypt_cached(credential)

        configs.append(
            {
                "tool_id": str(tool.id),
                "definition_key": tool.definition_key,
                "name": tool.name,
                "description": tool.description,
                "parameters": dict(tool.parameters or {}),
                "credentials": credentials,
                "credential_id": (str(tool.credential_id) if tool.credential_id else None),
                # Hook-framework correlation — wire down to ``tool_factory``.
                "user_id": str(runtime_actor_user_id),
                "agent_id": str(agent.id),
                "credential_subject_user_id": str(credential_subject_user_id),
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
        mcp_headers: dict[str, str] = dict(server.headers or {})
        if server.credential is not None:
            if db is not None:
                resolved_auth = await resolve_mcp_auth(
                    db,
                    credential_id=server.credential_id,
                    user_id=credential_subject_user_id,
                    static_headers=dict(server.headers or {}),
                )
                if resolved_auth.error:
                    if resolved_auth.status == "credential_not_found":
                        raise ValidationError(
                            "CREDENTIAL_SUBJECT_MISMATCH",
                            "credential is not available for this agent run identity",
                        )
                    raise ValidationError(
                        "MCP_CREDENTIAL_AUTH_NEEDED",
                        resolved_auth.error,
                    )
                mcp_credentials = resolved_auth.credentials
                mcp_headers = resolved_auth.headers
            else:
                mcp_credentials = await decrypt_cached(server.credential)
                mcp_headers = build_headers(dict(server.headers or {}), mcp_credentials)

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
                "mcp_transport_headers": mcp_headers,
                "credentials": mcp_credentials,
                "user_id": str(runtime_actor_user_id),
                "agent_id": str(agent.id),
                "credential_subject_user_id": str(credential_subject_user_id),
            }
        )

    return configs


async def trigger_blocked_tools_for_agent_tree(
    agent: Agent,
    *,
    db: AsyncSession,
) -> list[Any]:
    """Return trigger-unsafe capabilities for parent plus one-hop children."""

    from app.tools.risk import trigger_blocked_tools

    blocked = trigger_blocked_tools(
        await build_tools_config(agent, db=db, conversation_id=None),
        has_agent_skills=bool(build_agent_skills(agent)),
    )
    for link in agent.sub_agent_links:
        child = link.sub_agent
        if child is None:
            continue
        child_tools_config = await build_tools_config(
            child,
            db=db,
            conversation_id=None,
        )
        blocked.extend(
            trigger_blocked_tools(
                child_tools_config,
                has_agent_skills=bool(build_agent_skills(child)),
            )
        )
    return blocked
