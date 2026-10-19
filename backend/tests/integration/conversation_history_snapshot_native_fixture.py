from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, select

from app.credentials.service import encrypt_data
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.credential import Credential
from app.models.mcp_app_invocation import McpAppInvocationBinding
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.message_event import MessageEvent
from app.models.organization import OrganizationMember
from app.models.skill import AgentSkillLink, Skill
from app.models.tool import AgentToolLink, Tool
from tests.authz.agent_lifecycle_fixture import RegisteredLifecycleFixture


@dataclass(frozen=True, slots=True)
class NativeHistorySecrets:
    provider: str
    child_llm: str
    child_tool: str
    child_skill: str
    mcp_payload: str
    static_header: str
    static_env: str

    @property
    def labeled_values(self) -> tuple[tuple[str, str], ...]:
        return (
            ("provider", self.provider),
            ("child_llm", self.child_llm),
            ("child_tool", self.child_tool),
            ("child_skill", self.child_skill),
            ("mcp_payload", self.mcp_payload),
            ("static_header", self.static_header),
            ("static_env", self.static_env),
        )

    @property
    def values(self) -> tuple[str, ...]:
        return tuple(value for _, value in self.labeled_values)


@dataclass(frozen=True, slots=True)
class NativeHistoryPreparation:
    owner_membership_status: str


@dataclass(frozen=True, slots=True)
class _CredentialSpec:
    definition_key: str
    name: str
    payload: dict[str, str]
    user_id: UUID


def native_history_secrets() -> NativeHistorySecrets:
    return NativeHistorySecrets(
        provider="task7-native-provider-secret",
        child_llm="task7-native-child-llm-secret",
        child_tool="task7-native-child-tool-secret",
        child_skill="task7-native-child-skill-secret",
        mcp_payload="task7-native-mcp-payload-secret",
        static_header="task7-native-static-header-secret",
        static_env="task7-native-static-env-secret",
    )


