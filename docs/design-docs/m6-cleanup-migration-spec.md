# M6 m12 Cleanup Migration Spec

**리비전 ID**: `m12_drop_legacy_columns`
**down_revision**: `m11_custom_connection` (파일명 `m11_custom_credential_migration.py` 내부 `revision = "m11_custom_connection"`)
**작성자**: 피차이 (TTH 사일로 아키텍트)
**날짜**: 2026-04-21
**ADR 참조**: [ADR-008 Connection Entity](./adr-008-connection-entity.md) §4, §6

---

## 1. 목표

M4/M5 동안 Connection 엔티티로 이관된 이후 dead가 된 **legacy 컬럼/테이블/fallback**을 완전히 제거하여 `tools.connection_id` 경로를 **single source of truth**로 확정한다.

### 목표 아님
- 옵션 D (PATCH /api/tools/{id}에 connection_id 필드 추가) — M6.1 별도 PR
- `agent_tools.connection_id` override — M5.5 별도 PR
- 프론트 신규 기능/리디자인 — 금지

---

## 2. 제거 대상

### 2.1 컬럼 (drop)

| 테이블 | 컬럼 | 이전 FK/타입 | 생성 위치 |
|--------|------|--------------|-----------|
| `tools` | `mcp_server_id` | FK → `mcp_servers.id`, Uuid nullable | `aa5b4cc59ddb` (initial) — **이름 없이 생성** |
| `tools` | `auth_config` | JSON nullable (평문, M9에서 connections.extra_config.env_vars로 이관) | `aa5b4cc59ddb` (initial) |
| `tools` | `credential_id` | FK → `credentials.id` ON DELETE SET NULL | `m6_add_credentials` → constraint 이름 `fk_tools_credential_id` |
| `agent_tools` | `config` | JSON nullable | `b4e1a2f3c5d7_add_agent_tools_config` |

### 2.2 테이블 (drop)

| 테이블 | 비고 |
|--------|------|
| `mcp_servers` | M9에서 `connections(type='mcp')`로 복제 완료. 참조하는 FK: `tools.mcp_server_id`(drop 예정), `mcp_servers.user_id`(테이블과 함께 소멸), `mcp_servers.credential_id`(테이블과 함께 소멸) |

### 2.3 의존성 체크 결과 (S2 사전 조사)

- **`mcp_servers`를 FK로 참조하는 다른 테이블**: `tools.mcp_server_id` **단 하나**. `connections`는 `mcp_servers`를 참조하지 않음 (독립 복제 후 링크는 `tools.connection_id`에서만 발생). 따라서 `tools.mcp_server_id` drop → `mcp_servers` drop 순서로 안전.
- **`connections` 테이블 정의에서 mcp 관련 컬럼 여부**: 없음. `connections`는 `credentials`만 FK로 참조(`ondelete=SET NULL`). `extra_config.env_vars` JSON 안에 MCP URL/auth가 들어있을 뿐 스키마 FK는 없음.
- **migration chain 순서 확정**: `aa5b4cc59ddb` → ... → `m6_add_credentials`(fk_tools_credential_id) → `m7_add_credential_field_keys` → `m8_add_connections` → `m9_migrate_mcp_to_connections`(fk_tools_connection_id + tools.connection_id) → `m10_prebuilt_connection` → `m11_custom_connection` → **`m12_drop_legacy_columns`** (신규).

---

## 3. upgrade 순서 (중요)

PostgreSQL에서 DDL은 트랜잭션 내에서 실행되므로 중간 실패 시 Alembic이 자동 rollback한다. 그럼에도 의존성 순서는 엄격히 지킨다:

