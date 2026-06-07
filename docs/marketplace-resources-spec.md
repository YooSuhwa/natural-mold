# natural-mold Marketplace Resources Technical Spec

> 작성일: 2026-05-18
> 버전: v0.1 (historical implementation spec)
> 관련 문서: `docs/marketplace-resources-prd.md` v0.3, ADR-007/009/013/016/017/018
> 구현 범위: Phase 1 — Skill marketplace foundation + 신규 credential definitions + selected-skill runtime mount + credential env injection + k-skill built-in importer
> 상태: 2026-06-07 현재 Phase 1의 핵심 backend/runtime/frontend 구현은 완료되어 있다.
> 이 문서의 line number와 "현재 코드 상태" 표현은 2026-05-18 baseline 기준이다.
> 최신 상태는 `docs/marketplace-resources-prd.md`의 "2026-06-07 Current Implementation Status"와
> `docs/ARCHITECTURE.md`를 우선한다.

## 0. Design Posture

이 spec은 PRD v0.2의 결정과 2026-05-18 코드 심층 분석을 기반으로 구현 단위를 명확히 한 historical execution spec이다. 핵심 순서:

```
데이터 모델 (m40-m43)
  → 읽기 catalog (slice A)
  → skill install (slice B)
  → skill publish + secret scan (slice C)
  → credential requirements / binding (slice D)
  → runtime selected-skill mount + credential injection + redaction (slice E)
  → k-skill importer (slice F)
  → MCP/Agent marketplace (slice G — Phase 2/3, 본 spec 범위 밖)
```

### 0.1 사용자 결정 확정 사항

| 항목 | 결정 |
|------|------|
| SPEC 범위 | Phase 1 전체를 본 문서에 (한 SPEC) |
| Alembic 분할 | 슬라이스별 여러 마이그레이션 (m40-m43+) |
| Agent-Skill override | Option A — `agent_skills.config` JSON 필드 |
| Runtime mount 방식 | Option A — per-thread `copytree`로 데이터 격리 |
| k-skill source | GitHub `NomaDamas/k-skill` git clone |
| public publish 정책 | published vs listed 분리 (super_user가 `is_listed` 토글) |

### 0.2 Reject된 대안

| Approach | 거절 이유 |
|----------|-----------|
| 기존 `skills`에 `is_builtin`, `visibility`만 추가 | version/update/install 이력이 흐려지고 user-owned와 catalog가 섞임 |
| Marketplace item을 직접 runtime에서 실행 | upstream/owner 변경이 사용자 실행에 즉시 영향, credential binding 복잡 |
| k-skill을 git submodule로 직접 참조 | runtime이 upstream layout에 강결합 |
| 새 skill runner 도입 | 이미 `execute_in_skill` subprocess runner가 동작 중 — 그 위에 보안 빈 구멍 메우기로 충분 |
| symlink 기반 mount | 쓰기가 원본으로 흐를 위험 |
| 단일 큰 m40 마이그레이션 | rollback/review 부담. 슬라이스별 마이그레이션이 안전 |

## 1. 2026-05-18 baseline 코드 상태 (당시 검증된 사실)

### 1.1 Installed Resource Tables

| Domain | Table | Ownership |
|--------|-------|-----------|
| Agent | `agents` | `Agent.user_id` (FK CASCADE) |
| MCP | `mcp_servers`, `mcp_tools` | `McpServer.user_id`, M26에 `is_system`/`health_status` 추가 |
| Skill | `skills`, `agent_skills` | `Skill.user_id` (NOT NULL). `AgentSkillLink`는 (agent_id, skill_id) PK만 — **`config` 필드 없음** |
| Credential | `credentials` | `Credential.user_id`, `is_system` + CHECK(`is_system=false OR user_id IS NULL`) |

### 1.2 Skill Runtime (현재 코드 상태)

`backend/app/agent_runtime/executor.py`:

- line 19-20: `from deepagents import create_deep_agent` + `from deepagents.backends import FilesystemBackend`
- line 113-195: `_create_skill_execute_tool(output_dir, thread_id)` — `execute_in_skill` 도구 정의 (StructuredTool)
- line 126: `resolved = (_DATA_DIR / skill_directory.strip("/")).resolve()` — broad 경로 검증
- line 131: `if not args or args[0] != "python": return "Error: only python commands are allowed."`
- line 144-150: env dict는 `PATH`, `PYTHONPATH`, `HOME`, `SKILL_OUTPUT_DIR`, `OUTPUTS_DIR`만 (**credential 미주입**)
- line 160: 30초 timeout
- line 213-225: `create_deep_agent(model, tools, system_prompt, middleware, interrupt_on=None, checkpointer, store, backend, skills, memory, name)`
- line 544: `backend = FilesystemBackend(root_dir=str(_DATA_DIR), virtual_mode=True)`
- line 546-571: agent에 skill이 하나라도 있으면 `skills=["/skills/"]`로 broad mount

`backend/app/skills/runtime.py:build_skills_for_agent`: `AgentSkillLink` → `to_runtime_dict()` 리스트로만 변환. `to_runtime_dict()`는 `{id, name, slug, kind, storage_path, description}`만 반환 (body 없음).

`backend/app/skills/prompt.py:build_skills_prompt`: `## Available Skills` 텍스트로 LLM에게 read_file 지시. 본문은 LLM이 직접 `/skills/<slug>/SKILL.md`를 읽음.

### 1.3 Credential System (재사용 가능)

- `app/security/cipher.py`: Cipher V2 (HKDF-SHA256 + AES-256-GCM, info=`moldy-encryption-v1`)
- `app/credentials/definitions/`: 13개 정의 등록 (실측 2026-05-18) (k-skill용은 없음)
- `app/credentials/interpolation.py:resolve_deep`: `={{ $credentials.x }}` 보간 (MCP에서 사용)
- `app/credentials/external_secrets.py`: Vault/ENV 동적 resolver (feature flag)
- `credentials.field_keys` JSON (ADR-007): list API N+1 회피
- `credentials.is_system` + CHECK constraint
- ENV → system credential bootstrap (`seed/bootstrap_from_env.py`)

### 1.4 Auth (재사용)

- `app/dependencies.py:get_current_user, require_super_user, verify_csrf`
- JWT HS256 + HttpOnly Cookie + refresh rotation + CSRF double-submit (ADR-016)
- Mock user 흔적 제거 완료 (m36 + `migrate_mock_to_real_user.py`)

### 1.5 Schema gap (구현 대상)

- `Skill`에 `is_system`, `source_kind`, `source_marketplace_item_id`, `source_marketplace_version_id`, `source_commit`, `credential_requirements`, `execution_profile`, `origin_kind`, `origin_user_id`, `origin_marketplace_item_id`, `origin_marketplace_version_id`, `is_dirty` 컬럼 없음
- `AgentSkillLink.config` 필드 없음
- `packager.py`에 secret scan 없음
- `app/marketplace/` 모듈 없음
- `app/scripts/sync_k_skill.py` 없음
- k-skill 관련 credential definition(`srt_account` 등) 없음

## 2. 설계 원칙

1. Marketplace item/version은 배포 원본이다.
2. Installed resource는 사용자 계정의 실행 copy다.
3. Published version은 immutable이다.
4. Credential value는 marketplace payload에 포함하지 않는다.
5. Credential requirement와 credential binding을 분리한다.
6. Built-in k-skill은 system marketplace item으로 취급한다.
7. Upstream repository는 read-only source다.
8. 설치/업데이트는 명시적 사용자 동작이다.
9. Runtime에 노출되는 skill 디렉토리는 agent에 선택된 것만 포함한다.
10. Credential은 `execute_in_skill` subprocess env에만 주입되고 log/SSE/tool result에는 redact된다.

### 2.1 Decision Log

