# ADR-017 — Marketplace Resources (Skill / MCP / Agent 공유 레이어, Phase 1: Skill)

## 1. Status & Date

- **Status**: Proposed
- **Date**: 2026-05-18
- **Owner**: 피차이 (Sundar Pichai, TTH Architect)
- **Source documents**:
  - `docs/marketplace-resources-prd.md` v0.2
  - `docs/marketplace-resources-spec.md` v0.1
- **Branch**: `worktree-marketplace-resources`
- **Relates / Depends on**:
  - ADR-007 (`credentials.field_keys` 캐시) — N+1 회피 패턴 재사용
  - ADR-009 (Credential 그린필드) — Cipher V2 + `is_system` + CHECK constraint 패턴 재사용
  - ADR-013 (Service-side LLM Key) — 사용자 credential 우선순위 정책 재사용
  - ADR-016 (멀티유저 인증) — `get_current_user` / `require_super_user` / `verify_csrf` 의존성, system credential 격리 정책
- **Supersedes**: 없음

---

## 2. Context

### 2.1 왜 마켓플레이스가 필요한가 (PRD §1~2 요약)

natural-mold는 ADR-016으로 멀티유저 인증이 적용되어 Agent / MCP server / Skill / Credential / Tool이 모두 사용자 소유 리소스로 분리되어 있다. 다음 단계는 **사용자 간 공유 layer**와 **운영자가 제공하는 built-in 카탈로그**다.

특히 `NomaDamas/k-skill` 저장소는 한국형 업무/생활 자동화 skill 모음으로 natural-mold에 built-in 카탈로그로 적합하지만, hard fork 없이 특정 upstream commit snapshot을 catalog version으로 가져와야 한다.

### 2.2 현재 코드 상태에서 식별된 빈 구멍 (PRD §2)

PRD v0.2는 v0.1의 잘못된 가정("새 subprocess runner를 도입해야 한다")을 폐기하고, 코드 심층 분석으로 다음을 확인했다.

| # | 빈 구멍 | 코드 위치 |
|---|--------|-----------|
| G1 | Skill을 다른 사용자와 공유 불가 — `Skill.user_id` NOT NULL + service의 `user_id` 강제 필터 | `models/skill.py`, `skills/service.py` |
| G2 | 공통 marketplace 모델 없음 (item/version/installation/acl/publication) | `models/` |
| G3 | Skill에 marketplace 추적 컬럼 부재 (`source_*`, `origin_*`, `is_dirty`) | `models/skill.py` |
| G4 | `AgentSkillLink`에 override 슬롯 없음 (`config` 컬럼 부재) | `models/skill.py` |
| G5 | Broad skill mount — agent에 skill 하나라도 있으면 `skills=["/skills/"]` 전체 노출 | `agent_runtime/executor.py:544-571` |
| G6 | `execute_in_skill` env에 credential 미주입 (PATH/PYTHONPATH/HOME/SKILL_OUTPUT_DIR/OUTPUTS_DIR만) | `agent_runtime/executor.py:113-195` |
| G7 | `packager.py`에 secret scan 부재 (symlink/zip-slip/null-byte/50MB만 검사) | `skills/packager.py` |
| G8 | k-skill용 credential definition 부재 (기존 13개에 한국형 8개 누락) | `credentials/definitions/` |
| G9 | log/SSE/tool result에 mapped env 값 redact 미적용 | `streaming.py`, `executor.py` |

이미 갖춰진 인프라(재사용 대상): deepagents + `execute_in_skill` subprocess runner, FilesystemBackend, Cipher V2, field_keys 캐시, 13개 credential definition, `require_super_user`/`verify_csrf`, ENV→system credential bootstrap.

### 2.3 Phase 1 범위 (PRD §14, Spec §0)

Phase 1은 **Skill marketplace 한정**이다. MCP/Agent 마켓플레이스는 동일 모델로 Phase 2/3에서 확장한다 (테이블/스키마는 Phase 1에서 미리 준비, install/publish flow는 미구현).

```
m40~m43 데이터 모델
  → Slice A: Read Catalog (목록/상세 API, 접근 매트릭스)
  → Slice B: Install (사용자 소유 skill copy + binding)
  → Slice C: Publish + Secret Scan (immutable version + ACL)
  → Slice D: Credential Definitions + Binding API
  → Slice E: Runtime selected-skill mount + credential env injection + redaction (**보안 critical**)
  → Slice F: k-skill importer (super_user CLI 전용)
  → Slice G: Marketplace UI (Frontend)
```

---

## 3. Decision

