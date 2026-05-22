# Marketplace Module Contracts (M1-S2)

> 작성일: 2026-05-18
> 작성자: 피차이 (TTH Architect)
> 관련 ADR: [ADR-017 — Marketplace Resources](adr-017-marketplace-resources.md)
> 소스: `docs/marketplace-resources-spec.md` v0.1 §10.8, §11
> 목적: 백엔드 marketplace 도메인의 (a) 모듈 책임 + 임포트 방향, (b) ORM 클래스 시그니처(컬럼/relationship/index), (c) 핵심 Pydantic 스키마 계약을 명문화하여 젠슨(M2 Slice A~F 구현자)을 언블록한다.

---

## 1. 모듈 경계 (`backend/app/marketplace/`)

신규 폴더 11개 모듈. 각 모듈은 한 가지 책임을 가지며, 임포트 방향은 명시된 단방향만 허용한다.

| # | 모듈 | 책임 | 사용처 (import 방향) |
|---|------|------|----------------------|
| 1 | `__init__.py` | 패키지 마커. 공개 심볼 re-export 없음 (barrel export 금지). | — |
| 2 | `access.py` | 권한 매트릭스. `can_view_item`, `can_install_item`, `can_manage_item`. **부수효과 금지** — 순수 함수 + DB read만. | `service`, `install_service`, `publish_service`, `routers/marketplace` |
| 3 | `schemas.py` | Pydantic v2 입출력 스키마 (§3 참조). DB 객체 직접 노출 금지. | 모든 service, `routers/marketplace`, `routers/skills` (origin/publication summary embed) |
| 4 | `service.py` | Catalog list/detail 비즈니스 로직. filter/sort/pagination. | `routers/marketplace` |
| 5 | `install_service.py` | install/update/uninstall flow. transaction 경계 + filesystem move. `install_mode` 처리. | `routers/marketplace` |
| 6 | `publish_service.py` | publish flow. immutable version 생성 + ACL upsert + publication_links 갱신. **반드시 `secret_scan.scan_package`를 먼저 호출**. | `routers/marketplace`, `routers/skills` (간접: upload는 `secret_scan`만 사용) |
| 7 | `origin_service.py` | `derive_origin_summary`, `derive_publication_summary`, `derive_installation_summary`, `derive_credential_summary`, `mark_installation_dirty`. installed resource ↔ marketplace 양방향 derivation. | `service`, `install_service`, `publish_service`, `routers/skills`, `routers/mcp_servers`, `routers/agents` |
| 8 | `secret_scan.py` | `SECRET_FILE_PATTERNS`, `SECRET_CONTENT_PATTERNS`, `scan_package(extracted_dir) -> list[SecretFinding]`. **순수 함수** — DB 의존 없음. | `publish_service`, `install_service`(import용), `k_skill_importer`, `routers/skills` (upload 회귀 가드) |
| 9 | `redaction.py` | `redact_credential_values(text, mapped_env_vars)`, `redact_keys(payload)`. **순수 함수**. | `agent_runtime/executor`, `agent_runtime/streaming`, 모든 service layer의 exception 변환, raw logger formatter |
| 10 | `credential_requirements.py` | requirement 매핑 + validation + **runtime env injection plan**. `resolve_credential_bindings(db, skill, user, agent_skill_config) -> dict[str, ResolvedCredential]`. | `install_service`, `agent_runtime/executor` (Slice E), `routers/skills` (credential-bindings endpoints) |
| 11 | `k_skill_importer.py` | upstream sync 본체 (git fetch, discovery, packaging, metadata 추출, upsert). CLI 진입점이 호출. **import 시 부수효과 없음**. | `scripts/sync_k_skill` |

### 1.1 모듈 의존 그래프

```text
                  ┌──────────────────────────────────────────┐
                  │              schemas.py                  │
                  │  (Pydantic — 모든 service의 입출력 계약) │
                  └──────────────────────────────────────────┘
                          ▲   ▲   ▲   ▲   ▲   ▲
                          │   │   │   │   │   │
       ┌──────────────────┘   │   │   │   │   └────────────────────┐
       │                      │   │   │   │                        │
       │                      │   │   │   └──┐                     │
       │                      │   │   │      │                     │
   access.py            origin_service.py    │              credential_requirements.py
       ▲     ▲                ▲              │                     ▲
       │     │                │              │                     │
       │     └────────────────┤              │                     │
       │                      │              │                     │
   service.py          install_service.py    publish_service.py    │
                              ▲                  ▲                 │
                              │                  │                 │
                              │              secret_scan.py        │
                              │                  ▲                 │
                              │                  │                 │
                              └──────────────────┘                 │
                                                                   │
                              k_skill_importer.py ──▶ secret_scan ─┘
                                                  └──▶ credential_requirements

      redaction.py  ◀── (agent_runtime/executor, streaming, service exception 변환)
```

