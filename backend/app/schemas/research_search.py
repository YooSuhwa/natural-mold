"""Organization-owned research-search provider permissions and call budget."""

from datetime import datetime
from ipaddress import ip_address
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.outbound.hosts import is_ip_literal, private_allowed, validate_host_pattern
from app.outbound.transport import is_blocked_address

RESEARCH_SEARCH_POLICY_KEY = "research_search"
SearchProvider = Literal["tavily", "searxng"]


class ResearchSearchPolicy(BaseModel):
    """Providers are attempted in the configured order. Each dispatch consumes one attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    providers: tuple[SearchProvider, ...] = Field(max_length=2)
    searxng_endpoint: str | None = Field(default=None, max_length=2048)
    monthly_attempt_limit: int = Field(ge=0, le=2_147_483_647, strict=True)

    @field_validator("searxng_endpoint")
    @classmethod
    def https_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.isascii() or not value.isprintable() or any(c in value for c in " \\?#"):
            raise ValueError("Use a fixed HTTPS endpoint without a query or fragment")
        parts = urlsplit(value)
        host = parts.hostname
        if parts.scheme != "https" or not host or "@" in parts.netloc or "*" in host:
            raise ValueError("Use a fixed HTTPS endpoint without credentials")
        if parts.port == 0:
            raise ValueError("Port must be positive")
        validate_host_pattern(host)
        if (
            is_ip_literal(host)
            and is_blocked_address(ip_address(host))
            and not private_allowed(host)
        ):
            raise ValueError("Address is blocked by the outbound network policy")
        return value

    @model_validator(mode="after")
    def check_provider_configuration(self) -> Self:
        if len(self.providers) != len(set(self.providers)):
            raise ValueError("Each search provider can appear only once")
        if ("searxng" in self.providers) != (self.searxng_endpoint is not None):
            raise ValueError("Configure a SearXNG endpoint exactly when SearXNG is enabled")
        return self


class ResearchSearchPolicyView(BaseModel):
    """An absent policy is distinguishable from an explicitly empty provider list."""

    policy: ResearchSearchPolicy | None
    updated_at: datetime