| ID | Decision | Consequence |
|----|----------|-------------|
| D1 | `marketplace_versions`는 immutable | update 비교/audit 단순, 메타 수정은 item-level만 |
| D2 | installed resource는 반드시 `user_id`를 가진다 | 기존 ownership check 유지, system item을 agent에 직접 연결 안 함 |
| D3 | Phase 1 credential override는 `agent_skills.config` JSON (Option A) | 작은 마이그레이션, 현재 link model과 정합 |
| D4 | k-skill credential mapping은 curated map이 source of truth | regex는 review signal로만 |
| D5 | broad `/skills/` mount는 per-thread copytree로 교체 (Option A) | 데이터 격리 완전, retention 정책 필요 |
| D6 | required credential 누락 install은 `needs_setup` 허용 | 사용자가 catalog 먼저 가져온 뒤 credential 나중 연결. Runtime에서 fail-fast |
| D7 | restricted ACL 회수는 새 install만 막음 | 이미 설치된 copy는 사용자 소유로 유지 |
| D8 | installed resource API는 `origin_summary`와 `publication_summary` 포함 | `/skills`, `/mcp-servers`, agent dashboard 일관된 표시 |
| D9 | public publish는 `is_listed=False`로 시작, super_user만 토글 | 카탈로그 노출 게이트 |
| D10 | secret_scan은 publish + import 양방향 | packager 자체는 추가 검증 없이 호출자가 wrap |
| D11 | runtime root는 per-thread, 대화 종료 시 cleanup | LangGraph thread lifecycle과 결합 |
| D12 | k-skill importer는 super_user CLI 전용, web UI 노출 안 함 | 운영자만 sync trigger |

### 2.2 Scope Boundaries

**Phase 1 (본 spec) 포함**:
- marketplace tables (item/version/installation/acl/publication_links/credential_bindings)
- skills 컬럼 확장 + agent_skills.config 추가
- skill catalog list/detail API
- skill install/update API
- skill publish API + secret scan
- skill credential requirements + binding API
- selected-skill runtime mount + credential env injection + redaction
- k-skill built-in importer (CLI)
- 신규 credential definitions (srt_account 등 8개)
- Marketplace UI (skill 한정)

**Phase 1 제외 (Phase 2/3로)**:
- MCP/Agent marketplace 실제 install (스키마만 준비)
- payment/ranking/review
- auto-merge of dirty installed resources
- organization/team ACL
- full script sandbox (현재 allowlist + 30초 timeout 유지)

## 3. Data Model

### 3.1 마이그레이션 분할

| ID | 파일 | 내용 |
|----|------|------|
| m40 | `m40_marketplace_tables.py` | `marketplace_items`, `marketplace_item_acl`, `marketplace_versions`, `marketplace_installations`, `marketplace_publication_links` |
| m41 | `m41_skills_marketplace_columns.py` | `skills`에 12개 컬럼 추가 + backfill |
| m42 | `m42_agent_skills_config.py` | `agent_skills.config` JSON 컬럼 추가 |
| m43 | `m43_skill_credential_bindings.py` | `skill_credential_bindings` 테이블 |

각 마이그레이션은 독립 rollback 가능해야 한다. 단, m41은 m40에 의존(FK), m43은 m41에 의존(skills 컬럼 사용).

### 3.2 `marketplace_items` (m40)

```sql
CREATE TABLE marketplace_items (
  id UUID PRIMARY KEY,
  resource_type VARCHAR(20) NOT NULL,
  owner_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
  is_system BOOLEAN NOT NULL DEFAULT FALSE,
  is_listed BOOLEAN NOT NULL DEFAULT FALSE,

  name VARCHAR(200) NOT NULL,
  slug VARCHAR(220) NOT NULL,
  description TEXT NULL,
  icon_url TEXT NULL,

  visibility VARCHAR(20) NOT NULL DEFAULT 'private',
  status VARCHAR(20) NOT NULL DEFAULT 'draft',
  moderation_status VARCHAR(20) NOT NULL DEFAULT 'approved',

  source_kind VARCHAR(40) NULL,
  source_url TEXT NULL,
  source_external_id VARCHAR(240) NULL,

  latest_version_id UUID NULL,
  tags JSON NULL,
  categories JSON NULL,
  locale VARCHAR(20) NULL,
  metadata JSON NULL,

  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  published_at TIMESTAMP NULL,

  CONSTRAINT ck_marketplace_resource_type CHECK (resource_type IN ('agent', 'mcp', 'skill')),
  CONSTRAINT ck_marketplace_visibility CHECK (visibility IN ('private', 'restricted', 'public', 'unlisted', 'system')),
  CONSTRAINT ck_marketplace_status CHECK (status IN ('draft', 'published', 'deprecated', 'disabled')),
  CONSTRAINT ck_marketplace_system_owner CHECK ((is_system = false) OR (owner_user_id IS NULL))
);

CREATE UNIQUE INDEX uq_marketplace_items_system_slug
  ON marketplace_items(resource_type, slug) WHERE is_system = true;
CREATE UNIQUE INDEX uq_marketplace_items_owner_slug
  ON marketplace_items(owner_user_id, resource_type, slug) WHERE owner_user_id IS NOT NULL;
CREATE INDEX ix_marketplace_items_listed ON marketplace_items(is_listed, visibility, status);
```

- `source_kind`: `user`, `k-skill`, `import`, `system_seed`
- `source_external_id`: k-skill의 경우 upstream skill name
- `is_listed`: super_user만 토글, 기본 False
- `latest_version_id` FK는 m40 내에서 versions 테이블 생성 후 ALTER로 추가 (circular FK 회피, §3.8 참조)

### 3.3 `marketplace_item_acl` (m40)

```sql
CREATE TABLE marketplace_item_acl (
  item_id UUID NOT NULL REFERENCES marketplace_items(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  permission VARCHAR(20) NOT NULL DEFAULT 'install',
  created_at TIMESTAMP NOT NULL,
  PRIMARY KEY (item_id, user_id),
  CONSTRAINT ck_marketplace_acl_permission CHECK (permission IN ('view', 'install', 'manage'))
);
```

### 3.4 `marketplace_versions` (m40)

```sql
CREATE TABLE marketplace_versions (
  id UUID PRIMARY KEY,
  item_id UUID NOT NULL REFERENCES marketplace_items(id) ON DELETE CASCADE,
  version_label VARCHAR(80) NOT NULL,
  version_number INTEGER NOT NULL,

  resource_type VARCHAR(20) NOT NULL,
  payload_kind VARCHAR(40) NOT NULL,
  payload JSON NOT NULL,
  storage_path VARCHAR(500) NULL,
  content_hash VARCHAR(64) NOT NULL,
  size_bytes INTEGER NOT NULL DEFAULT 0,

  credential_requirements JSON NULL,
  dependency_requirements JSON NULL,
  execution_profile JSON NULL,
  release_notes TEXT NULL,

  source_commit VARCHAR(80) NULL,
  source_ref VARCHAR(120) NULL,
  source_path TEXT NULL,

  created_by UUID NULL REFERENCES users(id) ON DELETE SET NULL,
  created_at TIMESTAMP NOT NULL,

  CONSTRAINT ck_marketplace_version_resource_type CHECK (resource_type IN ('agent', 'mcp', 'skill')),
  CONSTRAINT ck_marketplace_payload_kind CHECK (payload_kind IN ('skill_package', 'agent_spec', 'mcp_template'))
);

CREATE UNIQUE INDEX uq_marketplace_versions_item_number ON marketplace_versions(item_id, version_number);
CREATE INDEX ix_marketplace_versions_content_hash ON marketplace_versions(content_hash);
```

Version immutability:

- `payload`, `storage_path`, `content_hash`에 대한 update endpoint 없음
- 메타 typo 수정은 새 version 또는 item-level metadata 업데이트로만

### 3.5 `marketplace_installations` (m40)

```sql
CREATE TABLE marketplace_installations (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  item_id UUID NOT NULL REFERENCES marketplace_items(id) ON DELETE CASCADE,
  version_id UUID NOT NULL REFERENCES marketplace_versions(id) ON DELETE RESTRICT,
  resource_type VARCHAR(20) NOT NULL,

  installed_agent_id UUID NULL REFERENCES agents(id) ON DELETE CASCADE,
  installed_mcp_server_id UUID NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
  installed_skill_id UUID NULL REFERENCES skills(id) ON DELETE CASCADE,

  install_status VARCHAR(30) NOT NULL DEFAULT 'active',
  is_dirty BOOLEAN NOT NULL DEFAULT FALSE,
  installed_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,

  CONSTRAINT ck_marketplace_install_resource_target CHECK (
    (resource_type = 'agent' AND installed_agent_id IS NOT NULL AND installed_mcp_server_id IS NULL AND installed_skill_id IS NULL)
    OR
    (resource_type = 'mcp' AND installed_agent_id IS NULL AND installed_mcp_server_id IS NOT NULL AND installed_skill_id IS NULL)
    OR
    (resource_type = 'skill' AND installed_agent_id IS NULL AND installed_mcp_server_id IS NULL AND installed_skill_id IS NOT NULL)
  ),
  CONSTRAINT ck_marketplace_install_status CHECK (install_status IN ('active', 'needs_setup', 'disabled', 'uninstalled'))
);

CREATE INDEX ix_marketplace_install_user_item ON marketplace_installations(user_id, item_id);
CREATE INDEX ix_marketplace_install_user_resource ON marketplace_installations(user_id, resource_type);
```

