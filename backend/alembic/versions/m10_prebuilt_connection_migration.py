"""M10: tools.provider_name + PREBUILT mock-user default connection seed

Revision ID: m10_prebuilt_connection
Revises: m9_migrate_mcp_to_connections
Create Date: 2026-04-18

ADR-008 §4/§11 이행 — PREBUILT 도구를 per-user connection 경유로 해석하기 위한
스키마 + 데이터 마이그레이션.

## upgrade
1. `tools.provider_name` VARCHAR(50) nullable 컬럼 추가 (SQLite는 batch_alter_table)
2. 기존 PREBUILT tools name 패턴 백필
   - `Naver *` → `naver`
   - `Google Search`, `Google Image Search`, `Google News Search` → `google_search`
   - `Gmail *`, `Calendar *` → `google_workspace`
   - `Google Chat *` → `google_chat`
   - 그 외 `type='prebuilt'` row는 WARN 로그 + NULL 유지 (수동 복구 대상)
3. mock user env 값 → credential + default connection 자동 시드 (idempotent)
   - settings.naver_client_id/secret → naver
   - settings.google_api_key + google_cse_id → google_search
   - settings.google_chat_webhook_url → google_chat
   - settings.google_oauth_client_id/secret/refresh_token → google_workspace
   - 동일 (user, type='prebuilt', provider_name, is_default=true) connection이 이미
     있으면 skip
   - mock user row가 users 테이블에 없으면 시드 전체 skip (WARN 로그).
     첫 서버 기동에서 app.main이 mock user를 시드하므로, 이후 사용자는 UI에서
     connection을 수동 생성하거나 마이그레이션을 재적용(회귀 안전) 한다.
   - m10이 생성한 credential/connection은 `M10_SEED_MARKER`를 name/display_name에
     포함해서 downgrade에서 정확히 역삭제한다. 사용자가 수동으로 만든 행은 보호.

## downgrade
- display_name/name에 `M10_SEED_MARKER`가 들어간 connection/credential 역삭제
- `tools.provider_name` 컬럼 drop (batch_alter_table, SQLite 호환)
- PREBUILT tool의 백필은 되돌리지 않음(컬럼이 사라지므로 자연 소멸)
"""

from __future__ import annotations

import json
import logging
import uuid

import sqlalchemy as sa

from alembic import op

revision = "m10_prebuilt_connection"
down_revision = "m9_migrate_mcp_to_connections"
branch_labels = None
depends_on = None


logger = logging.getLogger("alembic.m10")

# downgrade에서 "m10이 만든 행"만 정확히 식별하기 위한 마커.
# 사용자가 UI에서 만든 credential/connection은 마커가 없으므로 보존된다.
M10_SEED_MARKER = "[m10-auto-seed]"


