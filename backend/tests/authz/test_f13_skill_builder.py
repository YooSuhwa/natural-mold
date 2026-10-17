"""F-13: a skill builder run re-decides its target (plan 11.0 "스킬 빌더 숨은 에이전트").

Improving a skill needs ``skill#can_edit`` on every run; once revoked the run refuses
before the workspace or the system model is touched, and the target is decided before
the session row lock. Legacy and shadow keep the private session lookup as the answer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.prepared_run_input_policy import PreparedRunUnsupportedError
from app.agent_runtime.runtime_policy import SKILL_BUILDER_RUNTIME_POLICY
from app.authz.fake import FakeAuthz
from app.authz.run_decisions import fresh_run_decision_scope
from app.authz.service import AuthzService
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.skill_builder_session import SkillBuilderSession
from app.schemas.skill_builder import SkillBuilderMode
from app.services import conversation_stream_service, skill_builder_service, skill_draft_workspace
from app.services import skill_builder_run_access as run_access
from app.services import system_credential_resolver as system_models
from app.services.chat.runtime_context import get_agent_with_tools
from app.skills import service as skill_service
from app.skills.creation import TextSkillCreation

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_secret_collector import CollectorCase
    from tests.authz.test_private_skill_scope import SkillCase

pytest_plugins = [
    "tests.authz.test_private_secret_collector",
    "tests.authz.test_private_skill_scope",
]


class ReachedModel(Exception):
    """The run passed its decisions and reached the system model step."""


def _enforce(monkeypatch: pytest.MonkeyPatch, *allowed: tuple[str, str, str]) -> None:
    service = AuthzService(FakeAuthz(allowed=frozenset(allowed)), mode="enforce")
    monkeypatch.setattr("app.authz.runtime._current", service)


async def _resolve(db: AsyncSession, case: CollectorCase, scopes: PrivateScopes) -> None:
    conversation = case.conversation
    assert conversation.agent_id is not None
    agent = await get_agent_with_tools(db, conversation.agent_id, scopes.first)
    user = CurrentUser(id=scopes.first.user_id, email="fixture@example.com", name="Fixture")
    await conversation_stream_service._resolve_skill_builder_agent_context(
        db,
        conversation,
        agent,
        user,
        context=scopes.first,
        runtime_policy=SKILL_BUILDER_RUNTIME_POLICY,
        checkpoint_id=None,
    )


@pytest.fixture
async def improve_session(
    db: AsyncSession, collector_case: CollectorCase, skill_case: SkillCase, private_scopes
) -> str:
    session = await skill_builder_service.create_session(
        db,
        context=private_scopes.first,
        user_request="Improve the fixture",
        mode=SkillBuilderMode.IMPROVE,
        source_skill_id=skill_case.first.id,
    )
    session.conversation_id = collector_case.conversation.id
    await db.commit()
    return f"skill:{skill_case.first.id}"


async def test_skill_builder_run_refuses_after_can_edit_is_revoked(
    db: AsyncSession,
    collector_case: CollectorCase,
    private_scopes: PrivateScopes,
    improve_session: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    me = f"user:{private_scopes.first.user_id}"
    model = AsyncMock(side_effect=ReachedModel)
    workspace = Mock(return_value="unused")
    monkeypatch.setattr(system_models, "resolve_system_model", model)
    monkeypatch.setattr(skill_draft_workspace, "create_workspace", workspace)

    with pytest.raises(ReachedModel):  # legacy: the private session lookup decides
        await _resolve(db, collector_case, private_scopes)
    _enforce(monkeypatch, (me, "can_use", improve_session), (me, "can_edit", improve_session))
    with pytest.raises(ReachedModel):
        await _resolve(db, collector_case, private_scopes)
    assert model.await_count == 2

    _enforce(monkeypatch, (me, "can_use", improve_session))  # can_edit revoked
    with pytest.raises(AppError) as denied:
        await _resolve(db, collector_case, private_scopes)
    assert (denied.value.status, denied.value.code) == (403, "permission_required")
    assert model.await_count == 2
    workspace.assert_not_called()


async def test_a_sealed_lease_memo_falls_back_before_deciding(
    db: AsyncSession,
    collector_case: CollectorCase,
    private_scopes: PrivateScopes,
    improve_session: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enforce(monkeypatch)
    with fresh_run_decision_scope() as memo:
        memo.seal()
        with pytest.raises(PreparedRunUnsupportedError) as unsupported:
            await _resolve(db, collector_case, private_scopes)
    assert "runtime_profile:skill_builder" in str(unsupported.value)


async def test_shadow_keeps_the_legacy_answer(
    db: AsyncSession,
    collector_case: CollectorCase,
    private_scopes: PrivateScopes,
    improve_session: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.authz.runtime._current", AuthzService(FakeAuthz(), mode="shadow")
    )  # nothing granted: shadow must still answer like legacy
    monkeypatch.setattr(system_models, "resolve_system_model", AsyncMock(side_effect=ReachedModel))
    decide = AsyncMock()
    monkeypatch.setattr(run_access, "authorize_builder_target", decide)
    with pytest.raises(ReachedModel):
        await _resolve(db, collector_case, private_scopes)
    decide.assert_not_awaited()


def _trace(db: AsyncSession, monkeypatch: pytest.MonkeyPatch, *, before_lock=None) -> list[str]:
    events: list[str] = []
    decide, scalar = run_access.authorize_builder_target, db.scalar

    async def traced_decide(db_, authz, context, source_skill_id):
        events.append(f"decide:{source_skill_id}")
        await decide(db_, authz, context, source_skill_id)

    async def traced_scalar(statement, *args, **kwargs):
        if getattr(statement, "_for_update_arg", None) is not None:
            if before_lock is not None:
                await before_lock()
            events.append("lock")
        return await scalar(statement, *args, **kwargs)

    monkeypatch.setattr(run_access, "authorize_builder_target", traced_decide)
    monkeypatch.setattr(db, "scalar", traced_scalar)
    return events


async def test_the_target_is_decided_before_the_session_lock(
    db: AsyncSession,
    collector_case: CollectorCase,
    private_scopes: PrivateScopes,
    skill_case: SkillCase,
    improve_session: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    me = f"user:{private_scopes.first.user_id}"
    _enforce(monkeypatch, (me, "can_use", improve_session), (me, "can_edit", improve_session))
    monkeypatch.setattr(system_models, "resolve_system_model", AsyncMock(side_effect=ReachedModel))
    events = _trace(db, monkeypatch)
    with pytest.raises(ReachedModel):
        await _resolve(db, collector_case, private_scopes)
    assert events == [f"decide:{skill_case.first.id}", "lock"]


async def test_a_target_changed_before_the_lock_is_decided_again(
    db: AsyncSession,
    collector_case: CollectorCase,
    private_scopes: PrivateScopes,
    skill_case: SkillCase,
    improve_session: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = await skill_service.create_text_skill(
        db,
        TextSkillCreation(
            name="Other",
            slug="f13-other",
            description="d",
            content='---\nname: f13-other\ndescription: "Other"\n---\nx\n',
        ),
        private_scopes.first,
    )
    await db.commit()
    me = f"user:{private_scopes.first.user_id}"
    _enforce(monkeypatch, (me, "can_use", improve_session), (me, "can_edit", improve_session))

    async def retarget() -> None:
        await db.execute(update(SkillBuilderSession).values(source_skill_id=other.id))

    events = _trace(db, monkeypatch, before_lock=retarget)
    with pytest.raises(AppError) as denied:
        await _resolve(db, collector_case, private_scopes)
    assert denied.value.code == "SKILL_NOT_FOUND"  # nothing granted on the new target
    assert events == [f"decide:{skill_case.first.id}", "lock", f"decide:{other.id}"]
