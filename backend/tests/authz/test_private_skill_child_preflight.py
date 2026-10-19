"""Fresh exact-source public child preflight witnesses; no external effects."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal, assert_never
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.authz.runtime import legacy_service
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.skill import Skill
from app.models.skill_evaluation import SkillEvaluationSet
from app.models.skill_revision import SkillRevision
from app.routers import skill_evaluations_support, skill_revisions
from app.services import skill_evaluation_service, skill_revision_service
from app.skills import service as skills

LEGACY = legacy_service()

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]
EXPECTED_SOURCE = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("surface", ["revision_files", "rollback", "evaluation"])
@pytest.mark.parametrize("missing", [False, True])
async def test_public_child_denial_precedes_pending_private_sql(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    surface: Literal["revision_files", "rollback", "evaluation"],
    missing: bool,
) -> None:
    assert Path(skill_revision_service.__file__).is_relative_to(EXPECTED_SOURCE)
    assert Path(skill_evaluation_service.__file__).is_relative_to(EXPECTED_SOURCE)
    assert Path(skill_revisions.__file__).is_relative_to(EXPECTED_SOURCE)
    own, foreign = private_scopes.first, private_scopes.second
    own_skill = Skill(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="Owned",
        slug="f4-owned",
        kind="text",
    )
    foreign_skill = Skill(
        user_id=foreign.user_id,
        org_id=foreign.org_id,
        tenant_id=foreign.tenant_id,
        name="Foreign original",
        slug="f4-foreign",
        kind="text",
    )
    db.add_all([own_skill, foreign_skill])
    await db.flush()
    revision = SkillRevision(
        skill_id=foreign_skill.id,
        user_id=foreign.user_id,
        revision_number=1,
        operation="create",
        object_key="dummy-unused",
    )
    evaluation = SkillEvaluationSet(
        skill_id=foreign_skill.id, user_id=foreign.user_id, name="Foreign evaluation", evals=[]
    )
    db.add_all([revision, evaluation])
    await db.commit()
    foreign_id = foreign_skill.id
    foreign_skill.name = "UNAUTHORIZED PRE-DENIAL WRITE"
    user = CurrentUser(id=own.user_id, email="dummy@example.test", name="Fixture")
    target_revision = uuid4() if missing else revision.id
    target_evaluation = uuid4() if missing else evaluation.id
    flush = Mock()
    event.listen(db.sync_session, "before_flush", flush)
    try:
        match surface:
            case "revision_files":
                await skill_revisions.list_skill_revision_files(
                    own_skill.id, target_revision, db=db, user=user, context=own, authz=LEGACY
                )
            case "rollback":
                await skill_revisions.rollback_skill_revision(
                    own_skill.id,
                    target_revision,
                    Request({"type": "http"}),
                    db=db,
                    user=user,
                    context=own,
                    _csrf=None,
                    authz=LEGACY,
                )
            case "evaluation":
                authorized = await skills.get_skill(db, own_skill.id, own)
                assert authorized is not None
                await skill_evaluations_support.load_evaluation_set_or_404(
                    db, skill=authorized, user=user, evaluation_set_id=target_evaluation
                )
            case unreachable:
                assert_never(unreachable)
    except AppError as denied:
        assert denied.status == 404
    else:
        pytest.fail("Expected private child denial")
    finally:
        event.remove(db.sync_session, "before_flush", flush)
    with db.no_autoflush:
        stored = await db.scalar(select(Skill.name).where(Skill.id == foreign_id))
    remains_staged = foreign_skill in db.dirty
    await db.rollback()
    print(
        f"F4 surface={surface} missing={missing} denied=404 flushes={flush.call_count} "
        f"stored={stored!r} remains_staged={remains_staged}"
    )
    assert flush.call_count == 0
    assert stored == "Foreign original"
    assert remains_staged
