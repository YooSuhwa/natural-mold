"""B-01 executes the live chain and enforces tenant boundaries in PostgreSQL."""

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from app.models.organization import Organization, OrganizationMember, Tenant
from app.models.organization_group import Group, GroupMember
from app.models.organization_identity import SsoConnection, UserIdentity
from app.models.user import User
from schema_drafts.org_authz.schema_contract import schema_contract
from tests.authz.organization_migration_fixture import organization_lab
from tests.authz.test_organization_models import CORE_TABLES

pytestmark = pytest.mark.integration


def test_live_upgrade_downgrade_repeats_and_preserves_reviewed_constraints() -> None:
    # Given: actual m77 history upgraded through the deployable m80 head.
    with organization_lab() as lab:
        expected = {table.name: table for table in schema_contract().tables}
        for _ in range(2):
            inspector = sa.inspect(lab.engine)
            # When: the live schema is reflected, rather than constructed by create_all.
            for name in CORE_TABLES:
                columns = inspector.get_columns(name)
                assert {(c["name"], c["nullable"]) for c in columns} == {
                    (c.name, c.nullable) for c in expected[name].columns
                }
                actual_fks = {
                    (
                        fk["name"],
                        tuple(fk["constrained_columns"]),
                        fk["referred_table"],
                        tuple(fk["referred_columns"]),
                        fk.get("options", {}).get("ondelete"),
                    )
                    for fk in inspector.get_foreign_keys(name)
                }
                assert actual_fks == {
                    (
                        fk.name,
                        fk.columns,
                        fk.targets[0].split(".")[0],
                        tuple(target.split(".")[1] for target in fk.targets),
                        fk.ondelete,
                    )
                    for fk in expected[name].foreign_keys
                }
                assert inspector.get_pk_constraint(name)["name"] == expected[name].primary_key_name
                assert {check["name"] for check in inspector.get_check_constraints(name)} == {
                    check.name for check in expected[name].checks
                }
                assert {
                    (unique["name"], tuple(unique["column_names"]))
                    for unique in inspector.get_unique_constraints(name)
                } == {(unique.name, unique.columns) for unique in expected[name].unique_constraints}
            # Then: downgrade returns to m77 and the same live upgrade works again.
            command.downgrade(lab.config, "m77_side_chat_link")
            assert CORE_TABLES.isdisjoint(sa.inspect(lab.engine).get_table_names())
            assert "last_active_org_id" not in {
                column["name"] for column in sa.inspect(lab.engine).get_columns("users")
            }
            command.upgrade(lab.config, "m80_org_capabilities_invitations")


def test_mapped_models_reject_cross_org_membership_and_clear_deleted_active_org() -> None:
    # Given: the current head schema, two companies, one member in A, and a group in A.
    with organization_lab(at_head=True) as lab, Session(lab.engine) as session, session.begin():
        user = User(email="org-model@example.test", name="Organization user")
        tenants = [Tenant(name=name, slug=name, secrets_namespace=name) for name in ("a", "b")]
        session.add_all([user, *tenants])
        session.flush()
        orgs = [Organization(tenant_id=t.id, name=t.name, slug=t.slug) for t in tenants]
        session.add_all(orgs)
        session.flush()
        session.add(OrganizationMember(org_id=orgs[0].id, tenant_id=tenants[0].id, user_id=user.id))
        group = Group(org_id=orgs[0].id, tenant_id=tenants[0].id, name="A group")
        session.add(group)
        session.flush()
        # When: a group membership tries to substitute another organization's scope.
        with pytest.raises(IntegrityError), session.begin_nested():
            session.execute(
                sa.insert(GroupMember).values(
                    group_id=group.id,
                    user_id=user.id,
                    org_id=orgs[1].id,
                    tenant_id=tenants[1].id,
                )
            )
        # Then: the valid membership works; deleting B clears the optional active pointer.
        session.add(
            GroupMember(
                group_id=group.id,
                user_id=user.id,
                org_id=orgs[0].id,
                tenant_id=tenants[0].id,
            )
        )
        user.last_active_org_id = orgs[1].id
        session.flush()
        session.delete(orgs[1])
        session.flush()
        session.refresh(user)
        assert user.last_active_org_id is None


