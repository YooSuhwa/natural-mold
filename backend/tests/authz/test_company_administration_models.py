"""Company invitation, organization archive, and run materialization schema contracts."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, String, Table, UniqueConstraint

from app.database import Base
from app.models import TenantInvitation


def _table(name: str) -> Table:
    return Base.metadata.tables[name]


def _checks(table_name: str) -> set[str]:
    return {
        str(constraint.name)
        for constraint in _table(table_name).constraints
        if isinstance(constraint, CheckConstraint)
    }


def _foreign_key(table_name: str, column_name: str) -> ForeignKeyConstraint:
    return next(
        constraint
        for constraint in _table(table_name).foreign_key_constraints
        if tuple(constraint.column_keys) == (column_name,)
    )


def test_company_invitation_table_is_hash_only_and_tenant_scoped() -> None:
    table = _table("tenant_invitations")

    assert TenantInvitation.__table__ is table
    assert set(table.columns) >= {
        table.c.id,
        table.c.tenant_id,
        table.c.email,
        table.c.role,
        table.c.status,
        table.c.token_hash,
        table.c.created_by,
        table.c.created_at,
        table.c.expires_at,
        table.c.accepted_by,
        table.c.accepted_at,
        table.c.revoked_by,
        table.c.revoked_at,
    }
    assert "token" not in table.c
    assert "org_id" not in table.c
    assert table.c.role.nullable is True
    assert _foreign_key("tenant_invitations", "tenant_id").ondelete == "CASCADE"
    for actor in ("created_by", "accepted_by", "revoked_by"):
        foreign_key = _foreign_key("tenant_invitations", actor)
        assert foreign_key.referred_table.name == "users"
        assert foreign_key.ondelete == "SET NULL"


def test_company_invitation_constraints_and_pending_index_are_registered() -> None:
    table = _table("tenant_invitations")

    assert {
        "ck_tenant_invitations_email_normalized",
        "ck_tenant_invitations_role",
        "ck_tenant_invitations_status",
        "ck_tenant_invitations_expiration",
        "ck_tenant_invitations_accepted_actor_timestamp",
        "ck_tenant_invitations_revoked_actor_timestamp",
        "ck_tenant_invitations_terminal_state",
        "ck_tenant_invitations_token_hash",
    } <= _checks("tenant_invitations")
    assert any(
        isinstance(constraint, UniqueConstraint)
        and tuple(constraint.columns.keys()) == ("token_hash",)
        for constraint in table.constraints
    )
    pending = next(
        index
        for index in table.indexes
        if isinstance(index, Index) and index.name == "ix_tenant_invitations_pending"
    )
    assert tuple(pending.columns.keys()) == ("tenant_id", "email", "role")


def test_organization_archive_state_requires_matching_timestamp() -> None:
    table = _table("organizations")

    assert table.c.archived_at.nullable is True
    assert "ck_organizations_archive_state" in _checks("organizations")


def test_run_materialization_marker_is_an_atomic_non_secret_pair() -> None:
    table = _table("conversation_runs")

    assert table.c.runtime_materialized_at.nullable is True
    assert table.c.runtime_materialization_sha256.nullable is True
    digest_type = table.c.runtime_materialization_sha256.type
    assert isinstance(digest_type, String)
    assert digest_type.length == 64
    assert {
        "ck_conversation_runs_runtime_materialization_pair",
        "ck_conversation_runs_runtime_materialization_sha256",
    } <= _checks("conversation_runs")
    forbidden = {
        "compiled_config",
        "compiled_runtime",
        "materialized_config",
        "resource_closure",
        "secret_closure",
    }
    assert forbidden.isdisjoint(table.c.keys())
