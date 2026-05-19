# natural-mold Marketplace Resources PRD

> 작성일: 2026-05-18
> 버전: v0.2 (natural-mold 적용본 — 소스코드 심층 분석 반영)
> 베이스: `/Users/chester/dev/natural-mold/docs/maketplace/marketplace-resources-prd.md` v0.3, `marketplace-resources-spec.md` v0.3
> 범위: Agent / MCP / Skill 공유 마켓플레이스 + built-in k-skill 카탈로그 + credential 연결 UX

## Changelog

| 버전 | 날짜 | 변경 내용 |
|------|------|-----------|
| v0.2 | 2026-05-18 | natural-mold 소스코드 심층 분석 결과 반영. **execute_in_skill subprocess runner는 이미 도입되어 있음**을 정정(이전 v0.1의 잘못된 가정 폐기). 빈 구멍은 (a) credential env 주입 없음, (b) broad skill mount(`/skills/`), (c) packager에 secret scan 없음, (d) AgentSkillLink에 config 필드 없음, (e) Skill 모델에 source/dirty/origin 컬럼 없음. 14개 기존 credential definition 목록 명시 + k-skill용 신규 정의 추가 필요. ADR-007/009/013/016과의 정확한 매핑. |
| v0.1 | 2026-05-18 | (폐기됨) 초기 적용본. 잘못된 가정 다수 포함. |

---

## Decision Summary

이 PRD의 핵심 결정은 원본과 동일하다 — "마켓플레이스는 배포 원본이고, 실행은 사용자 계정에 설치된 copy가 담당한다". natural-mold의 실제 코드 상태에 맞춰 다음 결정을 명시한다.

| 결정 | 선택 | 이유 |
|------|------|------|
| 마켓플레이스 리소스 범위 | Skill, MCP, Agent (Tool은 비목표) | Tool은 `backend/app/tools/registry.py`의 메모리 기반 `ToolDefinition`으로 운영자가 코드로 정의하는 자산이며 사용자가 만들거나 공유하지 않는다 |
| 단계 분리 | Phase 1 Skill / Phase 2 MCP / Phase 3 Agent | 원본 PRD 단계 유지. Skill 루프가 가장 닫혀 있고 즉시 가치가 크다 |
| Skill runtime baseline | **이미 deepagents + `execute_in_skill` subprocess runner 도입됨** (`executor.py:113-195`). Phase 1은 그 위에 (1) selected-skill mount, (2) credential env injection, (3) redaction contract만 추가 | 새 runner를 도입하지 않고 기존 코드에 빈 구멍 세 개를 메우는 작업 |
| k-skill 도입 | GitHub `NomaDamas/k-skill` upstream을 sync job으로 fetch → system marketplace item으로 등록 | upstream 코드를 직접 수정하지 않고 immutable snapshot으로 가져옴 |
| Public publish 정책 | published vs listed 분리, super_user만 listed 토글 가능 | 누구나 publish 가능하되 카탈로그 노출은 운영자 게이트 |
| 인증 전제 | ADR-016 멀티유저 인증 완료 상태 | JWT(HS256) + HttpOnly Cookie + refresh rotation + super_user 권한이 이미 적용됨 |
| Visibility 모델 | private / restricted / public / unlisted / system | 원본 그대로 |
| Credential 처리 | 중앙 `credentials` 테이블 + Cipher V2 + field_keys 캐시 + is_system 분리 재사용 | ADR-007/009 인프라가 이미 갖춰져 있음. binding/requirement 모델만 신규 도입 |
| 설치 방식 | marketplace version을 사용자 소유 installed resource로 복사 | 원본 결정 유지 |
| 버전 정책 | published version immutable | 원본 결정 유지 |
| 업데이트 | 자동 업데이트 금지, 사용자가 명시적으로 적용 | 원본 결정 유지 |
| 출처 표시 | 모든 installed Agent/MCP/Skill에 origin과 publication state 표시 | 원본 결정 유지. 신규 컬럼 추가 필요 |

## MVP Product Bet

Phase 1의 목표는 "사용자가 built-in k-skill을 골라 자기 Skill로 설치하고, 필요한 경우 credential을 연결한 뒤 agent에 붙여 실행할 수 있다"이다. 이 흐름이 완성되면 사용자 제작 skill의 restricted 공유, 그리고 같은 구조 위에 MCP/Agent 마켓플레이스까지 확장 가능하다.

MVP에서 반드시 보여야 하는 경험:

- Marketplace > Skills에서 built-in/system skill을 찾고 설치한다.
- 인증이 필요한 skill은 설치 전후에 무엇이 필요한지 명확히 보인다.
- 사용자는 중앙 `credentials`에 값을 저장하고 skill requirement에 연결한다.
- 설치된 skill은 기존 내 Skill처럼 agent에 연결되고, agent 실행 시 **선택된 skill의 디렉토리만 노출**되며 **mapped env var로 credential이 subprocess에 주입**된다.
- restricted로 공유된 skill은 허용된 사용자에게만 보인다.
- upstream k-skill update는 update available로 표시되지만 자동 적용되지 않는다.
- 누구나 public publish할 수 있지만, super_user가 listed로 승인하지 않은 항목은 검색 카탈로그에 노출되지 않는다 (링크로만 접근 가능).

## 1. 배경

natural-mold는 ADR-016에 따라 멀티유저 인증을 완료했고, Agent / MCP server / Skill / Credential / Tool이 모두 사용자 소유 리소스로 관리된다. JWT(HS256) + HttpOnly Cookie + refresh token rotation + `is_super_user` 역할이 적용되어 있다.

이미 갖춰진 구조 (마켓플레이스 설계에서 재사용):