def test_enterprise_identity_is_global_and_ldap_transport_requires_encryption() -> None:
    # Given: one identity accepted through one tenant's connection.
    # The mapped SsoConnection carries post-m80 columns (H-08), so use the head schema.
    with organization_lab(at_head=True) as lab, Session(lab.engine) as session, session.begin():
        tenant = Tenant(name="Identity", slug="identity", secrets_namespace="identity")
        user = User(email="identity@example.test", name="Identity user")
        session.add_all([tenant, user])
        session.flush()
        connection = SsoConnection(tenant_id=tenant.id, issuer="https://issuer.example.test")
        session.add(connection)
        session.flush()
        session.add(
            UserIdentity(
                user_id=user.id,
                connection_id=connection.id,
                issuer=connection.issuer,
                subject="one",
            )
        )
        session.flush()
        # When: another historical connection pointer tries to duplicate issuer/subject.
        with pytest.raises(IntegrityError), session.begin_nested():
            session.execute(
                sa.insert(UserIdentity).values(
                    user_id=user.id,
                    issuer=connection.issuer,
                    subject="one",
                )
            )
        # Then: the global identity survives connection deletion and plaintext LDAP is rejected.
        session.delete(connection)
        session.flush()
        identity = session.scalar(sa.select(UserIdentity))
        assert identity is not None
        session.refresh(identity)
        assert identity.connection_id is None
        with pytest.raises(IntegrityError), session.begin_nested():
            session.execute(
                sa.insert(SsoConnection).values(
                    tenant_id=tenant.id,
                    issuer="ldap:fixture",
                    protocol="ldap",
                    ldap_server_url="ldap://directory.example.test",
                    ldap_start_tls=False,
                )
            )


_CATALOG_QUERIES = (
    "SELECT table_name, column_name, data_type, is_nullable, column_default"
    " FROM information_schema.columns WHERE table_schema = 'public' ORDER BY 1, 2",
    "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid)"
    " FROM pg_constraint WHERE connamespace = 'public'::regnamespace ORDER BY 1, 2",
    "SELECT tablename, indexname, indexdef FROM pg_indexes"
    " WHERE schemaname = 'public' ORDER BY 1, 2",
    "SELECT tablename, policyname, cmd, roles::text, qual, with_check FROM pg_policies"
    " WHERE schemaname = 'public' ORDER BY 1, 2",
    "SELECT relname, relkind, relrowsecurity, relforcerowsecurity FROM pg_class"
    " WHERE relnamespace = 'public'::regnamespace ORDER BY 1",
    "SELECT tgrelid::regclass::text, tgname, pg_get_triggerdef(oid) FROM pg_trigger"
    " WHERE NOT tgisinternal ORDER BY 1, 2",
    "SELECT proname, pg_get_functiondef(oid) FROM pg_proc"
    " WHERE pronamespace = 'public'::regnamespace ORDER BY 1, 2",
    "SELECT version_num FROM alembic_version",
)


def _schema_and_rows(engine: sa.Engine) -> list[object]:
    with engine.connect() as connection:
        observed: list[object] = [
            [tuple(row) for row in connection.execute(sa.text(query))] for query in _CATALOG_QUERIES
        ]
        tables = sa.inspect(connection).get_table_names()
        quote = connection.dialect.identifier_preparer.quote
        observed.append(
            {
                table: connection.scalar(sa.text(f"SELECT count(*) FROM {quote(table)}"))  # noqa: S608
                for table in sorted(tables)
            }
        )
    return observed


def test_head_template_clone_matches_a_real_empty_head_upgrade() -> None:
    # Given: one empty database really upgraded to head, and one cloned from the lane template.
    with organization_lab() as real, organization_lab(at_head=True) as cloned:
        command.upgrade(real.config, "head")
        # Then: schema, RLS state, policies, triggers, functions, seeded rows and revision match.
        assert _schema_and_rows(cloned.engine) == _schema_and_rows(real.engine)
