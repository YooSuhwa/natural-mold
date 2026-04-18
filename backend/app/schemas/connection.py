from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.schemas.markers import M10_SEED_MARKER
from app.services.credential_registry import CREDENTIAL_PROVIDERS
from app.services.env_var_resolver import _ENV_VAR_TEMPLATE

ConnectionType = Literal["prebuilt", "mcp", "custom"]
ConnectionStatus = Literal["active", "disabled"]
McpAuthType = Literal["none", "bearer", "api_key", "oauth2", "basic"]
McpTransport = Literal["http", "stdio"]

_PROVIDER_NAME_PATTERN = re.compile(r"^[a-z0-9_]+$")
# env_vars 템플릿 패턴은 runtime resolver와 단일 소스 공유.
_ENV_VAR_TEMPLATE_PATTERN = _ENV_VAR_TEMPLATE


def _check_reserved_marker(value: str, field_name: str) -> str:
    if value.startswith(M10_SEED_MARKER):
        raise ValueError(
            f"{field_name} cannot start with the reserved marker "
            f"'{M10_SEED_MARKER}' — this prefix is reserved for m10 "
            "auto-seeded rows so rollback can safely identify them."
        )
    return value


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
    """MCP connection 전용 설정 — strict key 집합.

    - PREBUILT/CUSTOM은 extra_config 자체가 None이어야 함 (ConnectionCreate 측에서 강제)
    - `extra="forbid"`로 알려지지 않은 키 거부 (unknown field 채널 차단)
    - **env_vars 템플릿 강제는 write-side (POST/PATCH)에서만** 수행
      (`ConnectionCreate` / `ConnectionUpdate`의 model_validator). 읽기 경로는
      m9 이관이 남긴 legacy 평문을 관용해야 하므로 스키마 자체는 값을 검증하지 않음.
      런타임(`env_var_resolver.resolve_env_vars`)도 평문을 경고 로그로 관용.
    """

    model_config = ConfigDict(extra="forbid")

    url: str
    auth_type: McpAuthType
    headers: dict[str, str] | None = None
    env_vars: dict[str, str] | None = None
    transport: McpTransport | None = None
    timeout: int | None = None


def _ensure_env_vars_template_only(
    env_vars: dict[str, str] | None,
) -> None:
    """Write-side (POST/PATCH) 가드 — 클라이언트가 평문 시크릿을 주입하지 못하게.

    m9 migration이 이관한 legacy 평문 데이터는 DB 직접 삽입 경로이므로 이
    가드를 거치지 않는다. 런타임은 legacy 평문을 관용하되 경고 로그로 M6
    cutoff 전 잔여를 추적한다.
    """
    if env_vars is None:
        return
    for key, val in env_vars.items():
        if not isinstance(val, str) or not _ENV_VAR_TEMPLATE_PATTERN.match(val):
            raise ValueError(
                f"env_vars['{key}'] must be a template "
                "'${credential.<field_name>}' — plaintext values are "
                "not allowed (store secrets in credentials and reference "
                "them by field name)"
            )


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

    @field_validator("display_name")
    @classmethod
    def _check_display_name_marker(cls, v: str) -> str:
        return _check_reserved_marker(v, "display_name")

    @model_validator(mode="after")
    def _check_extra_config_per_type(self) -> ConnectionCreate:
        if self.type == "mcp":
            if self.extra_config is None:
                raise ValueError(
                    "extra_config is required when type='mcp'"
                )
            # ConnectionExtraConfig 모델이 url/auth_type을 이미 required로 강제.
            # env_vars는 write-side에서만 template-only 검증
            _ensure_env_vars_template_only(self.extra_config.env_vars)

            # MCP + credential_id + 실인증(non-none): env_vars 템플릿 최소 1개
            # 필수. 그렇지 않으면 런타임에 credential이 어느 env로도 주입되지
            # 않아 조용히 unauthenticated 호출이 발생한다 (legacy
            # `resolve_server_auth`가 credential 전체를 auth로 반환하던
            # 동작의 새 경로 대응).
            if (
                self.credential_id is not None
                and self.extra_config.auth_type != "none"
                and not self.extra_config.env_vars
            ):
                raise ValueError(
                    "env_vars is required for MCP connections with "
                    "credential_id and auth_type != 'none'. "
                    "Map credential fields via ${credential.<field>} templates "
                    "(e.g., {'API_KEY': '${credential.api_key}'})."
                )
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

    @field_validator("display_name")
    @classmethod
    def _check_display_name_marker(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _check_reserved_marker(v, "display_name")

    @model_validator(mode="after")
    def _check_write_side_env_vars(self) -> ConnectionUpdate:
        # PATCH에서 새 extra_config를 보낼 때만 템플릿 규칙 적용. 기존 migrated
        # 평문은 다른 필드 PATCH 경로에선 revalidation 스킵으로 관용됨.
        if self.extra_config is not None:
            _ensure_env_vars_template_only(self.extra_config.env_vars)
        return self


class ConnectionExtraConfigResponse(BaseModel):
    """Read-side MCP config view.

    - `env_vars` 값은 secret 가능성이 있어 응답에 echo하지 않는다. 키 이름만
      `env_var_keys`로 노출
    - `headers` 값도 secret 가능성(Authorization, API key) 있어 동일하게
      redact. 키 이름만 `header_keys`로 노출
    - `MCPServerResponse`의 `auth_config` 전체 redaction 정책과 정합
    - m9 이관한 legacy `auth_config` dict의 비-string 값도 키만 살아남으므로
      schema 타입 충돌 발생 안 함
    """

    model_config = ConfigDict(extra="forbid")

    url: str
    auth_type: McpAuthType
    header_keys: list[str] | None = None
    env_var_keys: list[str] | None = None
    transport: McpTransport | None = None
    timeout: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _derive_redacted_keys(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        # 입력 dict를 그대로 mutate하면 ORM 객체의 `extra_config` dict를
        # 직접 건드리게 된다 (model_validate(from_attributes=True) 경로에서
        # SQLAlchemy identity map의 mutable state와 공유). 다음 flush 시
        # redacted 상태가 DB로 persist될 위험. 원본을 복사 후 파생값만 설정.
        values = dict(values)

        if "env_vars" in values:
            env_vars = values.pop("env_vars")
            if isinstance(env_vars, dict):
                values["env_var_keys"] = sorted(str(k) for k in env_vars)
            else:
                values["env_var_keys"] = None

        if "headers" in values:
            headers = values.pop("headers")
            if isinstance(headers, dict):
                values["header_keys"] = sorted(str(k) for k in headers)
            else:
                values["header_keys"] = None

        return values


class ConnectionResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    type: ConnectionType
    provider_name: str
    display_name: str
    credential_id: uuid.UUID | None
    extra_config: ConnectionExtraConfigResponse | None
    is_default: bool
    status: ConnectionStatus
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