### 3.6 `marketplace_publication_links` (m40)

```sql
CREATE TABLE marketplace_publication_links (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  item_id UUID NOT NULL REFERENCES marketplace_items(id) ON DELETE CASCADE,
  resource_type VARCHAR(20) NOT NULL,

  source_agent_id UUID NULL REFERENCES agents(id) ON DELETE CASCADE,
  source_mcp_server_id UUID NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
  source_skill_id UUID NULL REFERENCES skills(id) ON DELETE CASCADE,

  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,

  CONSTRAINT ck_pub_link_resource_type CHECK (resource_type IN ('agent', 'mcp', 'skill')),
  CONSTRAINT ck_pub_link_target CHECK (
    (resource_type = 'agent' AND source_agent_id IS NOT NULL AND source_mcp_server_id IS NULL AND source_skill_id IS NULL)
    OR
    (resource_type = 'mcp' AND source_agent_id IS NULL AND source_mcp_server_id IS NOT NULL AND source_skill_id IS NULL)
    OR
    (resource_type = 'skill' AND source_agent_id IS NULL AND source_mcp_server_id IS NULL AND source_skill_id IS NOT NULL)
  )
);

CREATE UNIQUE INDEX uq_pub_link_item ON marketplace_publication_links(item_id);
CREATE INDEX ix_pub_link_resource ON marketplace_publication_links(user_id, resource_type);
```

### 3.7 `skills` 컬럼 확장 (m41)

```sql
ALTER TABLE skills ADD COLUMN is_system BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE skills ADD COLUMN source_kind VARCHAR(40) NULL;
ALTER TABLE skills ADD COLUMN source_marketplace_item_id UUID NULL REFERENCES marketplace_items(id) ON DELETE SET NULL;
ALTER TABLE skills ADD COLUMN source_marketplace_version_id UUID NULL REFERENCES marketplace_versions(id) ON DELETE SET NULL;
ALTER TABLE skills ADD COLUMN source_commit VARCHAR(80) NULL;
ALTER TABLE skills ADD COLUMN credential_requirements JSON NULL;
ALTER TABLE skills ADD COLUMN execution_profile JSON NULL;
ALTER TABLE skills ADD COLUMN origin_kind VARCHAR(40) NOT NULL DEFAULT 'created_by_me';
ALTER TABLE skills ADD COLUMN origin_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE skills ADD COLUMN origin_marketplace_item_id UUID NULL REFERENCES marketplace_items(id) ON DELETE SET NULL;
ALTER TABLE skills ADD COLUMN origin_marketplace_version_id UUID NULL REFERENCES marketplace_versions(id) ON DELETE SET NULL;
ALTER TABLE skills ADD COLUMN is_dirty BOOLEAN NOT NULL DEFAULT FALSE;
```

Backfill:

- 모든 기존 행: `source_kind='user'`, `is_system=FALSE`
- text skill: `origin_kind='created_by_me'`
- package skill: `origin_kind='imported_by_me'`

System credential과 달리, 시스템에서 가져온 skill도 user_id는 설치자로 둔다(D2). `is_system=TRUE`는 system marketplace item에서 시드된 skill 자체에만 쓰이며 일반 사용 흐름에서는 거의 없다.

### 3.8 `agent_skills.config` (m42)

```sql
ALTER TABLE agent_skills ADD COLUMN config JSON NULL;
```

저장 예:

```json
{
  "credential_bindings": {
    "srt_account": "11111111-1111-1111-1111-111111111111"
  }
}
```

런타임은 이 override를 `skill_credential_bindings` 기본값보다 우선한다.

### 3.9 `skill_credential_bindings` (m43)

```sql
CREATE TABLE skill_credential_bindings (
  id UUID PRIMARY KEY,
  skill_id UUID NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  requirement_key VARCHAR(120) NOT NULL,
  credential_id UUID NOT NULL REFERENCES credentials(id) ON DELETE RESTRICT,
  scope VARCHAR(20) NOT NULL DEFAULT 'skill',
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,

  CONSTRAINT ck_skill_credential_binding_scope CHECK (scope IN ('skill', 'agent_skill')),
  UNIQUE (skill_id, user_id, requirement_key, scope)
);
```

`scope='agent_skill'`는 정규화 마이그레이션 시점을 위한 예약. Phase 1에서는 `scope='skill'`만 사용하고 agent-skill override는 `agent_skills.config`로 처리.

### 3.10 Referential integrity 처리

`marketplace_items.latest_version_id`가 `marketplace_versions.id`를 참조하는 circular FK는 m40 안에서 처리:

1. `marketplace_items`를 FK 없이 생성
2. `marketplace_versions` 생성
3. `ALTER TABLE marketplace_items ADD CONSTRAINT fk_latest_version FOREIGN KEY (latest_version_id) REFERENCES marketplace_versions(id) ON DELETE SET NULL`

### 3.11 Lifecycle state model

Item:

```text
draft → published → deprecated → disabled
  ↓        ↓
disabled  published (metadata changes only)
```

- `published`만 install 가능
- `deprecated`는 `metadata.allow_deprecated_install=true`일 때만 설치 가능 (기본 false)
- `disabled`는 listing/install 모두 차단
- item metadata는 변경 가능, version payload는 immutable

Installation:

```text
active ↔ needs_setup → active
   ↓                ↓
uninstalled    uninstalled
   or             or
disabled       disabled
```

- `needs_setup`: 설치본은 존재하지만 required credential binding 미완성
- `disabled`: runtime 차단, 설치본은 inspection/export용으로 보존
- `uninstalled`: marketplace link 비활성. 실제 resource는 `delete_resource=true`일 때만 삭제

## 4. Credential Definitions

`backend/app/credentials/definitions/` 폴더에 다음 모듈을 신규 추가한다. 기존 `__init__.py`의 자동 등록 패턴(import time)을 따른다.

| 모듈 파일 | definition_key | Fields | Used by |
|----------|----------------|--------|---------|
| `srt_account.py` | `srt_account` | `username`, `password` | `srt-booking` |
| `ktx_account.py` | `ktx_account` | `username`, `password` | `ktx-booking` |
| `foresttrip_account.py` | `foresttrip_account` | `username`, `password` | `foresttrip-vacancy` |
| `kipris_plus_api.py` | `kipris_plus_api` | `api_key` | `korean-patent-search` |
| `dart_api.py` | `dart_api` | `api_key` | `k-dart` |
| `odsay_api.py` | `odsay_api` | `api_key` | `korean-transit-route` |
| `coupang_partners.py` | `coupang_partners` | `access_key`, `secret_key` | `coupang-product-search` (optional) |
| `k_skill_proxy.py` | `k_skill_proxy` | `base_url`, optional `api_key` | hosted proxy skills |

각 모듈은 `CredentialDefinition` 인스턴스를 정의하고 `__init__.py`에서 register한다. 등록 후 자동으로 `field_keys` 캐시(ADR-007)와 호환된다.

Hosted proxy dependency 표기(version metadata):

```json
{
  "kind": "hosted_proxy",
  "default_base_url": "https://k-skill-proxy.nomadamas.org",
  "user_configurable_base_url": true
}
```

## 5. k-skill Importer (Slice F)

### 5.1 Settings

`backend/app/config.py`에 추가:

```python
k_skill_upstream_url: str = "https://github.com/NomaDamas/k-skill.git"
k_skill_upstream_ref: str = "main"
k_skill_sync_dir: str = "./data/upstreams/k-skill"
k_skill_builtin_storage_dir: str = "./data/marketplace/k-skill"
```

### 5.2 CLI

신규 파일 `backend/app/scripts/sync_k_skill.py`:

```bash
uv run python -m app.scripts.sync_k_skill --ref main
uv run python -m app.scripts.sync_k_skill --ref 80303f5 --dry-run
uv run python -m app.scripts.sync_k_skill --ref 80303f5 --only korean-spell-check,srt-booking
```