**임포트 방향 규칙**:

1. `schemas.py`는 marketplace 내 어떤 모듈도 import 안 함 (leaf). Pydantic + `datetime`/`uuid`/`typing` + `models.marketplace` 타입 힌트만.
2. `access.py`, `secret_scan.py`, `redaction.py`는 marketplace 내 다른 모듈을 import 안 함 (leaf 또는 utility).
3. `service.py`, `install_service.py`, `publish_service.py`, `k_skill_importer.py`는 위 leaf들을 자유롭게 import 가능. **서로 cross-import 금지** (예: install_service는 publish_service를 import 안 함).
4. `routers/marketplace.py`는 service 4종만 import. `models.marketplace`, `schemas.py`도 직접 import 가능.
5. `agent_runtime/`은 `redaction.py`와 `credential_requirements.py`만 import. service 레이어 우회 금지.
6. `origin_service.py`는 `models/marketplace`, `models/skill` 둘 다 의존하므로 `routers/skills`/`routers/mcp_servers`/`routers/agents`에서 직접 사용. service들도 호출 가능.

### 1.2 신규 라우터 / 모델 / 스크립트

| 경로 | 책임 |
|------|------|
| `backend/app/routers/marketplace.py` | 마켓플레이스 라우터 (Spec §10.1~§10.7). `Depends(get_current_user)` + 모든 mutation에 `Depends(verify_csrf)`. admin은 `Depends(require_super_user)` 추가 |
| `backend/app/models/marketplace.py` | 6개 ORM 클래스 (§2 참조). 단일 파일 |
| `backend/app/scripts/sync_k_skill.py` | super_user CLI 진입점 (`python -m app.scripts.sync_k_skill ...`). argparse + `k_skill_importer.sync_upstream(...)` 호출. **DB 세션은 CLI에서 자체 생성** (라우터 의존 X) |

### 1.3 기존 모듈 변경 영향

| 파일 | 변경 |
|------|------|
| `models/skill.py` | `Skill`에 12개 컬럼 추가 (m41). `AgentSkillLink`에 `config: Mapped[dict \| None]` 추가 (m42) |
| `routers/skills.py` | `/api/skills/{id}/credential-requirements`, `/credential-bindings[/{key}]` 4개 endpoint 추가. `upload` 엔드포인트에 `secret_scan.scan_package` 호출. 응답에 `origin_summary`/`publication_summary` 임베드 (`origin_service` 호출) |
| `routers/mcp_servers.py` | 응답에 `origin_summary`/`publication_summary` 임베드만 (Phase 1은 MCP marketplace install 미구현) |
| `routers/agents.py` | 응답에 `origin_summary`/`publication_summary` 임베드만 (Phase 1은 Agent marketplace install 미구현) |
| `agent_runtime/executor.py` | `build_agent`: per-thread copytree + skills_sources 변경. `_create_skill_execute_tool`: 시그니처 + env injection + redaction wrap (Slice E) |
| `agent_runtime/streaming.py` | tool_call_result payload에 `redaction.redact_keys` 적용 |
| `skills/runtime.py:build_skills_for_agent` | `SkillRuntimeDescriptor` 리스트 반환으로 확장 (id, slug, original_storage_path, storage_path, credential_bindings) |
| `skills/service.py` | content/files/PATCH 4개 endpoint에서 `origin_service.mark_installation_dirty` best-effort 호출 |
| `credentials/definitions/__init__.py` | 신규 8개 정의 import (자동 register) |
| `config.py` | `k_skill_upstream_url`, `k_skill_upstream_ref`, `k_skill_sync_dir`, `k_skill_builtin_storage_dir` 추가 |

---

## 2. ORM 계약 — `backend/app/models/marketplace.py`

`SkillCredentialBinding`은 의미상 marketplace 도메인의 일부지만 `skills`와 강결합되므로 본 파일에 함께 둔다 (총 6개 클래스). 모든 클래스는 SQLAlchemy 2.0 declarative + async + `mapped_column` 패턴(ADR-016/-009과 동일).

