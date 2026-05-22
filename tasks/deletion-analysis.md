# Deletion Analysis — Marketplace Resources Phase 1

> Author: 베조스 (Bezos / QA)
> Date: 2026-05-18
> Story: M1-S1
> Source: PRD v0.2 §2/§11.5b/§13, Spec v0.1 §1/§3/§4
> 검증 기준: 모든 파일/라인 인용은 worktree HEAD(`worktree-marketplace-resources`) 기준으로 grep 또는 Read 직접 확인.

> **참고**: 이전 세션(Greenfield Credentials, ADR-009)의 deletion-analysis는 `tasks/deletion-analysis-multiuser-auth.md`, `tasks/archive-multiuser-auth-2026-05/`에 보존되어 있다. 본 파일은 Marketplace Phase 1 전용으로 덮어쓴다.

---

## 0. 한 줄 요약

> 마켓플레이스는 "새 기능"이 아니라 **이미 작동 중인 skill runtime의 6가지 빈 구멍을 메우는 작업**이다. 죽일 코드는 거의 없고, 메울 구멍은 정확히 6 곳이다.

핵심 결론:
- (a) `executor.py:548` broad mount `["/skills/"]`은 **반드시** per-thread root로 교체. 같은 사용자의 미선택 skill 접근이 가능한 데이터 격리 침해.
- (b) `executor.py:144-150` env dict에 **credential 미주입**. 현재 srt/ktx 등 credential-required skill은 실행 불가 상태(silent skip 아닌 미설계).
- (c) `packager.py`는 zip-slip/symlink/null/50MB만 검사. **secret scan 부재** — `.env`, `*.pem`, `sk-…` 패턴을 publish/import 양쪽에서 차단해야 함.
- (d) `Skill` ORM에 marketplace 추적 컬럼 0개 (m41이 12개 추가).
- (e) `AgentSkillLink`에 `config` 컬럼 0개 (m42 추가). agent-skill 단위 credential override 자리가 아예 없음.
- (f) `credentials/definitions/`에 k-skill용 8개 정의 부재.

**Musk Step 2 (삭제)**: grep 결과 진짜 dead code는 **없다**. legacy 단어는 주석/문서 내 historical context로만 사용. 후술 §2 참조.

---

## 1. 마켓플레이스가 메워야 할 빈 구멍 (Gap Inventory)

각 항목은 file_path:line_number와 grep으로 검증한 사실만 기재.

### (a) Broad `/skills/` mount — 데이터 격리 침해 🔴 High

| | |
|---|---|
| 위치 | `backend/app/agent_runtime/executor.py:544-548` |
| 슬라이스 매핑 | **E** (Runtime selected-skill mount) |
| 검증 | `grep -n "/skills/\|FilesystemBackend\|_DATA_DIR" backend/app/agent_runtime/executor.py` |
| 회귀 위험 | 🔴 High — 잘못 구현 시 conversation cross-leak, FilesystemBackend 캐시 무효화 |

확인된 코드:
```python
# executor.py:544
backend = FilesystemBackend(root_dir=str(_DATA_DIR), virtual_mode=True)
# executor.py:546-548
skills_sources: list[str] | None = None
if cfg.agent_skills:
    skills_sources = ["/skills/"]
```

문제:
- `_DATA_DIR` = `backend/data/`. backend root에 `data/skills/<skill_id>/`가 모두 모임.
- `["/skills/"]`로 mount하면 deep-agents Filesystem backend는 `_DATA_DIR/skills/` 전체를 LLM `read_file` 대상으로 노출.
- LLM은 agent에 attach된 skill_id뿐 아니라 **같은 사용자의 다른 skill** 디렉토리도 `read_file("/skills/<other-id>/SKILL.md")`로 읽을 수 있음.
- `_create_skill_execute_tool`도 같은 broad root 사용 (line 126): `(_DATA_DIR / skill_directory.strip("/")).resolve()` — 검증은 `_DATA_DIR` 밖만 차단, 다른 skill_id의 디렉토리는 허용.