### 3.1 7가지 핵심 결정 (PRD Decision Summary + Spec §0.1)

| # | 항목 | 선택 | 근거 |
|---|------|------|------|
| D-01 | **마켓플레이스 리소스 범위** | Agent / MCP / Skill (Tool 비목표) | Tool은 `tools/registry.py`의 메모리 `ToolDefinition`으로 운영자가 코드로 정의하는 자산 — 사용자가 만들거나 공유하지 않는다 |
| D-02 | **Phase 분리** | Phase 1 = Skill, Phase 2 = MCP, Phase 3 = Agent | Skill 루프가 가장 닫혀 있고 즉시 가치가 크다. 데이터 모델은 처음부터 3종 모두 수용 |
| D-03 | **Skill runtime baseline** | 기존 deepagents + `execute_in_skill` subprocess runner 위에 (a) selected-skill mount, (b) credential env injection, (c) redaction 세 빈 구멍만 메움 | 새 runner 도입 없음. v0.1 가정 폐기 |
| D-04 | **Alembic 분할** | 슬라이스별 m40~m43 (단일 큰 마이그레이션 reject) | rollback/review 단위가 작아짐. m40 catalog tables, m41 skills 컬럼, m42 agent_skills.config, m43 skill_credential_bindings |
| D-05 | **Agent-Skill credential override** | Option A — `agent_skills.config` JSON 필드 | 현재 link model과 정합. 큰 정규화 마이그레이션 회피. `scope='agent_skill'` row는 향후 정규화 시점에 예약 |
| D-06 | **Runtime mount 격리** | Option A — per-thread `copytree`로 `data/runtime/<thread_id>/skills/<slug>/`에 선택된 skill만 복사 | symlink는 쓰기 위험. broad `/skills/` 노출은 같은 사용자의 미선택 skill 누출 위험. thread_id는 LangGraph checkpoint 키와 동일하여 SSE resume(ADR-011)과 호환 |
| D-07 | **k-skill 도입 방식** | GitHub `NomaDamas/k-skill` clone → super_user CLI(`uv run python -m app.scripts.sync_k_skill`)로 특정 commit snapshot을 system marketplace item으로 등록. git submodule 거부 | upstream 코드 수정 안 함. CLI 전용 — web UI 노출 안 함 |
| D-08 | **Public publish 정책** | published vs listed 분리. `is_listed=False`로 시작, super_user만 토글 | 누구나 publish 가능하되 카탈로그 노출은 운영자 게이트. `is_listed=False` public 항목은 unlisted와 동일한 효과(직접 ID/slug로 접근 가능) |
| D-09 | **비인가 접근 응답** | 모든 detail/install 비인가는 404 통일 (`marketplace_install_forbidden` 등) | enumeration oracle 방지 — private/restricted 존재 여부를 노출하지 않는다 |
| D-10 | **Credential 처리** | 중앙 `credentials` 테이블 + Cipher V2 + field_keys 캐시 + `is_system` 분리 그대로 재사용. k-skill용 8개 definition 신규 추가 | ADR-007/009 인프라 재사용. binding/requirement 모델만 신규 (`skill_credential_bindings`) |
| D-11 | **Visibility 모델** | `private / restricted / public / unlisted / system` | 원본 결정 유지 |
| D-12 | **Installed resource ownership** | marketplace access는 직접 access를 부여하지 않음. 설치는 항상 `current_user` 소유 row 생성 | 기존 ownership check 유지. system item을 agent에 직접 연결하지 않음 |
| D-13 | **Version immutability** | `marketplace_versions.payload/storage_path/content_hash`는 publish 후 수정 불가. 메타 typo는 item-level metadata만 | update 비교/audit 단순화 |
| D-14 | **Required credential 누락 install** | `install_missing_credentials='needs_setup'` 허용. Runtime에서 fail-fast (`marketplace_credential_required` 409) | 사용자가 catalog 먼저 가져온 뒤 credential을 나중에 연결 가능 |

> 비고: D-01~D-14가 전체 결정 표다. PRD/Spec의 "7가지 핵심" 요약은 **D-01~D-07이며**, D-08~D-14는 이 결정들의 즉시 따라오는 정책 세부다.

### 3.2 데이터 모델 요약 (Spec §3)

신규 6개 도메인 엔티티(5개 마켓플레이스 테이블 + 1개 credential binding 테이블) + skills/agent_skills 컬럼 확장.