### 2.1 공통 임포트 헤더 (참조용)

```python
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer,
    String, Text, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.user import User
```

### 2.2 `MarketplaceItem`

테이블: `marketplace_items` (m40).

| 컬럼 | 타입 | NULL | 기본 | 비고 |
|------|------|------|------|------|
| `id` | `Mapped[UUID]` `PG_UUID(as_uuid=True)` PK | NO | `uuid4` | |
| `resource_type` | `Mapped[str]` `String(20)` | NO | — | CHECK `('agent','mcp','skill')` |
| `owner_user_id` | `Mapped[UUID \| None]` FK `users.id` ON DELETE SET NULL | YES | NULL | system item은 NULL |
| `is_system` | `Mapped[bool]` `Boolean` | NO | FALSE | |
| `is_listed` | `Mapped[bool]` `Boolean` | NO | FALSE | super_user 토글 |
| `name` | `Mapped[str]` `String(200)` | NO | — | |
| `slug` | `Mapped[str]` `String(220)` | NO | — | |
| `description` | `Mapped[str \| None]` `Text` | YES | NULL | |
| `icon_url` | `Mapped[str \| None]` `Text` | YES | NULL | |
| `visibility` | `Mapped[str]` `String(20)` | NO | `'private'` | CHECK `('private','restricted','public','unlisted','system')` |
| `status` | `Mapped[str]` `String(20)` | NO | `'draft'` | CHECK `('draft','published','deprecated','disabled')` |
| `moderation_status` | `Mapped[str]` `String(20)` | NO | `'approved'` | 운영자 가시 상태 |
| `source_kind` | `Mapped[str \| None]` `String(40)` | YES | NULL | `'user' \| 'k-skill' \| 'import' \| 'system_seed'` |
| `source_url` | `Mapped[str \| None]` `Text` | YES | NULL | |
| `source_external_id` | `Mapped[str \| None]` `String(240)` | YES | NULL | k-skill upstream name |
| `latest_version_id` | `Mapped[UUID \| None]` FK `marketplace_versions.id` ON DELETE SET NULL | YES | NULL | circular FK — m40에서 ALTER로 추가 |
| `tags` | `Mapped[list \| None]` `JSON` | YES | NULL | |
| `categories` | `Mapped[list \| None]` `JSON` | YES | NULL | |
| `locale` | `Mapped[str \| None]` `String(20)` | YES | NULL | |
| `metadata_json` | `Mapped[dict \| None]` `JSON` | YES | NULL | 컬럼명은 `metadata` (예약어 회피 위해 attribute는 `metadata_json` 권장) |
| `created_at` | `Mapped[datetime]` `DateTime(timezone=True)` | NO | `now()` | |
| `updated_at` | `Mapped[datetime]` `DateTime(timezone=True)` | NO | `now()` | `onupdate=now()` |
| `published_at` | `Mapped[datetime \| None]` | YES | NULL | |

**Constraints**:
- `ck_marketplace_resource_type CHECK (resource_type IN ('agent','mcp','skill'))`
- `ck_marketplace_visibility CHECK (visibility IN ('private','restricted','public','unlisted','system'))`
- `ck_marketplace_status CHECK (status IN ('draft','published','deprecated','disabled'))`
- `ck_marketplace_system_owner CHECK ((is_system = false) OR (owner_user_id IS NULL))`

**Indexes**:
- `uq_marketplace_items_system_slug UNIQUE (resource_type, slug) WHERE is_system = true` (partial)
- `uq_marketplace_items_owner_slug UNIQUE (owner_user_id, resource_type, slug) WHERE owner_user_id IS NOT NULL` (partial)
- `ix_marketplace_items_listed (is_listed, visibility, status)`

**Relationships**:
- `owner: Mapped[User | None] = relationship(User, foreign_keys=[owner_user_id])`
- `versions: Mapped[list[MarketplaceVersion]] = relationship("MarketplaceVersion", back_populates="item", foreign_keys="MarketplaceVersion.item_id", cascade="all, delete-orphan")`
- `latest_version: Mapped[MarketplaceVersion | None] = relationship("MarketplaceVersion", foreign_keys=[latest_version_id], post_update=True)` — `post_update=True`로 circular FK 처리
- `acl_entries: Mapped[list[MarketplaceItemACL]] = relationship("MarketplaceItemACL", back_populates="item", cascade="all, delete-orphan")`
- `installations: Mapped[list[MarketplaceInstallation]] = relationship("MarketplaceInstallation", back_populates="item", cascade="all, delete-orphan")`
- `publication_links: Mapped[list[MarketplacePublicationLink]] = relationship("MarketplacePublicationLink", back_populates="item", cascade="all, delete-orphan")`