| 영역 | 모듈/경로 | 상태 |
|------|----------|------|
| Skill 모델 | `backend/app/models/skill.py:Skill` | text/package kind, storage_path, content_hash, version, package_metadata, used_by_count |
| Skill 서비스 | `backend/app/skills/service.py` | CRUD, `to_runtime_dict`, 파일시스템 관리(`data/skills/<id>/`) |
| Skill 패키저 | `backend/app/skills/packager.py` | `.skill` ZIP extract, symlink/zip-slip/null-byte 방지, 50MB 제한 |
| Skill 인스펙터 | `backend/app/skills/inspector.py` | SKILL.md frontmatter parse, 안전 파일 접근 |
| Skill 런타임 | `backend/app/skills/runtime.py:build_skills_for_agent` + `prompt.py:build_skills_prompt` | AgentSkillLink → descriptor + LLM prompt 주입 |
| Skill 실행 도구 | `backend/app/agent_runtime/executor.py:_create_skill_execute_tool` (line 113-195) | **`execute_in_skill` subprocess runner 이미 동작 중**. Python allowlist, 30초 timeout, `_DATA_DIR` 경로 검증 |
| Agent runtime | `executor.py:build_agent` + `create_deep_agent` + `FilesystemBackend(_DATA_DIR, virtual_mode=True)` | deepagents 기반, skill을 `["/skills/"]`로 mount |
| MCP 모델 | `models/mcp_server.py`, `mcp_tool.py`, `AgentMcpToolLink` | transport(stdio/sse/streamable_http), `is_system`(M26), health_status |
| MCP discovery | `app/mcp/client.py`, `app/mcp/discovery.py` | 연결 + `list_tools` + credential 보간 + last_seen_at upsert |
| MCP health polling | `app/scheduler.py:MCP_HEALTH_JOB_ID` | APScheduler 5분 interval |
| Credential 모델 | `models/credential.py` | `is_system` + CHECK constraint, key_id, field_keys 캐시 |
| Cipher V2 | `app/security/cipher.py` | HKDF-SHA256 + AES-256-GCM, multi-key rotation, info=`moldy-encryption-v1` |
| Credential 정의 | `app/credentials/definitions/` | 13개 등록 (실측 2026-05-18): anthropic, openai, google_genai, azure_openai, openrouter, openai_compatible, google_search, naver_search, google_workspace_oauth2, http_bearer, http_basic, http_api_key, mcp_oauth2 (k-skill용 정의는 미존재 → 신규 추가 필요) |
| Credential 보간 | `app/credentials/interpolation.py:resolve_deep` | `={{ $credentials.x }}` (MCP env_vars/headers에서 사용) |
| 인증 | `app/auth/`, `app/dependencies.py` | `get_current_user`, `require_super_user`, `verify_csrf` |

이미 갖춰진 인프라는 PRD에서 "신규 도입"이 아니라 "재사용"으로 명시한다.

다음 단계는 사용자가 만든 Agent/MCP/Skill을 다른 사용자와 공유하고, 운영자가 제공하는 built-in 리소스를 사용자가 선택적으로 가져와 쓸 수 있는 공유 layer다.

특히 `NomaDamas/k-skill` 저장소는 한국형 업무/생활 자동화 skill 모음으로 natural-mold에 built-in skill catalog로 적합하다. upstream은 계속 업데이트되므로 hard fork하지 않고, 특정 upstream commit의 skill snapshot을 검증 후 catalog version으로 가져온다.

## 2. 문제 정의

### 현재 문제 (소스코드 확인 기반)

1. **Skill 공유 불가**: `Skill.user_id`는 nullable=False이고 `skill_service.list_skills()`는 `Skill.user_id == user_id` 조건. 다른 사용자에게 공유하거나 built-in catalog로 제공할 수 없다.
2. **공통 marketplace layer 부재**: Agent/MCP/Skill을 게시·검색·설치·업데이트하는 공통 모델이 없다.
3. **built-in / 사용자 자산 통합 UX 부재**: 같은 화면에서 출처를 구분하면서 가져오는 구조가 없다.
4. **Skill credential 요구사항 미선언**: `Skill` 모델에 credential_requirements 컬럼이 없고, packager가 secret scan을 하지 않는다. SKILL.md 본문에 환경변수 이름만 적는 방식.
5. **AgentSkillLink에 override 자리 없음**: 현재 `AgentSkillLink`는 (agent_id, skill_id) PK만 있고 `config` 필드가 없다. agent-skill 단위 credential override를 저장할 곳이 없다.
6. **Skill 모델에 marketplace 추적 컬럼 없음**: `source_marketplace_item_id`, `source_marketplace_version_id`, `is_dirty`, `origin_kind` 같은 컬럼이 없다.
7. **Broad skill mount**: `executor.py`는 agent에 skill이 하나라도 있으면 `["/skills/"]` 전체를 `FilesystemBackend`에 노출한다. 같은 사용자의 다른(미선택) skill도 LLM이 `read_file`로 읽을 수 있다.
8. **`execute_in_skill` env에 credential 미주입**: `_create_skill_execute_tool`의 env dict는 `PATH`, `PYTHONPATH`, `HOME`, `SKILL_OUTPUT_DIR`, `OUTPUTS_DIR`만 포함하고 credential mapped env var(예: `KSKILL_SRT_ID`)는 없다.
9. **Secret scan 부재**: `packager.py`는 symlink/zip-slip/null-byte/50MB 제한만 검사하고 `.env`, `*.pem`, token 패턴 등을 차단하지 않는다.
10. **공유 시 private data 누출 위험**: credential 값, 대화, token usage, private config가 섞여 나갈 수 있다.

### 원하는 상태

- 사용자는 자신이 만든 Agent/MCP/Skill을 marketplace에 올릴 수 있다.
- 공유 범위는 private, 특정 사용자 제한, 전체 공개(listed/unlisted), built-in/system을 지원한다.
- 다른 사용자는 marketplace 항목을 자기 계정으로 설치할 수 있다.
- built-in k-skill 항목은 super_user가 upstream에서 동기화하고, 사용자는 필요한 skill만 선택 설치한다.
- Skill이 credential을 필요로 하면 marketplace와 설치 화면에 명확히 표시된다.
- credential 값은 절대 marketplace package에 포함되지 않고, 사용자별 `credentials`로 등록해 연결한다.
- 설치된 resource는 원본 marketplace version과 연결되어 업데이트 가능 상태를 표시한다.
- agent runtime은 **선택된 skill만 노출**하고, credential은 **subprocess env로만 주입**한다.

## 3. 목표

