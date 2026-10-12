from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.credentials.service import encrypt_data
from app.models.agent import Agent
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.model import Model
from app.models.skill import AgentSkillLink, Skill
from app.models.tool import AgentToolLink, Tool
from app.services.conversation_history_secrets import collect_strict_history_secrets
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession


def _credential(name: str, definition: str, payload: dict[str, str]) -> Credential:
    encrypted, key_id, field_keys = encrypt_data(payload)
    return Credential(
        id=uuid4(),
        tenant_id=TEST_TENANT_ID,
        org_id=TEST_ORG_ID,
        user_id=TEST_USER_ID,
        definition_key=definition,
        name=name,
        data_encrypted=encrypted,
        key_id=key_id,
        field_keys=field_keys,
        status="active",
        is_system=False,
    )


@pytest.mark.asyncio
async def test_archived_participant_closure_collects_runtime_secret_sources() -> None:
    provider_secret = "provider-matched-history-secret"
    child_secret = "child-llm-history-secret"
    tool_secret = "child-tool-history-secret"
    skill_secret = "child-skill-history-secret"
    mcp_secret = "child-mcp-history-secret"
    header_secret = "static-header-history-secret"
    proxy_header_secret = "proxy-header-history-secret"
    env_secret = "static-env-history-secret"
    model_id = uuid4()
    root_id = uuid4()
    child_id = uuid4()
    conversation_id = uuid4()
    run_id = uuid4()
    now = datetime.now(UTC)
    provider = _credential("Provider", "openai_compatible", {"api_key": provider_secret})
    child_llm = _credential("Child LLM", "anthropic", {"api_key": child_secret})
    tool_credential = _credential("Tool", "http_bearer", {"token": tool_secret})
    skill_credential = _credential("Skill", "http_bearer", {"token": skill_secret})
    mcp_credential = _credential("MCP", "mcp_secret", {"token": mcp_secret})

    async with TestSession() as db:
        db.add(
            Model(
                id=model_id,
                provider="openai_compatible",
                model_name="history-model",
                display_name="History model",
                is_default=False,
                is_visible=False,
                source="manual",
            )
        )
        db.add_all([provider, child_llm, tool_credential, skill_credential, mcp_credential])
        db.add_all(
            [
                Agent(
                    id=root_id,
                    tenant_id=TEST_TENANT_ID,
                    org_id=TEST_ORG_ID,
                    user_id=TEST_USER_ID,
                    name="History root",
                    system_prompt="root",
                    model_id=model_id,
                    archived_at=now,
                    status="active",
                ),
                Agent(
                    id=child_id,
                    tenant_id=TEST_TENANT_ID,
                    org_id=TEST_ORG_ID,
                    user_id=TEST_USER_ID,
                    name="History child",
                    system_prompt="child",
                    model_id=model_id,
                    llm_credential_id=child_llm.id,
                    archived_at=now,
                    status="active",
                ),
            ]
        )
        db.add(
            Conversation(
                id=conversation_id,
                tenant_id=TEST_TENANT_ID,
                org_id=TEST_ORG_ID,
                agent_id=root_id,
                user_id=TEST_USER_ID,
                agent_name_snapshot="History root",
                agent_lifecycle_state="archived",
                agent_archived_at_snapshot=now,
                last_activity_at=now,
            )
        )
        db.add(
            ConversationRun(
                id=run_id,
                conversation_id=conversation_id,
                agent_id=root_id,
                user_id=TEST_USER_ID,
                source="chat",
                status="completed",
                is_active=False,
            )
        )
        db.add_all(
            [
                ConversationRunAgent(
                    run_id=run_id,
                    agent_id=root_id,
                    agent_id_snapshot=root_id,
                    role="root",
                    path=[],
                ),
                ConversationRunAgent(
                    run_id=run_id,
                    agent_id=child_id,
                    agent_id_snapshot=child_id,
                    role="subagent",
                    path=[0],
                ),
            ]
        )
        tool = Tool(
            id=uuid4(),
            tenant_id=TEST_TENANT_ID,
            org_id=TEST_ORG_ID,
            user_id=TEST_USER_ID,
            is_system=False,
            definition_key="http_request",
            name="History tool",
            parameters={"literal": tool_secret},
            credential_id=tool_credential.id,
            enabled=True,
        )
        skill = Skill(
            id=uuid4(),
            tenant_id=TEST_TENANT_ID,
            org_id=TEST_ORG_ID,
            user_id=TEST_USER_ID,
            name="History skill",
            slug=f"history-{uuid4().hex}",
            kind="text",
            size_bytes=0,
            is_system=False,
        )
        server = McpServer(
            id=uuid4(),
            tenant_id=TEST_TENANT_ID,
            org_id=TEST_ORG_ID,
            user_id=TEST_USER_ID,
            name="History MCP",
            transport="stdio",
            credential_id=mcp_credential.id,
            headers={
                "Authorization": f"Static {header_secret}",
                "authorization": "Static",
                "pRoXy-AuThOrIzAtIoN": f"Static {proxy_header_secret}",
                "Resolved": "{{$credentials.token}}",
            },
            env_vars={"STATIC": env_secret, "TOKEN": "{{$credentials.token}}"},
            status="disabled",
            is_system=False,
        )
        db.add_all([tool, skill, server])
        await db.flush()
        mcp_tool = McpTool(
            id=uuid4(),
            server_id=server.id,
            name="history_mcp_tool",
            input_schema={"type": "object"},
            enabled=True,
        )
        db.add(mcp_tool)
        await db.flush()
        db.add_all(
            [
                AgentToolLink(agent_id=child_id, tool_id=tool.id),
                AgentMcpToolLink(agent_id=child_id, mcp_tool_id=mcp_tool.id),
                AgentSkillLink(
                    agent_id=child_id,
                    skill_id=skill.id,
                    config={"credential_bindings": {"auth": str(skill_credential.id)}},
                ),
            ]
        )
        await db.flush()

        collected = await collect_strict_history_secrets(db, root_id, conversation_id)

    assert {
        provider_secret,
        child_secret,
        tool_secret,
        skill_secret,
        mcp_secret,
        header_secret,
        proxy_header_secret,
        env_secret,
    }.issubset(collected)
