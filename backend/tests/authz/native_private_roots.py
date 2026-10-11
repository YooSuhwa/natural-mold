"""Native ORM fixtures; these are not evidence of production service writers."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.credentials import service as credentials
from app.credentials.creation import CredentialCreation
from app.models.agent import Agent
from app.models.agent_api import AgentApiKey, AgentDeployment
from app.models.agent_blueprint import AgentBlueprint
from app.models.agent_trigger import AgentTrigger
from app.models.builder_session import BuilderSession
from app.models.conversation import Conversation
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.models.credential import Credential
from app.models.credential_default import CredentialDefault
from app.models.marketplace import MarketplaceInstallation, MarketplaceItem, MarketplaceVersion
from app.models.mcp_server import McpServer
from app.models.memory import MemoryProposal, MemoryRecord
from app.models.message_attachment import MessageAttachment
from app.models.model import Model
from app.models.share_link import ShareLink
from app.models.skill import Skill
from app.models.skill_builder_session import SkillBuilderSession
from app.models.tool import Tool

type PrivateRow = (
    Agent
    | Skill
    | McpServer
    | Credential
    | Tool
    | MarketplaceItem
    | Conversation
    | ConversationArtifact
    | MessageAttachment
    | MemoryRecord
    | MemoryProposal
    | BuilderSession
    | SkillBuilderSession
    | AgentBlueprint
    | MarketplaceInstallation
    | AgentTrigger
    | AgentDeployment
    | AgentApiKey
    | CredentialDefault
    | ShareLink
)


@dataclass(frozen=True, slots=True)
class NativePrivateRows:
    context: OrgContext
    rows: tuple[PrivateRow, ...]


async def seed_private_roots(
    db: AsyncSession, context: OrgContext, model: Model
) -> NativePrivateRows:
    scope = {"user_id": context.user_id, "org_id": context.org_id, "tenant_id": context.tenant_id}
    suffix = context.org_id.hex[:10]
    agent = Agent(**scope, name="Matrix agent", system_prompt="Fixture", model_id=model.id)
    skill = Skill(
        **scope,
        name="Matrix skill",
        slug="same-scope-slug",
        kind="text",
        credential_requirements=[],
    )
    server = McpServer(**scope, name="Matrix MCP", transport="sse", url="http://127.0.0.1:9/mcp")
    credential = await credentials.create(
        db,
        CredentialCreation("openai", "Matrix credential", {"api_key": "dummy-native-matrix"}),
        context,
    )
    tool = Tool(**scope, name="Matrix tool", definition_key="current_datetime")
    item = MarketplaceItem(
        **scope,
        name="Matrix item",
        slug="same-item-slug",
        resource_type="skill",
        visibility="private",
    )
    builder = BuilderSession(**scope, user_request="Matrix builder")
    skill_builder = SkillBuilderSession(**scope, user_request="Matrix skill builder")
    blueprint = AgentBlueprint(**scope, name="Matrix blueprint", spec={}, spec_hash="a" * 64)
    key = AgentApiKey(
        **scope,
        name="Matrix key",
        key_id=suffix,
        key_hash="b" * 64,
        prefix=f"fixture-{suffix}",
        last_four="0000",
    )
    db.add_all([agent, skill, server, tool, item, builder, skill_builder, blueprint, key])
    await db.flush()
    conversation = Conversation(
        **scope,
        agent_id=agent.id,
        title="Matrix conversation",
        agent_name_snapshot=agent.name,
        last_activity_at=datetime.now(UTC),
    )
    deployment = AgentDeployment(**scope, agent_id=agent.id, public_id=f"matrix-{suffix}")
    trigger = AgentTrigger(
        **scope,
        agent_id=agent.id,
        name="Matrix trigger",
        trigger_type="cron",
        schedule_config={"cron": "0 9 * * *"},
        input_message="Fixture",
        status="disabled",
    )
    default = CredentialDefault(
        **scope, credential_id=credential.id, scope_kind="llm_model", scope_key="same-model"
    )
    version = MarketplaceVersion(
        item_id=item.id,
        version_label="fixture-1",
        version_number=1,
        resource_type="skill",
        payload_kind="skill_package",
        content_hash="c" * 64,
    )
    db.add_all([conversation, deployment, trigger, default, version])
    await db.flush()
    item.latest_version_id = version.id
    installation = MarketplaceInstallation(
        **scope,
        item_id=item.id,
        version_id=version.id,
        resource_type="skill",
        installed_skill_id=skill.id,
    )
    artifact = ConversationArtifact(
        **scope,
        agent_id=agent.id,
        conversation_id=conversation.id,
        assistant_msg_id=f"matrix-{suffix}",
        logical_path="fixture.txt",
        display_name="Fixture",
        mime_type="text/plain",
        size_bytes=0,
        sha256="d" * 64,
    )
    attachment = MessageAttachment(
        **scope,
        conversation_id=conversation.id,
        filename="fixture.txt",
        mime_type="text/plain",
        size_bytes=0,
        storage_path="unused-matrix/fixture.txt",
        url="/fixture.txt",
    )
    memory = MemoryRecord(
        **scope, scope="user", content="Matrix memory", store_path=f"matrix/{suffix}"
    )
    proposal = MemoryProposal(**scope, scope="user", content="Matrix proposal")
    share = ShareLink(**scope, conversation_id=conversation.id, share_token=f"matrix-{suffix}")
    db.add_all([installation, artifact, attachment, memory, proposal, share])
    await db.flush()
    artifact_version = ArtifactVersion(
        artifact_id=artifact.id,
        version_number=1,
        object_key="unused-matrix/fixture.txt",
        original_filename="fixture.txt",
        size_bytes=0,
        sha256=artifact.sha256,
    )
    db.add(artifact_version)
    await db.flush()
    artifact.current_version_id = artifact_version.id
    return NativePrivateRows(
        context,
        (
            agent,
            skill,
            server,
            credential,
            tool,
            item,
            conversation,
            artifact,
            attachment,
            memory,
            proposal,
            builder,
            skill_builder,
            blueprint,
            installation,
            trigger,
            deployment,
            key,
            default,
            share,
        ),
    )
