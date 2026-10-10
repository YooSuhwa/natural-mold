"""Frozen three-key installation adapter for historical m82 behavior."""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from pydantic import ConfigDict, JsonValue, TypeAdapter

LIMITS = TypeAdapter(dict[str, JsonValue], config=ConfigDict(allow_inf_nan=False))


@dataclass(frozen=True, slots=True)
class MigrationSettings:
    default_tenant_name: str = "기본 회사"
    default_org_name: str = "기본 조직"
    tenant_default_limits: dict[str, JsonValue] = field(default_factory=dict)

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] | None = None) -> "MigrationSettings":
        source = os.environ if environ is None else environ
        raw = source.get("TENANT_DEFAULT_LIMITS", "{}")
        limits = LIMITS.validate_python(
            json.loads(raw, parse_constant=reject_constant), strict=True
        )
        return cls(
            source.get("DEFAULT_TENANT_NAME", "기본 회사"),
            source.get("DEFAULT_ORG_NAME", "기본 조직"),
            limits,
        )


def reject_constant(value: str) -> None:
    message = f"Invalid JSON constant in TENANT_DEFAULT_LIMITS: {value}"
    raise ValueError(message)