검증 방법 (Slice E 완료 시):
- 두 사용자 A/B가 같은 server-side data 폴더를 공유한 상황에서, A의 agent가 B의 skill 디렉토리를 read_file로 읽을 수 없어야 한다 → `test_runtime_isolation.py::test_other_user_skill_unreachable`
- A가 자기 skill 두 개(S1, S2) 중 S1만 attach한 agent에서 `read_file("/skills/<S2>/SKILL.md")` → "Error: invalid skill directory" 또는 not-found
- `execute_in_skill(skill_directory="/skills/<S2>/")` → "Error: invalid skill directory"

### (b) `_create_skill_execute_tool` env에 credential 미주입 🔴 High

| | |
|---|---|
| 위치 | `backend/app/agent_runtime/executor.py:113-195` (특히 line 144-150) |
| 슬라이스 매핑 | **E** (Credential env injection) |
| 검증 | Read 직접 확인 |
| 회귀 위험 | 🔴 High — credential plaintext가 log/SSE/tool result/exception detail로 새지 않도록 redaction과 짝지어야 함 |

확인된 코드:
```python
# executor.py:144-150
env = {
    "PATH": "/usr/bin:/usr/local/bin",
    "PYTHONPATH": str(resolved),
    "HOME": str(resolved),
    "SKILL_OUTPUT_DIR": out,
    "OUTPUTS_DIR": out,
}
```

문제:
- subprocess env에 user credential의 mapped env var (예: `KSKILL_SRT_ID`, `KSKILL_SRT_PASSWORD`) 미주입.
- 따라서 srt-booking/ktx-booking/kipris-search 등 credential-required skill은 SKILL.md instruction을 따라도 실행 시 환경변수가 없어 401/None 반환.
- `_create_skill_execute_tool` 시그니처가 `(output_dir: Path, thread_id: str)`만 받음 — credential bundle을 받을 통로 없음.
- 호출처 단일: `executor.py:551 langchain_tools.append(_create_skill_execute_tool(conv_output_dir, cfg.thread_id))`. 시그니처 확장 시 이 한 곳만 패치.

검증 방법:
- `test_credential_injection.py::test_required_credential_injected_to_subprocess_env`: binding된 credential의 mapped env var 값이 subprocess env에 존재
- `test_credential_injection.py::test_unmapped_env_var_not_injected`: 다른 skill의 mapped var는 노출되지 않음
- `test_credential_injection.py::test_missing_required_credential_fails_fast`: needs_setup 상태에서 실행 시 `marketplace_credential_required` 에러 (Spec §10.7 에러 코드)
- `test_redaction.py::test_log_redacts_mapped_env_value`: log/SSE/tool result/exception detail에서 plaintext 미노출

### (c) `packager.py` secret scan 부재 🔴 High

| | |
|---|---|
| 위치 | `backend/app/skills/packager.py` (전체 175 lines) |
| 슬라이스 매핑 | **C** (Publish + secret scan) + **B** (import 시 회귀 가드) |
| 검증 | `grep -n "env\|pem\|sk-\|BEGIN PRIVATE\|secret_scan" backend/app/skills/packager.py` → 0 hits |
| 회귀 위험 | 🟡 Medium — 기존 upload 동작 변경. 정상 skill에서 false-positive 발생 시 사용자 차단 |

확인된 사실:
- `_validate_member`는 symlink, absolute path, null byte, path traversal만 검사 (line 65-78).
- `extract_package`는 50MB 제한 검사 (line 89-93).
- `.env`, `.env.local`, `*.pem`, `*.key`, `*.p12`, `cookies*`, `token*` 같은 secret-like filename **검사 없음**.
- `sk-[A-Za-z0-9]`, `-----BEGIN PRIVATE KEY-----`, `AWS_SECRET_ACCESS_KEY`, `GOOGLE_APPLICATION_CREDENTIALS` 등 내용 패턴 **검사 없음**.

문제:
- 사용자가 `.skill` package를 만들 때 실수로 `.env` 포함 → 그대로 marketplace publish → 다른 사용자가 install → credential 누출.
- 현재는 packager.py 자체에 검사 없고, 호출자(`routers/skills.py:64 upload_package_skill`)에도 secret scan 없음.