CLI 옵션:

- `--ref`: commit SHA 또는 branch (기본 `settings.k_skill_upstream_ref`)
- `--dry-run`: 변경/생성 카운트만 출력, DB/파일시스템 변경 없음
- `--only`: 콤마 구분 skill name (디버깅용)
- `--keep-deprecated`: 사라진 upstream skill을 `deprecated` 마킹 대신 유지

### 5.3 Discovery

`scripts/validate-skills.sh` exclusion 미러 (`.git`, `.github`, `.codex`, `.claude`, `.omx`, `.ouroboros`, `.changeset`, `.cursor`, `.vscode`, `.sisyphus`, `.idea`, `docs`, `dist`, `node_modules`, `packages`, `python-packages`, `scripts`, `examples`).

Valid skill 조건:

- 디렉토리가 repo root 바로 아래
- `SKILL.md` 존재
- frontmatter 존재 + `name`, `description` 키 존재
- frontmatter `name`이 디렉토리명과 일치

### 5.4 Packaging

각 skill 디렉토리 처리 순서:

1. 임시 staging 디렉토리에 복사
2. Secret-like 파일 거부 (`secret_scan.py` 참조): `.env`, `*.pem`, `*.key`, `*.p12`, `cookies*`, `token*`, `secrets.env`
3. `.skill` zip 빌드 (top-level `<skill-name>/`)
4. `app.skills.packager.extract_package()`로 검증 (재사용)
5. 추출 결과를 `data/marketplace/k-skill/<skill-name>/<source_commit>/`에 저장

### 5.5 Metadata 추출

Frontmatter에서:

- `name`, `description`, `license`
- `metadata.category`, `metadata.locale`, `metadata.phase`

Computed:

- `content_hash`: 패키지 canonical contents의 SHA-256
- `source_commit`, `source_path`
- `has_scripts`: `scripts/*.py` 존재 여부
- `file_count`, `size_bytes`
- `execution_profile`: §5.7 참조
- `credential_requirements`: §5.6 curated map

### 5.6 Credential Requirement Mapping

신규 파일 `backend/app/marketplace/k_skill_requirements.py`:

```python
K_SKILL_REQUIREMENT_MAP: dict[str, list[dict]] = {
    "srt-booking": [{
        "key": "srt_account",
        "definition_key": "srt_account",
        "required": True,
        "label": "SRT account",
        "description": "SRT 로그인 자격증명",
        "fields": ["username", "password"],
        "env_map": {"username": "KSKILL_SRT_ID", "password": "KSKILL_SRT_PASSWORD"},
        "injection": "env",
        "scope": "user",
    }],
    "ktx-booking": [...],
    "korean-patent-search": [...],
    "k-dart": [...],
    "korean-transit-route": [...],
    "foresttrip-vacancy": [...],
    "coupang-product-search": [...],  # optional=True
    # ... more
}

REGEX_HINTS = [
    re.compile(r"KSKILL_[A-Z_]+"),
    re.compile(r"API[_-]KEY"),
    # ...
]
```

Regex hint는 `detected_env_vars` review 신호로만 출력하고 자동 requirement 생성 안 함.

### 5.7 Execution Profile

```json
{
  "support_level": "ready_python",
  "runners": ["python"],
  "requires_network": true,
  "requires_browser": false,
  "requires_local_app": false,
  "requires_manual_login": false,
  "notes": []
}
```

Support levels: `ready_python`, `proxy_http`, `node_package`, `browser_or_local`, `manual_only`, `disabled`.

### 5.8 Sync Idempotency

- `content_hash` 동일 → 새 version 생성 안 함
- 패키지 hash 동일 + metadata만 변경 → item-level metadata만 update
- 패키지 hash 변경 → `version_number = max + 1`로 신규 version
- 사라진 upstream skill → item `status=deprecated` (`--keep-deprecated`로 회피 가능)
- 한 skill validation 실패는 전체 sync 중단 안 함. 결과 보고서에 실패 목록 포함

### 5.9 First-wave 권장 (super_user가 listed 토글)

| Group | Examples |
|-------|----------|
| Ready/no credential | `korean-spell-check` |
| Hosted proxy | `seoul-density` 류 |
| Required credential clear schema | `srt-booking`, `ktx-booking`, `korean-patent-search`, `k-dart` |

Hold back (unlisted 유지):

- KakaoTalk 자동화 (local app/session)
- 브라우저 로그인 필요 skill
- Node/npm/npx skill (runner 없음)
- 폐지된 upstream skill (`blue-ribbon-nearby` 등)

## 6. Publish Flow (Slice C)

### 6.1 Endpoint

```http
POST /api/marketplace/items/from-skill/{skill_id}
```

Body:

```json
{
  "item_id": "optional-existing-item",
  "visibility": "restricted",
  "name": "Korean Spell Check",
  "description": "한국어 문장 검사",
  "tags": ["korean", "writing"],
  "categories": ["writing"],
  "release_notes": "Initial shared version",
  "credential_requirements": [],
  "acl_user_ids": ["uuid"]
}
```

### 6.2 Server 동작

1. `skill_id` + `current_user.id`로 skill 로드 (ownership)
2. Package 검증:
   - text skill: `SKILL.md` 단일 파일로 패키지화
   - package skill: storage_path 복사
3. **Secret scan** (`backend/app/marketplace/secret_scan.py` 신규)
4. Item 없으면 생성, 있으면 ownership 확인
5. 신규 immutable version 생성 (content_hash 비교)
6. item `latest_version_id` 업데이트
7. restricted면 ACL row 생성
8. `marketplace_publication_links` 갱신
9. Audit log: `marketplace.publish`

### 6.3 Visibility 규칙

- `public` publish: `is_listed=False`로 시작
- `restricted` publish: `acl_user_ids` 최소 1명
- `private` publish: ACL/listing 무관

## 7. Install Flow (Slice B)

### 7.1 Endpoint

```http
POST /api/marketplace/items/{item_id}/install
```

Body:

```json
{
  "version_id": null,
  "name_override": "SRT 예약",
  "credential_bindings": {
    "srt_account": "credential-uuid"
  },
  "install_missing_credentials": "needs_setup",
  "install_mode": "reuse_or_update"
}
```

### 7.2 Server 동작

1. Item 가시성 access check (`can_install_item`)
2. Version resolve (없으면 latest)
3. Credential bindings 검증:
   - `credential.user_id == current_user.id`
   - `credential.definition_key == requirement.definition_key`
   - system credential은 거부 (D2/§8)
4. Installed resource 생성 — 트랜잭션 순서 (§7.3):
   - skill: package extract → 임시 디렉토리
   - mcp/agent: Phase 2/3에서 (Phase 1은 미구현)
5. `marketplace_installations` row 생성
6. `skill_credential_bindings` row 생성 (있는 경우)
7. Required 미해결 시 `install_status='needs_setup'`
8. Audit log: `marketplace.install`

### 7.3 Transaction + Filesystem 처리

1. Access/binding validation
2. Temp 디렉토리에 package extract (`data/skills/.staging/<install_id>/`)
3. DB 트랜잭션 내에서 `skills` + `marketplace_installations` row 생성
4. Temp 디렉토리를 최종 경로(`data/skills/<skill_id>/`)로 move
5. DB commit

실패 처리:

- DB commit 실패 → 디렉토리 best-effort 제거 + 사용자에게는 generic error (경로/credential 미노출)
- Filesystem move 실패 → DB rollback
- Cleanup 실패 → 로그만 (사용자 경로/credential 미노출)

### 7.4 Install Modes

| Mode | Behavior |
|------|----------|
| `reuse_or_update` (default) | 이미 설치돼 있으면 그 installation 반환 + state refresh, 없으면 신규 |
| `new_copy` | 항상 신규 installation |
| `overwrite_existing` | 명시적 요청 시에만 기존 덮어쓰기 |

### 7.5 Installed Skill row 채우기

```python
skill.user_id = current_user.id
skill.kind = "package"  # k-skill은 항상 package
skill.source_kind = item.source_kind  # "k-skill" or "user" or "import"
skill.source_marketplace_item_id = item.id
skill.source_marketplace_version_id = version.id
skill.source_commit = version.source_commit
skill.credential_requirements = version.credential_requirements
skill.execution_profile = version.execution_profile
skill.origin_kind = _derive_origin(item, current_user)
skill.origin_marketplace_item_id = item.id
skill.origin_marketplace_version_id = version.id
```

