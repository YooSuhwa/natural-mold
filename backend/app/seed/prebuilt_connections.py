"""PREBUILT default Connection seed — env → credential → default connection.

Alembic m10이 원래 담당하던 데이터 시드를 lifespan seed로 이동한 모듈.

**왜 lifespan으로 옮겼나** (Codex adversarial P1):
표준 배포 순서는 `alembic upgrade head` → `uvicorn ...` 이므로, migration 시점에는
app/main.py lifespan이 만드는 mock user가 아직 없다. migration이 mock user 부재를
감지해 silent skip하면 Alembic은 해당 revision을 applied 표시하고, 이후에는
재실행되지 않아 PREBUILT connection layer가 영구 비어 있는 split-brain 상태가
발생한다. env fallback으로 tool은 동작하지만 UI는 "미연결"로 표시되어 사용자가
혼란스러워진다.

해결: 시드 로직을 lifespan seed 블록으로 옮겨, mock user 생성 **직후** 매 기동마다
idempotent 재시도. Alembic migration은 순수 스키마 변경(provider_name 컬럼 추가 +
PREBUILT tool name 백필)만 담당한다.

**왜 downgrade는 migration에 남나**:
lifespan seed가 생성하는 row도 `M10_SEED_MARKER` 프리픽스를 쓰므로 migration
downgrade가 동일 LIKE 패턴으로 정리 가능 — 스키마를 되돌리는 경로에서 seed row도
함께 정리되어야 FK 무결성이 깨지지 않는다.
"""

from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.schemas.markers import M10_SEED_MARKER

logger = logging.getLogger(__name__)

# (provider_name, credential_type, env_field → credential data key 매핑)
# 필수 env가 하나라도 비어 있으면 해당 provider 시드 skip.
#
# credential data 저장 키(매핑 value)는 `credential_registry.fields[*].key`와 정확히
# 일치해야 한다. 런타임 tool builder(naver_tools / google_tools 등)가 그 키로
# auth_config를 lookup하므로, 일치하지 않으면 env fallback으로 조용히 떨어진다.
# 회귀 방지: tests/test_connection_prebuilt_resolve.py 의
# `test_seed_env_to_key_matches_credential_registry`.
PROVIDERS: list[dict] = [
    {
        "provider_name": "naver",
        "credential_type": "api_key",
        "env_to_key": {
            "naver_client_id": "naver_client_id",
            "naver_client_secret": "naver_client_secret",
        },
    },
    {
        "provider_name": "google_search",
        "credential_type": "api_key",
        "env_to_key": {
            "google_api_key": "google_api_key",
            "google_cse_id": "google_cse_id",
        },
    },
    {
        "provider_name": "google_chat",
        "credential_type": "api_key",
        # credential_registry.google_chat.fields = [{key: "webhook_url"}]
        # tool builder(google_workspace_tools.build_google_chat_webhook_tool)가
        # auth_config["webhook_url"]을 읽으므로 credential data 키도 "webhook_url"로
        # 저장해야 한다. env 소스 이름(settings.google_chat_webhook_url)은 유지.
        "env_to_key": {
            "google_chat_webhook_url": "webhook_url",
        },
    },
    {
        "provider_name": "google_workspace",
        "credential_type": "oauth2",
        "env_to_key": {
            "google_oauth_client_id": "google_oauth_client_id",
            "google_oauth_client_secret": "google_oauth_client_secret",
            "google_oauth_refresh_token": "google_oauth_refresh_token",
        },
    },
]


