"""Company and organization identifier rules."""

from typing import Annotated, Final
from uuid import UUID

from pydantic import StringConstraints, TypeAdapter
from pydantic_core import PydanticCustomError

from app.marketplace.publish_common import slugify

DEFAULT_ORGANIZATION_SLUG: Final = "default"
ORGANIZATION_SLUG_MAX_LENGTH: Final = 120
TenantSlug = Annotated[
    str,
    StringConstraints(
        min_length=3,
        max_length=40,
        pattern=r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$",
    ),
]
TENANT_SLUG_ADAPTER: Final = TypeAdapter(TenantSlug)


def parse_tenant_slug(value: str) -> str:
    return TENANT_SLUG_ADAPTER.validate_python(value)


def organization_slug_base(value: str) -> str:
    return slugify(value, fallback="org")[:ORGANIZATION_SLUG_MAX_LENGTH]


def organization_slug_for_ordinal(base: str, ordinal: int) -> str:
    if ordinal < 1:
        raise PydanticCustomError(
            "organization_slug_ordinal", "organization slug ordinal must be positive"
        )
    if ordinal == 1:
        return base[:ORGANIZATION_SLUG_MAX_LENGTH]
    suffix = f"-{ordinal}"
    available = ORGANIZATION_SLUG_MAX_LENGTH - len(suffix)
    return f"{base[:available]}{suffix}"


def tenant_secrets_namespace(tenant_id: UUID) -> str:
    return f"tenants/{tenant_id}"