### 2.3 `MarketplaceItemACL`

테이블: `marketplace_item_acl` (m40). composite PK.

| 컬럼 | 타입 | NULL | 기본 | 비고 |
|------|------|------|------|------|
| `item_id` | `Mapped[UUID]` FK `marketplace_items.id` ON DELETE CASCADE | NO | — | PK part |
| `user_id` | `Mapped[UUID]` FK `users.id` ON DELETE CASCADE | NO | — | PK part |
| `permission` | `Mapped[str]` `String(20)` | NO | `'install'` | CHECK `('view','install','manage')` |
| `created_at` | `Mapped[datetime]` `DateTime(timezone=True)` | NO | `now()` | |

**Constraints**: `PRIMARY KEY (item_id, user_id)`, `ck_marketplace_acl_permission`.

**Relationships**:
- `item: Mapped[MarketplaceItem] = relationship(back_populates="acl_entries")`
- `user: Mapped[User] = relationship(User)`

### 2.4 `MarketplaceVersion`

테이블: `marketplace_versions` (m40). **immutable** — service layer는 update 금지.

| 컬럼 | 타입 | NULL | 기본 | 비고 |
|------|------|------|------|------|
| `id` | `Mapped[UUID]` PK | NO | `uuid4` | |
| `item_id` | `Mapped[UUID]` FK `marketplace_items.id` ON DELETE CASCADE | NO | — | |
| `version_label` | `Mapped[str]` `String(80)` | NO | — | 사용자 표시용 (예: `0.1.0`) |
| `version_number` | `Mapped[int]` `Integer` | NO | — | item별 monotonic 증가 |
| `resource_type` | `Mapped[str]` `String(20)` | NO | — | CHECK `('agent','mcp','skill')` |
| `payload_kind` | `Mapped[str]` `String(40)` | NO | — | CHECK `('skill_package','agent_spec','mcp_template')` |
| `payload` | `Mapped[dict]` `JSON` | NO | — | resource_type별 구조 (skill_package: storage 메타 / agent_spec: agent JSON / mcp_template: server config) |
| `storage_path` | `Mapped[str \| None]` `String(500)` | YES | NULL | filesystem snapshot 경로 (skill에만 사용) |
| `content_hash` | `Mapped[str]` `String(64)` | NO | — | SHA-256 hex |
| `size_bytes` | `Mapped[int]` `Integer` | NO | 0 | |
| `credential_requirements` | `Mapped[list \| None]` `JSON` | YES | NULL | `[CredentialRequirementOut.model_dump()]` |
| `dependency_requirements` | `Mapped[list \| None]` `JSON` | YES | NULL | future use |
| `execution_profile` | `Mapped[dict \| None]` `JSON` | YES | NULL | `{support_level, runners, requires_*}` |
| `release_notes` | `Mapped[str \| None]` `Text` | YES | NULL | |
| `source_commit` | `Mapped[str \| None]` `String(80)` | YES | NULL | k-skill upstream commit |
| `source_ref` | `Mapped[str \| None]` `String(120)` | YES | NULL | branch/tag |
| `source_path` | `Mapped[str \| None]` `Text` | YES | NULL | upstream에서의 path |
| `created_by` | `Mapped[UUID \| None]` FK `users.id` ON DELETE SET NULL | YES | NULL | |
| `created_at` | `Mapped[datetime]` | NO | `now()` | |

**Constraints**:
- `ck_marketplace_version_resource_type`
- `ck_marketplace_payload_kind`

**Indexes**:
- `uq_marketplace_versions_item_number UNIQUE (item_id, version_number)`
- `ix_marketplace_versions_content_hash (content_hash)`

**Relationships**:
- `item: Mapped[MarketplaceItem] = relationship(back_populates="versions", foreign_keys=[item_id])`
- `installations: Mapped[list[MarketplaceInstallation]] = relationship(back_populates="version", foreign_keys="MarketplaceInstallation.version_id")`
- `created_by_user: Mapped[User | None] = relationship(User, foreign_keys=[created_by])`

### 2.5 `MarketplaceInstallation`

테이블: `marketplace_installations` (m40).

