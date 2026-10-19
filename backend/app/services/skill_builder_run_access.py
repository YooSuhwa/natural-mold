"""Skill builder hidden-agent runs: session owner + target decision (plan 11.0; F-13).

Every run of a builder conversation (send, edit/regenerate, resume) loads the caller's
own private session (session owner; another user's session is the same 404 as a
missing one) and, in enforce mode, re-decides the session's target with the confirm
rule (``authorize_builder_target``): improving a skill needs ``skill#can_edit``, a new
skill ``cap_create_skill``. A revoked permission refuses the run (404/403/503 per 8.4)
before the builder tools are bound. Only ``enforce`` decides: legacy and shadow keep the
private session lookup as the answer (no extra query). The target is read and decided
before the session row lock is taken, so OpenFGA is not called under it unless the
target changed between the two reads.

Builder runs are not prepared-lease eligible (``require_lease_supported`` rejects the
``skill_builder`` profile). Under a lease's sealed run decision memo no OpenFGA call may
run, so the run is reported unsupported with that same reason before deciding, and the
lease falls back to the ordinary path, which decides here.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_agent_checks import current_authz
from app.authz.org_context import OrgContext
from app.authz.run_decisions import decisions_sealed
from app.authz.service import AuthzService
from app.authz.skill_access import authorize_builder_target
from app.authz.skill_builder_scope import private_skill_builder_predicate
from app.models.skill_builder_session import SkillBuilderSession


async def authorize_skill_builder_run(
    db: AsyncSession,
    context: OrgContext,
    source_skill_id: UUID | None,
    authz: AuthzService | None = None,
) -> None:
    """Enforce only: shadow keeps the legacy answer (the private session lookup)."""
    service = authz or current_authz()
    if service.mode != "enforce":
        return
    if decisions_sealed():
        from app.agent_runtime.prepared_run_input_policy import unsupported

        raise unsupported("runtime_profile:skill_builder")
    await authorize_builder_target(db, service, context, source_skill_id)


def _session_query(
    conversation_id: UUID, context: OrgContext
) -> Select[tuple[SkillBuilderSession]]:
    return (
        select(SkillBuilderSession)
        .where(
            SkillBuilderSession.conversation_id == conversation_id,
            private_skill_builder_predicate(context),
        )
        # Session-conversation 1:1 is a convention, not a DB constraint: take the
        # newest one so a duplicate never becomes MultipleResultsFound (R2).
        .order_by(SkillBuilderSession.created_at.desc())
        .limit(1)
    )


async def load_skill_builder_run_session(
    db: AsyncSession, conversation_id: UUID, context: OrgContext
) -> SkillBuilderSession | None:
    """The caller's session of this conversation (locked), authorized for a run.

    The target is read and decided without the row lock, then the session is locked and
    re-read; only a target that changed in between is decided again (under the lock).
    """
    query = _session_query(conversation_id, context)
    decided: tuple[UUID | None] | None = None  # the target decided before the lock
    if current_authz().mode == "enforce":
        unlocked = await db.execute(
            query.with_only_columns(SkillBuilderSession.source_skill_id).execution_options(
                autoflush=False
            )
        )
        row = unlocked.first()
        if row is not None:
            await authorize_skill_builder_run(db, context, row[0])
            decided = (row[0],)
    session = await db.scalar(
        query.with_for_update(of=SkillBuilderSession).execution_options(
            populate_existing=True, autoflush=False
        )
    )
    if session is not None and decided != (session.source_skill_id,):
        await authorize_skill_builder_run(db, context, session.source_skill_id)
    return session


__all__ = ["authorize_skill_builder_run", "load_skill_builder_run_session"]