| 엔티티 | 테이블 | 마이그레이션 | 역할 |
|--------|--------|--------------|------|
| MarketplaceItem | `marketplace_items` | m40 | 공유 가능한 logical 항목 (resource_type + owner + visibility + is_listed + latest_version) |
| MarketplaceItemACL | `marketplace_item_acl` | m40 | restricted visibility의 user 단위 ACL (view/install/manage) |
| MarketplaceVersion | `marketplace_versions` | m40 | immutable snapshot (payload + content_hash + credential_requirements + execution_profile) |
| MarketplaceInstallation | `marketplace_installations` | m40 | 설치 기록 (user → item → version → installed_skill_id) |
| MarketplacePublicationLink | `marketplace_publication_links` | m40 | 내 리소스 ↔ 내가 publish한 item 역참조 (resource_type 별 UNIQUE) |
| Skill (확장) | `skills` ALTER | m41 | 12개 컬럼 추가: is_system, source_kind, source_marketplace_item_id, source_marketplace_version_id, source_commit, credential_requirements, execution_profile, origin_kind, origin_user_id, origin_marketplace_item_id, origin_marketplace_version_id, is_dirty |
| AgentSkillLink (확장) | `agent_skills` ALTER | m42 | `config JSON` 컬럼 추가 (agent-skill credential override) |
| SkillCredentialBinding | `skill_credential_bindings` | m43 | (skill_id, user_id, requirement_key, credential_id) — Phase 1은 `scope='skill'`만 |

Circular FK 처리 (m40 내): `marketplace_items.latest_version_id` → `marketplace_versions.id`는 items/versions 둘 다 생성 후 `ALTER TABLE ... ADD CONSTRAINT`로 추가.

상세 컬럼/CHECK constraint/INDEX는 Spec §3.2~§3.10 참조.

### 3.3 모듈 경계 요약 (Spec §11)

신규 폴더: `backend/app/marketplace/`

```
marketplace/
├── __init__.py
├── access.py                # can_view_item / can_install_item / can_manage_item (Slice A)
├── schemas.py               # Pydantic 모델 (Spec §10.8)
├── service.py               # catalog list/detail (Slice A)
├── install_service.py       # install/update flow (Slice B)
├── publish_service.py       # publish flow (Slice C)
├── origin_service.py        # origin/publication summary 파생 + mark_installation_dirty
├── secret_scan.py           # SECRET_FILE_PATTERNS + SECRET_CONTENT_PATTERNS (Slice C/F)
├── redaction.py             # redact_credential_values / redact_keys (Slice E)
├── credential_requirements.py  # mapping / validation / env injection plan (Slice D/E)
└── k_skill_importer.py      # upstream sync 호출부 (Slice F)

backend/app/scripts/sync_k_skill.py    # super_user CLI 진입점
backend/app/routers/marketplace.py     # 신규 라우터
backend/app/models/marketplace.py      # 단일 파일에 5개 테이블 ORM
```

### 3.4 보안 결정 (Spec §13, PRD §12)

- **Secret scan**: `secret_scan.py`는 publish + import + `routers/skills.py:upload` 세 곳에서 호출 (회귀 가드). `packager.py` 자체는 변경 없이 호출자가 wrap.
- **Credential safety**: API 응답에 decrypted value 절대 미노출. Runtime env injection은 mapped env var(예: `KSKILL_SRT_ID`)에만. 다른 env에 미주입.
- **Redaction**: `redact_credential_values`로 mapped env value를 `<redacted:ENV_NAME>`으로 교체. `redact_keys`로 `password|api_key|secret|token|access_key|refresh_token` 패턴 키 값을 `<redacted>`로 교체. 호출 지점: `_create_skill_execute_tool` 반환, `streaming.py` tool_call_result 페이로드, exception detail, raw log statement 전부.
- **System credential 격리**: `/api/system-credentials`는 super_user 전용 유지. marketplace에 노출 안 함. Hosted proxy는 system dependency로 표시.
- **Enumeration oracle**: 비인가 detail/install 모두 404 (CLAUDE.md 원칙).

### 3.5 Runtime 변경 요약 (Spec §8, §9)

`executor.py`의 변경 두 곳:

1. **`build_agent` 진입 시점**: per-thread runtime root 준비
   ```text
   data/runtime/<thread_id>/skills/<slug>/  ← copytree(skill.storage_path, ..., symlinks=False)
   skills_sources = [f"/runtime/{thread_id}/skills/"]
   ```
2. **`_create_skill_execute_tool` 시그니처**: `(output_dir, thread_id, skill_descriptors)`로 확장. 함수 내부에서:
   - slug → descriptor 매핑 검증 (없으면 `"Error: skill not attached to this agent"`)
   - descriptor.storage_path가 runtime_root 하위인지 `is_relative_to` 검증
   - descriptor.credential_bindings를 순회하며 `env[env_name] = decrypted[field]` 주입
   - subprocess 실행 결과/exception은 `redact_credential_values`로 마스킹

