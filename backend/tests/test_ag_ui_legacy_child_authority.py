"""Resume authority for a pre-F04 invocation whose checkpoint identifies a linked child."""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_agent_checks import bind_request_authz
from app.authz.org_context import OrgContext
from app.config import settings
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.organization import OrganizationMember, TenantMember
from app.models.user import User
from app.services.ag_ui_legacy_child_authority import require_legacy_child_binding
from app.services.ag_ui_legacy_child_binding import LegacyChildBinding
from app.services.agent_execution_acceptance import OwnedExecutionRequest, accept_owned_execution
from tests.authz.authz_service_testkit import RecordingClient, service
from tests.authz.execution_gateway_testkit import seed_graph


@dataclass(frozen=True)
class HistoricalChat:
    context: OrgContext
    conversation: Conversation
    run: ConversationRun
    child: Agent
    binding: LegacyChildBinding
    grants: RecordingClient

    async def authorize(
        self,
        db: AsyncSession,
        *,
        parent: ConversationRun | None = None,
        binding: LegacyChildBinding | None = None,
    ) -> UUID:
        return await require_legacy_child_binding(
            db,
            conversation=self.conversation,
            context=self.context,
            parent_run=parent or self.run,
            binding=binding or self.binding,
        )


async def _other_member(db: AsyncSession, context: OrgContext) -> UUID:
    member = User(email=f"historical-{uuid4()}@example.test", name="Resource owner", is_active=True)
    db.add(member)
    await db.flush()
    db.add_all(
        [
            TenantMember(tenant_id=context.tenant_id, user_id=member.id),
            OrganizationMember(
                org_id=context.org_id, tenant_id=context.tenant_id, user_id=member.id
            ),
        ]
    )
    return member.id


async def _historical_chat(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    *,
    separate_owners: bool = False,
    root_mode: str = "fixed",
    own_root: bool = False,
) -> HistoricalChat:
    # Before F04, ordinary acceptance stored F14 actor/subject attribution but
    # never wrote identity_mode_at_accept or a ConversationChildTask.
    monkeypatch.setattr(settings, "child_tasks_enabled", False)
    graph = await seed_graph(db, conversations=1)
    ctx = graph.context
    root = await db.get(Agent, graph.agent_id)
    conversation = await db.get(Conversation, graph.conversation_ids[0])
    assert root and conversation
    root.identity_mode = root_mode
    if separate_owners and not own_root:
        root.user_id = await _other_member(db, ctx)
    child_owner = await _other_member(db, ctx) if separate_owners else ctx.user_id
    child = Agent(
        user_id=child_owner,
        org_id=ctx.org_id,
        tenant_id=ctx.tenant_id,
        name="Reviewer",
        system_prompt="Check",
        model_id=root.model_id,
    )
    db.add(child)
    await db.flush()
    db.add(AgentSubAgentLink(parent_agent_id=root.id, sub_agent_id=child.id))
    await db.commit()
    grants = RecordingClient()
    caller = f"user:{ctx.user_id}"
    root_principal = f"agent:{root.id}"
    grants.allowed = frozenset(
        {
            (caller, "can_run", root_principal),
            (caller if root_mode == "per_user" else root_principal, "can_run", f"agent:{child.id}"),
        }
    )
    await bind_request_authz(service(grants, mode="enforce" if separate_owners else "legacy"))
    accepted = await accept_owned_execution(
        db, OwnedExecutionRequest(conversation.id, root.id, ctx, "chat", None)
    )
    run = await db.get(ConversationRun, accepted.run_id)
    assert run
    run.status, run.is_active, run.interrupt_id = "interrupted", False, "child-review"
    await db.commit()
    pins = (
        await db.scalars(select(ConversationRunAgent).where(ConversationRunAgent.run_id == run.id))
    ).all()
    assert {p.agent_id for p in pins} == {root.id, child.id}
    assert all(p.identity_mode_at_accept is None and p.credential_subject_user_id for p in pins)
    binding = LegacyChildBinding(
        runtime_name=child.runtime_name,
        tool_call_id="call-research",
        checkpoint_ns=f"tools:{uuid4()}",
        producer_run_id=run.id,
    )
    return HistoricalChat(ctx, conversation, run, child, binding, grants)


@pytest.mark.parametrize("producer_known", [True, False])
async def test_legacy_owner_can_answer_identified_direct_child(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, producer_known: bool
) -> None:
    chat = await _historical_chat(db, monkeypatch)
    binding = chat.binding if producer_known else replace(chat.binding, producer_run_id=None)
    assert await chat.authorize(db, binding=binding) == chat.child.id