1. Agent, MCP, Skill을 하나의 marketplace resource model로 공유한다.
2. 기존 사용자 소유 테이블(`agents`, `mcp_servers`, `skills`)을 실제 설치본으로 유지한다.
3. Marketplace item/version을 배포 가능한 immutable snapshot으로 관리한다.
4. 공개 범위와 특정 사용자 접근 제어를 지원한다.
5. Built-in k-skill을 system marketplace item으로 가져온다.
6. Skill credential requirements와 user credential binding을 도입한다 (Phase 1).
7. 공유/설치/업데이트 흐름에서 secret이 유출되지 않도록 한다 (packager에 secret scan 추가, publish 시 strip).
8. `execute_in_skill` 실행 시 selected-skill mount + credential env 주입 + redaction을 보장한다.

## 4. 비목표

- **Tool 마켓플레이스**: natural-mold의 도구는 `backend/app/tools/registry.py`의 메모리 기반 `ToolDefinition`으로 운영자가 코드로 정의한다. 사용자가 만들거나 공유하지 않는다. 사용자 정의 도구가 필요해지면 별도 PRD.
- **새 skill runner 도입**: 이미 `execute_in_skill` subprocess runner가 있으므로 새로 만들지 않는다. Node/curl 같은 추가 runtime은 향후 별도 ADR.
- 결제·유료 마켓플레이스
- 별점·리뷰·랭킹 알고리즘
- 외부 공개 URL로 anonymous 실행
- 조직/팀 단위 권한 모델
- Agent 실행 결과, 대화 기록, token usage 공유
- Skill code sandbox 완전 격리 (Phase 1에서는 현재 allowlist + 30초 timeout 유지)
- k-skill upstream에 PR을 보내거나 upstream 코드를 natural-mold용으로 수정

## 5. 사용자 유형

| 사용자 | 설명 | 주요 행동 |
|--------|------|-----------|
| 일반 사용자 | natural-mold 계정 (`users` row, `is_super_user=False`) | marketplace 탐색, resource 설치, 내 resource 게시, restricted 공유 대상 지정 |
| 제작자 | Agent/MCP/Skill을 만들어 공유하는 사용자 | version publish, 공개 범위 설정, 업데이트 게시 |
| 설치자 | 다른 사용자의 resource를 가져와 쓰는 사용자 | credential 연결, agent에 연결, 업데이트 적용 |
| super_user | 운영자/관리자 (`users.is_super_user=True`) | built-in/system resource sync, public item의 listed 승인/disable, system credential 관리, k-skill 동기화 실행 |

## 6. Resource 개념

### Installed Resource

사용자의 계정에 실제로 설치되어 실행되는 리소스다. 기존 테이블을 그대로 사용한다.

- Agent: `agents`
- MCP: `mcp_servers`, `mcp_tools`
- Skill: `skills`

Installed resource는 `user_id`를 가진다. 사용자는 자기 installed resource만 수정·삭제·agent에 연결할 수 있다.

Phase 1에서 `skills` 테이블에 추가할 컬럼 (모두 nullable, backfill):

- `is_system` (BOOLEAN DEFAULT FALSE) — 시스템 시드와 동일 패턴
- `source_kind` (VARCHAR 40) — `user`, `k-skill`, `import`, `system_seed`
- `source_marketplace_item_id` (UUID NULL FK)
- `source_marketplace_version_id` (UUID NULL FK)
- `source_commit` (VARCHAR 80 NULL)
- `credential_requirements` (JSON NULL) — version에서 복사한 fast-display copy
- `execution_profile` (JSON NULL) — version에서 복사
- `origin_kind` (VARCHAR 40 DEFAULT 'created_by_me')
- `origin_user_id` (UUID NULL FK users)
- `origin_marketplace_item_id` (UUID NULL FK)
- `origin_marketplace_version_id` (UUID NULL FK)
- `is_dirty` (BOOLEAN DEFAULT FALSE) — installed resource 수정 추적 (텍스트 content/files endpoint에서 설정)

`AgentSkillLink`에 추가할 컬럼:

- `config` (JSON NULL) — `{ "credential_bindings": { "<requirement_key>": "<credential_id>" } }` 형태의 agent-skill 단위 override

### Marketplace Item

공유 가능한 logical 항목이다. 하나의 item은 여러 version을 가진다.

- `resource_type`: `agent | mcp | skill`
- 작성자: 일반 사용자 또는 system (super_user)
- 공개 범위: private, restricted, public, unlisted, system
- 검색 카탈로그 노출 여부: `is_listed` (super_user만 토글)
- 검색/카탈로그 표시용 metadata 포함

### Marketplace Version

설치 가능한 immutable snapshot이다.

- Skill: `.skill` package snapshot 또는 storage snapshot
- MCP: MCP server template + auth requirements + tool discovery hints (Phase 2)
- Agent: agent spec + tool/MCP/skill references + model preference (Phase 3)

한번 publish된 version은 수정하지 않는다. 변경이 필요하면 새 version을 만든다. 오타 같은 메타데이터 수정은 item-level metadata 업데이트로 처리한다.

### Installation

사용자가 marketplace version을 자기 계정으로 가져온 기록이다.

- source item/version 추적
- 설치된 `agents.id`, `mcp_servers.id`, `skills.id`와 연결
- 업데이트 가능 여부 판단(`update_available`), dirty 상태(`is_dirty`) 추적

### Resource Origin

Installed Agent/MCP/Skill 목록과 상세 화면은 사용자가 리소스의 출처를 즉시 이해할 수 있게 표시해야 한다.

| Origin | 의미 | 표시 예시 |
|--------|------|-----------|
| `created_by_me` | 사용자가 직접 생성한 리소스 (text skill 등) | `Created by me` |
| `imported_by_me` | 사용자가 파일/URL로 가져온 리소스 (예: `.skill` ZIP 업로드) | `Imported by me` |
| `built_in_k_skill` | system marketplace의 k-skill에서 설치한 skill | `Built-in · k-skill` |
| `shared_with_me` | 다른 사용자가 restricted/public으로 공유한 리소스를 설치 | `Shared by {user}` |
| `community` | 공개 marketplace item을 설치 | `Community` |
| `system_seed` | 시스템 시드(`bootstrap_from_env`)가 기본 제공한 리소스 | `System` |

Origin은 ownership을 바꾸지 않는다. `Built-in · k-skill`로 표시되더라도 설치된 skill row는 설치자 소유다.

### Publication State