| 컬럼 | 타입 | NULL | 기본 | 비고 |
|------|------|------|------|------|
| `id` | `Mapped[UUID]` PK | NO | `uuid4` | |
| `user_id` | `Mapped[UUID]` FK `users.id` ON DELETE CASCADE | NO | — | 설치자 |
| `item_id` | `Mapped[UUID]` FK `marketplace_items.id` ON DELETE CASCADE | NO | — | |
| `version_id` | `Mapped[UUID]` FK `marketplace_versions.id` ON DELETE **RESTRICT** | NO | — | version 삭제 시 installation 보호 |
| `resource_type` | `Mapped[str]` `String(20)` | NO | — | |
| `installed_agent_id` | `Mapped[UUID \| None]` FK `agents.id` ON DELETE CASCADE | YES | NULL | |
| `installed_mcp_server_id` | `Mapped[UUID \| None]` FK `mcp_servers.id` ON DELETE CASCADE | YES | NULL | |
| `installed_skill_id` | `Mapped[UUID \| None]` FK `skills.id` ON DELETE CASCADE | YES | NULL | |
| `install_status` | `Mapped[str]` `String(30)` | NO | `'active'` | CHECK `('active','needs_setup','disabled','uninstalled')` |
| `is_dirty` | `Mapped[bool]` `Boolean` | NO | FALSE | |
| `installed_at` | `Mapped[datetime]` | NO | `now()` | |
| `updated_at` | `Mapped[datetime]` | NO | `now()` | `onupdate=now()` |

**Constraints**:
- `ck_marketplace_install_resource_target` — `resource_type` 별로 정확히 하나의 `installed_*_id`가 NOT NULL (Spec §3.5 그대로)
- `ck_marketplace_install_status`

**Indexes**:
- `ix_marketplace_install_user_item (user_id, item_id)`
- `ix_marketplace_install_user_resource (user_id, resource_type)`

**Relationships**:
- `item: Mapped[MarketplaceItem] = relationship(back_populates="installations", foreign_keys=[item_id])`
- `version: Mapped[MarketplaceVersion] = relationship(back_populates="installations", foreign_keys=[version_id])`
- `user: Mapped[User] = relationship(User, foreign_keys=[user_id])`
- `installed_skill: Mapped["Skill | None"] = relationship("Skill", foreign_keys=[installed_skill_id])` — `agents`/`mcp_servers`도 동일 패턴

### 2.6 `MarketplacePublicationLink`

테이블: `marketplace_publication_links` (m40). 내 리소스 ↔ 내가 publish한 item 역참조.

| 컬럼 | 타입 | NULL | 기본 | 비고 |
|------|------|------|------|------|
| `id` | `Mapped[UUID]` PK | NO | `uuid4` | |
| `user_id` | `Mapped[UUID]` FK `users.id` ON DELETE CASCADE | NO | — | publisher |
| `item_id` | `Mapped[UUID]` FK `marketplace_items.id` ON DELETE CASCADE | NO | — | |
| `resource_type` | `Mapped[str]` `String(20)` | NO | — | |
| `source_agent_id` | `Mapped[UUID \| None]` FK `agents.id` ON DELETE CASCADE | YES | NULL | |
| `source_mcp_server_id` | `Mapped[UUID \| None]` FK `mcp_servers.id` ON DELETE CASCADE | YES | NULL | |
| `source_skill_id` | `Mapped[UUID \| None]` FK `skills.id` ON DELETE CASCADE | YES | NULL | |
| `created_at` | `Mapped[datetime]` | NO | `now()` | |
| `updated_at` | `Mapped[datetime]` | NO | `now()` | `onupdate=now()` |

**Constraints**:
- `ck_pub_link_resource_type`
- `ck_pub_link_target` — resource_type별 정확히 하나의 source_*_id NOT NULL

**Indexes**:
- `uq_pub_link_item UNIQUE (item_id)` — item 1개당 publication link 1개
- `ix_pub_link_resource (user_id, resource_type)`

**Relationships**:
- `item: Mapped[MarketplaceItem] = relationship(back_populates="publication_links")`
- `user: Mapped[User] = relationship(User, foreign_keys=[user_id])`

### 2.7 `SkillCredentialBinding`

테이블: `skill_credential_bindings` (m43). Marketplace 도메인의 일부지만 `skills`와 강결합 → `models/marketplace.py`에 둔다.

