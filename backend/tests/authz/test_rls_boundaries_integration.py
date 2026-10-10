"""Exercise direct-ID reads and mutations with a real non-bypass PostgreSQL role."""

from uuid import uuid4

import pytest
import sqlalchemy as sa
from psycopg.errors import InsufficientPrivilege
from sqlalchemy.exc import DBAPIError

from tests.authz.rls_boundary_fixture import CompanyRows, two_companies
from tests.authz.test_rls_inventory_integration import RlsLab, tenant_actor
from tests.authz.test_rls_inventory_integration import rls_lab as rls_lab

pytestmark = pytest.mark.integration
PAYLOAD_TABLES = (
    "tenants",
    "agents",
    "conversations",
    "message_events",
    "message_event_chunks",
    "artifact_versions",
    "agent_api_threads",
    "agent_api_runs",
    "credential_audit_logs",
)


@pytest.fixture(scope="module")
def companies(rls_lab: RlsLab) -> tuple[sa.MetaData, CompanyRows, CompanyRows]:
    with rls_lab.engine.begin() as connection:
        return two_companies(connection)


@pytest.mark.parametrize("name", PAYLOAD_TABLES)
def test_direct_id_read_sees_only_own_company(
    rls_lab: RlsLab, companies: tuple[sa.MetaData, CompanyRows, CompanyRows], name: str
) -> None:
    # Given: both companies have actual payload rows, not just parent references.
    metadata, own, other = companies
    table = metadata.tables[name]
    with rls_lab.engine.begin() as connection:
        tenant_actor(connection, rls_lab)
        # When: the app misses the parent join and directly looks up both identifiers.
        rows = connection.scalars(
            sa.select(table.c.id).where(table.c.id.in_((own.rows[name], other.rows[name])))
        ).all()
        # Then: the child/history payload obeys the company boundary by itself.
        assert rows == [own.rows[name]]


@pytest.mark.parametrize("name", PAYLOAD_TABLES)
def test_direct_id_update_cannot_mutate_other_company(
    rls_lab: RlsLab, companies: tuple[sa.MetaData, CompanyRows, CompanyRows], name: str
) -> None:
    # Given: a foreign identifier is known and the app forgot authorization.
    metadata, _, other = companies
    table = metadata.tables[name]
    with rls_lab.engine.begin() as connection:
        tenant_actor(connection, rls_lab)
        # When: UPDATE targets that row without any parent/tenant filter.
        result = connection.execute(
            sa.update(table)
            .where(table.c.id == other.rows[name])
            .values(id=other.rows[name])
            .returning(table.c.id)
        ).all()
        # Then: no row qualifies for mutation through the role's USING predicate.
        assert result == []


@pytest.mark.parametrize("name", PAYLOAD_TABLES)
def test_direct_id_delete_cannot_remove_other_company(
    rls_lab: RlsLab, companies: tuple[sa.MetaData, CompanyRows, CompanyRows], name: str
) -> None:
    metadata, _, other = companies
    table = metadata.tables[name]
    # Given: a foreign payload identifier is known to the tenant app role.
    with rls_lab.engine.begin() as connection:
        tenant_actor(connection, rls_lab)
        # When: DELETE targets that identifier without a parent join.
        removed = connection.execute(
            sa.delete(table).where(table.c.id == other.rows[name]).returning(table.c.id)
        ).all()
        # Then: RLS makes the foreign record unavailable for deletion.
        assert removed == []


@pytest.mark.parametrize("name", PAYLOAD_TABLES)
def test_direct_insert_rejects_foreign_scope_or_parent(
    rls_lab: RlsLab, companies: tuple[sa.MetaData, CompanyRows, CompanyRows], name: str
) -> None:
    metadata, _, other = companies
    table = metadata.tables[name]
    with rls_lab.engine.begin() as connection:
        # Given: a complete foreign row is captured by the trusted fixture writer.
        prototype = dict(
            connection.execute(sa.select(table).where(table.c.id == other.rows[name]))
            .mappings()
            .one()
        )
        prototype["id"] = uuid4()
        for column in (
            "assistant_msg_id",
            "public_id",
            "runtime_name",
            "slug",
            "secrets_namespace",
        ):
            if column in prototype:
                prototype[column] = uuid4().hex
        if name == "artifact_versions":
            prototype["version_number"] = 2
        tenant_actor(connection, rls_lab)
        # When: INSERT directly references a foreign owner/parent that FK checks accept.
        with pytest.raises(DBAPIError) as failure, connection.begin_nested():
            connection.execute(sa.insert(table).values(**prototype))
        # Then: WITH CHECK, rather than a unique/FK error, rejects the insertion.
        assert isinstance(failure.value.orig, InsufficientPrivilege)


@pytest.mark.parametrize(
    ("name", "parent"),
    [
        ("message_events", "conversation_id"),
        ("artifact_versions", "artifact_id"),
        ("agent_api_threads", "conversation_id"),
        ("agent_api_runs", "deployment_id"),
        ("agent_api_runs", "thread_id"),
        ("agent_api_runs", "conversation_id"),
        ("credential_audit_logs", "credential_id"),
        ("message_event_chunks", "message_event_id"),
    ],
)
def test_existing_child_cannot_be_reparented_across_companies(
    rls_lab: RlsLab, companies: tuple[sa.MetaData, CompanyRows, CompanyRows], name: str, parent: str
) -> None:
    metadata, own, other = companies
    table = metadata.tables[name]
    with rls_lab.engine.begin() as connection:
        foreign_parent = connection.scalar(
            sa.select(table.c[parent]).where(table.c.id == other.rows[name])
        )
        # Given: the existing child is legitimately visible to the tenant app role.
        tenant_actor(connection, rls_lab)
        # When: a missed check tries to move it to another company's parent.
        with pytest.raises(DBAPIError) as failure, connection.begin_nested():
            connection.execute(
                sa.update(table)
                .where(table.c.id == own.rows[name])
                .values({parent: foreign_parent})
            )
        # Then: the post-update WITH CHECK predicate rejects the valid cross-company FK.
        assert isinstance(failure.value.orig, InsufficientPrivilege)


@pytest.mark.parametrize(
    ("name", "column"),
    [
        ("agents", "name"),
        ("message_events", "external_trace_id"),
        ("message_event_chunks", "assistant_msg_id"),
        ("artifact_versions", "object_key"),
        ("agent_api_threads", "external_user"),
        ("agent_api_runs", "error_message"),
        ("credential_audit_logs", "error"),
    ],
)
def test_owned_payload_can_be_updated_with_own_parent(
    rls_lab: RlsLab,
    companies: tuple[sa.MetaData, CompanyRows, CompanyRows],
    name: str,
    column: str,
) -> None:
    metadata, own, _ = companies
    table = metadata.tables[name]
    # Given: the tenant owns the row and every related parent.
    with rls_lab.engine.begin() as connection:
        tenant_actor(connection, rls_lab)
        # When: the normal app role updates a real payload field.
        updated = connection.scalar(
            sa.update(table)
            .where(table.c.id == own.rows[name])
            .values({column: "modified-by-owner"})
            .returning(table.c[column])
        )
        # Then: valid writes succeed; the negative checks are not a deny-all artifact.
        assert updated == "modified-by-owner"