검증 방법:
- `test_secret_scan.py::test_dotenv_in_package_rejected`: `.env` 포함 publish → `marketplace_secret_detected` 400 (Spec §10.7)
- `test_secret_scan.py::test_pem_in_package_rejected`
- `test_secret_scan.py::test_sk_pattern_in_skill_md_rejected`
- `test_secret_scan.py::test_aws_secret_access_key_in_script_rejected`
- 회귀 가드: `test_upload_existing_package_still_succeeds`: 기존 정상 .skill 업로드는 secret_scan 도입 후에도 200

### (d) `Skill` 모델에 marketplace 추적 컬럼 부재 🟡 Medium

| | |
|---|---|
| 위치 | `backend/app/models/skill.py:43-101` |
| 슬라이스 매핑 | **A** (m41) |
| 검증 | `grep -n "is_dirty\|origin_kind\|source_marketplace" backend/app/models/skill.py` → 0 hits |
| 회귀 위험 | 🟡 Medium — 12 컬럼 추가 시 SkillResponse / to_runtime_dict 변경 없이도 동작해야 하지만, list API 응답 크기 증가 |

부재 컬럼 (PRD §6 + Spec §3.1 m41):
1. `is_system` BOOLEAN
2. `source_kind` VARCHAR(40)
3. `source_marketplace_item_id` UUID FK
4. `source_marketplace_version_id` UUID FK
5. `source_commit` VARCHAR(80)
6. `credential_requirements` JSON
7. `execution_profile` JSON
8. `origin_kind` VARCHAR(40) DEFAULT 'created_by_me'
9. `origin_user_id` UUID FK users
10. `origin_marketplace_item_id` UUID FK
11. `origin_marketplace_version_id` UUID FK
12. `is_dirty` BOOLEAN DEFAULT FALSE

영향 받는 호출처 (회귀 검증 필수):
- `app/skills/service.py:389 to_runtime_dict()` — 새 컬럼 미추가 시 deepagents 동작 그대로 (호환). 응답 키 unchanged 회귀 테스트 필요.
- `app/schemas/skill.py:50 SkillResponse` — origin_summary/publication_summary 신규 필드 추가 필요 (Spec §0.1 D8).
- `app/services/chat_service.py:523 build_agent_skills()` — 변경 불필요.
- `app/skills/runtime.py:19 build_skills_for_agent` — 변경 불필요.
- 기존 마이그레이션 최신: m39. m40~m43 이름 충돌 없음.

검증 방법:
- `test_marketplace_migration.py::test_upgrade_then_downgrade_m40_m43_reversible`
- `test_marketplace_migration.py::test_existing_skill_rows_backfilled` — backfill 시 `origin_kind`/`is_dirty` 채워짐
- `test_skills_api_unchanged_response.py::test_get_skill_still_returns_legacy_fields` — 기존 SkillResponse 응답 회귀 가드

### (e) `AgentSkillLink`에 `config` 컬럼 부재 🟡 Medium

| | |
|---|---|
| 위치 | `backend/app/models/skill.py:25-40` (AgentSkillLink는 별도 파일 아닌 skill.py 안에 정의됨 — progress.txt L42 정확) |
| 슬라이스 매핑 | **A** (m42) |
| 검증 | Read 직접 확인 — 현재 (agent_id, skill_id) composite PK만 |
| 회귀 위험 | 🟡 Medium — `write_tools.py:398` `AgentSkillLink(skill_id=s.id)` 호출 경로 영향 없음 (nullable JSON으로 추가) |

확인된 사실:
- `AgentSkillLink`는 PK 2개 + `skill: Mapped[Skill] = relationship(lazy="joined")` 뿐.
- 사용처 (grep `AgentSkillLink` --include="*.py"):
  - `app/models/agent.py:77` — `skill_links: Mapped[list[AgentSkillLink]] = relationship`
  - `app/agent_runtime/assistant/tools/write_tools.py:398` — `agent.skill_links.append(AgentSkillLink(skill_id=s.id))`
  - `app/agent_runtime/assistant/tools/helpers.py:35`, `app/services/chat_service.py:473,511` — selectinload eager load
  - `app/skills/runtime.py:19 build_skills_for_agent` — iterate