내가 소유한 Agent/MCP/Skill은 marketplace에 게시된 상태를 별도로 가진다.

| Publication State | 의미 | 사용자 액션 |
|-------------------|------|-------------|
| `not_published` | marketplace item 없음 | Publish |
| `draft` | 게시 준비 중, owner만 접근 | Continue setup |
| `published_private` | marketplace item은 있으나 owner만 접근 | Change visibility, New version |
| `published_restricted` | 특정 사용자에게 공유됨 | Manage users, New version |
| `published_public_listed` | 공개 + 검색 노출 (super_user 승인 통과) | New version, Disable |
| `published_public_unlisted` | 공개됐지만 검색 노출 안 됨 (super_user 미승인) | Request listing, Copy link |
| `published_unlisted` | 명시적으로 unlisted 선택 (링크 접근만) | Copy link, Change visibility |
| `disabled` | super_user가 게시 중단 | Re-enable 또는 keep disabled |

Installed resource 화면에서는 origin badge와 publication badge를 동시에 보여준다.

## 7. 공개 범위

| Visibility | 의미 | 접근 |
|------------|------|------|
| `private` | 작성자만 볼 수 있는 draft 또는 개인 item | owner만 view/manage |
| `restricted` | 특정 사용자만 볼 수 있음 (ACL) | owner + ACL 대상 |
| `public` | 모든 로그인 사용자가 볼 수 있음 (단, 검색 노출은 `is_listed`에 따름) | 모든 사용자 view/install |
| `unlisted` | 링크를 가진 사용자만 접근 | 검색에는 노출 안 됨 |
| `system` | 운영자가 제공하는 built-in catalog | 모든 사용자 view/install, super_user manage |

`restricted`는 user 단위 ACL로 시작한다.

### Published vs Listed 분리

`visibility=public`인 항목은 별도로 `is_listed` 플래그를 가진다.

- 기본값: `is_listed=False`
- `is_listed=True`는 super_user만 토글 가능
- 카탈로그의 기본 listing(`/api/marketplace/items` 기본 조회)은 `is_listed=True OR visibility=system OR owner=current_user OR ACL` 조건만 보여준다
- `is_listed=False`인 public item은 직접 ID/슬러그를 알면 접근/설치 가능 (unlisted와 동일한 효과)
- super_user가 부적절한 public 항목을 발견하면 `is_listed=False`로 unlist하거나 `status=disabled`로 비활성화한다

### 권한 정책 매트릭스

| Actor | private | restricted | public | unlisted | system |
|-------|---------|------------|--------|----------|--------|
| Owner | view/manage/install | view/manage/install | view/manage/install | view/manage/install | N/A |
| ACL view | no | view | N/A | no | N/A |
| ACL install | no | view/install | N/A | no | N/A |
| Any logged-in user | no | no | view(listed만 검색)/install | direct-link view/install | view/install |
| super_user | view/manage | view/manage | view/manage/list/disable | view/manage/list/disable | view/manage |

비인가 사용자가 item detail 또는 install을 시도하면 기본적으로 404로 응답해 private/restricted item 존재 여부를 노출하지 않는다 (CLAUDE.md `enumeration oracle` 방지 원칙).

## 8. Credential 원칙

natural-mold는 이미 ADR-007/009에 따라 `credentials` 테이블 + Cipher V2 + field_keys 캐시를 갖추고 있다. 마켓플레이스는 이 시스템을 그대로 재사용한다.

1. Marketplace item/version에는 credential value를 저장하지 않는다.
2. Skill/MCP/Agent는 필요한 credential type과 field requirement만 선언한다 (`credential_requirements` JSON).
3. 사용자는 설치 시 자기 `credentials` row를 선택하거나 새로 만든다.
4. 실행 시 runtime은 연결된 credential을 복호화해 해당 process/tool call에만 주입한다.
5. System credential(`is_system=True`, `user_id IS NULL`)은 일반 사용자에게 노출하거나 자동 연결하지 않는다 (`/api/system-credentials`는 super_user 전용).
6. Hosted proxy 기반 skill은 사용자 credential이 필요 없는 것으로 표시하되, operator/system dependency로 구분한다.

### Credential 정의 추가

`backend/app/credentials/definitions/` 폴더에 다음 정의를 신규 추가한다 (k-skill 요구사항). 기존 13개 정의는 그대로 유지.

| definition_key | Fields | Used by |
|----------------|--------|---------|
| `srt_account` | `username`, `password` | `srt-booking` |
| `ktx_account` | `username`, `password` | `ktx-booking` |
| `foresttrip_account` | `username`, `password` | `foresttrip-vacancy` |
| `kipris_plus_api` | `api_key` | `korean-patent-search` |
| `dart_api` | `api_key` | `k-dart` |
| `odsay_api` | `api_key` | `korean-transit-route` |
| `coupang_partners` | `access_key`, `secret_key` | `coupang-product-search` (optional) |
| `k_skill_proxy` | `base_url`, optional `api_key` | self-host proxy |

이들 정의는 기존 `CredentialRegistry` 싱글톤(import time 자동 등록)에 동일한 패턴으로 추가한다. field_keys 캐시(ADR-007)는 정의 추가 후 신규 row 생성 시 자동 채워진다.

### Credential UX 원칙

- Marketplace 카드에는 credential value가 아니라 requirement 상태만 표시한다.
- 설치 wizard는 required credential을 먼저 해결하게 하되, 사용자가 `나중에 설정`을 선택하면 `needs_setup` 상태로 설치할 수 있다.
- `needs_setup`인 skill은 agent에 연결할 수는 있지만 runtime 실행 전 fail-fast로 막고 설정 CTA를 보여준다.
- compatible credential이 이미 있으면 기본 선택값으로 제안하지만 자동 저장하지 않는다.
- optional credential은 설치를 막지 않고, 연결하면 더 풍부한 기능이 열린다.
- manual login skill은 Credential 저장 화면으로 보내지 않고, 브라우저/로컬 앱 세션이 필요하다는 상태로 분리한다.

## 9. Skill Credential Requirement

Skill은 다음 상태 중 하나를 가져야 한다.