`_derive_origin()` 매핑:

| item state | origin_kind |
|------------|-------------|
| `is_system=True` + `source_kind='k-skill'` | `built_in_k_skill` |
| `is_system=True` + `source_kind='system_seed'` | `system_seed` |
| `visibility='restricted'`, owner != current_user | `shared_with_me` (origin_user_id = owner) |
| `visibility='public'`, owner != current_user | `community` |
| owner == current_user (재설치) | `imported_by_me` |

## 8. Runtime Credential Injection (Slice E)

### 8.1 현재 코드 빈 구멍

`executor.py:113-195`의 `_create_skill_execute_tool` env dict:

```python
env = {
    "PATH": "/usr/bin:/usr/local/bin",
    "PYTHONPATH": str(resolved),
    "HOME": str(resolved),
    "SKILL_OUTPUT_DIR": out,
    "OUTPUTS_DIR": out,
}
```

Credential 없음.

### 8.2 Required change

Agent runtime 빌드 시점에 다음 descriptor를 생성한다 (`build_skills_for_agent` 확장):

```python
@dataclass
class SkillRuntimeDescriptor:
    id: UUID
    slug: str
    storage_path: Path  # per-thread runtime root 하위
    credential_bindings: dict[str, ResolvedCredential]

@dataclass
class ResolvedCredential:
    credential_id: UUID
    definition_key: str
    env_map: dict[str, str]
    decrypted: dict[str, Any]  # in-memory only, never serialized
```

`_create_skill_execute_tool`을 `_create_skill_execute_tool(output_dir, thread_id, skill_descriptors)`로 시그니처 변경.

`execute_in_skill` 함수 안에서:

1. `skill_directory` 인자에서 slug 추출
2. `skill_descriptors`에 없는 slug → `"Error: skill not attached to this agent"`
3. 해당 descriptor의 `storage_path`로 resolve
4. env dict 빌드:

```python
env = {
    "PATH": "/usr/bin:/usr/local/bin",
    "PYTHONPATH": str(resolved),
    "HOME": str(resolved),
    "SKILL_OUTPUT_DIR": out,
    "OUTPUTS_DIR": out,
}
for req_key, resolved_cred in descriptor.credential_bindings.items():
    for field, env_name in resolved_cred.env_map.items():
        env[env_name] = resolved_cred.decrypted[field]
```

5. subprocess 실행 (기존 그대로)

### 8.3 Missing credential 처리

Agent 실행 시작 단계에서 `marketplace_installations.install_status == 'needs_setup'`인 attached skill을 확인하고, required credential이 누락된 skill이 있으면 에러로 abort:

```text
Error: skill 'srt-booking' requires credential 'srt_account'. Connect it in Skill settings.
```

`marketplace_credential_required` 에러 코드로 표현하고, frontend는 설정 CTA를 표시.

### 8.4 Override 우선순위

1. `agent_skills.config.credential_bindings.<key>`
2. `skill_credential_bindings`에서 `(skill_id, user_id, key, scope='skill')`
3. 없으면 `needs_setup`

### 8.5 Redaction Contract

신규 `backend/app/marketplace/redaction.py`:

```python
SENSITIVE_KEY_PATTERN = re.compile(r"(password|api_key|secret|token|access_key|refresh_token)", re.I)

def redact_credential_values(text: str, mapped_env_vars: dict[str, str]) -> str:
    for env_name, value in mapped_env_vars.items():
        if value and len(value) > 4:
            text = text.replace(value, f"<redacted:{env_name}>")
    return text

def redact_keys(payload: dict | list) -> dict | list:
    # 깊이 우선 순회, SENSITIVE_KEY_PATTERN 매칭 키의 value를 "<redacted>"로 교체
    ...
```

호출 지점:

- `_create_skill_execute_tool` 반환 텍스트 가공
- `streaming.py`의 tool_call_result 페이로드
- exception detail 변환
- 모든 raw log statement

## 9. Skill Mounting Fix (Slice E, Option A)

### 9.1 현재 문제

`executor.py:546-571`은 `skills=["/skills/"]`로 broad mount. 다른 사용자 skill은 ownership으로 막혀 있지만 같은 사용자의 미선택 skill은 `read_file('/skills/<other>/...')` 가능.

### 9.2 Required behavior

Runtime root: `data/runtime/<thread_id>/skills/<slug>/`

빌드 단계 (`executor.py:build_agent` 호출 전):

```python
runtime_skills_root = _DATA_DIR / "runtime" / cfg.thread_id / "skills"
runtime_skills_root.mkdir(parents=True, exist_ok=True)
for descriptor in cfg.skill_descriptors:
    target = runtime_skills_root / descriptor.slug
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(descriptor.original_storage_path, target, symlinks=False)
    descriptor.storage_path = target

skills_sources = [f"/runtime/{cfg.thread_id}/skills/"]
backend = FilesystemBackend(root_dir=str(_DATA_DIR), virtual_mode=True)
```

`_create_skill_execute_tool`의 경로 검증:

```python
runtime_root = _DATA_DIR / "runtime" / thread_id / "skills"
resolved = (runtime_root / Path(skill_directory).name).resolve()
if not resolved.is_relative_to(runtime_root.resolve()):
    return "Error: invalid skill directory"
```

### 9.3 Cleanup 정책

- Conversation 종료 시 `data/runtime/<thread_id>/` 제거
- 서버 시작 시 stale runtime root 가비지 컬렉션 (`startup` lifespan에서 1시간 이상 오래된 thread root 삭제)
- 강제 종료/crash 후 잔여 디렉토리는 retention job이 정리

### 9.4 Streaming/SSE 호환

`thread_id`는 LangGraph checkpoint 키와 동일. SSE resume(ADR-011) 시 같은 root 재사용. branch 분기는 별도 root는 만들지 않고 같은 thread root 공유.

## 10. API Surface

신규 라우터 `backend/app/routers/marketplace.py`. 모든 엔드포인트는 `Depends(get_current_user)`, 상태 변경은 `Depends(verify_csrf)` (ADR-016).

### 10.1 Catalog

```http
GET /api/marketplace/items
```

Query: `resource_type`, `q`, `visibility`, `category`, `locale`, `credential_status`, `installed`, `install_state`, `source`, `support_level`, `source_kind`, `is_listed`

Default filter (super_user 아닌 경우): `is_listed=True OR visibility=system OR owner=current_user OR ACL`.

### 10.2 Detail

```http
GET /api/marketplace/items/{item_id}
GET /api/marketplace/items/{item_id}/versions
GET /api/marketplace/versions/{version_id}
```

### 10.3 Install / Update / Delete

```http
POST   /api/marketplace/items/{item_id}/install
POST   /api/marketplace/installations/{installation_id}/update
DELETE /api/marketplace/installations/{installation_id}
```

Update body:

```json
{ "strategy": "overwrite" }
```

전략: `overwrite`, `install_new_copy`, `keep_current`. `is_dirty=True`면 명시 strategy 요구.

### 10.4 Publish / Manage

```http
POST   /api/marketplace/items/from-skill/{skill_id}
POST   /api/marketplace/items/{item_id}/versions/from-skill/{skill_id}
PATCH  /api/marketplace/items/{item_id}
POST   /api/marketplace/items/{item_id}/acl
DELETE /api/marketplace/items/{item_id}/acl/{user_id}
POST   /api/marketplace/items/{item_id}/disable
GET    /api/marketplace/publication-status
```

### 10.5 Admin (super_user)

```http
POST  /api/marketplace/admin/items/{item_id}/listed   # is_listed 토글
POST  /api/marketplace/admin/items/{item_id}/disable
POST  /api/marketplace/admin/k-skill/sync             # CLI 결과 status 조회 (실행은 CLI)
GET   /api/marketplace/admin/moderation               # is_listed=False && visibility='public' 목록
```

모두 `Depends(require_super_user)` + CSRF.

### 10.6 Skill Credential Bindings

```http
GET    /api/skills/{skill_id}/credential-requirements
GET    /api/skills/{skill_id}/credential-bindings
PUT    /api/skills/{skill_id}/credential-bindings/{requirement_key}
DELETE /api/skills/{skill_id}/credential-bindings/{requirement_key}
```