```
1) FK constraint drop (tools)
   - fk_tools_mcp_server_id            # initial에서 이름 없이 생성 → PG 기본명 "tools_mcp_server_id_fkey"
   - fk_tools_credential_id            # m6에서 명시 생성한 이름
2) tools 컬럼 drop
   - tools.mcp_server_id
   - tools.auth_config
   - tools.credential_id
3) agent_tools.config drop
4) mcp_servers 테이블 drop
   (mcp_servers 내부의 fk_mcp_servers_credential_id / user_id FK는 drop_table이 자동 정리)
5) orphan index cleanup — 현재 스코프 없음 (tools.mcp_server_id / auth_config / credential_id에 별도 index 없음, agent_tools.config도 없음)
```

### 3.1 FK 이름 실체 확정 (추측 금지)

| FK | 이름 | 근거 |
|----|------|------|
| `tools.mcp_server_id → mcp_servers.id` | **`tools_mcp_server_id_fkey`** (PostgreSQL 자동) | `aa5b4cc59ddb` line 145-148에서 `sa.ForeignKeyConstraint([...])`를 이름 없이 생성. PG는 `<table>_<col>_fkey` convention으로 자동 네이밍 |
| `tools.credential_id → credentials.id` | **`fk_tools_credential_id`** | `m6_add_credentials.py` line 38-45에서 `op.create_foreign_key("fk_tools_credential_id", ...)` |

**프로덕션 배포 전 반드시**:
```sql
\d tools
-- 또는
SELECT conname FROM pg_constraint
WHERE conrelid = 'tools'::regclass AND contype = 'f';
```
으로 실제 이름을 재확인한 뒤 m12 파일의 drop_constraint 이름을 확정. 다른 환경에서 수동 리네임된 경우 대비.

### 3.2 aiosqlite 테스트 호환

- aiosqlite in-memory 테스트는 `conftest.py`가 모델 정의로 테이블을 생성하므로 m12 실행 자체는 일반적으로 테스트에서 수행되지 않는다 (`upgrade head` 없이 모델 기반 `create_all`).
- m9/m10/m11 precedent: alembic round-trip 검증은 **docker PostgreSQL에서만** 수행한다.
- 단, `op.batch_alter_table`이 필요한 drop은 없다. tools/agent_tools는 PostgreSQL에서 단순 `op.drop_column`/`op.drop_constraint`로 충분.

---

## 4. downgrade 전략

**비가역**: 이 migration은 컬럼과 테이블을 **영구 삭제**한다. downgrade는 **스키마 구조만 복구**하고 원본 데이터는 복구 불가능하다.

### 4.1 downgrade 구현 요구사항

1. `mcp_servers` 테이블 재생성 (빈 테이블, 원본 데이터 없음) — initial migration과 컬럼 정의 일치해야 함
2. `tools.mcp_server_id` 컬럼 + FK(`tools_mcp_server_id_fkey`) 재생성 (NULL 기본값)
3. `tools.auth_config` JSON nullable 재생성
4. `tools.credential_id` 컬럼 + FK(`fk_tools_credential_id`) 재생성
5. `agent_tools.config` JSON nullable 재생성
6. `mcp_servers.credential_id` 컬럼 + FK(`fk_mcp_servers_credential_id`) 재생성 (m6에서 만들었던 것)

### 4.2 명시 주석

downgrade 함수 최상단에 다음 주석 필수:
```python
# downgrade: structure only — DATA LOSS IS PERMANENT.
# tools.mcp_server_id / auth_config / credential_id, agent_tools.config,
# mcp_servers 테이블의 원본 데이터는 복구되지 않는다.
# 프로덕션 롤백이 필요하면 alembic downgrade 대신 DB 스냅샷 복원을 사용하라.
```

### 4.3 downgrade 순서 (역순)

```
1) mcp_servers 테이블 재생성 (initial 컬럼 정의 그대로, 빈 상태)
2) mcp_servers.credential_id 컬럼 + fk_mcp_servers_credential_id 재생성
3) agent_tools.config 컬럼 재생성
4) tools.auth_config 컬럼 재생성
5) tools.credential_id 컬럼 + fk_tools_credential_id 재생성
6) tools.mcp_server_id 컬럼 + tools_mcp_server_id_fkey 재생성
```