# (provider_name, credential_type, env_field → credential data key 매핑)
# 필수 env가 하나라도 비어 있으면 해당 provider 시드 skip.
_PROVIDERS: list[dict] = [
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


def _backfill_provider_name(bind) -> None:
    """기존 PREBUILT tools의 name 패턴 → provider_name 백필."""
    mapping = [
        ("naver", "name LIKE 'Naver %'"),
        (
            "google_search",
            "name IN ('Google Search', 'Google Image Search', 'Google News Search')",
        ),
        (
            "google_workspace",
            "(name LIKE 'Gmail %' OR name LIKE 'Calendar %')",
        ),
        ("google_chat", "name LIKE 'Google Chat %'"),
    ]
    for provider, where in mapping:
        bind.execute(
            sa.text(
                f"UPDATE tools SET provider_name = :p "
                f"WHERE type = 'prebuilt' AND provider_name IS NULL AND {where}"
            ),
            {"p": provider},
        )

    # 매핑 실패 row 경고 — NULL로 남으면 런타임 legacy fallback 경로가 동작 (tolerance).
    leftovers = bind.execute(
        sa.text(
            "SELECT id, name FROM tools "
            "WHERE type = 'prebuilt' AND provider_name IS NULL"
        )
    ).fetchall()
    for row in leftovers:
        logger.warning(
            "m10: PREBUILT tool id=%s name=%r has no provider_name mapping — "
            "staying on legacy credential_id/env fallback path. "
            "Update app.seed.default_tools with provider_name and rerun m10.",
            row[0],
            row[1],
        )


def _seed_mock_user_connections(bind) -> None:
    """mock user env 값 → credential + default connection 자동 시드 (idempotent)."""
    from app.config import settings  # migration runtime import

    mock_user_id_str = settings.mock_user_id
    try:
        mock_user_id = uuid.UUID(mock_user_id_str)
    except (TypeError, ValueError):
        logger.warning(
            "m10: settings.mock_user_id=%r is not a valid UUID — skipping seed.",
            mock_user_id_str,
        )
        return

    user_row = bind.execute(
        sa.text("SELECT id FROM users WHERE id = :uid"),
        {"uid": mock_user_id},
    ).scalar()
    if user_row is None:
        logger.warning(
            "m10: mock user %s not found in users table — skipping credential/"
            "connection seed. Start the app (lifespan seeds mock user) and "
            "re-run m10 manually if you want env values auto-seeded.",
            mock_user_id,
        )
        return

    # ENCRYPTION_KEY 미설정 시 credential 시드를 skip한다. encrypt_api_key는
    # 키가 없으면 plaintext를 반환하는데 (legacy 호환), migration은 API의
    # 503 가드(credential_service.create_credential)를 우회하므로 설정 누락
    # 배포에서 Google/Naver secrets가 영구 plaintext로 저장될 수 있다.
    # 이를 차단 — ENCRYPTION_KEY 설정 후 수동 re-run으로만 시드된다.
    if not settings.encryption_key:
        logger.warning(
            "m10: ENCRYPTION_KEY not set — skipping env secret seed to avoid "
            "persisting plaintext credentials. Set ENCRYPTION_KEY and re-run "
            "m10 manually if you want env values auto-seeded."
        )
        return

    # Fernet 암호화는 app.services.encryption 헬퍼 재사용 (키 관리 일관성).
    from app.services.encryption import encrypt_api_key

    for prov in _PROVIDERS:
        provider_name = prov["provider_name"]
        credential_type = prov["credential_type"]

        # 필수 env가 모두 있어야 시드
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

        # 존재 체크 — 이미 default connection이 있으면 skip (idempotent).
        # Partial unique index (uq_connections_one_default_per_scope)가 동일 scope
        # default=true 를 하나로 제한하므로, 기존 connection 존재 시 insert 시도 자체를
        # 회피해야 한다.
        exists = bind.execute(
            sa.text(
                "SELECT 1 FROM connections "
                "WHERE user_id = :uid AND type = 'prebuilt' "
                "AND provider_name = :p AND is_default = TRUE"
            ),
            {"uid": mock_user_id, "p": provider_name},
        ).scalar()
        if exists:
            logger.info(
                "m10: default connection for (%s, prebuilt, %s) already exists — "
                "skipping env seed.",
                mock_user_id,
                provider_name,
            )
            continue

        credential_id = uuid.uuid4()
        credential_name = f"{M10_SEED_MARKER} {provider_name}"
        encrypted = encrypt_api_key(json.dumps(data))
        field_keys = list(data.keys())

        bind.execute(
            sa.text(
                "INSERT INTO credentials ("
                "id, user_id, name, credential_type, provider_name, "
                "data_encrypted, field_keys, is_active, created_at, updated_at"
                ") VALUES ("
                ":id, :uid, :name, :ctype, :provider, "
                ":data, CAST(:field_keys AS JSON), TRUE, "
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
        bind.execute(
            sa.text(
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
            "m10: seeded default connection for (%s, prebuilt, %s) from env.",
            mock_user_id,
            provider_name,
        )


def upgrade() -> None:
    # 1) tools.provider_name 컬럼 추가 — SQLite 호환을 위해 batch_alter_table
    with op.batch_alter_table("tools") as batch_op:
        batch_op.add_column(
            sa.Column("provider_name", sa.String(length=50), nullable=True)
        )

    bind = op.get_bind()

    # 2) 기존 PREBUILT tools 백필
    _backfill_provider_name(bind)

    # 3) mock user env → credential + default connection 시드 (idempotent)
    try:
        _seed_mock_user_connections(bind)
    except Exception as exc:  # noqa: BLE001 — migration 경계
        # env 시드 실패는 스키마 마이그레이션 자체를 막지 않도록 관용 처리
        # (ADR-008 §11 env fallback 유지 — connection 없어도 런타임 동작).
        logger.warning(
            "m10: env seed failed (%s) — falling back to legacy env path. "
            "Re-run m10 after fixing the cause if you want auto-seeded connections.",
            exc,
        )


def downgrade() -> None:
    bind = op.get_bind()

    # m10이 만든 connection만 역삭제 — display_name에 마커가 들어간 행만 선별.
    # 사용자가 UI에서 만든 connection/credential은 마커가 없으므로 보존.
    marker_like = f"{M10_SEED_MARKER}%"
    bind.execute(
        sa.text(
            "DELETE FROM connections "
            "WHERE type = 'prebuilt' AND display_name LIKE :m"
        ),
        {"m": marker_like},
    )
    bind.execute(
        sa.text("DELETE FROM credentials WHERE name LIKE :m"),
        {"m": marker_like},
    )

    # provider_name 컬럼 drop — SQLite 호환 위해 batch_alter_table
    with op.batch_alter_table("tools") as batch_op:
        batch_op.drop_column("provider_name")