| 상태 | 의미 | UX |
|------|------|----|
| none | 인증 불필요 | `No credential needed` |
| optional | 있으면 더 풍부한 경로 사용 | `Optional credential` |
| required | 실행 전 사용자 credential 필요 | `Credential required` |
| hosted_proxy | 운영자 proxy key 사용, 사용자 key 불필요 | `Uses hosted proxy` |
| manual_login | 사용자가 브라우저/앱에서 직접 로그인 필요 | `Manual login required` |

예시:

- `srt-booking`: `required`, `srt_account`
- `ktx-booking`: `required`, `ktx_account`
- `korean-patent-search`: `required`, `kipris_plus_api`
- `seoul-density`: `hosted_proxy`
- `kakaotalk-mac`: `manual_login`
- `korean-spell-check`: `none`

## 10. 주요 사용자 시나리오

### 10.1 built-in skill 설치

1. 사용자가 Marketplace > Skills에서 `korean-spell-check`를 검색한다.
2. 카드에 `Built-in`, `No credential needed`, `ko-KR`, `writing`이 표시된다.
3. 사용자가 Install을 누른다.
4. natural-mold가 해당 marketplace version을 사용자의 `skills` row로 설치한다 (storage는 `data/skills/<skill_id>/`).
5. `skills.origin_kind='built_in_k_skill'`, `source_marketplace_*` 컬럼이 채워진다.
6. 사용자는 agent 설정에서 해당 skill을 선택한다.

### 10.2 credential required skill 설치

1. 사용자가 `srt-booking`을 설치하려 한다.
2. 설치 화면이 `SRT account credential required`를 표시한다.
3. 사용자는 기존 `srt_account` credential을 선택하거나 새로 만든다.
4. 설치 후 `skill_credential_bindings` row가 생성된다.
5. Agent 실행 시 runtime은 binding된 credential을 복호화해 mapped env var(`KSKILL_SRT_ID`, `KSKILL_SRT_PASSWORD`)로 `execute_in_skill` subprocess에 주입한다.

### 10.3 사용자가 자기 skill 공유

1. 사용자가 `/skills`에서 자기 skill 상세를 연다.
2. `Publish to Marketplace`를 누른다.
3. 공유 전 검사 화면(secret scan + 파일 트리 preview)에서 name, description, tags, license, credential requirements, included files를 확인한다.
4. visibility를 `public` 또는 `restricted`로 선택한다.
5. publish하면 marketplace item/version이 생성된다 (`is_listed=False`로 시작).
6. 다른 사용자는 install해서 자기 skill copy로 가져온다.

### 10.4 특정 사용자에게만 공유

1. 제작자가 visibility를 `restricted`로 선택한다.
2. 허용할 사용자 이메일을 입력한다.
3. `marketplace_item_acl` row가 생성된다.
4. 대상 사용자만 marketplace에서 item을 볼 수 있고 설치할 수 있다.
5. 대상에서 제거되면 새 설치는 막히지만 이미 설치된 copy는 유지한다.

### 10.5 built-in k-skill 업데이트

1. super_user가 k-skill sync job을 실행한다 (`uv run python -m app.scripts.sync_k_skill --ref <commit>`).
2. job은 upstream commit을 fetch하고 skill layout을 validate한다.
3. 변경된 skill만 새 marketplace version으로 등록한다.
4. 기존 설치자는 `Update available` 배지를 본다.
5. 사용자가 업데이트를 적용하면 installed skill storage가 새 snapshot으로 교체된다.
6. 사용자가 직접 수정한 installed skill은 `is_dirty=True`로 표시되고 자동 덮어쓰지 않는다.

### 10.6 내 리소스 게시 상태 관리

1. 사용자가 `/skills`, `/mcp-servers`, dashboard에서 자기 리소스를 본다.
2. 각 row/card에 origin badge와 publication badge가 표시된다.
3. 사용자가 직접 만든 리소스에는 `Not published` 또는 `Published · Restricted` 같은 publication badge가 표시된다.
4. 사용자가 `Publish`를 누르면 marketplace publish wizard가 열린다.
5. publish 후 기존 리소스 상세에 marketplace 상태와 version/update 액션이 표시된다.

### 10.7 super_user가 public 항목을 listed 승인

1. super_user가 `Marketplace > Moderation`에서 `is_listed=False`인 public 항목 목록을 본다.
2. 항목을 열어 metadata, files, credential requirements, 작성자를 확인한다.
3. `Approve listing`을 누르면 `is_listed=True`로 바뀌고 카탈로그 기본 검색에 노출된다.
4. 부적절한 항목은 `Disable`로 `status=disabled` 처리한다 (새 설치 차단).

## 11. 기능 요구사항

### 11.1 Marketplace Catalog

- resource type별 목록 제공: Agent, MCP, Skill
- 검색: name, description, tag, category, locale
- 필터: resource type / install state / source / visibility / built-in/system / credential status / execution support level / category / locale
- 카드 표시: 이름, 설명, 작성자, resource type, visibility, latest version, credential requirement summary, installed/update available 상태, listed/unlisted 배지(public 한정)

Catalog는 사용자가 바로 설치 가능한 항목과 아직 실행 지원이 제한적인 항목을 구분해야 한다. 특히 k-skill은 80개 전체를 가져오더라도 `Python ready`, `Proxy required`, `Node required`, `Manual login`, `Unsupported` 같은 badge를 표시해 기대치를 정확히 맞춘다.

기본 탭: `All`, `Agents`, `MCP`, `Skills`, `Installed`

설치 상태별 빠른 보기: `Not installed`, `Installed`, `Needs setup`, `Update available`, `Disabled source`

카드 CTA:

| 상태 | Primary CTA |
|------|-------------|
| 미설치 | Install |
| 설치됨 | Installed 또는 Open |
| needs setup | Set up |
| update available | Update |
| dirty + update available | Review update |
| manual/unsupported | View details |

### 11.2 Publish

- 사용자는 자기 owned resource를 marketplace에 publish할 수 있다.
- publish 전 preview에서 제외되는 private data와 secret scan 결과를 보여준다.
- version은 immutable로 저장한다.
- 같은 item에 새 version publish 가능.
- public publish는 최소 metadata validation을 통과해야 한다.
- restricted publish는 ACL 대상이 1명 이상이어야 한다.
- public publish는 `is_listed=False`로 시작한다. 사용자는 super_user에게 listing 요청을 보낼 수 있다.
- publish는 단순 toggle이 아니라 preview, secret scan, credential stripping, immutable version 생성 과정을 거친다.

