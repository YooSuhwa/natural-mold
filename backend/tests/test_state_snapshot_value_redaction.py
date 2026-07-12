"""ADR-021 C2 — value-based redaction in the state-snapshot endpoint path.

The state / messages endpoints are plain HTTP GETs that run OUTSIDE any agent
run, so the run-scoped secret ContextVar is never set there. Before C2,
``load_thread_state_snapshot`` called ``redact_protocol_data`` with no
``secret_values`` and (since the ContextVar was ``None``) value-based masking
was a no-op — an opaque tool credential echoed into a persisted message
leaked through the state API verbatim.

These tests drive the real ``load_thread_state_snapshot`` (with a fake
checkpointer, mirroring the existing hydration tests) and assert that:

* passing ``secret_values`` explicitly masks the opaque secret (C2 behaviour),
* NOT passing it leaves the opaque secret intact — which is precisely the
  pre-C2 leak, and what proves the endpoint must thread the secret set in.

The opaque secret (``Zt4hWp7QmD2sLx9KbearerlessVAL``) carries no sensitive key
and matches no value heuristic, so only value-based replacement can mask it.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any, NamedTuple

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.model import Model
from app.models.tool import AgentToolLink, Tool
from app.models.user import User
from app.routers.conversation_agent_protocol_state_snapshot import (
    collect_state_secret_values,
    load_thread_state_snapshot,
    serialize_langchain_message,
)
from app.services.thread_branch_service import _CheckpointSlim
from tests.conftest import TEST_USER_ID

# Opaque, no sensitive-key prefix, not Bearer/sk-/JWT/DSN -> heuristics can't
# touch it; only value-based masking of the run's actual secret will.
OPAQUE_SECRET = "Zt4hWp7QmD2sLx9KbearerlessVAL"


class _FakeCheckpointer:
    def __init__(
        self,
        checkpoints: list[_CheckpointSlim],
        *,
        values_by_checkpoint: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._checkpoints = checkpoints
        self._values_by_checkpoint = values_by_checkpoint or {}

    async def alist(self, _config: Any) -> AsyncIterator[Any]:
        for checkpoint in self._checkpoints:
            channel_values = dict(self._values_by_checkpoint.get(checkpoint.checkpoint_id, {}))
            channel_values.setdefault("messages", checkpoint.messages)
            yield type(
                "CheckpointTuple",
                (),
                {
                    "config": {"configurable": {"checkpoint_id": checkpoint.checkpoint_id}},
                    "parent_config": (
                        {"configurable": {"checkpoint_id": checkpoint.parent_checkpoint_id}}
                        if checkpoint.parent_checkpoint_id
                        else None
                    ),
                    "checkpoint": {"channel_values": channel_values},
                },
            )()

    async def aget_tuple(self, _config: Any) -> Any:
        configurable = _config.get("configurable") if isinstance(_config, dict) else {}
        checkpoint_id = (
            configurable.get("checkpoint_id") if isinstance(configurable, dict) else None
        )
        if isinstance(checkpoint_id, str):
            for checkpoint in self._checkpoints:
                if checkpoint.checkpoint_id != checkpoint_id:
                    continue
                channel_values = dict(self._values_by_checkpoint.get(checkpoint_id, {}))
                channel_values.setdefault("messages", checkpoint.messages)
                return type(
                    "CheckpointTuple",
                    (),
                    {
                        "config": {"configurable": {"checkpoint_id": checkpoint_id}},
                        "checkpoint": {"channel_values": channel_values},
                        "pending_writes": [],
                    },
                )()
        return type("CheckpointTuple", (), {"pending_writes": []})()


async def _seed_conversation(db: AsyncSession) -> Conversation:
    user = await db.get(User, TEST_USER_ID)
    if user is None:
        user = User(id=TEST_USER_ID, email="state-redact@test.dev", name="State Redact")
        db.add(user)
    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add(model)
    await db.flush()
    agent = Agent(
        user_id=user.id,
        name="State Redact Agent",
        system_prompt="You are helpful.",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()
    conversation = Conversation(agent_id=agent.id, title="State Redact Conversation")
    db.add(conversation)
    await db.commit()
    return conversation


def _leaky_checkpoint() -> _CheckpointSlim:
    # An assistant turn that echoed a tool credential in plain prose.
    return _CheckpointSlim(
        checkpoint_id="ck-leaf",
        parent_checkpoint_id=None,
        messages=[
            HumanMessage(id="user-1", content="look it up"),
            AIMessage(
                id="assistant-1",
                content=f"the API returned {OPAQUE_SECRET} for your account",
            ),
        ],
    )


@pytest.mark.asyncio
async def test_state_snapshot_masks_secret_when_passed(monkeypatch, db: AsyncSession) -> None:
    conversation = await _seed_conversation(db)
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol_state_snapshot.get_checkpointer",
        lambda: _FakeCheckpointer([_leaky_checkpoint()]),
    )

    snapshot = await load_thread_state_snapshot(
        conversation,
        db=db,
        secret_values={OPAQUE_SECRET},
    )

    rendered = json.dumps(snapshot.values)
    assert OPAQUE_SECRET not in rendered, "opaque secret leaked through state snapshot"
    assert "<redacted>" in rendered


@pytest.mark.asyncio
async def test_state_snapshot_leaks_without_secret_values(monkeypatch, db: AsyncSession) -> None:
    """Pre-C2 behaviour: no run secrets + no ContextVar -> opaque secret leaks.

    This pins WHY the endpoint must collect and pass ``secret_values`` — the
    heuristics alone cannot mask an opaque, key-less value.
    """

    conversation = await _seed_conversation(db)
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol_state_snapshot.get_checkpointer",
        lambda: _FakeCheckpointer([_leaky_checkpoint()]),
    )

    snapshot = await load_thread_state_snapshot(conversation, db=db)  # no secret_values

    assert OPAQUE_SECRET in json.dumps(snapshot.values), (
        "without secret_values the opaque value must survive (heuristics can't "
        "catch it) — proving value-based masking is the real defence"
    )


def test_serialize_langchain_message_masks_secret_value() -> None:
    msg = AIMessage(id="m-1", content=f"the API returned {OPAQUE_SECRET} for your account")
    payload = serialize_langchain_message(msg, secret_values={OPAQUE_SECRET})
    assert OPAQUE_SECRET not in json.dumps(payload)
    assert "<redacted>" in json.dumps(payload)

    # And without the secret set, it is left intact (heuristics can't catch it).
    leaked = serialize_langchain_message(msg)
    assert OPAQUE_SECRET in json.dumps(leaked)


@pytest.mark.asyncio
async def test_collect_conversation_secrets_includes_fallback_base_url_userinfo(
    db: AsyncSession,
) -> None:
    """ADR-021 re-review #1 — the shared read-path collector must cover fallback
    model ``base_url`` userinfo. The live run masks it (collect_cfg_secret_values
    walks the fallback chain), so a read/poll path that skipped it would
    under-mask a credential embedded in a self-hosted fallback endpoint.
    """

    from app.services.chat_service import collect_conversation_secret_values

    user = await db.get(User, TEST_USER_ID)
    if user is None:
        user = User(id=TEST_USER_ID, email="fb-redact@test.dev", name="FB Redact")
        db.add(user)
    primary = Model(provider="openai", model_name="gpt-4o", display_name="Primary")
    fallback = Model(
        provider="openai_compatible",
        model_name="local-llm",
        display_name="Fallback",
        base_url="https://fbuser:fallbackpw99999@self-hosted.example/v1",
    )
    db.add_all([primary, fallback])
    await db.flush()
    agent = Agent(
        user_id=user.id,
        name="FB Agent",
        system_prompt="You are helpful.",
        model_id=primary.id,
        model_fallback_list=[str(fallback.id)],
    )
    db.add(agent)
    await db.flush()
    conversation = Conversation(agent_id=agent.id, title="FB Conversation")
    db.add(conversation)
    await db.commit()

    secrets = await collect_conversation_secret_values(db, conversation)

    assert "fallbackpw99999" in secrets, "fallback base_url password must be collected"
    assert "fbuser" in secrets


# ---------------------------------------------------------------------------
# collect_conversation_secret_values — one assert per collection source, so a
# partial-source-removal mutation (e.g. dropping only the tool-credentials
# block) fails exactly the test that pins that source instead of slipping
# through a single-source assertion (under-masking leak).
# ---------------------------------------------------------------------------


class _SeededSecretSources(NamedTuple):
    conversation: Conversation
    llm_api_key: str
    tool_credential_secret: str
    mcp_header_token: str
    base_url_password: str


async def _seed_conversation_with_secret_sources(db: AsyncSession) -> _SeededSecretSources:
    """Seed one agent wiring every collection source with a unique sentinel.

    Self-contained on purpose: unique names/values per call so the tests
    never depend on global state (xdist flake guard — see the fallback test's
    Model-lookup history). Sources:

    * LLM ``api_key`` via ``agent.llm_credential`` (tier-1 resolution),
    * tool config plaintext ``credentials`` (Tool → Credential),
    * MCP ``mcp_transport_headers`` (static server headers, no credential),
    * primary model ``base_url`` userinfo.
    """

    unique = uuid.uuid4().hex[:10]
    llm_api_key = f"sk-llm-{uuid.uuid4().hex}"
    tool_credential_secret = f"Zq{uuid.uuid4().hex[:16]}ToolVal"
    mcp_header_token = f"Zq{uuid.uuid4().hex[:16]}McpVal"
    base_url_password = f"primarypw{uuid.uuid4().hex[:12]}"

    user = await db.get(User, TEST_USER_ID)
    if user is None:
        user = User(id=TEST_USER_ID, email=f"collect-{unique}@test.dev", name="Collect")
        db.add(user)
        await db.flush()

    llm_cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="openai",
        name=f"collect-llm-{unique}",
        data={"api_key": llm_api_key},
    )
    tool_cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="naver_search",
        name=f"collect-naver-{unique}",
        data={"client_id": "nv-client-id", "client_secret": tool_credential_secret},
    )

    model = Model(
        provider="openai_compatible",
        model_name=f"local-llm-{unique}",
        display_name="Collect Primary",
        base_url=f"https://primaryuser:{base_url_password}@self-hosted.example/v1",
    )
    db.add(model)
    await db.flush()

    agent = Agent(
        user_id=TEST_USER_ID,
        name=f"Collect Agent {unique}",
        system_prompt="You are helpful.",
        model_id=model.id,
        llm_credential_id=llm_cred.id,
    )
    db.add(agent)
    await db.flush()

    tool = Tool(
        user_id=TEST_USER_ID,
        name=f"Collect Naver Blog {unique}",
        definition_key="naver_search_blog",
        parameters={},
        credential_id=tool_cred.id,
    )
    db.add(tool)
    await db.flush()
    db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id))

    server = McpServer(
        user_id=TEST_USER_ID,
        name=f"Collect MCP {unique}",
        transport="streamable_http",
        url="http://localhost:18010/mcp",
        headers={"X-Api-Token": mcp_header_token},
        env_vars={},
    )
    db.add(server)
    await db.flush()
    mcp_tool = McpTool(
        server_id=server.id,
        name=f"get_thing_{unique}",
        description="thing",
        input_schema={"type": "object"},
        enabled=True,
    )
    db.add(mcp_tool)
    await db.flush()
    db.add(AgentMcpToolLink(agent_id=agent.id, mcp_tool_id=mcp_tool.id))

    conversation = Conversation(agent_id=agent.id, title=f"Collect Conversation {unique}")
    db.add(conversation)
    await db.commit()
    return _SeededSecretSources(
        conversation=conversation,
        llm_api_key=llm_api_key,
        tool_credential_secret=tool_credential_secret,
        mcp_header_token=mcp_header_token,
        base_url_password=base_url_password,
    )


@pytest.mark.asyncio
async def test_collect_conversation_secrets_includes_llm_api_key(db: AsyncSession) -> None:
    """Source ①: the agent's resolved LLM ``api_key`` must be collected."""

    from app.services.chat_service import collect_conversation_secret_values

    seeded = await _seed_conversation_with_secret_sources(db)
    secrets = await collect_conversation_secret_values(db, seeded.conversation)
    assert seeded.llm_api_key in secrets, "LLM api_key must be collected"