---

## 5. 프로덕션 마이그레이션 가이드

### 5.1 pre-check 쿼리 (BEFORE `alembic upgrade`)

운영 PG에서 다음 쿼리를 **모두 실행**하고 결과를 확인한 뒤 진행:

```sql
-- (A) M4/M5 이관이 누락된 CUSTOM tool 확인 — 반드시 0
--     m11이 (user_id, credential_id) 기준으로 connection을 만들었어야 함
SELECT COUNT(*) AS stale_custom_tools
FROM tools
WHERE type = 'custom'
  AND credential_id IS NOT NULL
  AND connection_id IS NULL;
-- 기대: 0
-- 0이 아니면 → m11 재실행 or 수동 매핑 후 진행

-- (B) dead mcp_server_id 참조 확인 — 반드시 0
--     m9가 모든 mcp_server_id를 connection_id로 매핑했어야 함
SELECT COUNT(*) AS stale_mcp_tools
FROM tools
WHERE mcp_server_id IS NOT NULL
  AND connection_id IS NULL;
-- 기대: 0
-- 0이 아니면 → m9에서 credential_auth_recoverable=False로 건너뛴 MCP server 존재 (m9 line 183-228 fallthrough)
--            → credential.field_keys를 수복하고 m9를 재실행하거나, 해당 tool을 수동 재바인딩

-- (C) agent_tools.config 실제 사용 여부 확인 (S1 베조스 분석 input)
SELECT COUNT(*) AS non_empty_agent_tool_configs
FROM agent_tools
WHERE config IS NOT NULL
  AND config::text <> '{}'
  AND config::text <> 'null';
-- 기대: S1 삭제 분석에서 허용되는 수준. 값이 있다면 chat_service.py:445 merge 로직이 해당 값을 어떻게 쓰는지 S1 보고서 확인 후 drop 결정.

-- (D) 참고: mcp_servers row 수
SELECT COUNT(*) FROM mcp_servers;
-- 기록용. 이 값만큼의 connection이 M9에서 복제되었어야 함.
```

### 5.2 upgrade 실행

```bash
cd backend
uv run alembic upgrade head   # m12 적용
# 확인
psql -U moldy -d moldy -c "\d tools"
psql -U moldy -d moldy -c "\d agent_tools"
psql -U moldy -d moldy -c "SELECT to_regclass('mcp_servers');"  # NULL 기대
```

### 5.3 round-trip 검증 (dev/stg)

```bash
uv run alembic upgrade head
uv run alembic downgrade -1   # m12 downgrade (구조 복구, 데이터 없음)
uv run alembic upgrade head   # 재적용
```

### 5.4 rollback 시나리오

| 상황 | 대응 |
|------|------|
| `alembic upgrade` 중 실패 | PostgreSQL DDL은 transactional — 자동 rollback. 원인 분석 후 재시도. |
| upgrade 완료 후 runtime 문제 발견 | `alembic downgrade -1`은 **스키마만** 복구. 원본 데이터는 영영 상실. **DB 스냅샷 복원이 정공**. |
| aiosqlite 테스트 환경 | m12는 `upgrade head`가 선행되지 않은 환경에서 drop만 시도하면 실패. 테스트는 모델 정의 기반 `create_all`을 쓰므로 실제로는 이 경로를 타지 않음 (기존 precedent). |

---

## 6. 모델 레이어 변경 스펙 (S3 젠슨 가이드)

### 6.1 `backend/app/models/tool.py`

**1) `MCPServer` 클래스 전체 제거** (현재 line 35-59):
- class 정의 전체 + `__tablename__ = "mcp_servers"`
- `tools` relationship (mcp_server → tools back_populates)
- `credential` relationship