@pytest.mark.parametrize(
    "mutation", ["missing_pin", "changed_link", "missing_subject", "indirect_path", "display_name"]
)
async def test_legacy_name_alone_never_grants_a_delegation(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    chat = await _historical_chat(db, monkeypatch)
    child_pin = (
        ConversationRunAgent.run_id == chat.run.id,
        ConversationRunAgent.agent_id == chat.child.id,
    )
    binding = chat.binding
    if mutation == "missing_pin":
        await db.execute(delete(ConversationRunAgent).where(*child_pin))
    elif mutation == "changed_link":
        await db.execute(
            delete(AgentSubAgentLink).where(AgentSubAgentLink.sub_agent_id == chat.child.id)
        )
    elif mutation == "missing_subject":
        await db.execute(
            update(ConversationRunAgent).where(*child_pin).values(credential_subject_user_id=None)
        )
    elif mutation == "indirect_path":
        await db.execute(
            update(ConversationRunAgent)
            .where(*child_pin)
            .values(path=[str(chat.conversation.agent_id), str(uuid4()), str(chat.child.id)])
        )
    else:
        binding = replace(binding, runtime_name=chat.child.name)
    await db.commit()
    with pytest.raises(AppError) as denied:
        await chat.authorize(db, binding=binding)
    assert denied.value.status == 404


@pytest.mark.parametrize("mode", ["fixed", "per_user"])
async def test_unambiguous_historical_subject_uses_its_live_delegation_grant(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    chat = await _historical_chat(db, monkeypatch, separate_owners=True, root_mode=mode)
    assert await chat.authorize(db) == chat.child.id
    await db.commit()
    chat.grants.allowed = frozenset(
        rule for rule in chat.grants.allowed if rule[2] != f"agent:{chat.child.id}"
    )
    with pytest.raises(AppError) as denied:
        await chat.authorize(db)
    assert denied.value.status == 404


async def test_ambiguous_historical_parent_subject_requires_both_delegation_grants(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat = await _historical_chat(db, monkeypatch, separate_owners=True, own_root=True)
    with pytest.raises(AppError) as denied:
        await chat.authorize(db)
    assert denied.value.status == 404
    await db.commit()
    chat.grants.allowed |= {(f"user:{chat.context.user_id}", "can_run", f"agent:{chat.child.id}")}
    assert await chat.authorize(db) == chat.child.id


@pytest.mark.parametrize("change_origin", [False, True])
async def test_legacy_resume_rechecks_both_original_and_current_attribution(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, change_origin: bool
) -> None:
    chat = await _historical_chat(db, monkeypatch)
    assert chat.run.agent_id is not None
    accepted = await accept_owned_execution(
        db,
        OwnedExecutionRequest(
            chat.conversation.id,
            chat.run.agent_id,
            chat.context,
            "chat",
            None,
            run_source="resume",
            parent_run_id=chat.run.id,
            interrupt_id=chat.run.interrupt_id,
        ),
    )
    continuation = await db.get(ConversationRun, accepted.run_id)
    assert continuation
    continuation.status, continuation.is_active = "interrupted", False
    await db.commit()
    assert await chat.authorize(db, parent=continuation) == chat.child.id
    with pytest.raises(AppError) as missing_origin:
        await chat.authorize(
            db, parent=continuation, binding=replace(chat.binding, producer_run_id=None)
        )
    assert missing_origin.value.status == 404
    await db.commit()
    await db.execute(
        update(ConversationRunAgent)
        .where(
            ConversationRunAgent.run_id == (chat.run.id if change_origin else continuation.id),
            ConversationRunAgent.agent_id == chat.child.id,
        )
        .values(on_behalf_of_user_id=None)
    )
    await db.commit()
    with pytest.raises(AppError) as denied:
        await chat.authorize(db, parent=continuation)
    assert denied.value.status == 404


async def test_mode_change_that_switches_the_historical_child_credential_subject_is_denied(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat = await _historical_chat(db, monkeypatch, separate_owners=True)
    assert await chat.authorize(db) == chat.child.id
    await db.commit()
    await db.execute(
        update(Agent).where(Agent.id == chat.child.id).values(identity_mode="per_user")
    )
    await db.commit()
    with pytest.raises(AppError) as denied:
        await chat.authorize(db)
    assert denied.value.status == 404


async def test_another_owner_or_unrelated_checkpoint_producer_cannot_use_the_binding(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat = await _historical_chat(db, monkeypatch)
    stranger = await seed_graph(db, conversations=1)
    with pytest.raises(AppError) as wrong_owner:
        await replace(chat, context=stranger.context).authorize(db)
    assert wrong_owner.value.status == 404
    await db.commit()
    with pytest.raises(AppError) as wrong_producer:
        await chat.authorize(db, binding=replace(chat.binding, producer_run_id=uuid4()))
    assert wrong_producer.value.status == 404


async def test_authorization_outage_is_retryable_for_a_previously_accepted_child(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat = await _historical_chat(db, monkeypatch, separate_owners=True)
    chat.grants.unavailable = True
    with pytest.raises(AppError) as error:
        await chat.authorize(db)
    assert error.value.status == 503