### 11.3 Install

- 사용자는 접근 가능한 marketplace version을 install할 수 있다.
- 설치 결과는 사용자 소유 installed resource다 (`skills.user_id = current_user.id`).
- 설치 시 credential requirements를 확인하고 binding wizard를 제공한다.
- 설치 후 source item/version reference를 보존한다.
- 같은 item은 여러 번 설치할 수 있되 기본 UX는 기존 설치를 보여주고 update를 유도한다.

### 11.4 Update

- installed resource가 source version보다 오래되면 update available로 표시한다.
- 사용자가 직접 수정한 installed resource는 `is_dirty=True` 상태다. dirty 추적은 다음 endpoint에서 설정한다:
  - `PUT /api/skills/{id}/content`
  - `PATCH /api/skills/{id}`
  - `PUT|POST|DELETE /api/skills/{id}/files/{path}`
- dirty 상태 update는 다음 중 하나를 선택한다:
  - overwrite with latest
  - install as new copy
  - keep current
- Phase 1에서는 자동 병합을 하지 않는다.

### 11.5 Credential Binding

- Marketplace version은 credential requirements를 가진다 (`marketplace_versions.credential_requirements` JSON).
- 설치 시 required credential이 없으면 `needs_setup` 상태가 된다.
- 사용자는 설치 후에도 credential을 연결/교체할 수 있다.
- Binding scope:
  - skill installation default → 신규 `skill_credential_bindings` 테이블
  - agent-skill override → 신규 `agent_skills.config` JSON 필드 (현재 없음, 추가 필요)
- agent-skill override가 있으면 runtime은 override를 우선한다.

### 11.5b Runtime Skill Mount and Credential Injection

natural-mold는 이미 deepagents 기반이며 `execute_in_skill` subprocess runner가 동작 중이다 (`executor.py:113-195`). Phase 1에서 이 코드에 세 가지 빈 구멍을 메운다.

1. **Selected-skill mount**: 현재 `executor.py`는 agent에 skill이 하나라도 있으면 `skills=["/skills/"]`로 `FilesystemBackend(_DATA_DIR, virtual_mode=True)`에 broad mount한다. Phase 1에서는 per-thread 가상 root(예: `data/runtime/<thread_id>/skills/<slug>/`)에 선택된 skill만 복사하거나 심볼릭링크하고, agent에는 `/runtime/<thread_id>/skills/`만 노출한다. `_create_skill_execute_tool`의 `skill_directory` 검증도 이 runtime root 하위인지 확인한다. 미선택 skill 디렉토리와 다른 사용자의 skill은 절대 노출되지 않는다.

2. **Credential env injection**: 현재 `_create_skill_execute_tool`의 env dict는 `PATH`, `PYTHONPATH`, `HOME`, `SKILL_OUTPUT_DIR`, `OUTPUTS_DIR`만 포함한다. Phase 1에서는 skill의 credential requirement와 user binding을 로드해 복호화한 값을 mapped env var(예: `KSKILL_SRT_ID`)로 env dict에 추가한다. 주입 대상은 binding된 requirement에 한정한다. agent-skill override가 있으면 그 값을 우선한다.

3. **Redaction contract**: log·SSE event(`streaming.py`)·tool result(`execute_in_skill` 반환값)·exception detail·frontend toast에서 mapped env var 값과 다음 키들의 값을 redact한다: `password`, `api_key`, `secret`, `token`, `access_key`, `refresh_token`. 전용 redaction helper를 공유한다.

이 세 작업은 보안 영향이 크므로 marketplace 카탈로그/publish 작업과 분리된 별도 슬라이스로 검증한다. 새 subprocess runner를 도입하지 않고, 현재 코드의 env dict와 mount 검증 로직을 보강하는 방향이다.

### 11.6 k-skill Built-in Sync

- upstream repo: `https://github.com/NomaDamas/k-skill.git`
- 운영 설정:
  - `k_skill_upstream_url`: 기본 GitHub URL
  - `k_skill_upstream_ref`: 동기화할 commit 또는 branch (`main`이 기본)
  - `k_skill_sync_dir`: upstream을 clone할 임시 경로 (`./data/upstreams/k-skill`)
  - `k_skill_builtin_storage_dir`: 검증 완료 후 marketplace storage 경로 (`./data/marketplace/k-skill`)
- sync job은 upstream을 read-only로 사용한다. upstream 코드를 수정하지 않는다.
- 실행:
  - `uv run python -m app.scripts.sync_k_skill --ref main`
  - `uv run python -m app.scripts.sync_k_skill --ref <commit> --dry-run`
- 각 root skill directory를 marketplace skill item/version으로 upsert한다.
- 검증: SKILL.md 존재, frontmatter `name`이 디렉토리명과 일치 등 (원본 spec 5.2 그대로).
- unsupported or restricted skill은 catalog에 표시하되 execution support level을 명시한다 (`ready_python`, `proxy_http`, `node_package`, `browser_or_local`, `manual_only`, `disabled`).
- disabled upstream skill은 `deprecated` 또는 `disabled`로 표시한다.
- idempotent: 같은 commit으로 다시 실행하면 새 version이 생기지 않는다 (content_hash 비교).
- 한 skill의 validation 실패가 전체 sync를 막지 않는다.
- credential requirement 매핑은 curated map(`K_SKILL_REQUIREMENT_MAP` 등)이 source of truth. regex는 review signal로만 사용.

### 11.7 운영자 관리

- super_user는 system/built-in item을 생성·sync·disable할 수 있다.
- super_user는 사용자 credential을 복호화하거나 대신 연결할 수 없다 (Cipher V2 활성 키 접근권만 가지고, multi-tenant 정책상 다른 사용자 credential은 별도 ACL 없이 열람 불가).
- public item이 부적절하면 super_user가 `disabled` 처리할 수 있다.
- disabled item은 새 설치가 막히고, 기존 설치본은 계속 사용자 소유 copy로 남긴다.
- super_user는 public item의 `is_listed`를 토글한다 (검색 카탈로그 노출 승인/해제).
- system item에는 source URL, source commit, sync time, support level을 표시한다.
- 신규 `/api/marketplace/admin/*` 라우터는 `Depends(require_super_user)`로 보호한다.