| 컬럼 | 타입 | NULL | 기본 | 비고 |
|------|------|------|------|------|
| `id` | `Mapped[UUID]` PK | NO | `uuid4` | |
| `skill_id` | `Mapped[UUID]` FK `skills.id` ON DELETE CASCADE | NO | — | |
| `user_id` | `Mapped[UUID]` FK `users.id` ON DELETE CASCADE | NO | — | |
| `requirement_key` | `Mapped[str]` `String(120)` | NO | — | version.credential_requirements의 `key` |
| `credential_id` | `Mapped[UUID]` FK `credentials.id` ON DELETE **RESTRICT** | NO | — | binding 보호 |
| `scope` | `Mapped[str]` `String(20)` | NO | `'skill'` | CHECK `('skill','agent_skill')` — Phase 1은 `'skill'`만 |
| `created_at` | `Mapped[datetime]` | NO | `now()` | |
| `updated_at` | `Mapped[datetime]` | NO | `now()` | `onupdate=now()` |

**Constraints**:
- `ck_skill_credential_binding_scope`
- `UNIQUE (skill_id, user_id, requirement_key, scope)`

**Relationships**:
- `skill: Mapped["Skill"] = relationship("Skill")` — `Skill.credential_bindings` back_populates 추가 권장
- `credential: Mapped["Credential"] = relationship("Credential")`
- `user: Mapped[User] = relationship(User)`

### 2.8 `Skill` / `AgentSkillLink` 확장 (`models/skill.py` 수정)

`Skill`에 추가되는 컬럼은 모두 `mapped_column(..., nullable=...)` 형태로 정의. m41 backfill: 기존 행은 `source_kind='user'`, text skill은 `origin_kind='created_by_me'`, package skill은 `origin_kind='imported_by_me'`.

| 컬럼 | 타입 | NULL | 기본 |
|------|------|------|------|
| `is_system` | `Boolean` | NO | FALSE |
| `source_kind` | `String(40)` | YES | NULL |
| `source_marketplace_item_id` | `PG_UUID` FK `marketplace_items.id` SET NULL | YES | NULL |
| `source_marketplace_version_id` | `PG_UUID` FK `marketplace_versions.id` SET NULL | YES | NULL |
| `source_commit` | `String(80)` | YES | NULL |
| `credential_requirements` | `JSON` | YES | NULL |
| `execution_profile` | `JSON` | YES | NULL |
| `origin_kind` | `String(40)` | NO | `'created_by_me'` |
| `origin_user_id` | `PG_UUID` FK `users.id` SET NULL | YES | NULL |
| `origin_marketplace_item_id` | `PG_UUID` FK `marketplace_items.id` SET NULL | YES | NULL |
| `origin_marketplace_version_id` | `PG_UUID` FK `marketplace_versions.id` SET NULL | YES | NULL |
| `is_dirty` | `Boolean` | NO | FALSE |

`AgentSkillLink`에 추가:

| 컬럼 | 타입 | NULL | 기본 |
|------|------|------|------|
| `config` | `JSON` | YES | NULL |

저장 예: `{"credential_bindings": {"srt_account": "<credential-uuid>"}}`.

---

## 3. 핵심 Pydantic 스키마 계약 (`backend/app/marketplace/schemas.py`)

Spec §10.8을 그대로 따른다. Pydantic v2 (`model_config = ConfigDict(from_attributes=True)`로 ORM 변환 허용). 모든 시간은 `datetime` (UTC 가정).

### 3.1 `MarketplaceItemOut` — Catalog/Detail 응답

```python
class MarketplaceItemOut(BaseModel):
    id: UUID
    resource_type: Literal["agent", "mcp", "skill"]
    name: str
    slug: str
    description: str | None
    visibility: Literal["private", "restricted", "public", "unlisted", "system"]
    status: Literal["draft", "published", "deprecated", "disabled"]
    is_system: bool
    is_listed: bool
    latest_version: MarketplaceVersionSummary | None
    credential_summary: CredentialSummaryOut
    execution_profile: dict[str, Any] | None = None
    origin_summary: ResourceOriginSummaryOut | None = None
    publication_summary: ResourcePublicationSummaryOut
    installation: MarketplaceInstallationSummary
    model_config = ConfigDict(from_attributes=True)
```

- `latest_version`은 item.latest_version → `MarketplaceVersionSummary.model_validate(...)`.
- `credential_summary`, `installation`은 `origin_service.derive_*` 호출 결과.
- `origin_summary`는 owner 관점에서의 출처 — list 응답에서는 null 허용, detail에서는 채움.
- `publication_summary`는 모든 응답에서 채움 (item-level).

