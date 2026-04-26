"""M9: migrate mcp_servers → connections, add tools.connection_id

Revision ID: m9_migrate_mcp_to_connections
Revises: m8_add_connections
Create Date: 2026-04-18

ADR-008 §6 이행 — MCP 도구의 credential/서버 설정 해석 경로를 connection
경유로 전환하기 위한 데이터 이관 마이그레이션.

## upgrade
1. `tools.connection_id` UUID nullable FK(connections.id) ON DELETE SET NULL 추가
2. 각 `mcp_servers` row를 `connections` row(type='mcp')로 복제
   - provider_name: server.name 정규화(소문자/언더스코어). (user_id, type)
     scope 내 충돌 시 `_2`, `_3` 등 suffix
   - display_name = server.name
   - credential_id = server.credential_id (SET NULL 시맨틱 동일)
   - extra_config = {url, auth_type, headers: {}, env_vars: <server.auth_config>}
   - is_default = True (provider_name 충돌을 suffix로 회피하므로 각 scope 1건)
   - status = 'active' / timestamps = server.created_at
3. tools.mcp_server_id IS NOT NULL 인 row → 매핑된 connection_id로 설정
   (`tools.mcp_server_id`는 유지. M6 마이그레이션에서 drop 예정)

## auth_config 평문 이관 정책 (M2 리스크 4)
ADR-008 §2는 `extra_config.env_vars` 값을 `${credential.<field>}` 템플릿으로만
허용한다. 그러나 기존 `mcp_servers.auth_config`에는 평문 값이 들어 있을 수
있으므로, 데이터 신뢰성을 위해 **본 마이그레이션에서만** 평문을 그대로
`extra_config.env_vars`에 복사한다. 런타임(S3, chat_service/mcp_client)은
template 우선 + legacy 평문 fallback으로 해석한다. 신규 생성 connection은
애플리케이션 계층(connection_service)에서 template-only 검증된다.

## downgrade
- `tools.connection_id` 컬럼 drop (tools.mcp_server_id는 그대로 보존되어
  legacy 경로 복원 가능)
- `connections.type = 'mcp'` row 삭제 (이관으로 생성된 connection만 존재)
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision = "m9_migrate_mcp_to_connections"
down_revision = "m8_add_connections"
branch_labels = None
depends_on = None


_SLUG_RE = re.compile(r"[^a-z0-9_]+")
_COLLAPSE_RE = re.compile(r"_+")


def _normalize_provider_name(name: str) -> str:
    """server.name → connections.provider_name 슬러그 변환.

    - 소문자화, 영숫자/언더스코어 외 문자는 `_`로 치환, 연속 `_` 축약
    - 빈 문자열이면 'mcp' 로 fallback
    - String(50) 컬럼. suffix 공간(_NN)을 위해 base는 45자로 truncate
    """
    slug = _SLUG_RE.sub("_", (name or "").lower())
    slug = _COLLAPSE_RE.sub("_", slug).strip("_")
    if not slug:
        slug = "mcp"
    return slug[:45]


def upgrade() -> None:
    # 1) tools.connection_id 컬럼 + FK
    op.add_column(
        "tools",
        sa.Column("connection_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_tools_connection_id",
        "tools",
        "connections",
        ["connection_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 2) 이관 추적 테이블 — downgrade에서 "m9가 만든 connection"만 안전히
    # 삭제할 수 있도록 출처 기록. user-facing `extra_config`에 sentinel을
    # 박으면 `ConnectionExtraConfig(extra="forbid")` 재검증에 걸려
    # GET/PATCH가 깨진다. 따라서 별도 테이블로 분리.
    op.create_table(
        "_m9_migrated_connections",
        sa.Column(
            "connection_id",
            sa.Uuid(),
            nullable=False,
            primary_key=True,
        ),
    )

    # 3) mcp_servers → connections 이관
    bind = op.get_bind()

    servers = bind.execute(
        sa.text(
            "SELECT id, user_id, name, url, auth_type, auth_config, "
            "credential_id, status, created_at "
            "FROM mcp_servers"
        )
    ).fetchall()

    if not servers:
        return

    # credential 조회 batch — server별 N+1 SELECT 방지. auth_config가 비어있고
    # credential_id가 달린 server가 존재하는 경우에만 수행.
    credential_ids_needed = {s[6] for s in servers if (not s[5]) and s[6] is not None}
    credentials_by_id: dict[uuid.UUID, tuple] = {}
    if credential_ids_needed:
        stmt = sa.text(
            "SELECT id, field_keys, data_encrypted FROM credentials WHERE id IN :ids"
        ).bindparams(sa.bindparam("ids", expanding=True))
        rows = bind.execute(stmt, {"ids": list(credential_ids_needed)}).fetchall()
        credentials_by_id = {r[0]: (r[1], r[2]) for r in rows}

    # (user_id, provider_name) scope 내 충돌을 추적. 기존 connections에도
    # 동일 scope의 row가 있을 수 있으므로 미리 로드.
    existing = bind.execute(
        sa.text("SELECT user_id, provider_name FROM connections WHERE type = 'mcp'")
    ).fetchall()
    taken: set[tuple[str, str]] = {(str(row[0]), row[1]) for row in existing}

    server_to_connection: dict[uuid.UUID, uuid.UUID] = {}

    for server in servers:
        server_id = server[0]
        user_id = server[1]
        name = server[2]
        url = server[3]
        auth_type = server[4] or "none"
        auth_config = server[5] or {}
        credential_id = server[6]
        status = server[7] or "active"
        created_at = server[8] or datetime.now(UTC).replace(tzinfo=None)

        # auth_config 는 dict 가정. asyncpg + JSON 컬럼은 dict로 반환되지만,
        # 문자열로 떨어지는 드라이버 경우를 방어.
        if isinstance(auth_config, str):
            try:
                auth_config = json.loads(auth_config) if auth_config else {}
            except ValueError:
                auth_config = {}
        if not isinstance(auth_config, dict):
            auth_config = {}

        base = _normalize_provider_name(name)
        provider_name = base
        suffix = 1
        while (str(user_id), provider_name) in taken:
            suffix += 1
            provider_name = f"{base}_{suffix}"[:50]
        taken.add((str(user_id), provider_name))

        # env_vars 결정 로직 (auth 누락 regression 방지):
        # 1) mcp_servers.auth_config가 dict이면 legacy 평문으로 그대로 이관
        #    (런타임은 env_var_resolver에서 관용 + 경고)
        # 2) auth_config 비어있고 credential 연결된 server는 기존
        #    resolve_server_auth 동작을 유지하기 위해 credentials.field_keys
        #    (ADR-007) 또는 data_encrypted 복호화로 키를 유도 → 템플릿 자동 생성
        # 3) credential도 auth_config도 없으면 빈 env_vars로 이관 (정상)
        # 4) credential은 있으나 키를 복구 못 한 경우 → 이 server는 **migrate
        #    스킵** (connections row + tools.connection_id 모두 미생성). 기존
        #    mcp_servers row와 tools.mcp_server_id가 그대로 남아 런타임
        #    legacy fallback 경로로 계속 동작 (Codex 8차 adversarial F1)
        credential_auth_recoverable = True
        if auth_config:
            env_vars_out = auth_config
        elif credential_id:
            cred_row = credentials_by_id.get(credential_id)
            field_keys_list: list[str] | None = None
            if cred_row is not None:
                field_keys_raw = cred_row[0]
                if isinstance(field_keys_raw, str):
                    try:
                        field_keys_raw = json.loads(field_keys_raw)
                    except ValueError:
                        field_keys_raw = None
                if isinstance(field_keys_raw, list) and field_keys_raw:
                    field_keys_list = [str(k) for k in field_keys_raw]
                else:
                    data_encrypted = cred_row[1]
                    try:
                        from app.services.encryption import decrypt_api_key

                        decoded = json.loads(decrypt_api_key(data_encrypted))
                        if isinstance(decoded, dict) and decoded:
                            field_keys_list = [str(k) for k in decoded]
                    except Exception:  # noqa: BLE001 — migration 경계
                        field_keys_list = None

            if field_keys_list:
                env_vars_out = {k: f"${{credential.{k}}}" for k in field_keys_list}
            else:
                # 복구 실패 → 이관하지 않고 legacy path 유지. connection 생성
                # 자체를 건너뛰어 tools.connection_id가 설정되지 않게 한다.
                import logging

                logging.getLogger("alembic.m9").warning(
                    "m9: leaving MCP server %s on legacy path — credential %s "
                    "auth could not be reconstructed (NULL field_keys and "
                    "decrypt/JSON fallback failed). tools.mcp_server_id + "
                    "resolve_server_auth 경로 유지. M7 backfill을 실행하거나 "
                    "credential을 재설정한 뒤 m9을 다시 적용하면 이관됨.",
                    server_id,
                    credential_id,
                )
                credential_auth_recoverable = False
                env_vars_out = {}
        else:
            env_vars_out = {}

        if not credential_auth_recoverable:
            # scope taken에서 provider_name 반납 (이 server는 connection 미생성)
            taken.discard((str(user_id), provider_name))
            continue

        extra_config = {
            "url": url,
            "auth_type": auth_type,
            "headers": {},
            "env_vars": env_vars_out,
        }

        connection_id = uuid.uuid4()
        bind.execute(
            sa.text(
                "INSERT INTO connections ("
                "id, user_id, type, provider_name, display_name, "
                "credential_id, extra_config, is_default, status, "
                "created_at, updated_at"
                ") VALUES ("
                ":id, :user_id, 'mcp', :provider_name, :display_name, "
                ":credential_id, CAST(:extra_config AS JSON), TRUE, :status, "
                ":created_at, :updated_at"
                ")"
            ),
            {
                "id": connection_id,
                "user_id": user_id,
                "provider_name": provider_name,
                "display_name": (name or "MCP Server")[:200],
                "credential_id": credential_id,
                "extra_config": json.dumps(extra_config),
                "status": status,
                "created_at": created_at,
                "updated_at": created_at,
            },
        )
        server_to_connection[server_id] = connection_id

        # 이관 추적: downgrade에서 이 row만 정확히 지울 수 있도록 기록
        bind.execute(
            sa.text("INSERT INTO _m9_migrated_connections (connection_id) VALUES (:id)"),
            {"id": connection_id},
        )

    # 4) tools.mcp_server_id → tools.connection_id 매핑
    for server_id, connection_id in server_to_connection.items():
        bind.execute(
            sa.text("UPDATE tools SET connection_id = :cid WHERE mcp_server_id = :sid"),
            {"cid": connection_id, "sid": server_id},
        )


def downgrade() -> None:
    bind = op.get_bind()

    # tools.mcp_server_id 는 upgrade에서 그대로 유지했으므로 역매핑 불필요.
    # connection_id FK/컬럼만 제거.
    op.drop_constraint("fk_tools_connection_id", "tools", type_="foreignkey")
    op.drop_column("tools", "connection_id")

    # upgrade에서 만든 connections만 제거. `_m9_migrated_connections`
    # 추적 테이블의 row 기반이라 사용자가 수동으로 만든 type='mcp'
    # connection은 보존. 추적 테이블 자체도 그 뒤 drop.
    # 이전 m9 버전이 적용된 DB(추적 테이블 없음)에서도 안전하게 downgrade
    # 되도록 테이블 존재 여부를 먼저 확인한다.
    tracking_exists = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables WHERE table_name = '_m9_migrated_connections'"
        )
    ).scalar()
    if tracking_exists:
        bind.execute(
            sa.text(
                "DELETE FROM connections "
                "WHERE id IN (SELECT connection_id FROM _m9_migrated_connections)"
            )
        )
        op.execute("DROP TABLE _m9_migrated_connections")
