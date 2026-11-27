from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictError
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.user import User
from app.routers import conversation_agent_protocol_state as state
from app.routers.conversation_agent_protocol_contracts import UpdateStateRequest
from app.schemas.conversation_scope import ConversationBranchMutation
from app.services.chat.conversation_mutations import set_active_branch
from app.services.conversation_run_queue_worker_metadata import queue_current_user
from app.services.conversation_run_state import transition_run
from tests.ag_ui_deferred_testkit import seed_deferred
from tests.authz.org_context_fixture import org_fixture as org_fixture
from tests.authz.private_scope_fixture import PrivateScopes
from tests.authz.private_scope_fixture import private_scopes as private_scopes


async def test_sealed_command_pins_branch_through_worker_claim(
    db: AsyncSession, private_scopes: PrivateScopes, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = await seed_deferred(db, private_scopes.first)
    await db.commit()
    target = ConversationBranchMutation(case.conversation_id, case.context, case.checkpoint_id)
    await set_active_branch(db, target)  # repeated selection is read-equivalent
    other = ConversationBranchMutation(case.conversation_id, case.context, str(uuid4()))
    with pytest.raises(ConflictError, match="접수된"):
        await set_active_branch(db, other)
    await db.rollback()  # Separate requests have separate lifecycle proofs.
    prepare = AsyncMock()
    monkeypatch.setattr(state, "_prepare_agent", prepare)
    conversation = await db.get(Conversation, case.conversation_id)
    user = await db.get(User, case.context.user_id)
    assert conversation is not None and user is not None
    # The saver writes immediately; even graph preparation must occur after the guard.
    with pytest.raises(ConflictError, match="접수된"):
        await state.update_thread_state_response(
            db,
            conversation=conversation,
            context=case.context,
            request=UpdateStateRequest(values={"messages": []}),
            user=queue_current_user(user),
        )
    prepare.assert_not_awaited()
    await db.rollback()
    run = await db.get(ConversationRun, case.run_id, with_for_update=True)
    assert run is not None
    await transition_run(db, run, "running", worker_instance_id="owned")
    await set_active_branch(db, other)
    assert conversation.active_branch_checkpoint_id == other.checkpoint_id
