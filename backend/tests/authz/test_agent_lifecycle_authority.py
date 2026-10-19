"""Lifecycle governance is derived from fresh persisted account and role geometry."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_lifecycle_scope import (
    AgentLifecycleAuthorization,
    authorize_agent_lifecycle,
    load_agent_lifecycle_authority,
    record_agent_lifecycle_success,
)
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.audit_event import AuditEvent
from app.models.model import Model
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRoleGrant,
    Tenant,
    TenantMember,
    TenantRoleGrant,
)
from app.models.organization_group import Group, GroupMember
from app.models.user import User
from app.schemas.agent_lifecycle import AgentLifecycleMetadataResponse

Action = Literal["restore", "purge"]


@dataclass(frozen=True, slots=True)
class LifecycleGraph:
    agent_id: UUID
    org_id: UUID
    owner: CurrentUser
    direct_admin: CurrentUser
    group_admin: CurrentUser
    company_admin: CurrentUser
    member: CurrentUser
    operator: CurrentUser


def _current(row: User) -> CurrentUser:
    return CurrentUser(
        id=row.id,
        email=row.email,
        name=row.name,
        is_super_user=row.is_super_user,
    )


@pytest.fixture
async def lifecycle_graph(db: AsyncSession) -> LifecycleGraph:
    tenant = Tenant(name="Lifecycle", slug="lifecycle", secrets_namespace="lifecycle")
    db.add(tenant)
    await db.flush()
    organization = Organization(tenant_id=tenant.id, name="Lifecycle", slug="lifecycle")
    db.add(organization)
    await db.flush()
    labels = ("owner", "direct-admin", "group-admin", "member", "company-admin")
    rows = {label: User(email=f"{label}@example.test", name=label) for label in labels}
    operator = User(email="operator@example.test", name="operator", is_super_user=True)
    db.add_all([*rows.values(), operator])
    await db.flush()
    org_members = tuple(rows[label] for label in labels[:4])
    db.add_all(
        [TenantMember(tenant_id=tenant.id, user_id=row.id) for row in rows.values()]
        + [
            OrganizationMember(org_id=organization.id, tenant_id=tenant.id, user_id=row.id)
            for row in org_members
        ]
    )
    db.add(TenantRoleGrant(tenant_id=tenant.id, user_id=rows["company-admin"].id, role="admin"))
    db.add(
        OrganizationRoleGrant(
            org_id=organization.id,
            tenant_id=tenant.id,
            role="admin",
            principal_type="user",
            principal_id=rows["direct-admin"].id,
        )
    )
    group = Group(org_id=organization.id, tenant_id=tenant.id, name="Governors")
    db.add(group)
    await db.flush()
    db.add_all(
        [
            GroupMember(
                group_id=group.id,
                user_id=rows["group-admin"].id,
                org_id=organization.id,
                tenant_id=tenant.id,
            ),
            OrganizationRoleGrant(
                org_id=organization.id,
                tenant_id=tenant.id,
                role="admin",
                principal_type="group",
                principal_id=group.id,
            ),
        ]
    )
    model = Model(provider="openai", model_name="lifecycle", display_name="Lifecycle")
    db.add(model)
    await db.flush()
    agent = Agent(
        user_id=rows["owner"].id,
        org_id=organization.id,
        tenant_id=tenant.id,
        name="Lifecycle agent",
        system_prompt="PRIVATE_PROMPT_SENTINEL",
        model_id=model.id,
    )
    db.add(agent)
    await db.commit()
    return LifecycleGraph(
        agent.id,
        organization.id,
        _current(rows["owner"]),
        _current(rows["direct-admin"]),
        _current(rows["group-admin"]),
        _current(rows["company-admin"]),
        _current(rows["member"]),
        _current(operator),
    )


@pytest.mark.parametrize(
    "case",
    [
        ("owner", (True, True, False)),
        ("direct_admin", (True, True, True)),
        ("group_admin", (True, True, True)),
        ("company_admin", (True, False, False)),
        ("member", None),
    ],
)
async def test_exact_lifecycle_role_matrix(
    db: AsyncSession,
    lifecycle_graph: LifecycleGraph,
    case: tuple[str, tuple[bool, bool, bool] | None],
) -> None:
    actor_name, expected = case
    actors = {
        "owner": lifecycle_graph.owner,
        "direct_admin": lifecycle_graph.direct_admin,
        "group_admin": lifecycle_graph.group_admin,
        "company_admin": lifecycle_graph.company_admin,
        "member": lifecycle_graph.member,
    }
    actor = actors[actor_name]
    if expected is not None:
        authority = await load_agent_lifecycle_authority(db, actor, lifecycle_graph.agent_id)
        assert (authority.can_archive, authority.can_restore, authority.can_purge) == expected
        return
    with pytest.raises(AppError) as denied:
        await load_agent_lifecycle_authority(db, actor, lifecycle_graph.agent_id)
    assert denied.value.status == 403
    assert denied.value.code == "AGENT_LIFECYCLE_PERMISSION_REQUIRED"


async def test_operator_and_missing_or_foreign_targets_are_indistinguishable(
    db: AsyncSession, lifecycle_graph: LifecycleGraph
) -> None:
    errors: list[tuple[int, str, str]] = []
    for agent_id in (lifecycle_graph.agent_id, UUID(int=0)):
        with pytest.raises(AppError) as hidden:
            await load_agent_lifecycle_authority(db, lifecycle_graph.operator, agent_id)
        errors.append((hidden.value.status, hidden.value.code, hidden.value.message))
    assert errors == [(404, "AGENT_NOT_FOUND", "에이전트를 찾을 수 없습니다")] * 2


@pytest.mark.parametrize(
    "action", ["restore", "purge"], ids=["company_admin_restore", "company_admin_purge"]
)
async def test_company_admin_cannot_restore_or_purge(
    db: AsyncSession, lifecycle_graph: LifecycleGraph, action: Action
) -> None:
    with pytest.raises(AppError) as denied:
        await authorize_agent_lifecycle(
            db,
            lifecycle_graph.company_admin,
            AgentLifecycleAuthorization(lifecycle_graph.agent_id, action),
        )
    assert denied.value.status == 403
    assert denied.value.code == "AGENT_LIFECYCLE_PERMISSION_REQUIRED"


async def test_purge_name_mismatch_writes_no_audit_or_name(
    db: AsyncSession, lifecycle_graph: LifecycleGraph
) -> None:
    confirmation = "Lifecycle agent WRONG_SENTINEL"
    with pytest.raises(AppError) as mismatch:
        await authorize_agent_lifecycle(
            db,
            lifecycle_graph.direct_admin,
            AgentLifecycleAuthorization(lifecycle_graph.agent_id, "purge", confirmation),
        )
    assert mismatch.value.status == 409
    assert mismatch.value.code == "AGENT_PURGE_NAME_MISMATCH"
    rows = list((await db.scalars(select(AuditEvent))).all())
    assert rows == []
    assert all(confirmation not in str(row.event_metadata) for row in rows)


async def test_nonowner_loader_selects_only_metadata_columns(
    db: AsyncSession, lifecycle_graph: LifecycleGraph
) -> None:
    statements: list[str] = []

    def capture_statement(_conn, _cursor, statement, _parameters, _context, _many) -> None:
        statements.append(statement)

    connection = await db.connection()
    event.listen(connection.sync_connection, "before_cursor_execute", capture_statement)
    try:
        authority = await load_agent_lifecycle_authority(
            db, lifecycle_graph.direct_admin, lifecycle_graph.agent_id
        )
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", capture_statement)
    assert AgentLifecycleMetadataResponse.model_validate(authority).name == "Lifecycle agent"
    sql = "\n".join(statements).lower()
    assert not any(
        forbidden in sql
        for forbidden in (
            "system_prompt",
            "model_params",
            "conversation",
            "agent_tools",
            "agent_skills",
            "agent_mcp_tools",
        )
    )


async def test_nonowner_success_audit_keeps_actor_and_owner_distinct(
    db: AsyncSession, lifecycle_graph: LifecycleGraph
) -> None:
    authority = await authorize_agent_lifecycle(
        db,
        lifecycle_graph.direct_admin,
        AgentLifecycleAuthorization(lifecycle_graph.agent_id, "archive"),
    )
    await record_agent_lifecycle_success(db, authority, "archive")
    await db.commit()
    row = await db.scalar(select(AuditEvent).where(AuditEvent.action == "agent.archive"))
    assert row is not None
    assert (row.actor_user_id, row.owner_user_id, row.target_owner_user_id) == (
        lifecycle_graph.direct_admin.id,
        lifecycle_graph.owner.id,
        lifecycle_graph.owner.id,
    )
    assert row.actor_user_id != row.target_owner_user_id


async def test_suspended_actor_loses_stale_authority(
    db: AsyncSession, lifecycle_graph: LifecycleGraph
) -> None:
    membership = await db.get(
        OrganizationMember, (lifecycle_graph.org_id, lifecycle_graph.direct_admin.id)
    )
    assert membership is not None
    membership.status = "suspended"
    await db.commit()
    with pytest.raises(AppError) as hidden:
        await load_agent_lifecycle_authority(
            db, lifecycle_graph.direct_admin, lifecycle_graph.agent_id
        )
    assert (hidden.value.status, hidden.value.code) == (404, "AGENT_NOT_FOUND")
