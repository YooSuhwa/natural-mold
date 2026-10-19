"""Conversation secret collection + tool-call redaction.

BE-S1 split from ``app.services.chat_service`` — pure move, no behavior
change. Shared by the read/poll endpoints (GET /messages, thread state,
share render) per the ADR-021 C2 lightweight-collector contract.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.credential_access import AgentCredentialAccess
from app.agent_runtime.credential_resolution import resolve_llm_api_key_for_agent
from app.agent_runtime.identity import AgentRunSource, resolve_agent_run_identity
from app.agent_runtime.protocol_redaction import redact_protocol_data
from app.agent_runtime.run_secrets import collect_secret_values, collect_url_userinfo
from app.authz.conversation_scope import require_private_conversation
from app.authz.org_context import OrgContext
from app.authz.run_agent_scope import masking_scope
from app.authz.runtime_capability_scope import require_runtime_capability_tree
from app.authz.scoping import org_scoped
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.schemas.conversation import MessageResponse
from app.services.chat.runtime_context import _agent_runtime_load_options, build_tools_config

logger = logging.getLogger(__name__)


async def collect_conversation_secret_values(
    db: AsyncSession,
    conversation: Conversation,
    context: OrgContext,
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

    from app.services.conversation_stream_service import resolve_fallback_chain

    conversation = await require_private_conversation(db, conversation.id, context)
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
        with db.no_autoflush:
            result = await db.execute(
                org_scoped(select(Agent).where(Agent.id == agent_id), Agent, context)
                .options(*_agent_runtime_load_options())
                .execution_options(populate_existing=True, autoflush=False)
            )
            agent = result.unique().scalar_one_or_none()
    except Exception:  # noqa: BLE001 — read must not fail on secret-collect
        logger.debug("secret-collect agent load failed for %s", conversation.id, exc_info=True)
        return secrets
    if agent is None:
        return secrets
    identity = resolve_agent_run_identity(
        agent_id=agent.id,
        agent_owner_user_id=agent.user_id,
        runtime_name=agent.runtime_name,
        identity_mode=agent.identity_mode,
        source=AgentRunSource.CHAT,
        caller_user_id=context.user_id,
    )
    # The same agent scope as the run (F-05): a shared agent the caller may run masks its
    # LLM key, shared tool credentials and the caller's own connections too.
    scope = await masking_scope(agent, context, identity)
    if scope is None:
        return secrets
    if scope == "own":
        require_runtime_capability_tree(agent, context)
    # Each source is isolated: a failure in ONE (e.g. resolve_llm_api_key_for_agent
    # raises for an agent whose LLM credential was deleted) must NOT abort the
    # others, or tool/MCP/base_url secrets would silently go uncollected →
    # under-masking (ADR-021 re-review #1).
    try:
        api_key = await resolve_llm_api_key_for_agent(
            db, agent, AgentCredentialAccess(context, identity)
        )
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
        # read_only (BE-P3, 11.3): no decision, no resolve_mcp_auth (a SELECT … FOR
        # UPDATE row lock plus potential OAuth refresh WRITE) on this GET-polled path.
        # Stored values are decrypted as-is (mask what's stored; the live run path
        # collects refreshed tokens itself); the session only reads per-user
        # connections so their values are masked too.
        tools_config = await build_tools_config(
            agent,
            context=context,
            identity=identity,
            db=db,
            read_only=True,
            conversation_id=str(conversation.id),
        )
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
    """Redact every browser-facing message field after egress projection.

    The historical name is retained because ``chat_service`` exports this
    helper for compatibility.  GET/share paths call it after message-path
    projection, so content and tool arguments need the same explicit secret
    values outside a run-scoped ContextVar.
    """

    for response in responses:
        redacted_content = redact_protocol_data(
            "messages", response.content, secret_values=secret_values
        )
        if isinstance(redacted_content, str):
            response.content = redacted_content
        if response.tool_calls:
            redacted = redact_protocol_data(
                "messages", response.tool_calls, secret_values=secret_values
            )
            if isinstance(redacted, list) and all(isinstance(item, dict) for item in redacted):
                response.tool_calls = redacted
