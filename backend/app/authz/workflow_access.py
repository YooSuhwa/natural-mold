"""Workflow decisions through ``AuthzService`` (workflow-starts-sharing-plan 3.3).

The legacy answer is the existing private lookup (the owner's own workflows), so
``AUTHZ_MODE=legacy`` keeps the same queries and the same ``WORKFLOW_NOT_FOUND``. Shadow and
enforce also read candidate rows (owned, or shared through an active grant, in the current
org) so OpenFGA can be asked about a workflow the caller does not own: ``can_run`` to see and
run it, ``can_edit`` to change the draft and publish, ``can_share`` to manage its grants.
Another organization's row stays 404 even when granted.

Runs stay the runner's own (S7): this module decides the workflow, never its runs.
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.authz.candidates import candidate_predicate
from app.authz.conversation_agent_checks import current_authz
from app.authz.org_context import OrgContext
from app.authz.principal import Principal
from app.authz.private_actor import require_private_actor
from app.authz.scoping import private_scoped
from app.authz.service import AuthzService, Verdict
from app.exceptions import NotFoundError
from app.models.workflow import Workflow

WORKFLOW_MIN_VISIBLE: Final = "can_run"  # consumer is the widest workflow relation


def workflow_not_found() -> NotFoundError:
    return NotFoundError("WORKFLOW_NOT_FOUND", "워크플로를 찾을 수 없습니다")


def _candidates(context: OrgContext) -> ColumnElement[bool]:
    return candidate_predicate(Workflow, "workflow", context)


async def _candidate(
    db: AsyncSession, workflow_id: UUID, context: OrgContext, *, for_update: bool
) -> Workflow | None:
    await require_private_actor(db, context)
    stmt = select(Workflow).where(Workflow.id == workflow_id, _candidates(context))
    if for_update:
        stmt = stmt.with_for_update()
    return await db.scalar(stmt.execution_options(autoflush=False))


async def _owned(
    db: AsyncSession, workflow_id: UUID, context: OrgContext, *, for_update: bool
) -> Workflow | None:
    stmt = private_scoped(select(Workflow).where(Workflow.id == workflow_id), Workflow, context)
    if for_update:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


async def authorize_workflow(
    db: AsyncSession,
    context: OrgContext,
    workflow_id: UUID,
    permission: str,
    *,
    for_update: bool = False,
    authz: AuthzService | None = None,
) -> Workflow:
    """The workflow when ``permission`` holds; 404 ``WORKFLOW_NOT_FOUND`` / 403 otherwise."""
    service = authz or current_authz()
    obj = f"workflow:{workflow_id}"
    if service.enforces("workflow"):
        found = await _candidate(db, workflow_id, context, for_update=for_update)
        await service.require(
            Principal.for_user(context),
            permission,
            obj,
            min_visible=WORKFLOW_MIN_VISIBLE,
            resource_org_id=found.org_id if found else None,
            legacy=Verdict.NOT_FOUND,  # unused: the type is enforced
            not_found=workflow_not_found(),
        )
        assert found is not None  # noqa: S101 - require raised for a missing row
        return found
    owned = await _owned(db, workflow_id, context, for_update=for_update)
    sample = owned
    if owned is None and service.mode != "legacy":
        sample = await _candidate(db, workflow_id, context, for_update=False)  # shadow only
    await service.require(
        Principal.for_user(context),
        permission,
        obj,
        min_visible=WORKFLOW_MIN_VISIBLE,
        resource_org_id=sample.org_id if sample else None,
        legacy=Verdict.ALLOW if owned is not None else Verdict.NOT_FOUND,
        not_found=workflow_not_found(),
    )
    assert owned is not None  # noqa: S101 - require raised for NOT_FOUND
    return owned


async def visible_workflows(
    db: AsyncSession,
    context: OrgContext,
    *,
    include_archived: bool = False,
    authz: AuthzService | None = None,
) -> list[Workflow]:
    """``GET /api/workflows``: the owner's own plus shared ones the caller may run."""
    service = authz or current_authz()
    legacy: list[Workflow] = []
    if not service.enforces("workflow"):
        stmt = private_scoped(select(Workflow), Workflow, context)
        legacy = list((await db.execute(_ordered(stmt, include_archived))).scalars())
    if service.mode == "legacy":
        return legacy
    await require_private_actor(db, context)
    extra = list(
        (
            await db.execute(
                _ordered(select(Workflow).where(_candidates(context)), include_archived)
            )
        ).scalars()
    )
    known = {row.id for row in legacy}
    rows = [*legacy, *(row for row in extra if row.id not in known)]
    kept = set(
        await service.filter_visible(
            Principal.for_user(context),
            "can_run",
            [f"workflow:{row.id}" for row in rows],
            legacy=[f"workflow:{row.id}" for row in legacy],
        )
    )
    return [row for row in rows if f"workflow:{row.id}" in kept]


def _ordered(stmt, include_archived: bool):  # type: ignore[no-untyped-def]
    if not include_archived:
        stmt = stmt.where(Workflow.archived_at.is_(None))
    return stmt.order_by(Workflow.updated_at.desc(), Workflow.id)


async def my_permissions(
    context: OrgContext,
    workflow: Workflow,
    permissions: list[str],
    *,
    authz: AuthzService | None = None,
) -> list[str]:
    """What the caller may do with a workflow it can see (the editor's buttons)."""
    service = authz or current_authz()
    owner = workflow.user_id == context.user_id and workflow.org_id == context.org_id
    return await service.my_permissions(
        Principal.for_user(context),
        f"workflow:{workflow.id}",
        permissions,
        legacy=list(permissions) if owner else [],
    )


__all__ = [
    "WORKFLOW_MIN_VISIBLE",
    "authorize_workflow",
    "my_permissions",
    "visible_workflows",
    "workflow_not_found",
]
