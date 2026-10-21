"""``TENANT_DEFAULT_LIMITS``: the enforced company limit keys a new company starts with.

Imported by the settings validator, so this module must not import ``app.config``.
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class DefaultTenantLimits(BaseModel):
    """Every key a company limit is enforced by (``app.tenancy.limit_capacity``/``limits``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    member_limit: int | None = Field(default=None, ge=0)
    monthly_spend_limit: Decimal | None = Field(default=None, ge=0)
    concurrent_run_limit: int | None = Field(default=None, ge=0)
    request_rate_limit: int | None = Field(default=None, ge=0)
    allow_system_models: bool | None = None


def parse_default_limits(raw: str) -> dict[str, JsonValue]:
    """The keys that were set; an absent key means unlimited/allowed. Raises on bad input."""
    limits = DefaultTenantLimits.model_validate_json(raw)
    return limits.model_dump(mode="json", exclude_unset=True)