async def prepare_native_history_sources(
    fixture: RegisteredLifecycleFixture,
    secrets: NativeHistorySecrets,
) -> NativeHistoryPreparation:
    graph = fixture.graph
    async with fixture.sessions() as db:
        provider_credential = _credential(
            fixture,
            _CredentialSpec(
                "openai_compatible",
                "Task7 native masking key",
                {"api_key": secrets.provider},
                fixture.actors.owner_one_id,
            ),
        )
        child_credential = _credential(
            fixture,
            _CredentialSpec(
                "openai_compatible",
                "Task7 child LLM key",
                {"api_key": secrets.child_llm},
                fixture.actors.owner_two_id,
            ),
        )
        tool_credential = _credential(
            fixture,
            _CredentialSpec(
                "http_bearer",
                "Task7 child tool key",
                {"token": secrets.child_tool},
                fixture.actors.owner_two_id,
            ),
        )
        skill_credential = _credential(
            fixture,
            _CredentialSpec(
                "http_bearer",
                "Task7 child skill key",
                {"token": secrets.child_skill},
                fixture.actors.owner_two_id,
            ),
        )
        mcp_credential = _credential(
            fixture,
            _CredentialSpec(
                "mcp_secret",
                "Task7 child MCP key",
                {"token": secrets.mcp_payload},
                fixture.actors.owner_two_id,
            ),
        )
        db.add_all(
            [
                provider_credential,
                child_credential,
                tool_credential,
                skill_credential,
                mcp_credential,
            ]
        )
        await db.flush()
        agent = await db.get(Agent, graph.root_agent_id)
        child = await db.get(Agent, graph.child_agent_ids[0])
        conversation = await db.get(Conversation, graph.conversation_ids[0])
        owner_membership = await db.get(
            OrganizationMember,
            (fixture.actors.org_id, fixture.actors.owner_one_id),
        )
        event = await db.scalar(
            select(MessageEvent).where(MessageEvent.conversation_id == graph.conversation_ids[0])
        )
        mcp_binding = await db.get(McpAppInvocationBinding, graph.mcp_binding_id)
        mcp_tool = (
            await db.get(McpTool, mcp_binding.mcp_tool_id) if mcp_binding is not None else None
        )
        mcp_server = (
            await db.get(McpServer, mcp_binding.mcp_server_id) if mcp_binding is not None else None
        )
        assert agent is not None and child is not None and conversation is not None
        assert owner_membership is not None and event is not None
        assert mcp_server is not None and mcp_tool is not None
        assert mcp_tool.server_id == mcp_server.id
        assert mcp_server.tenant_id == fixture.actors.tenant_id
        assert mcp_server.org_id == fixture.actors.org_id

        child.llm_credential_id = child_credential.id
        mcp_server.user_id = fixture.actors.owner_two_id
        mcp_server.credential_id = mcp_credential.id
        mcp_server.headers = {
            "Authorization": f"Static {secrets.static_header}",
            "X-Resolved": "{{$credentials.token}}",
        }
        mcp_server.env_vars = {
            "MCP_STATIC": secrets.static_env,
            "MCP_TOKEN": "{{$credentials.token}}",
        }
        child_tool = Tool(
            id=uuid4(),
            tenant_id=fixture.actors.tenant_id,
            org_id=fixture.actors.org_id,
            user_id=fixture.actors.owner_two_id,
            is_system=False,
            definition_key="http_request",
            name="Task7 child tool",
            parameters={"literal": secrets.child_tool},
            credential_id=tool_credential.id,
            enabled=True,
        )
        child_skill = Skill(
            id=uuid4(),
            tenant_id=fixture.actors.tenant_id,
            org_id=fixture.actors.org_id,
            user_id=fixture.actors.owner_two_id,
            name="Task7 child skill",
            slug=f"task7-child-{uuid4().hex}",
            kind="text",
            size_bytes=7,
            is_system=False,
        )
        db.add_all([child_tool, child_skill])
        await db.flush()
        db.add_all(
            [
                AgentToolLink(agent_id=child.id, tool_id=child_tool.id),
                AgentMcpToolLink(agent_id=child.id, mcp_tool_id=mcp_tool.id),
                AgentSkillLink(
                    agent_id=child.id,
                    skill_id=child_skill.id,
                    config={"credential_bindings": {"auth": str(skill_credential.id)}},
                ),
            ]
        )
        now = datetime.now(UTC)
        agent.archived_at = now
        agent.archived_by = fixture.actors.direct_org_admin_id
        conversation.agent_lifecycle_state = "archived"
        conversation.agent_archived_at_snapshot = now
        owner_membership.status = "removed"
        event.events = [
            {
                "id": f"{event.assistant_msg_id}-1",
                "event": "message_end",
                "data": {"content": f"native event {' '.join(secrets.values)}"},
            }
        ]
        event.external_trace_url = f"https://trace.invalid/{secrets.static_header}"
        await db.execute(
            delete(AgentSubAgentLink).where(
                AgentSubAgentLink.parent_agent_id == graph.root_agent_id
            )
        )
        await db.commit()
        return NativeHistoryPreparation(owner_membership.status)


def _credential(
    fixture: RegisteredLifecycleFixture,
    spec: _CredentialSpec,
) -> Credential:
    encrypted, key_id, field_keys = encrypt_data(spec.payload)
    return Credential(
        id=uuid4(),
        tenant_id=fixture.actors.tenant_id,
        org_id=fixture.actors.org_id,
        user_id=spec.user_id,
        definition_key=spec.definition_key,
        name=spec.name,
        data_encrypted=encrypted,
        key_id=key_id,
        field_keys=field_keys,
        status="active",
        is_system=False,
    )


__all__ = [
    "NativeHistoryPreparation",
    "NativeHistorySecrets",
    "native_history_secrets",
    "prepare_native_history_sources",
]