`build_skills_for_agent`(`skills/runtime.py`)는 `SkillRuntimeDescriptor` 리스트를 반환하도록 확장 (id, slug, original_storage_path, storage_path, credential_bindings: dict[str, ResolvedCredential]).

`ResolvedCredential`은 in-memory only 데이터 클래스 — JSON 직렬화/로그 출력 금지.

### 3.6 Cleanup 정책

- Conversation 종료 시 `data/runtime/<thread_id>/` best-effort 제거.
- 서버 시작 시 lifespan에서 1시간 이상 오래된 runtime root GC.
- 강제 종료/crash 잔여는 retention job이 정리.

---

## 4. Rejected Alternatives (Spec §0.2)

| 거절안 | 거절 이유 |
|--------|-----------|
| 기존 `skills`에 `is_builtin`, `visibility`만 추가 | version/update/install 이력이 흐려지고 user-owned와 catalog가 섞인다 |
| Marketplace item을 직접 runtime에서 실행 (설치 없이) | upstream/owner 변경이 사용자 실행에 즉시 영향. credential binding이 복잡해진다 |
| k-skill을 git submodule로 직접 참조 | runtime이 upstream layout에 강결합. immutable snapshot 부재 |
| 새 skill runner 도입 (subprocess 외) | 이미 `execute_in_skill` subprocess runner가 동작 중. 그 위에 보안 빈 구멍만 메우는 것으로 충분 |
| symlink 기반 mount | 쓰기가 원본으로 흐를 위험. 같은 사용자의 다른 skill을 LLM이 `read_file`로 읽을 수 있음 |
| 단일 큰 m40 마이그레이션 | rollback/review 부담. 슬라이스별 마이그레이션이 안전 |
| Tool 마켓플레이스 포함 | Tool은 운영자가 코드로 정의하는 자산 (`tools/registry.py`). 사용자가 만들거나 공유하지 않음. 별도 PRD가 필요해지면 다시 검토 |
| localStorage + Bearer 토큰 / Redis 기반 refresh | ADR-016이 이미 HttpOnly Cookie + DB whitelist로 결정. 마켓플레이스는 그 위에 얹힘 |

---

## 5. Consequences

### 5.1 Positive

- **단일 catalog 모델**로 Skill/MCP/Agent 3종을 동일 패턴으로 확장 가능. Phase 2/3에서 스키마 재설계 불필요.
- **Immutable version + content_hash**로 설치 audit와 update 비교가 단순. dirty 추적도 명확.
- **per-thread mount**로 같은 사용자의 미선택 skill까지 격리. Phase 1의 가장 큰 보안 개선.
- **Credential env injection**으로 SKILL.md 본문에 secret을 적을 동기가 사라짐. secret_scan과 함께 publish 시 secret leak을 양방향에서 차단.
- **`is_listed` 게이트**로 누구나 publish하되 카탈로그 노출만 운영자가 통제. moderation 부담을 일정 수준으로 유지.
- **k-skill CLI 전용**으로 super_user 이외 sync trigger 불가. upstream layout 변경의 폭발 반경을 운영자에게 한정.
- **ADR-016과 충돌 없음**: `get_current_user` / `require_super_user` / `verify_csrf` / system credential 격리 정책을 그대로 재사용.

### 5.2 Negative / Trade-offs

- **per-thread copytree 비용**: 큰 package skill (~50MB 제한)을 가진 대화당 디스크 I/O와 retention job 부담. → mitigation: 1시간 이상 stale GC + conversation 종료 시 즉시 cleanup.
- **`agent_skills.config` JSON**: 정규화된 `skill_credential_bindings`와 중복되는 override 슬롯. Phase 2에서 `scope='agent_skill'`로 정규화 가능하도록 예약 컬럼 유지. 지금은 JSON이 더 빠른 path.
- **Circular FK (items↔versions)**: m40 안에서 ALTER로 처리. rollback 시 역순으로 drop 필요.
- **`is_listed=False` public 항목 처리**: unlisted와 동일한 효과(직접 link로 install 가능)라 사용자에게 두 상태가 헷갈릴 수 있음. → mitigation: publication badge에 `Published · Public · Unlisted (pending listing)` 같은 구분 텍스트.
- **k-skill upstream layout 변경 리스크**: validate-skills.sh exclusion을 mirror해야 하며, frontmatter schema가 바뀌면 sync 실패. → mitigation: 단일 skill validation 실패가 전체 sync를 중단하지 않음. 결과 보고서에 실패 목록 포함.
- **Secret scan false positive**: 정당한 PEM-like 콘텐츠를 가진 skill을 publish 차단. → mitigation: 사용자에게 명확한 finding path/pattern 응답 + 운영 가이드 제공.
- **`is_dirty` 추적의 부재 비용**: skill content 수정 endpoint 4곳(`PUT /content`, `PATCH /skill`, files `PUT|POST|DELETE`)에서 `mark_installation_dirty` 호출이 누락되면 update 충돌 감지가 실패. → mitigation: `origin_service.mark_installation_dirty`를 단일 진입점으로 만들고 endpoint 4곳 모두에서 best-effort 호출.