- 사용처 모두 `link.skill` 또는 PK 두 컬럼만 참조. `config` nullable JSON 추가 시 기존 호출 변경 없음.

문제 (PRD §11.5 Binding Scope):
- agent-skill 단위 credential override를 저장할 자리가 없음.
- 현재 디자인은 `agent_skills.config = {"credential_bindings": {"<requirement_key>": "<credential_id>"}}` (Spec §0.1 D3).
- runtime override 우선순위: agent-skill override > skill_credential_bindings default.

검증 방법:
- `test_agent_skill_config_override.py::test_override_takes_precedence_over_default_binding`
- `test_agent_skill_config_override.py::test_no_override_falls_back_to_default`

### (f) k-skill용 credential definitions 부재 🟡 Medium

| | |
|---|---|
| 위치 | `backend/app/credentials/definitions/` |
| 슬라이스 매핑 | **D** (Credential Definitions) |
| 검증 | `ls backend/app/credentials/definitions/` |
| 회귀 위험 | 🟢 Low — 추가만, 기존 definition 영향 없음 |

확인된 사실 — 현재 폴더에 13개 파일 (`__init__.py` 제외):
```
anthropic.py, azure_openai.py, google_genai.py, google_search.py,
google_workspace_oauth2.py, http_api_key.py, http_basic.py,
http_bearer.py, mcp_oauth2.py, naver_search.py, openai.py,
openai_compatible.py, openrouter.py
```