PUT body:

```json
{ "credential_id": "uuid" }
```

검증:

- skill ownership (`Skill.user_id == current_user.id`)
- requirement key 존재
- credential ownership + `definition_key` 일치
- system credential 거부

### 10.7 Error Codes (구조화)

기존 응답 패턴 `{ "detail": { "code": "...", "message": "..." } }` 사용.

| Code | HTTP | When |
|------|------|------|
| `marketplace_item_not_found` | 404 | Item 없음 또는 view 권한 없음 |
| `marketplace_version_not_found` | 404 | Version 없음 |
| `marketplace_install_forbidden` | 404 | Install 권한 없음 (enumeration 방지) |
| `marketplace_manage_forbidden` | 403 | view 가능하지만 manage 불가 |
| `marketplace_item_disabled` | 409 | Disabled item install 시도 |
| `marketplace_invalid_visibility` | 400 | Visibility 전이 invalid |
| `marketplace_acl_required` | 400 | restricted에 ACL 없음 |
| `marketplace_invalid_package` | 400 | Package extract/validate 실패 |
| `marketplace_secret_detected` | 400 | secret_scan 거부 |
| `marketplace_credential_required` | 409 | Required credential 누락 (install/run) |
| `marketplace_credential_mismatch` | 400 | definition_key 불일치 |
| `marketplace_dirty_installation` | 409 | Dirty 상태에서 strategy 없는 update |

### 10.8 Pydantic 스키마

`backend/app/marketplace/schemas.py`:

```python
class MarketplaceVersionSummary(BaseModel):
    id: UUID
    version_label: str
    version_number: int
    content_hash: str
    source_commit: str | None = None
    created_at: datetime

class CredentialRequirementOut(BaseModel):
    key: str
    definition_key: str
    required: bool
    label: str
    description: str | None = None
    fields: list[str]
    injection: Literal["env", "config"]
    scope: Literal["user", "system_dependency", "manual"]

class CredentialSummaryOut(BaseModel):
    status: Literal["none", "optional", "required", "hosted_proxy", "manual_login"]
    required_count: int = 0
    optional_count: int = 0
    missing_required_count: int = 0

class ResourceOriginSummaryOut(BaseModel):
    kind: Literal["created_by_me", "imported_by_me", "built_in_k_skill", "shared_with_me", "community", "system_seed"]
    label: str
    source_name: str | None = None
    source_user_id: UUID | None = None
    marketplace_item_id: UUID | None = None
    marketplace_version_id: UUID | None = None

class ResourcePublicationSummaryOut(BaseModel):
    state: Literal["not_published", "draft", "published_private", "published_restricted", "published_public_listed", "published_public_unlisted", "published_unlisted", "disabled"]
    item_id: UUID | None = None
    visibility: Literal["private", "restricted", "public", "unlisted", "system"] | None = None
    status: Literal["draft", "published", "deprecated", "disabled"] | None = None
    is_listed: bool = False
    latest_version_id: UUID | None = None
    version_number: int | None = None
    shared_user_count: int = 0

class MarketplaceInstallationSummary(BaseModel):
    installed: bool
    installation_id: UUID | None = None
    installed_resource_id: UUID | None = None
    status: Literal["active", "needs_setup", "disabled", "uninstalled"] | None = None
    update_available: bool = False
    dirty: bool = False

class MarketplaceItemOut(BaseModel):
    id: UUID
    resource_type: Literal["agent", "mcp", "skill"]
    name: str
    slug: str
    description: str | None
    visibility: str
    status: str
    is_system: bool
    is_listed: bool
    latest_version: MarketplaceVersionSummary | None
    credential_summary: CredentialSummaryOut
    execution_profile: dict[str, Any] | None = None
    origin_summary: ResourceOriginSummaryOut | None = None
    publication_summary: ResourcePublicationSummaryOut
    installation: MarketplaceInstallationSummary

class InstallMarketplaceItemIn(BaseModel):
    version_id: UUID | None = None
    name_override: str | None = None
    credential_bindings: dict[str, UUID] = Field(default_factory=dict)
    install_missing_credentials: Literal["reject", "needs_setup"] = "needs_setup"
    install_mode: Literal["reuse_or_update", "new_copy", "overwrite_existing"] = "reuse_or_update"

class UpdateMarketplaceInstallationIn(BaseModel):
    strategy: Literal["overwrite", "install_new_copy", "keep_current"]

class PublishSkillIn(BaseModel):
    item_id: UUID | None = None
    visibility: Literal["private", "restricted", "public", "unlisted"]
    name: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    release_notes: str | None = None
    credential_requirements: list[CredentialRequirementIn] = Field(default_factory=list)
    acl_user_ids: list[UUID] = Field(default_factory=list)
```

Skill/MCP/Agent 기존 detail 응답에도 `origin_summary`, `publication_summary` 임베드.

### 10.9 Dirty State helper

`backend/app/marketplace/origin_service.py:mark_installation_dirty`:

```python
async def mark_installation_dirty(db, *, resource_type: str, resource_id: UUID) -> None:
    stmt = (
        update(MarketplaceInstallation)
        .where(MarketplaceInstallation.resource_type == resource_type)
        .where(or_(
            MarketplaceInstallation.installed_agent_id == resource_id,
            MarketplaceInstallation.installed_mcp_server_id == resource_id,
            MarketplaceInstallation.installed_skill_id == resource_id,
        ))
        .values(is_dirty=True, updated_at=utcnow())
    )
    await db.execute(stmt)
    # 동시에 skills.is_dirty도 set
```

호출 지점 (skill content/file 변경):

- `PUT /api/skills/{id}/content`
- `PATCH /api/skills/{id}`
- `PUT|POST|DELETE /api/skills/{id}/files/{path}`

Best-effort: installation row 없어도 fail 안 함.

## 11. Backend Service Modules

신규 폴더 `backend/app/marketplace/`:

```
marketplace/
├── __init__.py
├── access.py             # can_view_item / can_install_item / can_manage_item
├── schemas.py            # Pydantic 모델 (§10.8)
├── service.py            # catalog list/detail
├── install_service.py    # install/update flow
├── publish_service.py    # publish flow
├── origin_service.py     # origin/publication summary 파생
├── secret_scan.py        # secret-like 파일/패턴 검사
├── redaction.py          # redact_credential_values, redact_keys
├── credential_requirements.py  # mapping / validation / env injection plan
└── k_skill_importer.py   # upstream sync (CLI에서 호출)
```

신규 라우터: `backend/app/routers/marketplace.py`.

신규 모델: `backend/app/models/marketplace.py` (단일 파일에 5개 테이블 ORM).

## 12. Access Control (Slice A)

`access.py`:

```python
async def can_view_item(db, item: MarketplaceItem, user: CurrentUser) -> bool
async def can_install_item(db, item: MarketplaceItem, user: CurrentUser) -> bool
async def can_manage_item(db, item: MarketplaceItem, user: CurrentUser) -> bool
```

규칙:

- super_user: 모든 item view/manage 가능. user credential 복호화는 불가
- owner: 자기 item view/install/manage
- public/system: 모든 로그인 사용자 view/install. 카탈로그 검색은 `is_listed=True`만
- unlisted: 직접 id로 view/install 가능, 검색 결과에 없음
- restricted: ACL 필요
- disabled: owner/super_user만 view, 누구도 install 불가
- 비인가 detail/install: 404 (enumeration 방지)

### 12.1 Access Matrix

| Actor | List | Detail | Install | Manage | Disable | Listed toggle |
|-------|------|--------|---------|--------|---------|---------------|
| Owner | own | own | own | own | own | no |
| ACL view | restricted only | yes | no | no | no | no |
| ACL install | restricted only | yes | yes | no | no | no |
| ACL manage | restricted only | yes | yes | metadata/version/ACL except owner removal | no | no |
| Any logged-in | public(listed)/system | public/system/unlisted | public/system/unlisted | no | no | no |
| super_user | all | all | system/public if desired | all | all | yes |

### 12.2 Installed Resource Ownership

Marketplace access never grants direct access to installed resources owned by another user.

- 설치는 항상 현재 사용자 소유 row 생성
- Installation update: `marketplace_installations.user_id == current_user.id`
- Skill content 편집: `skills.user_id == current_user.id`
- Credential binding: `skills.user_id == current_user.id` AND `credentials.user_id == current_user.id`
- Runtime 실행: agent ownership 경로로 skill 로드 (marketplace visibility 거치지 않음)