**2) `Tool` 모델에서 제거**:
- `mcp_server_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mcp_servers.id"))` (line 72)
- `auth_config: Mapped[dict | None] = mapped_column(JSON)` (line 82)
- `credential_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True)` (line 83-85)
- `mcp_server: Mapped[MCPServer | None] = relationship(back_populates="tools")` (line 92)
- `credential: Mapped[Credential | None] = relationship(foreign_keys=[credential_id], lazy="joined")` (line 96-98)

**3) `AgentToolLink`에서 제거**:
- `config: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)` (line 30)

**4) `Tool.auth_type` 필드**는 **보존** — auth_type는 PREBUILT/CUSTOM 구분 등 runtime에서 아직 사용 중. M6 스코프는 auth_config / credential_id / mcp_server_id만.

### 6.2 import 영향 범위 (grep 필수)

S3 구현 시 다음 grep으로 사용처 전수 확인:
```bash
# 모델 import
rg "from app.models.tool import .*MCPServer" backend/
rg "from app.models import .*MCPServer" backend/

# 컬럼 참조
rg "\.mcp_server_id" backend/
rg "\.auth_config" backend/
rg "\.credential_id" backend/app/models/ backend/app/services/ backend/app/routers/ backend/app/agent_runtime/
rg "link\.config|AgentToolLink.*config" backend/

# 테이블명 참조
rg '"mcp_servers"|\bmcp_servers\b' backend/
```

### 6.3 schemas/tool.py 연쇄 수정

ToolResponse 필드 정리 (S1 베조스 보고서와 교차 검증 필수):
- `_mask_auth_config` 메서드 제거
- `ToolResponse.auth_config` / `ToolResponse.credential_id` / `ToolResponse.mcp_server_id` 필드 제거
- `MCPServerCreate` / `MCPServerResponse` 스키마 전체 제거
- `AgentToolResponse.config` 필드 제거 (API 응답 일관성)

프론트 `lib/types/` 에서도 동일 필드가 dead로 남음 → S4 저커버그 담당.

---

## 7. 테스트 guidance

### 7.1 마이그레이션 자체 테스트
- 존재 시 `backend/tests/test_migrations.py` 확장 — m12 round-trip 정적 검증 (m11 precedent: `inspect.getsource`로 upgrade/downgrade 함수 본문 계약 확인)
- aiosqlite는 FK cascade 동작이 PG와 다를 수 있지만 **conftest.py가 모델 기반 create_all을 사용**하므로 m12가 drop한 컬럼/테이블은 자연스럽게 존재하지 않음 → pytest는 모델 변경에 자동 적응

### 7.2 기능 회귀 테스트
- MCP legacy 시나리오 테스트(예: `test_tool_service_mcp_legacy.py` 류)는 **삭제** — connection 경로 테스트로 대체 (S5 베조스 담당)
- CUSTOM bridge override 테스트는 삭제 (chat_service에서 해당 코드 제거)
- PREBUILT env fallback 테스트는 **유지** (CUSTOM/MCP와 비대칭 의도, HANDOFF.md invariant)

### 7.3 실제 PG 검증 (필수)
docker-compose PostgreSQL에서 `alembic upgrade head && alembic downgrade -1 && alembic upgrade head` 전체 round-trip PASS.

---

## 8. 젠슨에게 주는 구체 지시

### 8.1 m12 파일 템플릿

파일: `backend/alembic/versions/m12_drop_legacy_columns.py`

