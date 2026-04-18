from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.services.credential_registry import CREDENTIAL_PROVIDERS

ConnectionType = Literal["prebuilt", "mcp", "custom"]
ConnectionStatus = Literal["active", "disabled"]
McpAuthType = Literal["none", "bearer", "api_key", "oauth2", "basic"]
McpTransport = Literal["http", "stdio"]

_PROVIDER_NAME_PATTERN = re.compile(r"^[a-z0-9_]+$")
# env_vars 값은 "${credential.<field_name>}" 템플릿만 허용 — 평문 시크릿 저장 방지.
_ENV_VAR_TEMPLATE_PATTERN = re.compile(
    r"^\$\{credential\.[a-z_][a-z0-9_]*\}$"
)


def _validate_provider_name(
    provider_name: str, connection_type: str
) -> str:
    if connection_type == "prebuilt":
        if provider_name not in CREDENTIAL_PROVIDERS:
            allowed = ", ".join(sorted(CREDENTIAL_PROVIDERS.keys()))
            raise ValueError(
                f"provider_name '{provider_name}' not allowed for "
                f"type='prebuilt'. Allowed: {allowed}"
            )
        return provider_name

    if len(provider_name) > 50:
        raise ValueError("provider_name must be 50 characters or fewer")
    if not _PROVIDER_NAME_PATTERN.match(provider_name):
        raise ValueError(
            "provider_name must match ^[a-z0-9_]+$ for type='mcp'|'custom'"
        )
    return provider_name


class ConnectionExtraConfig(BaseModel):
    """MCP connection 전용 설정 — 평문 시크릿 유입 방지를 위해 strict 타입.

    - PREBUILT/CUSTOM은 extra_config 자체가 None이어야 함 (ConnectionCreate 측에서 강제)
    - env_vars 값은 반드시 `${credential.<field_name>}` 템플릿. 평문 문자열은 422
    - `extra="forbid"`로 알려지지 않은 키 거부
    """

    model_config = ConfigDict(extra="forbid")

    url: str
    auth_type: McpAuthType
    headers: dict[str, str] | None = None
    env_vars: dict[str, str] | None = None
    transport: McpTransport | None = None
    timeout: int | None = None

    @field_validator("env_vars")
    @classmethod
    def _check_env_vars_templates(
        cls, v: dict[str, str] | None
    ) -> dict[str, str] | None:
        if v is None:
            return v
        for key, val in v.items():
            if not isinstance(val, str) or not _ENV_VAR_TEMPLATE_PATTERN.match(
                val
            ):
                raise ValueError(
                    f"env_vars['{key}'] must be a template "
                    "'${credential.<field_name>}' — plaintext values are "
                    "not allowed (store secrets in credentials and reference "
                    "them by field name)"
                )
        return v


class ConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ConnectionType
    provider_name: str
    display_name: str
    credential_id: uuid.UUID | None = None
    extra_config: ConnectionExtraConfig | None = None
    is_default: bool = False
    status: ConnectionStatus = "active"

    @field_validator("provider_name")
    @classmethod
    def _check_provider_name(cls, v: str, info) -> str:
        connection_type = info.data.get("type")
        if connection_type is None:
            return v
        return _validate_provider_name(v, connection_type)

    @model_validator(mode="after")
    def _check_extra_config_per_type(self) -> ConnectionCreate:
        if self.type == "mcp":
            if self.extra_config is None:
                raise ValueError(
                    "extra_config is required when type='mcp'"
                )
            # ConnectionExtraConfig 모델이 url/auth_type을 이미 required로 강제
        else:
            # PREBUILT/CUSTOM — extra_config는 허용하지 않음. 평문 시크릿 채널
            # 생성 방지 (Codex adversarial P2). credential_id로 이동시킬 것
            if self.extra_config is not None:
                raise ValueError(
                    f"extra_config is not allowed when type='{self.type}'. "
                    "Put secret material in credentials and reference by "
                    "credential_id instead."
                )
        return self


class ConnectionUpdate(BaseModel):
    """PATCH 페이로드. `type`은 생성 후 불변이라 필드 자체를 두지 않고,
    `extra="forbid"`로 전송 시 422 반환.
    `credential_id`/`extra_config`은 `None` 전송 시 명시적 해제로 해석 —
    서비스 레이어에서 `model_dump(exclude_unset=True)`로 "미전송"과 구분."""

    model_config = ConfigDict(extra="forbid")

    provider_name: str | None = None
    display_name: str | None = None
    credential_id: uuid.UUID | None = None
    extra_config: ConnectionExtraConfig | None = None
    is_default: bool | None = None
    status: ConnectionStatus | None = None


class ConnectionResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    type: ConnectionType
    provider_name: str
    display_name: str
    credential_id: uuid.UUID | None
    extra_config: ConnectionExtraConfig | None
    is_default: bool
    status: ConnectionStatus
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