### 3.2 `ResourceOriginSummaryOut`

```python
class ResourceOriginSummaryOut(BaseModel):
    kind: Literal[
        "created_by_me", "imported_by_me", "built_in_k_skill",
        "shared_with_me", "community", "system_seed",
    ]
    label: str
    source_name: str | None = None
    source_user_id: UUID | None = None
    marketplace_item_id: UUID | None = None
    marketplace_version_id: UUID | None = None
```

derivation 규칙 (Spec §7.5 + PRD §6):

| 조건 | kind |
|------|------|
| `skills.is_system=True AND skills.source_kind='k-skill'` | `built_in_k_skill` |
| `skills.is_system=True AND skills.source_kind='system_seed'` | `system_seed` |
| `skills.source_kind in ('user','import')` AND `origin_user_id != current_user AND item.visibility='restricted'` | `shared_with_me` |
| `skills.source_kind in ('user','import')` AND `origin_user_id != current_user AND item.visibility='public'` | `community` |
| `origin_user_id == current_user AND source_marketplace_item_id IS NOT NULL` | `imported_by_me` |
| `source_marketplace_item_id IS NULL` (직접 생성) | `created_by_me` |

### 3.3 `ResourcePublicationSummaryOut`

```python
class ResourcePublicationSummaryOut(BaseModel):
    state: Literal[
        "not_published", "draft",
        "published_private", "published_restricted",
        "published_public_listed", "published_public_unlisted",
        "published_unlisted", "disabled",
    ]
    item_id: UUID | None = None
    visibility: Literal["private", "restricted", "public", "unlisted", "system"] | None = None
    status: Literal["draft", "published", "deprecated", "disabled"] | None = None
    is_listed: bool = False
    latest_version_id: UUID | None = None
    version_number: int | None = None
    shared_user_count: int = 0
```

state derivation은 publication_link → item.status × item.visibility × item.is_listed의 결정 테이블.

| item 상태 | state |
|-----------|-------|
| 없음 | `not_published` |
| `status='draft'` | `draft` |
| `status='disabled'` | `disabled` |
| `status='published'` AND `visibility='private'` | `published_private` |
| `status='published'` AND `visibility='restricted'` | `published_restricted` |
| `status='published'` AND `visibility='public'` AND `is_listed=true` | `published_public_listed` |
| `status='published'` AND `visibility='public'` AND `is_listed=false` | `published_public_unlisted` |
| `status='published'` AND `visibility='unlisted'` | `published_unlisted` |

`shared_user_count`는 `marketplace_item_acl` row count.

### 3.4 `MarketplaceInstallationSummary`

```python
class MarketplaceInstallationSummary(BaseModel):
    installed: bool
    installation_id: UUID | None = None
    installed_resource_id: UUID | None = None  # installed_skill_id / installed_agent_id / installed_mcp_server_id
    status: Literal["active", "needs_setup", "disabled", "uninstalled"] | None = None
    update_available: bool = False
    dirty: bool = False
```

derivation 입력: `(current_user, item, latest_version, installation_row?)`. `update_available = installation.version_id != item.latest_version_id`. `dirty`는 installation.is_dirty와 installed_skill.is_dirty의 OR.

### 3.5 `CredentialRequirementOut`

```python
class CredentialRequirementOut(BaseModel):
    key: str                       # version.credential_requirements 내 unique
    definition_key: str            # credentials.definitions에 등록된 키 (예: 'srt_account')
    required: bool
    label: str
    description: str | None = None
    fields: list[str]              # ['username', 'password']
    injection: Literal["env", "config"]   # Phase 1은 'env'만 사용
    scope: Literal["user", "system_dependency", "manual"]
```

- `scope='user'`: 일반 사용자 credential 필요.
- `scope='system_dependency'`: hosted_proxy 등 system credential. 사용자 binding 불필요.
- `scope='manual'`: 사용자가 외부에서 직접 로그인 (kakaotalk-mac 등). credential 미사용.

Publish/k-skill import 입력용 `CredentialRequirementIn`은 동일 필드 + `env_map: dict[str, str]` 추가 (사용자 publish 시 env_map은 옵션).

### 3.6 `InstallMarketplaceItemIn`

