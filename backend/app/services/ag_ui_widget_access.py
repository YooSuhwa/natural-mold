"""Recover only live MCP widget pointers for this authenticated physical run."""

from sqlalchemy import and_, case, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_execution_scope import (
    assert_agent_tree_executable,
    load_agent_tree_for_history,
)
from app.authz.conversation_scope import can_read_conversation_run_id
from app.authz.credential_binding_scope import my_bindings
from app.authz.org_context import OrgContext
from app.authz.private_actor import require_private_actor
from app.authz.scoping import private_scope_predicate
from app.exceptions import AppError
from app.mcp.apps_binding_connection import binding_connection_visible
from app.mcp.apps_binding_store import McpAppsError, authorize_binding
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.credential import Credential
from app.models.mcp_app_invocation import McpAppInvocationBinding
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.organization import Organization, Tenant
from app.models.resource_credential_binding import ResourceCredentialBinding as Connection


async def readable_widget_bindings(
    db: AsyncSession, *, conversation: Conversation, context: OrgContext, run: ConversationRun
) -> frozenset[tuple[str, str]]:
    binding = McpAppInvocationBinding
    query = (
        select(binding.tool_call_id, binding.id)
        .select_from(binding)
        .join(ConversationRun, ConversationRun.id == binding.run_id)
        .join(Conversation, Conversation.id == ConversationRun.conversation_id)
        .where(
            Conversation.id == conversation.id,
            ConversationRun.id == run.id,
            private_scope_predicate(Conversation, context),
            ConversationRun.user_id == context.user_id,
            or_(
                private_scope_predicate(ConversationRun, context),
                ConversationRun.org_id.is_(None) & ConversationRun.tenant_id.is_(None),
            ),
            binding.conversation_id == Conversation.id,
            binding.user_id == context.user_id,
            binding.credential_subject_user_id == context.user_id,
            binding.agent_id == ConversationRun.agent_id,
            binding.agent_id == Conversation.agent_id,
        )
    )
    with db.no_autoflush:
        if await db.scalar(query.limit(1)) is None:
            return frozenset()
        if run.agent_id is None or not await can_read_conversation_run_id(
            db, conversation, run.id, context
        ):
            return frozenset()

        # Commands and replay share the live policy check. Compare the private
        # database bindings on either side of remote authorization as well.
        stamps = (
            query.add_columns(
                binding.resource_uri,
                binding.remote_tool_name,
                binding.agent_id,
                McpServer.id,
                McpServer.updated_at,
                McpServer.credential_mode,
                McpServer.credential_id,
                McpServer.credential_definition_key,
                McpTool.id,
                McpTool.updated_at,
                AgentMcpToolLink.delegated_by,
                Connection.id,
                Connection.credential_id,
                Credential.id,
                Credential.updated_at,
                Credential.status,
                Organization.authz_epoch,
                Tenant.updated_at,
            )
            .join(McpServer, McpServer.id == binding.mcp_server_id)
            .join(
                McpTool, and_(McpTool.id == binding.mcp_tool_id, McpTool.server_id == McpServer.id)
            )
            .join(
                AgentMcpToolLink,
                and_(
                    AgentMcpToolLink.agent_id == binding.agent_id,
                    AgentMcpToolLink.mcp_tool_id == McpTool.id,
                ),
            )
            .join(Organization, Organization.id == Conversation.org_id)
            .join(Tenant, Tenant.id == Organization.tenant_id)
            .outerjoin(
                Connection,
                and_(
                    McpServer.credential_mode == "per_user",
                    my_bindings(context),
                    Connection.resource_type == "mcp_server",
                    Connection.resource_id == McpServer.id,
                ),
            )
            .outerjoin(
                Credential,
                Credential.id
                == case(
                    (McpServer.credential_mode == "per_user", Connection.credential_id),
                    else_=McpServer.credential_id,
                ),
            )
            .where(
                AgentMcpToolLink.link_status == "active",
                McpTool.enabled.is_(True),
                McpTool.review_state == "approved",
                McpServer.registration_state == "approved",
                McpServer.status != "disabled",
                Organization.status == "active",
                Tenant.status == "active",
            )
        )
        accepted = set()
        for stamp in (await db.execute(stamps)).all():
            call, ident = stamp[0], stamp[1]
            try:
                current = await authorize_binding(
                    db,
                    conversation_id=conversation.id,
                    run_id=run.id,
                    tool_call_id=call,
                    context=context,
                    binding_id=ident,
                    for_replay=True,
                )
                if await binding_connection_visible(db, current):
                    accepted.add(stamp)
            except McpAppsError:
                continue
            except AppError as error:
                if error.status not in (403, 404, 409, 503):
                    raise
        if not accepted:
            return frozenset()
        try:
            await require_private_actor(db, context)
            tree = await load_agent_tree_for_history(db, run.agent_id)
            assert_agent_tree_executable(tree, context)
        except AppError as error:
            if error.status in (403, 404, 409):
                return frozenset()
            raise
        return frozenset(
            (row[0], str(row[1])) for row in (await db.execute(stamps)).all() if row in accepted
        )
