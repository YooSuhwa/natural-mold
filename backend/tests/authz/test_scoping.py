"""Private scope requires owner, organization and company even for operators."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import select

from app.authz import scoping
from tests.authz.scoping_fixture import RequiredScopedResource, ScopedChild, ScopedResource
from tests.authz.scoping_fixture import scope_fixture as scope_fixture

if TYPE_CHECKING:
    from tests.authz.scoping_fixture import ScopeFixture


def test_required_scope_columns_keep_typed_private_query(scope_fixture: ScopeFixture) -> None:
    # Given: a private table whose owner and scope columns are all non-null.
    statement = select(RequiredScopedResource.id)
    # When: that model is passed to the same typed private boundary.
    scoped = scoping.private_scoped(statement, RequiredScopedResource, scope_fixture.context)
    # Then: required columns retain the query's scalar row type and behavior.
    assert scope_fixture.connection.scalars(scoped).all() == [1]


@pytest.mark.parametrize("operator", [False, True])
def test_private_query_excludes_other_org_owner_and_tenant_when_operator_changes(
    scope_fixture: ScopeFixture, operator: bool
) -> None:
    # Given: rows belonging to two orgs, two owners and an inconsistent company pair.
    context = replace(scope_fixture.context, is_super_user=operator)
    # When: the private resource query runs in the explicit current organization.
    statement = scoping.private_scoped(select(ScopedResource.id), ScopedResource, context)
    # Then: only the current user's row in that exact org/company is returned.
    assert scope_fixture.connection.scalars(statement).all() == [1]


def test_private_query_changes_rows_when_same_user_selects_another_company(
    scope_fixture: ScopeFixture,
) -> None:
    # Given: the same user participates in a second company and organization.
    context = scope_fixture.second
    # When: a new private query receives that explicitly selected context.
    statement = scoping.private_scoped(select(ScopedResource.id), ScopedResource, context)
    # Then: the first organization's row is absent.
    assert scope_fixture.connection.scalars(statement).all() == [2]


def test_org_query_includes_other_owners_only_within_exact_org_company(
    scope_fixture: ScopeFixture,
) -> None:
    # Given: an organization resource query, whose ownership policy is checked separately.
    statement = select(ScopedResource.id).order_by(ScopedResource.id)
    # When: the organization boundary is applied.
    scoped = scoping.org_scoped(statement, ScopedResource, scope_fixture.context)
    # Then: another owner in that org is a candidate; foreign and platform rows are not.
    assert scope_fixture.connection.scalars(scoped).all() == [1, 3]


def test_missing_org_returns_no_rows_when_owner_matches(scope_fixture: ScopeFixture) -> None:
    # Given: an explicit organization with no resource rows, for the same owner.
    context = replace(scope_fixture.context, org_id=UUID(int=999))
    # When: the owner-qualified private query executes.
    statement = scoping.private_scoped(select(ScopedResource.id), ScopedResource, context)
    # Then: ownership alone cannot return anything.
    assert scope_fixture.connection.scalars(statement).all() == []


def test_child_query_checks_parent_scope_when_child_has_no_scope_columns(
    scope_fixture: ScopeFixture,
) -> None:
    # Given: every parent has one child, including foreign and platform parents.
    parent = scoping.PrivateParent(
        model=ScopedResource, on=ScopedChild.parent_id == ScopedResource.id
    )
    # When: the child query joins its private parent through the centralized helper.
    statement = scoping.private_parent_scoped(select(ScopedChild.id), parent, scope_fixture.context)
    # Then: the parent's owner/company/org jointly limit visible children.
    assert scope_fixture.connection.scalars(statement).all() == [1]