### 11.8 사용자 알림과 상태

- `installed`: 이미 설치됨
- `needs_setup`: 설치됐지만 required credential 미연결
- `update_available`: 최신 version 존재
- `dirty`: 사용자가 설치본을 수정해 업데이트 시 충돌 가능
- `disabled_source`: 원본 marketplace item이 disabled/deprecated
- `unlisted_public`: 사용자가 publish한 public 항목인데 아직 super_user 승인 전이라 카탈로그에 노출되지 않음

상태는 서로 중첩될 수 있다.

### 11.9 Installed Resource 화면 요구사항

`/skills`, `/mcp-servers`, agent dashboard는 marketplace와 독립된 내 리소스 관리 화면이지만, marketplace 출처와 게시 상태를 보여줘야 한다.

공통 표시:

- origin badge
- publication badge
- marketplace source item/version
- update available
- needs setup
- disabled/deprecated source

Skill 목록 권장 컬럼: Name / Kind / Origin / Marketplace / Credential / Used by / Updated
MCP 목록 권장 컬럼: Name / Transport / Origin / Marketplace / Credential / Health / Updated
Agent dashboard/card 권장 badge: Origin / Published visibility / Shared·Installed source / Needs setup / Update available

### 11.10 Marketplace UI Reference

Marketplace UI는 기존 natural-mold의 dashboard/table/card/dialog-shell 패턴(`frontend/src/components/`)을 유지하되, 다음 오픈소스 사례를 참고한다.

| Reference | 참고 포인트 |
|-----------|-------------|
| Cal.com App Store | app install 여부, app package 구조, PR/review 기반 publish 흐름 |
| Dify Marketplace | resource type별 탐색, Marketplace/GitHub/local package 설치 source 구분 |
| Open VSX Registry | versioned extension registry, web UI + publish CLI + server separation |
| shadcn/ui ecosystem | 현재 UI stack과 맞는 compact card, filter, dialog pattern |

UI를 통째로 가져오지 않는다. 정보 구조, 상태 표시, 설치·게시 흐름만 참고하고 natural-mold의 기존 sidebar, DataTable, card, dialog-shell 패턴(ADR-010)에 맞춘다.

## 12. 정책 요구사항

### Secret Safety

- Credential data는 marketplace payload에 포함 금지.
- `.env`, token, cookie, local secret 파일은 package에 포함 금지.
- import/publish 시 secret-like filenames와 patterns를 검사한다 (`secret_scan.py` 신규 모듈).
  - 파일명: `.env`, `.env.*`, `secrets.env`, `*.pem`, `*.key`, `*.p12`, `cookies*`, `token*`
  - 내용 패턴: `sk-[A-Za-z0-9]`, `-----BEGIN PRIVATE KEY-----`, `AWS_SECRET_ACCESS_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`
- MCP export는 credential reference를 제거하고 requirement로 변환한다.
- runtime log·SSE event·tool result·toast는 mapped env var 값을 redact한다.
- 현재 `packager.py`에는 secret scan이 없으므로 신규 모듈로 추가해 publish/import 양방향에서 호출한다.

### Access Control

- private item은 owner만 접근.
- restricted item은 owner와 ACL 대상만 접근.
- public/system item은 로그인 사용자 모두 접근(검색 노출은 `is_listed`에 따름).
- system item manage는 super_user만 가능.
- 설치된 resource는 설치자 소유이며 원작성자가 직접 수정할 수 없다.
- runtime은 agent에 선택된 skill만 mount root에 노출한다.
- 비인가 접근은 404(enumeration oracle 방지).

### Mutability

- Marketplace item metadata는 owner가 수정할 수 있다.
- Marketplace version payload는 immutable.
- Installed resource는 사용자가 자유롭게 수정할 수 있다 (단, `is_dirty=True`로 마킹됨).
- Source version update는 명시적 사용자 동작으로만 적용한다.

## 13. 성공 지표

- built-in k-skill 중 최소 10개를 catalog에서 설치 가능
- credential required skill 설치 시 누락 credential을 100% 감지
- public/restricted/private access control 테스트 통과
- marketplace install 후 기존 agent 설정에서 skill 선택 가능
- shared skill publish/install E2E 성공
- credential value가 marketplace payload/API 응답에 노출되지 않음
- agent runtime에서 선택되지 않은 skill의 파일/디렉토리에 접근 불가
- `execute_in_skill` subprocess env에 mapped env var 값이 주입되지만 log·SSE·tool result에는 redact

### Phase 1 출시 게이트

Phase 1은 다음 조건을 모두 만족해야 사용자에게 노출한다.

| Gate | 통과 조건 |
|------|-----------|
| Access control | private/restricted/public/system item에 대해 owner, ACL user, unrelated user 테스트 통과. 비인가 접근은 404 |
| Secret safety | publish/import payload, API 응답, log·SSE·tool result에서 credential value 미노출. `secret_scan.py`가 `.env`/PEM/sk- 패턴 등을 차단 |
| Runtime isolation | agent에 선택된 skill만 per-thread runtime root에 노출됨. 미선택 skill 디렉토리·다른 사용자 skill 접근 불가. `execute_in_skill`이 runtime root 하위만 허용 |
| Credential runtime | required binding 누락 시 실행 차단(`marketplace_credential_required` 에러), binding 존재 시 mapped env var에만 주입. log·SSE·tool result에는 redact 후 노출 |
| k-skill sync | dry-run 결과와 실제 sync 결과가 변경 skill/version을 정확히 보고. 같은 commit 재실행 시 신규 version 생성 없음. 한 skill 실패가 전체 sync를 중단하지 않음 |
| Backward compatibility | 기존 skill upload/edit/delete, agent skill 연결, `/api/skills` 응답이 회귀 테스트 통과. `is_dirty` 추가가 기존 편집 UX를 깨지 않음 |
| Listing 승인 | public 항목은 `is_listed=True`로 토글되기 전까지 카탈로그 기본 검색에 노출되지 않음. super_user 토글이 정상 동작 |
| ADR-016 정합 | 모든 신규 라우터가 `get_current_user` 또는 `require_super_user` 의존성을 가짐. 상태 변경은 CSRF 검증 |