@pytest.mark.asyncio
async def test_collect_conversation_secrets_includes_tool_credential_values(
    db: AsyncSession,
) -> None:
    """Source ②: each tool config's decrypted plaintext ``credentials``."""

    from app.services.chat_service import collect_conversation_secret_values

    seeded = await _seed_conversation_with_secret_sources(db)
    secrets = await collect_conversation_secret_values(db, seeded.conversation)
    assert seeded.tool_credential_secret in secrets, "tool credential values must be collected"


@pytest.mark.asyncio
async def test_collect_conversation_secrets_includes_mcp_transport_headers(
    db: AsyncSession,
) -> None:
    """Source ③: MCP transport header values (static server headers)."""

    from app.services.chat_service import collect_conversation_secret_values

    seeded = await _seed_conversation_with_secret_sources(db)
    secrets = await collect_conversation_secret_values(db, seeded.conversation)
    assert seeded.mcp_header_token in secrets, "MCP transport header values must be collected"


@pytest.mark.asyncio
async def test_collect_conversation_secrets_includes_model_base_url_userinfo(
    db: AsyncSession,
) -> None:
    """Source ④: userinfo embedded in the primary model's ``base_url``."""

    from app.services.chat_service import collect_conversation_secret_values

    seeded = await _seed_conversation_with_secret_sources(db)
    secrets = await collect_conversation_secret_values(db, seeded.conversation)
    assert seeded.base_url_password in secrets, "primary base_url password must be collected"
    assert "primaryuser" in secrets, "primary base_url username must be collected"


