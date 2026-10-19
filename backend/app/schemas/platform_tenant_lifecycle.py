"""Optional operator note for company suspend/resume."""

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


class PlatformTenantLifecycleRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str | None = Field(default=None, max_length=500)

    @field_validator("reason")
    @classmethod
    def _sanitize(cls, value: str | None) -> str | None:
        """Collapse control characters and surrounding whitespace; blank means absent."""
        if value is None:
            return None
        cleaned = _CONTROL.sub(" ", value).strip()
        return cleaned or None
