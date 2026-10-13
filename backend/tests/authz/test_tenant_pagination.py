"""Company list cursors are opaque and bound to the exact request scope."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.exceptions import AppError
from app.services.tenants.pagination import (
    TenantCursor,
    TenantCursorScope,
    TenantPageQuery,
    decode_cursor,
    encode_cursor,
)


def tenant_scope(tenant_id: UUID, filter_key: str = "status=active") -> TenantCursorScope:
    """Build the shared scope used by the tenant-list cursor cases."""

    return TenantCursorScope(
        list_kind="tenant_organizations", tenant_id=tenant_id, filter_key=filter_key
    )


def test_tenant_cursor_round_trips_stable_position_with_exact_scope() -> None:
    # Given: one tenant list request and a deterministic keyset position.
    scope = tenant_scope(uuid4())
    cursor = TenantCursor(
        scope=scope,
        sort_value=datetime(2026, 10, 13, tzinfo=UTC),
        position=uuid4(),
    )
    # When: the list emits and later resumes its opaque cursor.
    resumed = decode_cursor(encode_cursor(cursor), scope)
    # Then: the exact scope and stable sort position are preserved.
    assert resumed == cursor


@pytest.mark.parametrize(
    "requested_scope",
    [
        TenantCursorScope(
            list_kind="tenant_organizations", tenant_id=uuid4(), filter_key="status=active"
        ),
        TenantCursorScope(
            list_kind="tenant_members", tenant_id=uuid4(), filter_key="status=active"
        ),
        TenantCursorScope(
            list_kind="tenant_organizations", tenant_id=uuid4(), filter_key="status=suspended"
        ),
    ],
)
def test_cursor_rejects_other_tenant_list_kind_and_filter(
    requested_scope: TenantCursorScope,
) -> None:
    # Given: a cursor issued for a different tenant organization listing.
    issued_scope = tenant_scope(uuid4())
    cursor = TenantCursor(
        scope=issued_scope,
        sort_value=datetime(2026, 10, 13, tzinfo=UTC),
        position=uuid4(),
    )
    # When: a caller replays it against a different tenant, list, or filter.
    with pytest.raises(AppError) as raised:
        decode_cursor(encode_cursor(cursor), requested_scope)
    # Then: the existing invalid-cursor contract denies without exposing the position.
    assert (raised.value.status, raised.value.code) == (422, "invalid_cursor")
    assert str(cursor.position) not in raised.value.message


def test_cursor_rejects_malformed_payload() -> None:
    # Given: a valid tenant scope and a malformed opaque value.
    scope = tenant_scope(uuid4())
    # When: the value is decoded.
    with pytest.raises(AppError) as raised:
        decode_cursor("not-a-valid-cursor", scope)
    # Then: the shared invalid-cursor contract is retained.
    assert (raised.value.status, raised.value.code) == (422, "invalid_cursor")


def test_tenant_page_query_uses_directory_default_and_bounds() -> None:
    # Given: company list query input.
    # When: the default and an oversized page are parsed.
    default_query = TenantPageQuery()
    with pytest.raises(ValidationError):
        TenantPageQuery(limit=201)
    # Then: it preserves the product's 50/200 page contract.
    assert default_query.limit == 50
