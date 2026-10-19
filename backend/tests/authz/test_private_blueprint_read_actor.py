"""Fresh actor decisions occur before Blueprint target SQL and pending writes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState

from app.exceptions import AppError
from app.models.organization import OrganizationMember, TenantMember
from app.models.tool import Tool
from app.models.user import User
from app.routers.agent_blueprints import get_agent_blueprint, list_agent_blueprints
from tests.authz.blueprint_read_support import pending_tool

if TYPE_CHECKING:
    from tests.authz.blueprint_read_fixture import BlueprintReadCase

pytest_plugins = ["tests.authz.blueprint_read_fixture"]


@pytest.mark.parametrize("revocation", ["organization", "company", "account"])
async def test_revoked_actor_blocks_both_readers_before_target_sql_and_pending_insert(
    db: AsyncSession,
    blueprint_read_case: BlueprintReadCase,
    revocation: Literal["organization", "company", "account"],
) -> None:
    case = blueprint_read_case
    match revocation:
        case "organization":
            await db.execute(
                update(OrganizationMember)
                .where(
                    OrganizationMember.org_id == case.context.org_id,
                    OrganizationMember.user_id == case.context.user_id,
                )
                .values(status="removed")
                .execution_options(synchronize_session=False)
            )
        case "company":
            await db.execute(
                update(TenantMember)
                .where(
                    TenantMember.tenant_id == case.context.tenant_id,
                    TenantMember.user_id == case.context.user_id,
                )
                .values(status="removed")
                .execution_options(synchronize_session=False)
            )
        case "account":
            await db.execute(
                update(User)
                .where(User.id == case.context.user_id)
                .values(is_active=False)
                .execution_options(synchronize_session=False)
            )
        case unreachable:
            assert_never(unreachable)
    await db.commit()
    pending = pending_tool(db, case.context)
    sql: list[str] = []

    def record_statement(state: ORMExecuteState) -> None:
        sql.append(str(state.statement))

    event.listen(db.sync_session, "do_orm_execute", record_statement)
    try:
        with pytest.raises(AppError) as detail_error:
            await get_agent_blueprint(case.latest.id, db=db, user=case.actor, context=case.context)
        with pytest.raises(AppError) as list_error:
            await list_agent_blueprints(db=db, user=case.actor, context=case.context)
    finally:
        event.remove(db.sync_session, "do_orm_execute", record_statement)
    assert detail_error.value.status in {403, 404} and list_error.value.status in {403, 404}
    assert sql and all("agent_blueprints" not in statement for statement in sql)
    assert pending in db.new
    with db.no_autoflush:
        assert await db.scalar(select(Tool.id).where(Tool.id == pending.id)) is None


async def test_current_actor_queries_precede_normal_target_queries(
    db: AsyncSession, blueprint_read_case: BlueprintReadCase
) -> None:
    case = blueprint_read_case
    sql: list[str] = []

    def record_statement(state: ORMExecuteState) -> None:
        sql.append(str(state.statement))

    event.listen(db.sync_session, "do_orm_execute", record_statement)
    try:
        await get_agent_blueprint(case.latest.id, db=db, user=case.actor, context=case.context)
    finally:
        event.remove(db.sync_session, "do_orm_execute", record_statement)
    target_index = next(
        index for index, statement in enumerate(sql) if "agent_blueprints" in statement
    )
    assert target_index > 0
    assert any("users" in statement for statement in sql[:target_index])
    assert any("organization_members" in statement for statement in sql[:target_index])
    assert any("tenant_members" in statement for statement in sql[:target_index])