# ---------------------------------------------------------------------------
# End-to-end read-path lock: facade → collect → redact. Mirrors the
# GET /threads/{id}/state handler pair (conversation_agent_protocol.py):
# ``collect_state_secret_values`` (which imports through the
# ``app.services.chat_service`` facade) feeds ``load_thread_state_snapshot``.
# Catches BOTH a facade re-export removal (ImportError at call time) and a
# neutered collector (empty set → the sentinel leaks into the response).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thread_state_read_path_masks_collected_secret_end_to_end(
    monkeypatch, db: AsyncSession
) -> None:
    seeded = await _seed_conversation_with_secret_sources(db)
    conversation = seeded.conversation
    sentinel = seeded.tool_credential_secret

    # A persisted turn whose tool_call args echo the tool credential — opaque
    # value under an innocuous key, so key/value heuristics can't mask it.
    checkpoint = _CheckpointSlim(
        checkpoint_id="ck-leaf",
        parent_checkpoint_id=None,
        messages=[
            HumanMessage(id="user-1", content="call the tool"),
            AIMessage(
                id="assistant-1",
                content="calling the tool now",
                tool_calls=[
                    {
                        "name": "naver_search_blog",
                        "args": {"note": f"looked up {sentinel} for you"},
                        "id": "call-1",
                    }
                ],
            ),
        ],
    )
    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol_state_snapshot.get_checkpointer",
        lambda: _FakeCheckpointer([checkpoint]),
    )

    # Negative control: without the collected set the sentinel survives —
    # proving the masking assertion below can only pass via the
    # collect → redact chain (no heuristic tautology).
    unmasked = await load_thread_state_snapshot(conversation, db=db)
    assert sentinel in json.dumps(unmasked.values)

    collected = await collect_state_secret_values(db, conversation)
    snapshot = await load_thread_state_snapshot(conversation, db=db, secret_values=collected)

    rendered = json.dumps(snapshot.values)
    assert sentinel not in rendered, "collected tool secret leaked through the state read path"
    assert "<redacted>" in rendered