### 5.3 다른 ADR에의 영향

| ADR | 관계 | 영향 |
|-----|------|------|
| ADR-001 (Deep Agent 엔진) | depends-on | `create_deep_agent`의 `skills=[...]`/`backend=FilesystemBackend(virtual_mode=True)` 인터페이스 유지. mount root만 per-thread로 변경 — 엔진 자체는 변경 없음 |
| ADR-003 (Skills + Memory) | extends | 기존 `build_skills_for_agent`는 `to_runtime_dict()` 리스트만 반환했음. 이 ADR은 `SkillRuntimeDescriptor`로 확장 (credential_bindings + per-thread storage_path 포함). prompt.py는 변경 불필요 |
| ADR-007 (`field_keys` 캐시) | reuses | 신규 8개 credential definition도 동일하게 `field_keys` 자동 채움. 변경 없음 |
| ADR-009 (Credential 그린필드) | reuses | Cipher V2 + `is_system` + CHECK constraint 패턴을 marketplace 도메인에도 적용 (`marketplace_items.is_system`, `marketplace_publication_links` 등) |
| ADR-011 (SSE Stream Resume) | compatible | `thread_id`가 LangGraph checkpoint 키와 동일하므로 resume 시 같은 runtime root 재사용 |
| ADR-012 (HiTL Middleware) | neutral | marketplace는 HiTL 흐름과 직교. install/publish는 동기 REST |
| ADR-013 (Service-side LLM Key) | reuses | 사용자 credential 우선순위 정책(사용자 키 → system 키)을 그대로 따른다. marketplace skill credential도 사용자 owned credential만 binding 가능 |
| ADR-016 (멀티유저 인증) | depends-on | `get_current_user` / `require_super_user` / `verify_csrf` / system credential 격리 / `is_super_user` 단일 플래그를 그대로 재사용. 모든 marketplace mutation은 CSRF 보호 |

### 5.4 후속 ADR 예고

- **Phase 2 MCP marketplace**: `marketplace_items.resource_type='mcp'`, `marketplace_versions.payload_kind='mcp_template'` 실제 install flow. 본 ADR에서 스키마는 준비됨.
- **Phase 3 Agent marketplace**: `payload_kind='agent_spec'` install flow + skill/MCP 참조 resolution.
- **Phase 4 Curation/Moderation**: 별점/리뷰/랭킹/검색 알고리즘. 현 ADR 범위 밖.
- **Workspace 확장 (ADR-016 §7 후속)**: `user_id` → `TenantContext`로 시그니처 변경 시 marketplace ownership 컬럼도 동일하게 마이그레이션. 현 ADR은 user 단위 ownership 가정.

---

## 6. References

- **소스 문서**: `docs/marketplace-resources-prd.md` v0.2, `docs/marketplace-resources-spec.md` v0.1
- **관련 ADR**:
  - [ADR-007 — Credentials field_keys 캐시 컬럼](adr-007-credentials-field-keys-cache.md)
  - [ADR-009 — Credential / Tools / Skills 그린필드 리라이트](adr-009-greenfield-credentials.md)
  - [ADR-013 — Service-side LLM Key from Credentials](adr-013-service-llm-key-from-credentials.md)
  - [ADR-016 — 멀티유저 인증](adr-016-multiuser-auth.md)
- **외부 참고** (UI/구조만 차용, 코드/UI 복사 금지):
  - Cal.com App Store — install 여부 표시, app package 구조
  - Dify Marketplace — resource type별 탐색 + 설치 source 구분
  - Open VSX Registry — versioned extension registry, web UI + publish CLI 분리
  - `NomaDamas/k-skill` GitHub — Phase 1 built-in catalog 소스
- **모듈 계약 보조 문서**: `docs/design-docs/marketplace-module-contracts.md` (M1-S2 산출물)
