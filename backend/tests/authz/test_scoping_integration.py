"""Parent-scoped queries execute against owned PostgreSQL, not compiled SQL alone."""

from uuid import UUID

import pytest
from sqlalchemy import insert, select

from app.authz.org_context import OrgContext
from app.authz.scoping import PrivateParent, private_parent_scoped
from tests.authz.organization_migration_fixture import organization_lab
from tests.authz.scoping_fixture import ScopeBase, ScopedChild, ScopedResource

pytestmark = pytest.mark.integration


def test_private_parent_scope_excludes_other_company_in_postgres_for_operator() -> None:
    # Given: one owner has child records in two different companies and orgs.
    context = OrgContext(
        org_id=UUID(int=1), tenant_id=UUID(int=10), user_id=UUID(int=100), is_super_user=True
    )
    with organization_lab() as lab:
        ScopeBase.metadata.create_all(lab.engine)
        with lab.engine.begin() as connection:
            connection.execute(
                insert(ScopedResource),
                [
                    {
                        "id": 1,
                        "org_id": context.org_id,
                        "tenant_id": context.tenant_id,
                        "user_id": context.user_id,
                    },
                    {
                        "id": 2,
                        "org_id": UUID(int=2),
                        "tenant_id": UUID(int=20),
                        "user_id": context.user_id,
                    },
                ],
            )
            connection.execute(
                insert(ScopedChild), [{"id": 1, "parent_id": 1}, {"id": 2, "parent_id": 2}]
            )
            # When: the operator's private child lookup executes on PostgreSQL.
            parent = PrivateParent(
                model=ScopedResource, on=ScopedChild.parent_id == ScopedResource.id
            )
            statement = private_parent_scoped(select(ScopedChild.id), parent, context)
            actual = connection.scalars(statement).all()
            # Then: the same owner in another company remains invisible.
            assert actual == [1]