async def seed_mock_user_prebuilt_connections(db: AsyncSession) -> None:
    """Idempotent env→credential→default connection seed for the mock user.

    Lifespan 시드 블록에서 mock user 시드 **직후** 호출한다. 매 기동마다 실행
    가능하며 이미 존재하는 default connection은 skip한다. 생성된 credential/
    connection의 name/display_name은 `M10_SEED_MARKER` 프리픽스를 포함해
    downgrade(migration)의 cleanup 대상으로 식별된다.

    Skip 조건:
    - `settings.encryption_key` 미설정 — plaintext 저장 방지 (API credential_service
      가 503을 내는 것과 정합).
    - `settings.mock_user_id`가 유효한 UUID가 아님.
    - mock user가 users 테이블에 없음 (lifespan에서 mock user 시드가 실패했거나
      순서가 역전된 드문 경우 — 다음 기동에서 재시도).
    - provider의 env 값이 하나라도 비어 있음 — 해당 provider만 skip.
    - 동일 scope에 이미 default=true connection이 있음 — 해당 provider만 skip.
    """
    if not settings.encryption_key:
        logger.info(
            "skip prebuilt connection seed: ENCRYPTION_KEY not set "
            "(credentials cannot be persisted safely)."
        )
        return

    try:
        mock_user_id = uuid.UUID(settings.mock_user_id)
    except (TypeError, ValueError):
        logger.warning(
            "skip prebuilt connection seed: settings.mock_user_id=%r is not a valid UUID.",
            settings.mock_user_id,
        )
        return

    # mock user 존재 확인 — lifespan이 먼저 만들도록 호출 순서를 보장한다.
    user_exists = await db.execute(
        text("SELECT 1 FROM users WHERE id = :uid"),
        {"uid": mock_user_id},
    )
    if user_exists.scalar() is None:
        logger.warning(
            "skip prebuilt connection seed: mock user %s not found. "
            "Ensure mock user is seeded before this step runs.",
            mock_user_id,
        )
        return

    from app.services.encryption import encrypt_api_key

    for prov in PROVIDERS:
        provider_name = prov["provider_name"]
        credential_type = prov["credential_type"]

        data: dict[str, str] = {}
        missing = False
        for env_field, data_key in prov["env_to_key"].items():
            value = getattr(settings, env_field, "") or ""
            if not value:
                missing = True
                break
            data[data_key] = value
        if missing:
            continue

        exists = await db.execute(
            text(
                "SELECT 1 FROM connections "
                "WHERE user_id = :uid AND type = 'prebuilt' "
                "AND provider_name = :p AND is_default = TRUE"
            ),
            {"uid": mock_user_id, "p": provider_name},
        )
        if exists.scalar():
            continue

        credential_id = uuid.uuid4()
        credential_name = f"{M10_SEED_MARKER} {provider_name}"
        encrypted = encrypt_api_key(json.dumps(data))
        field_keys = list(data.keys())

        await db.execute(
            text(
                "INSERT INTO credentials ("
                "id, user_id, name, credential_type, provider_name, "
                "data_encrypted, field_keys, created_at, updated_at"
                ") VALUES ("
                ":id, :uid, :name, :ctype, :provider, "
                ":data, CAST(:field_keys AS JSON), "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP"
                ")"
            ),
            {
                "id": credential_id,
                "uid": mock_user_id,
                "name": credential_name,
                "ctype": credential_type,
                "provider": provider_name,
                "data": encrypted,
                "field_keys": json.dumps(field_keys),
            },
        )

        connection_id = uuid.uuid4()
        connection_display = f"{M10_SEED_MARKER} {provider_name}"
        await db.execute(
            text(
                "INSERT INTO connections ("
                "id, user_id, type, provider_name, display_name, "
                "credential_id, extra_config, is_default, status, "
                "created_at, updated_at"
                ") VALUES ("
                ":id, :uid, 'prebuilt', :provider, :display, "
                ":cred_id, NULL, TRUE, 'active', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP"
                ")"
            ),
            {
                "id": connection_id,
                "uid": mock_user_id,
                "provider": provider_name,
                "display": connection_display,
                "cred_id": credential_id,
            },
        )

        logger.info(
            "seeded default connection for (%s, prebuilt, %s) from env.",
            mock_user_id,
            provider_name,
        )
