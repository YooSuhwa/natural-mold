"""R17 duplicate MCP provenance must use persisted children before SQL effects."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final
from unittest.mock import Mock

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp import apps_binding_store
from app.mcp.apps_binding_store import BindingDraft, McpAppsError, mint_invocation_binding
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import utc_now_naive
from app.models.mcp_app_invocation import McpAppInvocationBinding
from app.services.agent_lifecycle_locks import AgentLifecycleTransactionDirtyError

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_mcp_apps_scope import McpAppCase

pytest_plugins = ["tests.authz.test_private_mcp_apps_scope"]
SOURCE_ROOT: Final = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("tampered", [False, True])
@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("pending", [False, True])
async def test_duplicate_mint_validates_persisted_provenance_before_effects(
    db: AsyncSession,
    mcp_app_case: McpAppCase,
    private_scopes: PrivateScopes,
    tampered: bool,
    cached: bool,
    pending: bool,
) -> None:
    assert Path(apps_binding_store.__file__).is_relative_to(SOURCE_ROOT)
    binding = await db.scalar(select(McpAppInvocationBinding))
    assert binding is not None
    if binding.agent_id is None:
        pytest.fail("active MCP App provenance fixture must remain Agent-bound")
    binding_agent_id = binding.agent_id
    agent = await db.get(Agent, binding_agent_id)
    assert agent is not None
    original_name = agent.name
    binding_id = binding.id
    draft = BindingDraft(
        run_id=binding.run_id,
        conversation_id=binding.conversation_id,
        user_id=binding.user_id,
        org_context=mcp_app_case.context,
        agent_id=binding_agent_id,
        credential_subject_user_id=binding.credential_subject_user_id,
        mcp_server_id=binding.mcp_server_id,
        mcp_tool_id=binding.mcp_tool_id,
        tool_call_id=binding.tool_call_id,
        remote_tool_name=binding.remote_tool_name,
        resource_uri=binding.resource_uri,
        tool_meta=binding.tool_meta,
        result_meta=None,
        structured_content=None,
    )
    if tampered:
        foreign = private_scopes.second
        history = Conversation(
            user_id=foreign.user_id,
            org_id=foreign.org_id,
            tenant_id=foreign.tenant_id,
            agent_id=None,
            agent_lifecycle_state="purged",
            agent_purged_at=utc_now_naive(),
            title="Foreign preserved history",
            agent_name_snapshot="Historical agent",
            last_activity_at=utc_now_naive(),
        )
        db.add(history)
        await db.flush()
        await db.execute(
            update(McpAppInvocationBinding)
            .where(McpAppInvocationBinding.id == binding_id)
            .values(conversation_id=history.id)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    if not cached:
        db.expunge(binding)
    if pending:
        agent.name = "Unrelated staged private content"
    flush = Mock()
    event.listen(db.sync_session, "before_flush", flush)
    denied = False
    dirty_rejected = False
    result = None
    try:
        result = await mint_invocation_binding(db, draft)
    except AgentLifecycleTransactionDirtyError:
        dirty_rejected = True
    except McpAppsError as exc:
        assert exc.code == "mcp_app_tool_call_conflict"
        denied = True
    finally:
        event.remove(db.sync_session, "before_flush", flush)
    with db.no_autoflush:
        stored = await db.scalar(select(Agent.name).where(Agent.id == agent.id))
    remains_staged = agent in db.dirty
    await db.rollback()
    assert dirty_rejected is pending
    assert denied is (tampered and not pending)
    if not tampered and not pending:
        assert result == binding_id
    if tampered or pending:
        assert (flush.call_count, stored, remains_staged) == (0, original_name, pending)