```python
"""M12: drop legacy columns/tables after connection entity migration

Revision ID: m12_drop_legacy_columns
Revises: m11_custom_connection
Create Date: 2026-04-21

ADR-008 §4 / §6 이행 완료 — M4/M5에서 Connection 엔티티로 이관된 이후
dead가 된 legacy 컬럼/테이블을 제거한다. tools.connection_id가 single
source of truth가 된다.

## 제거 대상
- tools.mcp_server_id (FK: tools_mcp_server_id_fkey, PG 기본명)
- tools.auth_config
- tools.credential_id (FK: fk_tools_credential_id)
- agent_tools.config
- mcp_servers 테이블 전체 (m9에서 connections로 복제 완료)

## pre-check (프로덕션 필수)
docs/design-docs/m6-cleanup-migration-spec.md §5.1 쿼리 3건 0/0/허용수준 확인 후 적용.

## downgrade
구조만 복구, 데이터는 영구 상실. 프로덕션 롤백은 DB 스냅샷 복원을 사용.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m12_drop_legacy_columns"
down_revision = "m11_custom_connection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) FK drop (tools)
    # tools.mcp_server_id FK는 initial migration이 이름 없이 만들어 PG 기본명.
    # 배포 전 `\d tools`로 실제 이름 재확인 필수.
    op.drop_constraint("tools_mcp_server_id_fkey", "tools", type_="foreignkey")
    op.drop_constraint("fk_tools_credential_id", "tools", type_="foreignkey")

    # 2) tools legacy 컬럼 drop
    op.drop_column("tools", "mcp_server_id")
    op.drop_column("tools", "auth_config")
    op.drop_column("tools", "credential_id")

    # 3) agent_tools.config drop
    op.drop_column("agent_tools", "config")

    # 4) mcp_servers 테이블 drop (내부 FK는 drop_table이 자동 정리)
    op.drop_table("mcp_servers")


def downgrade() -> None:
    # downgrade: structure only — DATA LOSS IS PERMANENT.
    # tools.mcp_server_id / auth_config / credential_id, agent_tools.config,
    # mcp_servers 테이블의 원본 데이터는 복구되지 않는다.
    # 프로덕션 롤백이 필요하면 alembic downgrade 대신 DB 스냅샷 복원을 사용하라.

    # 역순: mcp_servers → agent_tools.config → tools.auth_config →
    # tools.credential_id → tools.mcp_server_id

    # 1) mcp_servers 재생성 (initial + m6 컬럼 합본, 빈 테이블)
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("auth_type", sa.String(length=20), nullable=False),
        sa.Column("auth_config", sa.JSON(), nullable=True),
        sa.Column("credential_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["credentials.id"],
            name="fk_mcp_servers_credential_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 2) agent_tools.config 복원
    op.add_column(
        "agent_tools",
        sa.Column("config", sa.JSON(), nullable=True),
    )

    # 3) tools.auth_config 복원
    op.add_column(
        "tools",
        sa.Column("auth_config", sa.JSON(), nullable=True),
    )

    # 4) tools.credential_id + FK 복원
    op.add_column(
        "tools",
        sa.Column("credential_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_tools_credential_id",
        "tools",
        "credentials",
        ["credential_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 5) tools.mcp_server_id + FK 복원 (initial과 동일하게 이름 없이 생성해
    #    PG 기본명 tools_mcp_server_id_fkey가 재부여되도록)
    op.add_column(
        "tools",
        sa.Column("mcp_server_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        None,  # PG 기본명 fallback
        "tools",
        "mcp_servers",
        ["mcp_server_id"],
        ["id"],
    )
```

### 8.2 FK 실제 이름 재확인 절차

젠슨이 구현 시작 시 docker-compose PostgreSQL을 올린 상태에서:

```bash
cd backend
docker-compose up -d postgres
uv run alembic upgrade head  # m11까지 적용

# FK 이름 실측
psql postgresql://moldy:moldy@localhost:5432/moldy -c "\
  SELECT conname, pg_get_constraintdef(oid) \
  FROM pg_constraint \
  WHERE conrelid = 'tools'::regclass AND contype = 'f';"
```