**⚠️ progress.txt 정정 필요**: progress.txt L38, L70(PRD)는 "14개"라 적었으나 실측 **13개**. PRD §6 표 L68도 14개 나열이라 정합 — 그러나 disk에는 13개 파일만 존재. `(한 개 더 — 확인 필요)` 표시도 동일. 사티아/젠슨에게 확정 보고 필요. (Open Item #OI-1)

추가 대상 (PRD §8):
1. `srt_account` — username, password (`srt-booking`)
2. `ktx_account` — username, password (`ktx-booking`)
3. `foresttrip_account` — username, password (`foresttrip-vacancy`)
4. `kipris_plus_api` — api_key (`korean-patent-search`)
5. `dart_api` — api_key (`k-dart`)
6. `odsay_api` — api_key (`korean-transit-route`)
7. `coupang_partners` — access_key, secret_key (`coupang-product-search`, **optional**)
8. `k_skill_proxy` — base_url, optional api_key (self-host proxy)

검증 방법:
- `test_credential_definitions.py::test_all_k_skill_definitions_registered`: import time 자동 등록 확인
- `test_credential_definitions.py::test_field_keys_cache_populated_on_create` (ADR-007 회귀)

---

## 2. Musk Step 2 — 삭제 가능 항목

**결론: 즉시 삭제 가능 항목 없음.**

검증 방법: grep `dead\|deprecated\|legacy\|TODO\|FIXME\|unused` 전체 스캔 + 호출처 확인.

### 검토 결과

| 후보 | 위치 | 판정 | 근거 |
|------|------|------|------|
| executor.py "legacy" 주석 | line 238, 311, 488, 614 | **유지** | 모두 historical context 설명 주석. 코드 자체는 활성 경로 |
| `auth_config["headers"]` fallback | `executor.py:236-239` `_auth_config_to_headers` | **유지** | M26 이전 legacy MCP server 데이터가 있을 수 있어 fallback 유지 (mcp_transport_headers는 신규 경로) |
| `skills/runtime.py` "legacy executor" 주석 | line 3 | **유지** | docstring 내 historical 설명. 코드 자체는 현재 경로 |
| `skills/service.py:3` "legacy ``content`` text column" 언급 | line 3 | **유지** | m18 greenfield 이후 historical context 설명 |
| `_DATA_DIR` 경로 검증 (`executor.py:127`) | broad mount과 함께 패치 대상 | **유지(수정)** | Slice E에서 per-thread root로 교체하지만 함수 자체는 보존 |
| skill_directory virtual path strip (`executor.py:126`) | broad mount과 함께 패치 대상 | **유지(수정)** | 동일 |

### 마켓플레이스 도입으로 obsolete 가능성 확인

- 별도 fallback 코드 없음. `Skill.user_id` NOT NULL 정책은 그대로 유지 (system은 별도 `is_system=True` 플래그, owner_user_id NULL은 `marketplace_items`에만 적용).
- `is_super_user` 권한 분기는 ADR-016에 이미 도입 — marketplace 라우터 재사용.
- `is_system` 패턴: `mcp_servers.is_system` (M26), `credentials.is_system` (ADR-009) 이미 존재. `skills.is_system`은 동일 패턴 추가.
- 기존 `seed/bootstrap_from_env.py`는 ENV → system credential. marketplace와 무관 — 유지.

### Step 3 (단순화) 제안

- `_create_skill_execute_tool` 시그니처가 Slice E에서 `(output_dir, thread_id, runtime_root, credential_env)` 등으로 확장됨. 인자 폭증을 막기 위해 `SkillToolContext` dataclass로 묶는 것을 권장.
- `executor.py:546-571` skill mount + prompt append 블록은 별도 helper(`_build_skill_runtime_context`)로 추출 권장 (테스트 단순화 + Slice E 패치 격리).

위 두 제안은 **제안만** — 본 분석 보고서는 deletion에 한정.

---

## 3. 회귀 위험 영역 (m41/m42/secret_scan 도입 시)

### 3.1 m41 — `skills` 테이블 12 컬럼 추가

영향 가능 위치 (grep 검증 완료):

| 호출처 | 위험 | 검증 방법 |
|--------|------|-----------|
| `app/skills/service.py:389 to_runtime_dict()` | 🟢 Low — 기존 키만 사용 | 회귀 테스트: `test_to_runtime_dict_keys_unchanged` |
| `app/schemas/skill.py:50 SkillResponse` | 🟡 Medium — 신규 origin_summary/publication_summary 추가 시 응답 shape 확장 | API contract 테스트, frontend 영향 (저커버그 통보) |
| `app/services/chat_service.py:523 build_agent_skills()` | 🟢 Low | 변경 불필요 |
| `app/skills/runtime.py:19 build_skills_for_agent` | 🟢 Low | 변경 불필요 |
| `app/skills/prompt.py:build_skills_prompt` | 🟢 Low — slug/description만 사용 | 회귀: `test_skills_prompt_block_unchanged` |
| `app/agent_runtime/assistant/tools/write_tools.py:398` | 🟢 Low — AgentSkillLink만 생성 | 변경 불필요 |
| 기존 skill upload 흐름 (`routers/skills.py:64`) | 🟡 Medium — 새 컬럼 default 값 채워야 함 | backfill + `test_legacy_upload_sets_origin_kind_created_by_me` |
| Alembic m18 greenfield 흔적 (`models/skill.py:9`) | 🟢 Low — docstring만 | 변경 불필요 |

**Gotcha (progress.txt L43-44에 이미 기록)**: m41에서 `origin_kind NOT NULL DEFAULT 'created_by_me'` 후 package skill을 `imported_by_me`로 backfill UPDATE 필수. 그렇지 않으면 모든 기존 package skill이 "created_by_me"로 잘못 표시됨.

### 3.2 m42 — `AgentSkillLink.config` JSON 추가

영향 가능 위치:

| 호출처 | 위험 |
|--------|------|
| `write_tools.py:398 AgentSkillLink(skill_id=s.id)` | 🟢 Low — `config`는 nullable JSON, default `None` |
| `chat_service.py:473,511 selectinload(AgentSkillLink.skill)` | 🟢 Low — `config` 추가 시 동일 쿼리, 자동 채워짐 |
| `runtime.py:19 build_skills_for_agent` | 🟡 Medium — Slice E에서 `link.config["credential_bindings"]`를 읽어 override 우선 적용해야 함 |
| `helpers.py:35 selectinload` | 🟢 Low |
| Agent settings PUT endpoint (skill 연결/해제) | 🟡 Medium — config 보존/교체 정책 필요 (Slice B/D에서 결정) |

### 3.3 secret_scan을 `routers/skills.py:upload`에 도입 시

`backend/app/routers/skills.py:64` upload 엔드포인트는 현재 packager.py만 호출. secret_scan을 끼우면 동작 변화 예상:

- ✅ 정상 `.skill` 업로드: 변경 없음
- ⚠️ `.env` 포함 패키지: 기존 200 → 400 `marketplace_secret_detected`
- ⚠️ `*.pem` 포함 패키지: 동일
- ⚠️ SKILL.md 또는 scripts/*.py에 `sk-…` 패턴: 400

회귀 가드 테스트 (필수):
- `test_secret_scan_upload_regression::test_legitimate_skill_still_uploads`
- `test_secret_scan_upload_regression::test_dotenv_inclusion_rejected_with_specific_error_code`
- `test_secret_scan_upload_regression::test_false_positive_rate_acceptable` (정상 docstring에 "sk-example"같은 placeholder는 통과)

**Gotcha**: false-positive는 사용자 차단으로 직결. `sk-`는 boundary 확인 (`\bsk-[A-Za-z0-9]{20,}\b`)으로 placeholder/예제 미차단. (Open Item #OI-4)

### 3.4 `_create_skill_execute_tool` 시그니처 변경 시

영향 위치 (grep 확인):
- `executor.py:551 langchain_tools.append(_create_skill_execute_tool(conv_output_dir, cfg.thread_id))` — 호출처 1곳만.
- 시그니처 확장(예: `runtime_root`, `credential_env` 추가) 시 이 한 군데만 수정. test에서 직접 호출하는 곳 없음 (`grep _create_skill_execute_tool backend/tests/` → 0 hits).

---

## 4. Phase 1 출시 게이트 매트릭스 (PRD §13)

| Gate | 통과 조건 | 담당 슬라이스 | 검증 테스트 |
|------|-----------|---------------|-------------|
| **Access control** | private/restricted/public/system 권한 매트릭스 통과, 비인가 접근 404 (enumeration oracle 방지) | A (catalog), B (install) | `test_marketplace_access.py::test_owner_acl_unrelated_user_matrix`, `test_marketplace_access.py::test_unauthorized_returns_404_not_403` |
| **Secret safety** | publish/import payload, API 응답, log·SSE·tool result에서 credential value 미노출. `secret_scan.py`가 `.env`/PEM/sk- 패턴 차단 | C (secret_scan), E (redaction) | `test_secret_scan.py::*`, `test_redaction.py::*`, `test_api_response_no_credential_value` |
| **Runtime isolation** | agent에 선택된 skill만 per-thread root 노출. 미선택 skill 디렉토리·다른 사용자 skill 접근 불가. `execute_in_skill`이 runtime root 하위만 허용 | E | `test_runtime_isolation.py::test_unselected_skill_unreachable`, `test_runtime_isolation.py::test_cross_user_skill_unreachable`, `test_runtime_isolation.py::test_execute_in_skill_rejects_outside_runtime_root` |
| **Credential runtime** | required binding 누락 시 실행 차단 (`marketplace_credential_required`), binding 존재 시 mapped env var에만 주입. log·SSE·tool result redact | D (binding), E (injection + redaction) | `test_credential_injection.py::test_missing_required_credential_fails_fast`, `test_credential_injection.py::test_only_mapped_env_var_injected`, `test_redaction.py::test_mapped_value_redacted_in_all_channels` |
| **k-skill sync** | dry-run 결과 = 실제 sync 결과. 같은 commit 재실행 시 신규 version 없음. 한 skill 실패가 전체 sync 중단하지 않음 | F (k_skill_importer) | `test_k_skill_importer.py::test_idempotent_same_commit`, `test_k_skill_importer.py::test_single_skill_failure_does_not_block_sync`, `test_k_skill_importer.py::test_dry_run_matches_real_run` |
| **Backward compatibility** | 기존 skill upload/edit/delete, agent skill 연결, `/api/skills` 응답 회귀 통과. `is_dirty` 추가가 기존 편집 UX 미파괴 | A (m41/m42 backfill) | `test_skills_api_regression.py::test_legacy_upload_still_works`, `test_skills_api_regression.py::test_get_skill_response_shape_preserved`, `test_agent_skill_link_creation_unchanged` |
| **Listing 승인** | public 항목은 `is_listed=True` 토글 전까지 카탈로그 기본 검색 미노출. super_user 토글 정상 | A (catalog filter) + admin router | `test_marketplace_listing.py::test_public_unlisted_hidden_from_default_search`, `test_marketplace_listing.py::test_super_user_can_toggle_is_listed`, `test_marketplace_listing.py::test_non_super_user_cannot_toggle_403` |
| **ADR-016 정합** | 모든 신규 라우터 `get_current_user` 또는 `require_super_user`. 상태 변경 CSRF 검증 | 모든 라우터 (A/B/C/D/F) | `test_marketplace_auth.py::test_every_mutation_router_has_csrf`, `test_marketplace_auth.py::test_admin_router_requires_super_user` |

### 슬라이스 → 게이트 역인덱스

- **Slice A** (catalog + 데이터): Access control, Backward compatibility, Listing 승인, ADR-016
- **Slice B** (install): Access control, ADR-016
- **Slice C** (publish + secret scan): Secret safety
- **Slice D** (credential definitions + binding): Credential runtime
- **Slice E** (runtime mount + injection + redaction): Runtime isolation, Credential runtime, Secret safety
- **Slice F** (k-skill importer): k-skill sync
- **Slice G** (frontend UI): 직접 게이트 책임 없음 — 백엔드 게이트가 frontend behavior로 노출되는지만 e2e 검증 (M9)

---

## 5. 핵심 발견 사항 (사티아 보고용 — 3~5줄)

1. **삭제 가능 코드 없음** — 마켓플레이스는 "메우는 작업". `legacy` 주석 6곳은 모두 historical context로 유지.
2. **빈 구멍 6개 검증 완료** — (a) `executor.py:544-548` broad mount 🔴, (b) `executor.py:144-150` env credential 미주입 🔴, (c) `packager.py` secret scan 부재 🔴, (d) `skills` 12 컬럼 0개, (e) `agent_skills.config` 0개, (f) k-skill용 credential definition 0개.
3. **progress.txt 정정 필요** — credential definitions 14개라 적혔으나 실측 **13개** (anthropic, openai, google_genai, azure_openai, openrouter, openai_compatible, google_search, naver_search, google_workspace_oauth2, http_bearer, http_basic, http_api_key, mcp_oauth2). PRD §6 표는 14개 나열 — 합의 필요.
4. **회귀 핵심 2개** — m41 backfill에서 기존 package skill을 `imported_by_me`로 마킹하지 않으면 origin badge 오류. secret_scan `\bsk-…\b` boundary 없으면 false-positive로 정상 docstring 차단.
5. **8개 출시 게이트 모두 Slice A~F에 매핑 완료**. 각 게이트당 최소 2개 검증 테스트 식별. G(frontend)는 직접 책임 없음 — M9 e2e에서만 검증.

---

## 6. 추적 (Open Items)

| ID | 항목 | 담당 |
|----|------|------|
| OI-1 | credential definitions 13 vs 14 — progress.txt L38/L70 + PRD §6 표 정정 | 사티아/피차이 확인 |
| OI-2 | `_create_skill_execute_tool` SkillToolContext dataclass 리팩토링 (Step 3 단순화) | 젠슨 — Slice E 구현 시 적용 검토 |
| OI-3 | `executor.py:546-571` skill mount/prompt 블록을 `_build_skill_runtime_context`로 분리 (테스트 격리) | 젠슨 — Slice E |
| OI-4 | secret_scan false-positive 기준 (boundary regex 명세) | 젠슨 — Slice C 구현 전 spec 보강 |
| OI-5 | m41 backfill 정책 — `package` kind는 `imported_by_me`, `text` kind는 `created_by_me`인지 명문화 | 사티아/피차이 — Spec §3.x 보강 |

---

**End of M1-S1 deletion analysis.**
