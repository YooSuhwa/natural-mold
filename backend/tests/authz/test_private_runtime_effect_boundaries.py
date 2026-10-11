"""F2 independent runtime sibling and consent parent witnesses for exact R11 source."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_api import runtime_service, service
from app.agent_api.dependencies import ApiKeyPrincipal
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.agent_api import AgentApiKey, AgentApiKeyDeployment, AgentApiRun, AgentDeployment
from app.routers import conversation_agent_protocol_consent as consent
from app.routers.session_consent_scope import SessionConsentPayload, SessionConsentTarget
from app.schemas.agent_api import AgentApiKeyCreate
from app.schemas.conversation_scope import ConversationPolicyTarget
from app.services import (
    conversation_stream_service,
    skill_builder_service,
    skill_draft_workspace,
)
from app.services import (
    system_credential_resolver as system_models,
)
from app.services.chat.runtime_context import get_agent_with_tools
from app.services.conversation_runtime_policy import ensure_conversation_runtime_policy
from app.storage.skill_draft_paths import skill_draft_storage_path
from tests.test_skill_builder_consent import _pending, _resume

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase
    from tests.authz.test_private_secret_collector import CollectorCase
    from tests.authz.test_private_skill_scope import SkillCase

pytest_plugins = [
    "tests.authz.test_private_agent_api_scope",
    "tests.authz.test_private_secret_collector",
    "tests.authz.test_private_skill_scope",
]

Edge = Literal["source_skill_id", "finalized_skill_id"]


async def test_runtime_key_refreshes_cached_revocation_before_usage(
    db: AsyncSession, api_case: ApiCase
) -> None:
    await db.execute(
        update(AgentApiKey)
        .where(AgentApiKey.id == api_case.key.id)
        .values(revoked_at=service.utc_now_naive())
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    assert api_case.key.revoked_at is None
    response = await api_case.client.get(
        "/v1/agents", headers={"Authorization": f"Bearer {api_case.cleartext}"}
    )
    assert response.status_code == 401
    await db.refresh(api_case.key)
    assert api_case.key.usage_count == 0


@pytest.mark.parametrize("foreign", [False, True])
async def test_runtime_catalog_rejects_foreign_agent_parent(
    db: AsyncSession, api_case: ApiCase, foreign: bool
) -> None:
    marker = "F2_FOREIGN_AGENT_PRIVATE_NAME"
    foreign_agent_id = api_case.second.agent_id
    await db.execute(update(Agent).where(Agent.id == foreign_agent_id).values(name=marker))
    if foreign:
        await db.execute(delete(AgentDeployment).where(AgentDeployment.id == api_case.second.id))
        await db.execute(
            update(AgentDeployment)
            .where(AgentDeployment.id == api_case.first.id)
            .values(agent_id=foreign_agent_id)
            .execution_options(synchronize_session=False)
        )
    await db.commit()
    response = await api_case.client.get(
        "/v1/agents", headers={"Authorization": f"Bearer {api_case.cleartext}"}
    )
    print(f"CATALOG foreign={foreign} status={response.status_code} body={response.text}")
    assert response.status_code == 200
    assert marker not in response.text
    assert len(response.json()["data"]) == (0 if foreign else 1)


@pytest.mark.parametrize("foreign", [False, True])
async def test_runtime_key_checks_deployment_parent_before_usage_write(
    db: AsyncSession, api_case: ApiCase, private_scopes: PrivateScopes, foreign: bool
) -> None:
    key, cleartext = await service.create_api_key(
        db,
        private_scopes.first,
        AgentApiKeyCreate(
            name="F2 runtime linked key", scopes=["read"], deployment_ids=[api_case.first.id]
        ),
    )
    if foreign:
        await db.execute(
            update(AgentApiKeyDeployment)
            .where(AgentApiKeyDeployment.api_key_id == key.id)
            .values(deployment_id=api_case.second.id)
            .execution_options(synchronize_session=False)
        )
    await db.commit()
    response = await api_case.client.get(
        "/v1/agents", headers={"Authorization": f"Bearer {cleartext}"}
    )
    await db.refresh(key)
    print(f"KEY foreign={foreign} status={response.status_code} usage={key.usage_count}")
    assert response.status_code == (401 if foreign else 200)
    assert key.usage_count == (0 if foreign else 1)


@pytest.mark.parametrize("foreign", [False, True])
async def test_runtime_run_rejects_foreign_agent_before_durable_row(
    db: AsyncSession, api_case: ApiCase, private_scopes: PrivateScopes, foreign: bool
) -> None:
    foreign_agent_id = api_case.second.agent_id
    if foreign:
        await db.execute(delete(AgentDeployment).where(AgentDeployment.id == api_case.second.id))
        await db.execute(
            update(AgentDeployment)
            .where(AgentDeployment.id == api_case.first.id)
            .values(agent_id=foreign_agent_id)
            .execution_options(synchronize_session=False)
        )
    await db.commit()
    principal = ApiKeyPrincipal(api_case.key, api_case.key.user_id, private_scopes.first)
    denied = False
    try:
        await runtime_service.create_run_row(
            db,
            principal=principal,
            deployment=api_case.first,
            conversation_id=None,
            thread_id=None,
            mode="wait",
            input_payload={},
            metadata=None,
        )
    except AppError:
        denied = True
    count = await db.scalar(select(func.count()).select_from(AgentApiRun))
    print(f"RUN foreign={foreign} denied={denied} durable_rows={count}")
    assert denied == foreign
    assert count == (0 if foreign else 1)


@pytest.mark.parametrize("edge", ["source_skill_id", "finalized_skill_id"])
@pytest.mark.parametrize("foreign", [False, True])
async def test_runtime_checks_skill_parents_before_model_and_workspace(
    db: AsyncSession,
    api_case: ApiCase,
    skill_case: SkillCase,
    private_scopes: PrivateScopes,
    edge: Edge,
    foreign: bool,
) -> None:
    context = private_scopes.first
    principal = ApiKeyPrincipal(api_case.key, api_case.key.user_id, context)
    conversation = await runtime_service.create_api_conversation(
        db, api_case.first, principal=principal
    )
    agent = await get_agent_with_tools(db, api_case.first.agent_id, context)
    assert agent is not None
    agent.runtime_profile = "skill_builder"
    await ensure_conversation_runtime_policy(db, ConversationPolicyTarget(conversation.id, context))
    session = await skill_builder_service.create_session(
        db, context=context, user_request="F2 runtime parent fixture"
    )
    session.conversation_id = conversation.id
    setattr(session, edge, (skill_case.second if foreign else skill_case.first).id)
    await db.commit()
    user = CurrentUser(id=context.user_id, email="fixture@example.com", name="Fixture")
    create = Mock(return_value=skill_draft_storage_path(session.id))
    model = AsyncMock(
        return_value=system_models.ResolvedSystemModel(
            provider="openai", model_name="fixture", api_key=None, base_url=None
        )
    )
    denied = False
    with pytest.MonkeyPatch.context() as patches:
        patches.setattr(skill_draft_workspace, "create_workspace", create)
        patches.setattr(system_models, "resolve_system_model", model)
        try:
            await conversation_stream_service.resolve_agent_context(
                db,
                conversation.id,
                user,
                context=context,
                checkpoint_id=None,
            )
        except AppError:
            denied = True
    await db.commit()
    await db.refresh(session)
    print(
        f"BUILDER_RUNTIME edge={edge} foreign={foreign} denied={denied} "
        f"models={model.await_count} workspace_creates={create.call_count} "
        f"stored_path={session.draft_workspace_path}"
    )
    assert denied == foreign
    assert model.await_count == (0 if foreign else 1)
    assert create.call_count == (0 if foreign else 1)


@pytest.mark.parametrize("edge", ["source_skill_id", "finalized_skill_id"])
@pytest.mark.parametrize("foreign", [False, True])
async def test_consent_checks_every_skill_parent_before_draft_read(
    db: AsyncSession,
    collector_case: CollectorCase,
    skill_case: SkillCase,
    private_scopes: PrivateScopes,
    edge: Edge,
    foreign: bool,
) -> None:
    session = await skill_builder_service.create_session(
        db, context=private_scopes.first, user_request="F2 consent fixture"
    )
    session.conversation_id = collector_case.conversation.id
    session.draft_workspace_path = skill_draft_storage_path(session.id)
    setattr(session, edge, (skill_case.second if foreign else skill_case.first).id)
    await db.commit()
    target = SessionConsentTarget(collector_case.conversation.id, private_scopes.first)
    payload = SessionConsentPayload(
        _resume([{"type": "approve", "scope": "session"}]), _pending(["test_skill_draft"])
    )
    read = Mock(return_value=False)
    denied = False
    recorded: list[str] = []
    with pytest.MonkeyPatch.context() as patches:
        patches.setattr(skill_draft_workspace, "draft_requires_network", read)
        try:
            recorded = await consent.apply_session_consent_decisions(db, target, payload)
        except AppError:
            denied = True
    await db.refresh(session)
    print(
        f"CONSENT edge={edge} foreign={foreign} denied={denied} "
        f"reads={read.call_count} recorded={recorded} stored={session.tool_consents}"
    )
    assert read.call_count == (0 if foreign else 1)
    assert not session.tool_consents if foreign else session.tool_consents