예상 출력:
```
 conname                   | pg_get_constraintdef
 tools_user_id_fkey        | FOREIGN KEY (user_id) REFERENCES users(id)
 tools_mcp_server_id_fkey  | FOREIGN KEY (mcp_server_id) REFERENCES mcp_servers(id)
 fk_tools_credential_id    | FOREIGN KEY (credential_id) REFERENCES credentials(id) ON DELETE SET NULL
 fk_tools_connection_id    | FOREIGN KEY (connection_id) REFERENCES connections(id) ON DELETE SET NULL
```

`tools_mcp_server_id_fkey`가 다르면 m12 파일의 `drop_constraint` 이름을 수정.

### 8.3 구현 체크리스트 (젠슨 self-verify)

1. [ ] `m12_drop_legacy_columns.py` 생성 + FK 이름 실측 반영
2. [ ] `backend/app/models/tool.py` — MCPServer 클래스 + Tool.mcp_server_id/auth_config/credential_id + relationship 2종 + AgentToolLink.config 제거
3. [ ] `backend/app/schemas/tool.py` — _mask_auth_config + MCPServer 스키마 + ToolResponse.{auth_config,credential_id,mcp_server_id} + AgentToolResponse.config 제거
4. [ ] `backend/app/services/` — resolve_server_auth + MCPServer CRUD + chat_service legacy fallback 제거 (S1 베조스 보고서 라인 단위 참조)
5. [ ] `backend/app/routers/tools.py` — /api/tools/mcp-server* 4개 엔드포인트 제거
6. [ ] `backend/app/agent_runtime/` — legacy 필드 참조 정리
7. [ ] 테스트 삭제/수정 — S1 분류에 따라 MCP legacy 시나리오 파일 삭제, connection path 유지
8. [ ] `rg "mcp_server_id|auth_config|resolve_server_auth|MCPServer|register_mcp_server" backend/app/` → 0
9. [ ] round-trip: `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head` PASS (docker PG)
10. [ ] `uv run ruff check .` PASS
11. [ ] `uv run pytest` PASS (허용 감소 후 0 regression)

---

## 9. 리스크 & 완화

| # | 리스크 | 완화 |
|---|--------|------|
| R1 | `tools_mcp_server_id_fkey` 이름이 환경별로 다를 수 있음 | 8.2 절차로 배포 전 실측 강제 |
| R2 | `agent_tools.config`에 실사용 데이터가 남아있어 기능 회귀 | §5.1 (C) pre-check + S1 베조스 보고서 교차 검증 — 비어있지 않으면 S1/S2/S3 공동 재검토 |
| R3 | m9에서 `credential_auth_recoverable=False`로 스킵된 MCP server가 남아있어 m12 이후 dangling | §5.1 (B) pre-check 필수, 0이 아니면 m9 재실행 선행 |
| R4 | downgrade 후 데이터 상실 발견 | §4.2 주석 명시 + §5.4 rollback 가이드 — DB 스냅샷 복원이 정공 |
| R5 | aiosqlite 테스트에서 drop 대상 모델 참조가 남아 import 에러 | S3 6.2 grep 체크리스트로 전수 정리 |
| R6 | PostgreSQL DDL transactional이라 중간 실패 시 자동 rollback이지만, `op.drop_table`이 dangling FK를 참조하면 실패 가능 | §3 순서 (FK drop → column drop → table drop) 엄수 |

---

## 10. 완료 조건 (done-when)

1. `backend/alembic/versions/m12_drop_legacy_columns.py` 생성, `down_revision = "m11_custom_connection"` 확인
2. `uv run alembic upgrade head` PASS (docker PG), round-trip PASS
3. `psql \d tools` — `mcp_server_id`, `auth_config`, `credential_id` 없음
4. `psql \d agent_tools` — `config` 없음
5. `psql -c "SELECT to_regclass('mcp_servers')"` — NULL
6. `rg "mcp_server_id|auth_config|resolve_server_auth|MCPServer" backend/app/` — 0
7. `uv run pytest` PASS (허용 감소 후 0 regression)
8. `uv run ruff check .` PASS
