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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.schemas.markers import M10_SEED_MARKER
from app.services.credential_registry import CREDENTIAL_PROVIDERS

logger = logging.getLogger(__name__)


def _iter_seedable_providers() -> list[dict]:
    """credential_registry의 provider 중 **모든 필드에 `env_field`가 있는** 것만
    seed 대상. custom_api_key처럼 사용자 입력 기반 provider는 제외된다.

    이 파생으로 env-to-data 매핑의 싱글 소스는 `CREDENTIAL_PROVIDERS`가 된다 —
    필드 추가/이름 변경 시 registry 한 곳만 갱신하면 tool builder(data key
    lookup)와 seed(env_field → data key 변환)가 자동 동기화.
    """
    seedable: list[dict] = []
    for provider_name, provider_def in CREDENTIAL_PROVIDERS.items():
        fields = provider_def["fields"]
        if not all(f.get("env_field") for f in fields):
            continue
        seedable.append(
            {
                "provider_name": provider_name,
                "credential_type": provider_def["credential_type"],
                "env_to_key": {f["env_field"]: f["key"] for f in fields},
            }
        )
    return seedable


# 테스트/외부 모듈에서 참조하는 상수 (가독성). registry 변경 시 자동 반영.
PROVIDERS = _iter_seedable_providers()


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
        connection_id = uuid.uuid4()
        connection_display = f"{M10_SEED_MARKER} {provider_name}"

        # SAVEPOINT 기반 race-safe insert: 여러 Pod가 동시 기동해 두 workers가
        # SELECT를 동시에 통과해도, partial unique index
        # `uq_connections_one_default_per_scope` 가 두 번째 connection INSERT를
        # IntegrityError로 차단한다. SAVEPOINT 안에서 catch하면 credential
        # INSERT도 함께 rollback되어 orphan이 남지 않고, outer transaction은
        # 살아있어 다음 provider seed로 진행할 수 있다. lifespan 자체가 abort
        # 되는 것을 방지 (Codex adversarial P1).
        try:
            async with db.begin_nested():
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
        except IntegrityError:
            # race loser — 다른 워커가 먼저 default connection을 만듦. SAVEPOINT
            # rollback으로 credential insert도 함께 취소된다.
            logger.info(
                "seed race: (%s, prebuilt, %s) default already created by "
                "another worker — skipping.",
                mock_user_id,
                provider_name,
            )
            continue

        logger.info(
            "seeded default connection for (%s, prebuilt, %s) from env.",
            mock_user_id,
            provider_name,
        )