```python
class InstallMarketplaceItemIn(BaseModel):
    version_id: UUID | None = None
    name_override: str | None = None
    credential_bindings: dict[str, UUID] = Field(default_factory=dict)  # {requirement_key: credential_id}
    install_missing_credentials: Literal["reject", "needs_setup"] = "needs_setup"
    install_mode: Literal["reuse_or_update", "new_copy", "overwrite_existing"] = "reuse_or_update"
```

### 3.7 `PublishSkillIn`

```python
class PublishSkillIn(BaseModel):
    item_id: UUID | None = None      # 신규 item이면 None, 새 version이면 기존 item id
    visibility: Literal["private", "restricted", "public", "unlisted"]
    name: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    release_notes: str | None = None
    credential_requirements: list[CredentialRequirementIn] = Field(default_factory=list)
    acl_user_ids: list[UUID] = Field(default_factory=list)  # restricted 시 최소 1명

    @model_validator(mode="after")
    def _validate_acl(self) -> "PublishSkillIn":
        if self.visibility == "restricted" and not self.acl_user_ids:
            raise ValueError("marketplace_acl_required")
        return self
```

### 3.8 보조 스키마 (참조)

```python
class MarketplaceVersionSummary(BaseModel):
    id: UUID
    version_label: str
    version_number: int
    content_hash: str
    source_commit: str | None = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class CredentialSummaryOut(BaseModel):
    status: Literal["none", "optional", "required", "hosted_proxy", "manual_login"]
    required_count: int = 0
    optional_count: int = 0
    missing_required_count: int = 0

class UpdateMarketplaceInstallationIn(BaseModel):
    strategy: Literal["overwrite", "install_new_copy", "keep_current"]

class CredentialRequirementIn(BaseModel):
    key: str
    definition_key: str
    required: bool = True
    label: str
    description: str | None = None
    fields: list[str]
    injection: Literal["env", "config"] = "env"
    scope: Literal["user", "system_dependency", "manual"] = "user"
    env_map: dict[str, str] | None = None
```

### 3.9 응답 임베드 — `routers/skills.py`

기존 skill detail/list 응답에 두 필드 추가:

```python
class SkillOut(BaseModel):  # 기존 + 확장
    # ... existing fields ...
    origin_summary: ResourceOriginSummaryOut
    publication_summary: ResourcePublicationSummaryOut
```

list 응답은 N+1 회피 위해 한 번의 query로 publication_links + latest installations + acl_count를 join (origin_service.bulk_derive_*).

`routers/mcp_servers.py`, `routers/agents.py`에도 동일하게 두 필드 추가 (publication만 의미가 있고 origin은 항상 `created_by_me`로 채움 — Phase 1은 install 미구현).

---

## 4. 검증 체크리스트 (M2~M6 진입 전)

젠슨은 본 문서의 §2.2~§2.8 ORM 시그니처를 그대로 구현해야 하고, §3의 Pydantic 스키마 필드명/타입을 변경하지 말 것. 변경이 필요하면 사티아에게 ESCALATION하여 본 문서를 먼저 수정한다.

- [ ] `models/marketplace.py`에 6개 클래스가 모두 존재 (`MarketplaceItem`, `MarketplaceItemACL`, `MarketplaceVersion`, `MarketplaceInstallation`, `MarketplacePublicationLink`, `SkillCredentialBinding`)
- [ ] CHECK constraint 11개가 모두 `__table_args__`에 선언됨
- [ ] partial unique index 2개(`marketplace_items`)가 `Index(..., postgresql_where=...)`로 선언됨
- [ ] `marketplace_items.latest_version_id` FK가 `post_update=True` relationship + m40 ALTER 패턴으로 구현됨
- [ ] `marketplace_installations.version_id`와 `skill_credential_bindings.credential_id`가 ON DELETE **RESTRICT**
- [ ] `models/skill.py`의 12개 신규 컬럼 + `AgentSkillLink.config` 컬럼이 추가됨
- [ ] `app/marketplace/`에 11개 모듈 파일이 존재 (빈 파일이라도 OK)
- [ ] `schemas.py`의 모든 클래스가 Spec §10.8과 필드명/타입 일치
- [ ] `__init__.py`는 비어 있음 (barrel export 금지)
- [ ] `routers/marketplace.py`의 모든 mutation에 `Depends(verify_csrf)` 적용
- [ ] admin 라우터에 `Depends(require_super_user)` 적용

본 문서와 ADR-017의 결정이 충돌하면 ADR-017이 우선한다. Spec과 충돌하면 Spec이 우선한다 (소스 원본).
