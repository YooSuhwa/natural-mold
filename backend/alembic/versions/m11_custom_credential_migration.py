"""M11: CUSTOM tool credential → connection 이관

Revision ID: m11_custom_connection
Revises: m10_prebuilt_connection
Create Date: 2026-04-18

ADR-008 §4 M4 이행 — 기존 CUSTOM tools의 `tool.credential_id` 경로를
`tool.connection_id → connection.credential_id` 경유로 이관한다.

## upgrade
1. 대상 식별: `tools.type='custom' AND credential_id IS NOT NULL AND connection_id IS NULL`
2. (user_id, credential_id) 단위 dedup 그룹 (1 credential = 1 connection, N tools)
3. 각 그룹:
   - 기존 `[m11-auto-seed]` 마커 connection 존재 → 재사용 (idempotent)
   - 없으면 새 connection INSERT (type='custom', provider_name='custom_api_key',
     display_name=f'[m11-auto-seed] {credential.name}', status='active').
     `is_default`는 user별 CUSTOM scope 첫 connection만 true —
     `uq_connections_one_default_per_scope` partial unique index 위반 방지
     (ADR-008 §1 / §5). 이후 CUSTOM connection은 false.
4. 그룹 내 모든 tool의 connection_id FK 설정

## 보존 (M6까지 legacy fallback)
- `tools.credential_id`: drop 하지 않음 (chat_service legacy 경로 유지)
- `tools.auth_config`: drop 하지 않음

## downgrade
- `[m11-auto-seed]%` 마커 connection만 대상 — 해당 connection을 참조하는
  tool의 connection_id → NULL 역설정 → connection DELETE
- 수동 생성된 CUSTOM connection은 보존

## aiosqlite 테스트 호환 정책 (M9/M10 precedent)
PG-native 쿼리가 섞이더라도 실제 왕복은 PostgreSQL에서만 실행한다.
pytest는 `inspect.getsource`로 헬퍼 본문 계약(대상 식별 SQL, 마커 적용,
is_default race 가드 등)을 정적 검증한다.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision = "m11_custom_connection"
down_revision = "m10_prebuilt_connection"
branch_labels = None
depends_on = None


logger = logging.getLogger("alembic.m11")

# downgrade가 m11이 만든 connection 행만 안전히 역삭제하기 위한 마커.
# UI로 수동 생성된 CUSTOM connection에는 이 마커가 없어 보존된다.
M11_SEED_MARKER = "[m11-auto-seed]"


def _migrate_custom_credentials(bind) -> None:
    """CUSTOM tool의 credential_id → connection 이관 본체.

    - (user_id, credential_id) dedup → 1 credential = 1 connection (N:1 공유)
    - idempotent: 재실행 시 마커 기반으로 기존 connection 재사용
    - `uq_connections_one_default_per_scope` 위반 방지: user별 CUSTOM scope에
      이미 default가 있으면 새 connection은 is_default=false로 INSERT
    """
    rows = bind.execute(
        sa.text(
            "SELECT id, user_id, credential_id FROM tools "
            "WHERE type = 'custom' "
            "AND credential_id IS NOT NULL "
            "AND connection_id IS NULL"
        )
    ).fetchall()
    if not rows:
        return

    # (user_id, credential_id) 단위 dedup — 같은 credential을 참조하는 여러
    # CUSTOM tool은 하나의 connection을 공유한다 (N:1).
    groups: dict[tuple, list[uuid.UUID]] = {}
    for tool_id, user_id, credential_id in rows:
        groups.setdefault((user_id, credential_id), []).append(tool_id)

    # user별 CUSTOM scope의 default 점유 여부. partial unique index가
    # (user_id, type='custom', provider_name='custom_api_key') WHERE
    # is_default=true 에 걸려 있으므로, 한 user 내 2건 이상을 default로
    # INSERT하면 IntegrityError가 발생한다.
    custom_default_taken: set[uuid.UUID] = set()
    pre_existing = bind.execute(
        sa.text(
            "SELECT user_id FROM connections "
            "WHERE type = 'custom' "
            "AND provider_name = 'custom_api_key' "
            "AND is_default = TRUE"
        )
    ).fetchall()
    for (uid,) in pre_existing:
        custom_default_taken.add(uid)

    now = datetime.now(UTC).replace(tzinfo=None)

    for (user_id, credential_id), tool_ids in groups.items():
        # idempotent + manual-respect 재사용: 사용자가 M1 connection POST API로
        # 미리 만들어 둔 manual CUSTOM connection이 있으면 그것을 재사용해야 한다.
        # 마커 LIKE 필터로 우리가 만든 행만 찾으면 manual connection은 고립되고
        # 새로운 [m11-auto-seed] 행이 SOT를 가로채 ADR-008의 1-credential→
        # 1-connection 불변이 깨진다 (Codex 2차 P1). 마커는 INSERT 시점에만
        # 부착해 downgrade에서 정확한 역삭제를 가능하게 하고, lookup은 (user_id,
        # type, credential_id) 단위로 넓게 매칭한다.
        # `status = 'active'` 필터 필수 (Codex 3차 P2): pre-m11에는 legacy tool이
        # `tool.credential_id` 경로로 실행되고 있었는데, 같은 credential에 대한
        # disabled manual connection이 있으면 m11이 그걸 재사용해 모든 tool을
        # disabled connection에 rebind → post-m11에 fail-closed로 일괄 실행 불가
        # 회귀. active 행이 없으면 fall-through해 새 [m11-auto-seed] active
        # connection을 생성 (기존 동작 보존). 사용자가 disabled 유지와 seeded
        # connection을 어떻게 reconcile할지는 M5 UI의 몫.
        existing = bind.execute(
            sa.text(
                "SELECT id FROM connections "
                "WHERE user_id = :uid "
                "AND type = 'custom' "
                "AND credential_id = :cid "
                "AND status = 'active' "
                "ORDER BY is_default DESC, created_at ASC "
                "LIMIT 1"
            ),
            {
                "uid": user_id,
                "cid": credential_id,
            },
        ).first()

        if existing is not None:
            connection_id = existing[0]
            logger.info(
                "m11: reused existing CUSTOM connection %s for user=%s "
                "credential=%s (%d tools)",
                connection_id,
                user_id,
                credential_id,
                len(tool_ids),
            )
        else:
            cred_row = bind.execute(
                sa.text("SELECT name FROM credentials WHERE id = :cid"),
                {"cid": credential_id},
            ).first()
            cred_name = (cred_row[0] if cred_row else "credential") or "credential"
            display_name = f"{M11_SEED_MARKER} {cred_name}"[:200]

            # user별 CUSTOM scope의 첫 default만 is_default=true.
            # 이후 CUSTOM connection은 is_default=false (partial unique index 회피).
            is_default = user_id not in custom_default_taken
            if is_default:
                custom_default_taken.add(user_id)

            connection_id = uuid.uuid4()
            bind.execute(
                sa.text(
                    "INSERT INTO connections ("
                    "id, user_id, type, provider_name, display_name, "
                    "credential_id, extra_config, is_default, status, "
                    "created_at, updated_at"
                    ") VALUES ("
                    ":id, :user_id, 'custom', 'custom_api_key', "
                    ":display_name, :credential_id, NULL, :is_default, "
                    "'active', :created_at, :updated_at"
                    ")"
                ),
                {
                    "id": connection_id,
                    "user_id": user_id,
                    "display_name": display_name,
                    "credential_id": credential_id,
                    "is_default": is_default,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            logger.info(
                "m11: inserted CUSTOM connection %s for user=%s credential=%s "
                "(%d tools, is_default=%s)",
                connection_id,
                user_id,
                credential_id,
                len(tool_ids),
                is_default,
            )

        # 그룹 내 모든 tool.connection_id FK 설정. tool.credential_id 는
        # 그대로 유지 (M6까지 legacy fallback 경로용). provenance에 tool_id를
        # 기록해 downgrade가 대칭으로 되돌릴 수 있게 한다 (Codex adversarial 2차
        # [high] #1) — 재사용된 manual connection에 바인딩된 tool까지 포함.
        # 이 SELECT는 이미 `connection_id IS NULL`인 행만 필터하므로 provenance
        # 에 기록된 tool의 pre-m11 상태는 항상 NULL이다.
        for tool_id in tool_ids:
            bind.execute(
                sa.text(
                    "UPDATE tools SET connection_id = :conn_id "
                    "WHERE id = :tool_id AND connection_id IS NULL"
                ),
                {"conn_id": connection_id, "tool_id": tool_id},
            )
            bind.execute(
                sa.text(
                    "INSERT INTO _m11_tool_backfill_provenance (tool_id) "
                    "VALUES (:tool_id) "
                    "ON CONFLICT (tool_id) DO NOTHING"
                ),
                {"tool_id": tool_id},
            )


def _dedup_preexisting_custom_duplicates(bind) -> None:
    """CREATE UNIQUE INDEX 전에 기존 `connections`의 (user_id, credential_id)
    중복을 정리 (Codex adversarial 2차 [high] #2). m11 이전에는 제약이 없어
    M1 API로 같은 credential에 대해 여러 CUSTOM connection을 만들 수 있었다.

    **Fully reversible** (Codex adversarial 4차 [high] #2):
    삭제되는 duplicate 행을 `_m11_dedup_connection_snapshot`에 full row로 보관,
    repoint되는 tool은 `_m11_dedup_tool_remap`에 (tool_id, original_connection_id)
    로 기록. downgrade에서 두 테이블로부터 정확히 복원된다.

    canonical 선택 순위 (Codex adversarial 4차 [high] #1 — health first):
    1. status = 'active' (disabled default를 canonical로 뽑아 working tool을
       disable connection에 repoint하는 post-migration 장애 회피)
    2. is_default = true (active 끼리는 user 의도된 default 보존)
    3. created_at ASC (동률이면 가장 오래된 것 = 먼저 만든 것)
    4. id ASC (결정적 tie-breaker)

    duplicates는 canonical로 tool.connection_id를 repoint 후 DELETE.
    agent_tools.connection_id(M5)는 현재 존재하지 않으므로 생략.
    """
    groups = bind.execute(
        sa.text(
            "SELECT user_id, credential_id "
            "FROM connections "
            "WHERE type = 'custom' AND credential_id IS NOT NULL "
            "GROUP BY user_id, credential_id "
            "HAVING COUNT(*) > 1"
        )
    ).fetchall()

    for user_id, credential_id in groups:
        rows = bind.execute(
            sa.text(
                "SELECT id FROM connections "
                "WHERE user_id = :uid AND type = 'custom' AND credential_id = :cid "
                "ORDER BY "
                "(CASE WHEN status = 'active' THEN 0 ELSE 1 END), "
                "is_default DESC, "
                "created_at ASC, id ASC"
            ),
            {"uid": user_id, "cid": credential_id},
        ).fetchall()
        canonical_id = rows[0][0]
        for (dup_id,) in rows[1:]:
            # Snapshot full row for downgrade restoration — INSERT ... SELECT로
            # JSON 직렬화 없이 type 보존. ON CONFLICT idempotent.
            bind.execute(
                sa.text(
                    "INSERT INTO _m11_dedup_connection_snapshot ("
                    "connection_id, user_id, type, provider_name, display_name, "
                    "credential_id, extra_config, is_default, status, "
                    "created_at, updated_at"
                    ") SELECT id, user_id, type, provider_name, display_name, "
                    "credential_id, extra_config, is_default, status, "
                    "created_at, updated_at "
                    "FROM connections WHERE id = :id "
                    "ON CONFLICT (connection_id) DO NOTHING"
                ),
                {"id": dup_id},
            )
            # Record tool remap BEFORE UPDATE — downgrade가 tool.connection_id를
            # 원래 duplicate로 되돌릴 수 있도록.
            bind.execute(
                sa.text(
                    "INSERT INTO _m11_dedup_tool_remap "
                    "(tool_id, original_connection_id) "
                    "SELECT id, connection_id FROM tools "
                    "WHERE connection_id = :dup "
                    "ON CONFLICT (tool_id) DO NOTHING"
                ),
                {"dup": dup_id},
            )
            bind.execute(
                sa.text(
                    "UPDATE tools SET connection_id = :canonical "
                    "WHERE connection_id = :dup"
                ),
                {"canonical": canonical_id, "dup": dup_id},
            )
            bind.execute(
                sa.text("DELETE FROM connections WHERE id = :id"),
                {"id": dup_id},
            )
        logger.info(
            "m11: deduped %d duplicate CUSTOM connections for user=%s "
            "credential=%s → canonical=%s",
            len(rows) - 1,
            user_id,
            credential_id,
            canonical_id,
        )


def upgrade() -> None:
    bind = op.get_bind()

    # 1) Provenance 테이블 3종 — downgrade가 대칭 복원할 수 있도록.
    # (Codex adversarial 2차/4차 [high]).
    #
    # - `_m11_tool_backfill_provenance`: _migrate_custom_credentials가 set한
    #   tool_id (pre-state = NULL). downgrade에서 NULL로 복원.
    # - `_m11_dedup_connection_snapshot`: _dedup이 삭제한 duplicate connection
    #   행의 full snapshot. downgrade에서 INSERT로 원복.
    # - `_m11_dedup_tool_remap`: _dedup이 repoint한 tool의 (tool_id,
    #   original_connection_id). downgrade에서 원래 duplicate로 되돌림.
    op.execute(
        "CREATE TABLE IF NOT EXISTS _m11_tool_backfill_provenance ("
        "tool_id UUID PRIMARY KEY"
        ")"
    )
    op.execute(
        "CREATE TABLE IF NOT EXISTS _m11_dedup_connection_snapshot ("
        "connection_id UUID PRIMARY KEY, "
        "user_id UUID NOT NULL, "
        "type VARCHAR(20) NOT NULL, "
        "provider_name VARCHAR(50), "
        "display_name VARCHAR(200) NOT NULL, "
        "credential_id UUID, "
        "extra_config JSON, "
        "is_default BOOLEAN NOT NULL, "
        "status VARCHAR(20) NOT NULL, "
        "created_at TIMESTAMP NOT NULL, "
        "updated_at TIMESTAMP NOT NULL"
        ")"
    )
    op.execute(
        "CREATE TABLE IF NOT EXISTS _m11_dedup_tool_remap ("
        "tool_id UUID PRIMARY KEY, "
        "original_connection_id UUID NOT NULL"
        ")"
    )

    # 2) CUSTOM tool credential → connection 이관.
    _migrate_custom_credentials(bind)

    # 3) CREATE UNIQUE INDEX 전에 기존 duplicate 제거 — 그렇지 않으면
    # 기존 데이터에 중복이 있으면 CREATE UNIQUE INDEX가 실패해 배포 블록
    # (Codex adversarial 2차 [high] #2). 삭제되는 duplicate는 snapshot +
    # tool_remap 테이블에 저장돼 downgrade에서 완전 복원 가능
    # (Codex adversarial 4차 [high] #2).
    _dedup_preexisting_custom_duplicates(bind)

    # 4) ADR-008 N:1 invariant — CUSTOM tool은 (user_id, credential_id) 당 1
    # connection만 허용 (Codex adversarial 1차 [medium]). service-level
    # get-or-create와 defense-in-depth — 동시 INSERT race를 DB가 차단.
    # partial unique index로 제한: type='custom' AND credential_id IS NOT NULL.
    # MCP / PREBUILT는 이 제약 밖 (extra_config 기반 다중 connection 허용).
    # PG/SQLite 3.8+ partial index 지원. `IF NOT EXISTS`로 재실행 idempotent.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uq_connections_custom_one_per_credential "
        "ON connections (user_id, credential_id) "
        "WHERE type = 'custom' AND credential_id IS NOT NULL"
    )


def downgrade() -> None:
    bind = op.get_bind()

    # 0) Provenance 테이블 존재 보장 — 과거 버전의 m11(provenance 로직 이전)이
    # 적용된 DB에서 downgrade 실행 시에도 안전하도록. 빈 테이블이면 아래 복원
    # 단계들이 no-op이 되고, marker DELETE가 여전히 [m11-auto-seed] 행을 정리한다.
    op.execute(
        "CREATE TABLE IF NOT EXISTS _m11_tool_backfill_provenance ("
        "tool_id UUID PRIMARY KEY"
        ")"
    )
    op.execute(
        "CREATE TABLE IF NOT EXISTS _m11_dedup_connection_snapshot ("
        "connection_id UUID PRIMARY KEY, "
        "user_id UUID NOT NULL, "
        "type VARCHAR(20) NOT NULL, "
        "provider_name VARCHAR(50), "
        "display_name VARCHAR(200) NOT NULL, "
        "credential_id UUID, "
        "extra_config JSON, "
        "is_default BOOLEAN NOT NULL, "
        "status VARCHAR(20) NOT NULL, "
        "created_at TIMESTAMP NOT NULL, "
        "updated_at TIMESTAMP NOT NULL"
        ")"
    )
    op.execute(
        "CREATE TABLE IF NOT EXISTS _m11_dedup_tool_remap ("
        "tool_id UUID PRIMARY KEY, "
        "original_connection_id UUID NOT NULL"
        ")"
    )

    # 1) N:1 partial unique index 제거 (upgrade step 4 역). dedup 복원이 다시
    # duplicate를 만들 수 있으므로 반드시 먼저 drop.
    op.execute("DROP INDEX IF EXISTS uq_connections_custom_one_per_credential")

    # 2) dedup이 삭제한 duplicate connection 복원 (upgrade step 3 역).
    # INSERT … SELECT로 JSON 직렬화 없이 타입 보존. ON CONFLICT는 idempotent용.
    bind.execute(
        sa.text(
            "INSERT INTO connections ("
            "id, user_id, type, provider_name, display_name, credential_id, "
            "extra_config, is_default, status, created_at, updated_at"
            ") SELECT connection_id, user_id, type, provider_name, display_name, "
            "credential_id, extra_config, is_default, status, created_at, updated_at "
            "FROM _m11_dedup_connection_snapshot "
            "ON CONFLICT (id) DO NOTHING"
        )
    )

    # 3) dedup이 repoint한 tool을 원래 duplicate로 되돌림 (upgrade step 3 역).
    # UPDATE ... FROM은 PG 전용. backfill_provenance NULL 복원보다 먼저 실행해야
    # 두 집합이 disjoint함이 유지된다 (backfill set = NULL→conn, dedup set =
    # old_conn→canonical). 순서가 뒤집히면 backfill-설정 tool 중 일부가 dedup에
    # 기록돼 있을 때 잘못 NULL됐다가 restore로 덮어씌워지는 복잡성이 생긴다.
    bind.execute(
        sa.text(
            "UPDATE tools SET connection_id = r.original_connection_id "
            "FROM _m11_dedup_tool_remap r "
            "WHERE tools.id = r.tool_id"
        )
    )

    # 4) backfill provenance 기반으로 tool.connection_id를 NULL로 복원
    # (upgrade step 2 역). pre-m11 상태는 항상 NULL이었음 (upgrade SELECT 필터).
    bind.execute(
        sa.text(
            "UPDATE tools SET connection_id = NULL "
            "WHERE id IN (SELECT tool_id FROM _m11_tool_backfill_provenance)"
        )
    )

    marker_like = f"{M11_SEED_MARKER}%"

    # 5) 마커 connection 삭제 — 수동 생성분은 마커가 없어 보존된다.
    #    reused manual connection은 upgrade에서 canonical로 선택돼 남아있어도
    #    마커가 없어 DELETE 대상에서 제외, 보존된다.
    bind.execute(
        sa.text(
            "DELETE FROM connections "
            "WHERE type = 'custom' "
            "AND provider_name = 'custom_api_key' "
            "AND display_name LIKE :m"
        ),
        {"m": marker_like},
    )

    # 6) Provenance 테이블 drop (upgrade step 1 역).
    op.execute("DROP TABLE IF EXISTS _m11_dedup_tool_remap")
    op.execute("DROP TABLE IF EXISTS _m11_dedup_connection_snapshot")
    op.execute("DROP TABLE IF EXISTS _m11_tool_backfill_provenance")