## 13. Security

### 13.1 Secret Scan

`backend/app/marketplace/secret_scan.py`:

```python
SECRET_FILE_PATTERNS = [
    re.compile(r"^\.env(\..+)?$"),
    re.compile(r"^secrets\.env$"),
    re.compile(r".*\.pem$"),
    re.compile(r".*\.key$"),
    re.compile(r".*\.p12$"),
    re.compile(r"^cookies.*$"),
    re.compile(r"^token.*$"),
]

SECRET_CONTENT_PATTERNS = [
    # OI-4 (M1-S1 베조스): 반드시 word boundary 사용 — 그렇지 않으면 정상
    # docstring/예제의 "sk-example" 같은 placeholder도 차단되어 false-positive
    # 사용자 차단을 유발. 최소 길이를 20으로 올려 placeholder와 실제 키 구분.
    re.compile(rb"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(rb"-----BEGIN (RSA |EC |DSA |OPENSSH |)PRIVATE KEY-----"),
    re.compile(rb"AWS_SECRET_ACCESS_KEY"),
    re.compile(rb"GOOGLE_APPLICATION_CREDENTIALS"),
]

def scan_package(extracted_dir: Path) -> list[SecretFinding]:
    # 파일 트리 순회, 패턴 매칭, 발견 시 finding 목록 반환
    ...

class SecretFinding(BaseModel):
    path: str
    kind: Literal["filename", "content"]
    pattern: str
```

호출 지점:

- `publish_service.py`: publish 시 fail
- `k_skill_importer.py`: import 시 해당 skill만 skip (전체 sync는 계속)
- `routers/skills.py:upload` (`.skill` ZIP 업로드): 신규 import에도 적용 (회귀 가드)

### 13.2 Credential Safety

- API 응답에 decrypted value 절대 미노출
- Runtime env injection은 mapped env var에만 (다른 env에는 미주입)
- Redaction helper로 log/SSE/tool result 통일
- Audit log (신규 audit_events 모듈 또는 기존 logger):

| Action | Actor | Metadata |
|--------|-------|----------|
| `marketplace.publish` | owner/super_user | item_id, version_id, resource_type, visibility |
| `marketplace.install` | installer | item_id, version_id, installed_resource_id |
| `marketplace.update_installation` | installer | installation_id, from_version_id, to_version_id, strategy |
| `marketplace.disable` | super_user/owner | item_id, reason |
| `marketplace.listed_toggle` | super_user | item_id, is_listed |
| `skill_credential.bind` | skill owner | skill_id, requirement_key, credential_id |
| `skill_credential.use` | runtime | skill_id, agent_id, requirement_key (값 미포함) |

### 13.3 System Credential 격리

- System credential은 user skill credential의 단축경로가 아님
- Hosted proxy는 system dependency로 표시, 사용자 binding으로 노출 안 함
- `/api/system-credentials`는 super_user 전용 (기존 ADR-016 유지)

## 14. Testing Plan

### 14.1 Unit

- `access.py`: view/install/manage 규칙 매트릭스 전부
- ACL view/install/manage
- Immutable version 생성 (재시도 시 동일 결과)
- Install creates user-owned skill
- Credential requirement validation
- Binding rejects 다른 사용자 credential
- Binding rejects 잘못된 definition_key
- `secret_scan` rejects `.env`, PEM, sk- 패턴
- k-skill discovery excludes non-skill dirs
- k-skill importer maps requirements (curated map만 통과, regex hint는 review 출력)
- Origin summary derivation (created/imported/built-in/shared/community)
- Publication summary derivation
- Redaction helper: env value, sensitive keys

### 14.2 Integration

- Publish skill → User B 설치 → 실행
- Restricted item invisible to non-ACL user
- Public item invisible from default catalog when `is_listed=False`
- Public item visible after super_user toggles `is_listed=True`
- Built-in install creates `skills` row with source references and `origin_kind='built_in_k_skill'`
- Required credential missing → installation `needs_setup`
- Bound credential injected into subprocess env (mapped env var만)
- Wrong credential not injected
- `/api/skills` includes origin/publication summaries
- Marketplace filters (installed/not installed/needs setup/update available) 정확

### 14.3 Regression

- 기존 `/api/skills` 회귀 통과
- Agent 설정에서 skill 선택 회귀
- Package upload 회귀
- MCP credential flow 회귀
- System credential super_user 보호 회귀
- `not_published` 상태의 기존 리소스 페이지 정상 사용

### 14.4 E2E

1. User A가 package skill 생성
2. User A가 restricted (대상: User B)로 publish
3. User C는 item 안 보임
4. User B가 install
5. User B가 skill을 agent에 연결
6. Chat runtime이 installed skill 본문 사용

Credential E2E:

1. Built-in `srt-booking` 설치 (credential 없음) → `needs_setup`
2. User가 `srt_account` credential 생성
3. User가 skill에 credential 바인딩
4. Runtime에서 `KSKILL_SRT_ID`/`PASSWORD`가 subprocess env에 주입됨 (직접 노출 안 됨)
5. log/SSE/tool result에는 값 redact

### 14.5 Permission test matrix

테스트 사용자 최소 4명:

| User | Role |
|------|------|
| A | publisher/owner |
| B | restricted ACL target |
| C | unrelated authenticated user |
| Admin | super_user |

Assertions:

- A는 private/restricted/public skill publish 가능
- B는 ACL에 포함된 restricted item만 view/install
- C는 restricted item에 404
- C는 public/system item view
- Admin은 public/system disable 가능, listed toggle 가능
- Disabled item은 B/C 신규 install 불가
- A가 item metadata 변경해도 B의 installed copy 유지

### 14.6 Runtime Isolation Tests

Skill A, Skill B 생성. Agent에 A만 attach.

- Runtime skills root에 A만 존재
- `execute_in_skill("skill-a", ...)` 성공
- `execute_in_skill("skill-b", ...)` → "not attached" 구조화 에러
- Prompt-visible `/skills/` 인스트럭션에 B 미노출
- LLM이 `read_file('/skills/skill-b/...')` 시도해도 backend가 차단

### 14.7 Secret Safety Tests

- `.env` 포함 package publish 실패
- `-----BEGIN PRIVATE KEY-----` 포함 publish 실패
- Catalog list response에 payload 파일 내용 미포함
- Detail response에 다른 사용자 credential_id 미노출
- Credential decrypt 후 raise되는 exception에 mapped env value 미포함

## 15. Migration Plan

### 15.1 순서

1. **m40** `m40_marketplace_tables.py`: marketplace 5개 테이블 + circular FK 핸들링
2. **m41** `m41_skills_marketplace_columns.py`: skills 12개 컬럼 추가 + backfill
3. **m42** `m42_agent_skills_config.py`: `agent_skills.config` JSON 컬럼
4. **m43** `m43_skill_credential_bindings.py`: `skill_credential_bindings` 테이블
5. (별도 PR) 신규 credential definition 8개 import
6. (별도 PR) `secret_scan.py`, `redaction.py`, `access.py` 모듈
7. (별도 PR) `app/marketplace/service.py` + 라우터 read endpoint (Slice A)
8. (별도 PR) Slice B install
9. (별도 PR) Slice C publish + secret scan integration
10. (별도 PR) Slice D credential requirement/binding
11. (별도 PR) Slice E runtime mount + injection (보안 영향 큼)
12. (별도 PR) Slice F k-skill importer + CLI
13. (별도 PR) 프론트엔드 Marketplace UI

### 15.2 Backfill

m41 안에서:

- 모든 기존 skills: `is_system=FALSE`, `source_kind='user'`
- text skill (`kind='text'`): `origin_kind='created_by_me'`
- package skill (`kind='package'`): `origin_kind='imported_by_me'`
- 기타 origin/source 컬럼: NULL

### 15.3 Rollback

- 각 마이그레이션 별 downgrade 구현 (마켓플레이스 미사용 상태로 되돌리기 가능)
- 컬럼 drop 시 데이터 손실 경고
- m42/m43 rollback 시 `agent_skills.config`/`skill_credential_bindings` 데이터는 사라짐
- m41 rollback 시 origin 컬럼 데이터 손실

### 15.4 Feature Flags

`backend/app/config.py`:

```python
marketplace_enabled: bool = False
k_skill_builtin_sync_enabled: bool = False
skill_runtime_credential_injection_enabled: bool = False  # Slice E gating
```

- `marketplace_enabled=False`: 라우터 미등록 또는 503
- `k_skill_builtin_sync_enabled=False`: CLI 거부
- `skill_runtime_credential_injection_enabled=False`: 기존 broad mount 유지 (Slice E 미배포 상태)

각 슬라이스 배포 시 점진적 enable.

## 16. Implementation Slices

### Slice A — Data + Read Catalog

- m40-m43 마이그레이션
- ORM 모델 (`models/marketplace.py`, `models/skill.py` 컬럼)
- `access.py`, `origin_service.py`, `schemas.py`
- `GET /api/marketplace/items`, detail, version 엔드포인트
- 기존 `/api/skills`, `/api/mcp-servers` 응답에 origin/publication summary 추가
- Install/publish 없음

검증:

- 마이그레이션 upgrade/downgrade
- Catalog 응답이 access 규칙 준수 (private/restricted/public/system 시나리오)
- Existing skill list에 `not_published` summary 표시

### Slice B — Skill Install

- `install_service.py`
- `POST /api/marketplace/items/{id}/install`
- `POST /api/marketplace/installations/{id}/update`
- `DELETE /api/marketplace/installations/{id}`
- Installed skill row에 source 메타 채우기
- `install_mode` 처리

검증:

- 설치는 user-owned skills row 생성
- Installation row가 item/version/resource IDs 추적
- 중복 install이 `install_mode`를 정확히 따름

### Slice C — Skill Publish + Secret Scan

- `publish_service.py`, `secret_scan.py`
- `POST /api/marketplace/items/from-skill/{skill_id}`
- `POST /api/marketplace/items/{item_id}/versions/from-skill/{skill_id}`
- ACL 관리 API
- `marketplace_publication_links` 생성
- Installed skill detail에 published 상태 표시

검증:

- Publish가 private data strip
- Secret scan rejects `.env`/PEM
- Restricted ACL 제어
- Immutable version
- API summary가 `Published · Restricted/Public/Unlisted` 정확

### Slice D — Credential Requirements + Bindings

- 신규 8개 credential definition import
- `credential_requirements.py`
- `GET|PUT|DELETE /api/skills/{id}/credential-bindings/...`
- Install 흐름에 binding wizard 통합
- Frontend setup UX

검증:

- 잘못된 owner credential 거부
- 잘못된 definition_key 거부
- `needs_setup` 상태가 catalog/detail/install 응답에 정확히 노출

### Slice E — Runtime Mount + Credential Injection (보안 critical)

- `executor.py:build_agent` 패치 (per-thread copytree)
- `_create_skill_execute_tool` 시그니처 변경 + env injection
- `redaction.py` 통합
- Cleanup job (stale runtime root)
- Fail-fast: missing required credential

검증:

- 미선택 skill 접근 불가
- Decrypted credential은 subprocess env에만 노출
- log/SSE/tool result redaction
- Missing credential → `marketplace_credential_required` 에러

### Slice F — k-skill Importer

- `k_skill_importer.py`, `k_skill_requirements.py`
- `scripts/sync_k_skill.py` CLI
- Dry-run / partial sync
- Execution profile 분류
- Idempotent sync (content_hash 비교)

검증:

- Dry-run이 create/update/deprecate 카운트 보고
- 같은 commit 재실행 시 신규 version 없음
- 하나의 invalid skill이 전체 sync 중단 안 함
- Secret scan failure는 해당 skill만 skip

### Slice G — Frontend UX

- Marketplace 페이지 (`/marketplace`, `/marketplace/installed`)
- Install wizard (4 steps)
- Publish wizard (5 steps)
- Installed resource page 보강 (origin/publication badges)
- Admin moderation 화면 (super_user)

검증:

- Catalog 필터/검색/정렬
- Install 후 agent 설정에서 skill 선택 가능
- Publish wizard secret scan 결과 표시
- 카드 CTA 상태 매핑 정확

## 17. Acceptance Criteria

Phase 1 완료 조건:

- super_user가 k-skill sync 실행 → system skill item 카탈로그 생성
- 일반 사용자가 built-in skill을 자기 계정에 설치
- Installed skill list/detail이 origin과 publication 상태 표시
- Owned skill을 restricted/public/unlisted/private로 publish
- Restricted 대상 사용자만 install 가능
- 비대상 사용자는 item이 보이지 않음 (404)
- Required credential skill은 binding 없이 실행 차단
- Binding은 중앙 `credentials`를 사용하고 ownership/type 검증
- Runtime은 선택된 skill만 노출
- Mapped env var는 subprocess env에만 주입되고 log/SSE/tool result에는 redact
- Public publish는 `is_listed=True`로 토글되기 전까지 카탈로그 기본 검색에 노출 안 됨
- 기존 skill upload/edit/delete 회귀 통과

### Definition of Done by Feature Area

| Area | Done means |
|------|------------|
| Catalog | 접근 가능한 item list/detail이 필터, install state, credential summary, execution profile 포함 |
| Install | Skill install이 user-owned copy + installation link + 선택적 credential binding 생성 |
| Publish | User skill publish가 immutable version 생성, secret strip, visibility/ACL 강제 |
| Credentials | Definition, install setup, binding API, runtime injection, redaction 테스트 통과 |
| k-skill | Importer가 dry-run/sync 가능 + pinned commit에서 idempotent |
| Runtime | Selected skill만 mount, missing credential은 command 실행 전 fail |
| Frontend | Marketplace list/detail/install/publish flow가 JSON 지식 없이 사용 가능 |

## 18. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Credential leak through marketplace package | Critical | secret_scan, strip fields, immutable review, no decrypted data in payload |
| Runtime exposes all skills | High | Per-thread runtime root + execute_in_skill slug 검증 |
| Upstream k-skill 악의적 변경 | High | Sync는 super_user CLI만, source commit pin, secret scan, support level |
| Node/npm skills 미지원 | Medium | Execution profile로 사용자에게 명시 |
| Public spam | Medium | super_user disable, `is_listed` 게이트, moderation_status, publish rate limit |
| Dirty installed skill update overwrite | Medium | `is_dirty` flag, no auto-update |
| Per-thread runtime root 디스크 사용 | Low | Cleanup job, 짧은 retention, disk 모니터링 |
| Marketplace version FK + circular | Low | m40 안에서 후행 ALTER로 처리 |

## 19. Implementation Handoff Order

이 spec을 새 세션에서 구현 시작할 때 권장 순서:

1. Slice A 마이그레이션 (m40) + ORM
2. Slice A catalog API (read-only)
3. Slice B install (텍스트 skill 한 개를 수동으로 marketplace item으로 등록해 install 검증)
4. Slice D credential definitions + binding API
5. Slice E **runtime mount + credential injection** (보안 critical, 별도 PR/리뷰)
6. Slice C publish + secret_scan
7. Slice F k-skill importer
8. Slice G frontend (병렬 진행 가능)

Slice E는 marketplace 전체 가치의 근본 보안 게이트이므로 별도 PR로 분리하고, runtime isolation test와 secret safety test가 모두 통과한 뒤 main에 머지.

---

## 20. 참고

- PRD: `docs/marketplace-resources-prd.md` v0.2
- 원본 spec (Moldy 본가): `/Users/chester/dev/natural-mold/docs/maketplace/marketplace-resources-spec.md` v0.3
- ADR-007 / 009 / 013 / 016
- Key 코드 위치:
  - `backend/app/agent_runtime/executor.py:113-195` (`_create_skill_execute_tool`)
  - `backend/app/agent_runtime/executor.py:213-225` (`create_deep_agent` 호출)
  - `backend/app/agent_runtime/executor.py:544-571` (FilesystemBackend + skills mount)
  - `backend/app/skills/service.py:46-51` (`_storage_root`)
  - `backend/app/skills/service.py:389-404` (`to_runtime_dict`)
  - `backend/app/skills/packager.py:59-93` (zip validation)
  - `backend/app/skills/prompt.py:27-50` (`build_skills_prompt`)
  - `backend/app/credentials/definitions/__init__.py:22-37` (registry 자동 등록)
  - `backend/app/dependencies.py` (`get_current_user`, `require_super_user`, `verify_csrf`)
