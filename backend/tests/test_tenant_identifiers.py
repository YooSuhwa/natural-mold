from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.tenant_overview import TenantIdentifierFields
from app.services.tenants.identifiers import (
    DEFAULT_ORGANIZATION_SLUG,
    organization_slug_base,
    organization_slug_for_ordinal,
    parse_tenant_slug,
    tenant_secrets_namespace,
)


@pytest.mark.parametrize("value", ["ab", "-abc", "abc-", "ABC", "회사", "a" * 41])
def test_tenant_slug_rejects_noncanonical_api_values(value: str) -> None:
    with pytest.raises(ValidationError):
        TenantIdentifierFields(slug=value)
    with pytest.raises(ValidationError):
        parse_tenant_slug(value)


@pytest.mark.parametrize("value", ["abc", "a-b", "a" * 40])
def test_tenant_slug_accepts_exact_public_boundaries(value: str) -> None:
    assert TenantIdentifierFields(slug=value).slug == value
    assert parse_tenant_slug(value) == value


def test_tenant_slug_schema_publishes_public_constraints() -> None:
    schema = TenantIdentifierFields.model_json_schema()["properties"]["slug"]
    assert schema == {
        "maxLength": 40,
        "minLength": 3,
        "pattern": "^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$",
        "title": "Slug",
        "type": "string",
    }


def test_organization_slug_rules_remain_separate_and_storage_bounded() -> None:
    base = organization_slug_base("A" * 120)
    assert organization_slug_base("한글") == "org"
    assert organization_slug_base("  ") == "org"
    assert organization_slug_base("Hello_World Name") == "hello-world-name"
    assert len(organization_slug_for_ordinal(base, 1)) == 120
    assert organization_slug_for_ordinal(base, 2) == f"{'a' * 118}-2"
    assert organization_slug_for_ordinal(base, 123) == f"{'a' * 116}-123"


def test_bootstrap_identifiers_are_exact() -> None:
    tenant_id = uuid4()
    assert DEFAULT_ORGANIZATION_SLUG == "default"
    assert tenant_secrets_namespace(tenant_id) == f"tenants/{tenant_id}"