### 사용성 성공 기준

- 사용자가 credential 없는 built-in skill을 3분 이내 설치해 agent에 연결할 수 있다.
- credential required skill 설치 중 필요한 credential type과 field를 혼동하지 않는다.
- restricted 공유를 받은 사용자가 별도 설명 없이 Marketplace에서 item을 발견하고 설치할 수 있다.
- update available 상태에서 overwrite와 install new copy의 차이를 UI 문구만으로 이해할 수 있다.
- super_user가 미승인 public 항목 목록을 한 화면에서 확인하고 listing 승인/거부할 수 있다.

## 14. 단계별 출시

### Phase 1: Skill Marketplace Foundation

- Marketplace item/version/install schema (Alembic 마이그레이션)
- `skills` 테이블 컬럼 확장 (origin, source_marketplace_*, is_dirty, credential_requirements 등)
- `AgentSkillLink.config` 필드 추가
- `skill_credential_bindings` 테이블
- 신규 credential definitions(`srt_account`, `ktx_account`, `kipris_plus_api` 등) 추가
- `secret_scan.py` 모듈
- Skill publish/install/update API
- Visibility + ACL + `is_listed`
- Credential requirements + binding API
- Built-in k-skill sync CLI + system item
- **Runtime selected-skill mount fix** (`executor.py` + `_create_skill_execute_tool`)
- **Runtime credential env injection** + redaction contract
- Marketplace Skills UI + install/publish wizard
- super_user listing 승인/disable 도구

Phase 1에서 제외:

- MCP/Agent marketplace 실제 설치
- 결제, 리뷰, 랭킹
- 자동 update/merge
- 완전한 script sandbox (현재 allowlist + 30초 timeout 유지)
- 조직/팀 ACL
- subprocess runner 외 새 runner (Node/curl 등)

### Phase 2: MCP Marketplace

- MCP server template publish/install
- MCP credential requirements (env_vars/headers의 `{{$credentials.x}}` 보간 자리를 marketplace requirement로 변환)
- MCP discovery result snapshot
- MCP import/export secret stripping
- 기존 `mcp_servers.is_system`(M26)과 marketplace system item 조화

### Phase 3: Agent Marketplace

- Agent spec publish/install
- Required tools/MCP/skills dependency graph
- Bundle install wizard
- Model/credential rebinding (ADR-013 우선순위 재해석)

### Phase 4: Curation and Governance

- moderation status (현 `is_listed`를 확장)
- deprecate/disable versions
- usage analytics
- user-facing update notes
- 카테고리 큐레이션 (운영자 추천 컬렉션)

## 15. Open Questions와 권장 답

1. **public publish를 모든 사용자에게 열 것인가, super_user approval을 거칠 것인가?**
   → published vs listed를 분리한다. 누구나 publish할 수 있되 `is_listed=True`는 super_user만 토글한다. 본 PRD §7, §11.2, §11.7에 반영됨.

2. **restricted item의 접근권 회수 시 이미 설치된 copy를 유지할 것인가 삭제·비활성화할 것인가?**
   → 유지한다. 회수는 새 설치만 막는다. 후속에서 revoke propagation을 옵션으로 추가할 수 있다.

3. **system credential을 사용하는 hosted proxy skill을 일반 사용자에게 어떻게 비용 제한할 것인가?**
   → 별도 rate limit과 system dependency 표시를 둔다. 구체 수치는 운영 데이터 확보 후 결정.

4. **Skill script execution은 Python-only로 유지할 것인가, Node/curl runner를 추가할 것인가?**
   → 이미 Python subprocess runner(`execute_in_skill`)가 동작 중이다. Phase 1은 그 위에 credential 주입과 selected-skill mount를 추가한다. Node/curl runner는 별도 ADR로 분리한다.

5. **k-skill 전체 80개를 모두 catalog에 표시할 것인가, supported subset부터 공개할 것인가?**
   → 전체 catalog를 표시하되 execution support level로 기대치를 명확히 한다. super_user는 first-wave(Python ready / hosted proxy / required credential with clear schema)를 우선 `is_listed=True`로 승인하고 나머지는 unlisted로 둔다.

## 16. 참고 자료

- 원본 PRD: `/Users/chester/dev/natural-mold/docs/maketplace/marketplace-resources-prd.md` v0.3
- 원본 Spec: `/Users/chester/dev/natural-mold/docs/maketplace/marketplace-resources-spec.md` v0.3 (후속 자체 spec 작성 시 베이스)
- ADR-007: Credentials field_keys Cache
- ADR-009: Greenfield Credentials (Cipher V2, multi-key rotation)
- ADR-013: Service-side LLM Key from Credentials
- ADR-016: Multi-user Auth
- 기존 코드 모듈:
  - `backend/app/agent_runtime/executor.py` (특히 `_create_skill_execute_tool` line 113-195, `build_agent` line 198+)
  - `backend/app/skills/` (service, packager, inspector, runtime, prompt)
  - `backend/app/mcp/` (client, discovery)
  - `backend/app/credentials/` (definitions, interpolation, external_secrets)
  - `backend/app/security/cipher.py`
  - `backend/app/auth/`, `backend/app/dependencies.py`
- k-skill upstream: `https://github.com/NomaDamas/k-skill`
- UI 패턴 참고: Cal.com App Store, Dify Marketplace, Open VSX Registry, shadcn/ui

---

## 후속 작업 (이 PRD 범위 밖)

이 PRD가 합의되면 다음 산출물을 별도 세션에서 작성한다.

- `docs/marketplace-resources-spec.md`: 정확한 SQL 스키마, Alembic 마이그레이션 순서(`m40` 이후), API endpoint 시그니처, Pydantic 모델, `_create_skill_execute_tool` 패치 디테일, per-thread runtime root 구조, `secret_scan.py` 패턴, k-skill importer 모듈 구조, 신규 credential definition Python 모듈
- ADR: Skill runtime mount/credential injection 변경 (보안 영향이 크므로 PR/리뷰 분리). ADR-001/003/012 연관
- 마이그레이션 작업: `skills` 컬럼 추가 + `agent_skills.config` 추가 + 신규 marketplace_* 테이블 4개 (item / version / installation / acl) + `marketplace_publication_links` + `skill_credential_bindings`
