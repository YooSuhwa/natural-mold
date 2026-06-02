# Credential / Tools / Skills 그린필드 리라이트

## Context

natural-mold(Moldy)의 현재 Credential/Connection/Tools/Skills 시스템은 M6~M11 마이그레이션 누적으로 이원화·다중분류가 누적되어 있다. 점진 리팩토링은 M11과 같은 복잡도를 한 번 더 만들 가능성이 크다.

PoC 단계라 데이터 손실이 허용되므로, Credential/Tool/Skill 스택을 Python/React 기반 Moldy 고유 모델로 그린필드 리라이트한다.

## 사용자 결정 사항 (확정)

1. **Cipher 포맷**: 단일 블롭 `[version 1B][salt 32B][authTag 16B][ciphertext]` Base64 + HKDF-SHA256(info=`'moldy-encryption-v1'`)로 키/IV 도출. 키 식별은 별도 컬럼(`key_id`)으로.
2. **LLM 모델 테이블**: `models` 유지(api_key_encrypted 컬럼 제거), `agents.llm_credential_id` FK 추가, `llm_providers` 테이블 폐기. LLM API 키도 신규 Credential로 통합.
3. **PR 단위**: 단일 PR.
4. **범위**: Cipher V2 + Credential 도메인 + OAuth2 자동 refresh + 키 로테이션 cron + **Vault provider 실구현**(HVAC SDK).

## 탐색에서 발견한 보완 사항

| 항목 | 발견 사항 |
|---|---|
| **chat_service.py** | `build_tools_config()` (L369-462)가 인증 해석 본체. 초안에 누락 — **전면 재배선 필요**. |
| **trigger_executor.py L44-46** | `_load_user_default_connection_map()` 미호출 — 현 prod 잠재 버그. 신규 시스템에선 PREBUILT default credential 프리로드 로직을 동등하게 재구현 필요. |
| **mcp_client.py + env_var_resolver.py** | `${credential.<field>}` 템플릿 해석 로직. 신규 Credential 시스템으로 재배선 필요. |
| **마이그레이션 번호** | `m12_drop_legacy_columns.py`가 이미 존재 — 신규 마이그레이션은 **m13_greenfield_credentials**로 명명. |
| **Fernet → Cipher V2 데이터 마이그레이션** | PoC라 dev DB는 drop & recreate. 단 `ENCRYPTION_KEY` (Fernet) → `ENCRYPTION_KEYS` (Cipher V2) 환경 변수 전환 명시 필요. |
| **테스트 의존성** | `tests/`에서 `naver_tools`, `credential_service`, `connection_service` 직접 import 여부 grep 후 제거/대체. |

## 작업 순서 (단일 PR 내부 마일스톤)

CHECKPOINT.md에 다음 6개 마일스톤을 등록하고 각 마일스톤 검증 통과 후 다음으로 진행. 머지는 모든 마일스톤 완료 후 1회.

### M1. 브랜딩 검증 + Cipher V2

**파일**:
- `scripts/check_branding.py` (신규) — 설정된 금지 식별자, 패키지 prefix, 자산 SHA-256 블랙리스트 검사
- `backend/app/security/cipher.py` (신규) — `cryptography` 라이브러리 사용
- `backend/app/security/key_provider.py` (신규) — 활성/검증 키 다중 관리
- `backend/app/config.py` 수정 — `encryption_keys: list[str]` 추가, 부팅 시 비면 실패
- `backend/.env.example` 수정 — `ENCRYPTION_KEYS` 예시
- `backend/tests/test_cipher.py` (신규) — round-trip, 다중 키, 키 식별, 손상 검증
- `backend/tests/test_branding.py` (신규) — `check_branding.py` 호출

**Cipher V2 정확한 스펙**:
- KEY: 64-char hex (32 bytes)
- SALT: 32 bytes random
- IV: 12 bytes (HKDF 도출분의 마지막 12바이트)
- AUTH_TAG: 16 bytes
- HKDF info: `b'moldy-encryption-v1'`
- 포맷: Base64(`0x01` + salt + authTag + ciphertext)
- 멀티키: `key_id`는 키의 `sha256(key)[:8].hex()`로 도출. 암호화 시 활성 키 사용, 복호화 시 모든 키 시도(첫 성공). 별도 컬럼(`credentials.key_id`)에 저장하여 로테이션 식별.

**검증**: `uv run pytest tests/test_cipher.py tests/test_branding.py -v` 통과, `python scripts/check_branding.py` 통과.

### M2. Credential 도메인 + ORM + 라우터 + 정의 카탈로그 + Vault

**모델** — `backend/app/models/`:
- `credential.py` (재작성)
  ```python
  class Credential(Base):
      id, user_id, definition_key, name
      data_encrypted: str          # Cipher V2
      key_id: str                  # 활성 키 식별 (로테이션용)
      field_keys: list[str]        # JSON, 복호화 회피 캐시
      is_shared: bool
      status: Literal["active", "disabled", "expired"]
      last_used_at, last_tested_at
      last_test_result: dict | None  # JSON
      created_at, updated_at
  ```
- `credential_audit_log.py` (신규)
- `credential_default.py` (신규) — `(user_id, scope_kind, scope_key, credential_id)`

**도메인** — `backend/app/credentials/`:
- `field.py` — `FieldDef`, `FieldKind` enum: `string|password|number|select|multiline|json|oauth_button|toggle|collection`. `display_options.show: dict[str, list[Any]]` (조건부 표시), `type_options: { password, multiline, expirable, ... }`
- `domain.py` — `CredentialDefinition`: `key, display_name, icon_id, properties, authenticate, test, pre_authentication, extends`
- `interpolation.py` — `={{ $credentials.<field> }}` 한정 표현식 평가기 (전체 JS 평가는 의도적으로 미지원, 보안 표면 최소화)
- `authenticate.py` — `GenericAuth(type='generic', properties={headers, qs, body, basic})` + `httpx.Auth` 어댑터
- `registry.py` — `CredentialRegistry` 싱글턴, 정의 등록/조회
- `oauth2_base.py` — OAuth2 베이스. `expirable` typeOptions 있는 토큰 필드 만료 검사 → `pre_authentication()` 호출 → refresh → 재암호화 후 UPDATE → audit log `refresh`. **동시성 가드: SQLAlchemy `with_for_update()`로 직렬화**.
- `tester.py` — `CredentialTester.run(definition, decrypted)` → test request 실행 → rules 평가 → 결과 dict
- `external_secrets/base.py` — `SecretsProvider` ABC: `init/connect/get_secret/has_secret/test`
- `external_secrets/env_provider.py` — 기본 (env vars)
- `external_secrets/vault_provider.py` — **HVAC SDK 실구현**, feature flag `settings.external_secrets_enabled`
- `external_secrets/proxy.py` — `__external__: { provider, ref }` 마커 런타임 해석

**정의** — `backend/app/credentials/definitions/`:
- `naver_search.py`, `google_search.py`, `google_workspace_oauth2.py`, `openai.py`, `anthropic.py`, `google_genai.py`, `azure_openai.py`
- `http_bearer.py`, `http_api_key.py` (header 이름 사용자 지정), `http_basic.py`
- `mcp_oauth2.py`

**라우터** — `backend/app/routers/credentials.py` (재작성):
- `GET /api/credential-types` — 정의 카탈로그
- `GET /api/credentials` — 목록
- `POST /api/credentials` — 생성 (암호화)
- `GET /api/credentials/{id}` — 단일 (data 미반환, field_keys만)
- `PATCH /api/credentials/{id}` — 갱신
- `DELETE /api/credentials/{id}`
- `POST /api/credentials/{id}/test` — 저장된 credential 테스트
- `POST /api/credentials/preview-test` — 저장 전 폼 데이터로 테스트
- `GET /api/credentials/{id}/audit-logs` — 최근 N건
- `POST /api/oauth2-credential/auth/{id}` — OAuth2 인증 시작
- `GET /api/oauth2-credential/callback` — 콜백

**테스트** — `backend/tests/test_credentials.py`, `test_oauth2.py`, `test_tester.py`, `test_external_secrets.py`

**검증**: 정의 카탈로그 응답 / Credential CRUD / Test 호출 / OAuth2 mock refresh / Vault dev 컨테이너 secret 조회.

### M3. Tools 재정의 + MCP 서버

**모델**:
- `backend/app/models/tool.py` (재작성)
  ```python
  class Tool(Base):
      id, user_id, definition_key, name, description
      parameters: dict        # JSON, 사용자 입력값
      credential_id: UUID | None  # FK credentials, SET NULL
      enabled: bool
      last_used_at, created_at, updated_at
  ```
- `backend/app/models/mcp_server.py` (신규)
- `backend/app/models/mcp_tool.py` (신규)

**도메인** — `backend/app/tools/`:
- `domain.py` — `ToolDefinition` (간소화 INodeType): `key, display_name, icon_id, category, parameters, credential_definition_keys, runner`
- `parameters.py` — `FieldDef` 재사용
- `registry.py`
- `runner.py` — HTTP 호출 + GenericAuth 적용 (Credential 시스템 위임)
- `definitions/`: `http_request.py`, `naver_search.py`, `google_search.py`, `gmail_send.py`, `google_calendar_event.py`, `google_chat_message.py`

**MCP** — `backend/app/mcp/`:
- `domain.py` — `McpServerDefinition`
- `client.py` — `langchain-mcp-adapters` 래퍼. **`${credential.<field>}` env_vars 템플릿 해석을 신규 `interpolation.py` 호출로 교체** (현 `env_var_resolver.py` 폐기)
- `discovery.py` — tools 자동 검색
- `oauth.py` — MCP OAuth2 (oauth2_base 상속)

**라우터**:
- `backend/app/routers/tools.py` (재작성): `GET /api/tool-types`, `GET/POST/PATCH/DELETE /api/tools`, `POST /api/tools/{id}/run`
- `backend/app/routers/mcp.py` (신규): `GET/POST/PATCH/DELETE /api/mcp-servers`, `POST /api/mcp-servers/{id}/test`, `POST /api/mcp-servers/{id}/discover`

**테스트**: `test_tools.py`, `test_mcp.py`

**검증**: 도구 카탈로그 / 도구 인스턴스 생성 / HTTP Request 도구 실제 호출 / MCP discover.

### M4. Skills + 마이그레이션 m13 + 시드

**Skills**:
- `backend/app/models/skill.py` 재작성: 기존 `type/storage_path` 컬럼 활용 + `content_hash, size_bytes, version, package_metadata, used_by_count, last_modified_at`
- `backend/app/skills/`: `service.py`, `packager.py` (.skill zip), `inspector.py` (SKILL.md 파싱), `runtime.py`
- `backend/app/routers/skills.py` (재작성): `GET/POST /api/skills`, `GET /api/skills/{id}/files`, `GET /api/skills/{id}/files/{path}`

**마이그레이션** — `backend/alembic/versions/m13_greenfield_credentials.py`:
- DROP: `credentials, connections, credential_audit_logs(있으면), tools, skills, models, llm_providers, mcp_servers, mcp_tools(있으면), agent_tools, agent_skills`
- CREATE 신규 스키마:
  - `credentials, credential_audit_logs, credential_defaults`
  - `tools, mcp_servers, mcp_tools, skills`
  - `models` (api_key_encrypted 제거된 새 스키마)
  - `agent_tools` (재생성, FK tool 새 스키마)
  - `agent_skills` (재생성)
- ALTER: `agents` 테이블에 `llm_credential_id UUID FK credentials` 추가
- downgrade: `raise NotImplementedError("m13 is intentionally non-reversible")`

**시드** — `backend/app/seed/`:
- `bootstrap_from_env.py` (재작성) — `OPENAI_API_KEY` 등 env 발견 시 mock_user 소유 Credential 자동 생성. `settings.environment != "production"`에서만 동작.
- `tool_definitions.py`, `credential_definitions.py` — registry-only로 충분하면 생략

**기존 시드/스크립트 제거**:
- `backend/app/seed/prebuilt_connections.py` 삭제
- `backend/scripts/google_oauth_setup.py` 삭제

**검증**: `uv run alembic upgrade head` 클린 재구성. 부팅 시 시드 로그.

### M5. agent_runtime 재배선 + 키 로테이션 cron

**파일 재배선**:
- `backend/app/services/chat_service.py` — **`build_tools_config()` + `get_agent_with_tools()` 전면 재작성**. 신규 `tool.credential_id`로 직결 (default connection map 폐기). PREBUILT/CUSTOM 분기 → 단일 경로(definition + credential_id).
- `backend/app/agent_runtime/executor.py` — import 정리(`tool_factory`만 새 시스템 사용)
- `backend/app/agent_runtime/tool_factory.py` — 신규 `tools/runner.py` 위임으로 단순화
- `backend/app/agent_runtime/model_factory.py` — `agent.llm_credential` 복호화 → API 키 추출
- `backend/app/agent_runtime/trigger_executor.py` — chat_service 신규 함수 호출. **L44-46 prefetch 누락 버그 동시 수정.**
- `backend/app/agent_runtime/creation_agent.py` — chat_service 의존만 따라감
- `backend/app/agent_runtime/mcp_client.py` — `${credential.<field>}` 템플릿을 `app/credentials/interpolation.py`로 위임

**삭제**:
- `backend/app/agent_runtime/naver_tools.py`
- `backend/app/agent_runtime/google_tools.py`
- `backend/app/agent_runtime/google_workspace_tools.py`
- `backend/app/agent_runtime/env_var_resolver.py`
- `backend/app/services/encryption.py`
- `backend/app/services/credential_service.py` (옛)
- `backend/app/services/credential_registry.py` (옛)
- `backend/app/services/connection_service.py`
- `backend/app/models/connection.py`
- `backend/app/routers/connections.py`

**키 로테이션 cron**:
- `backend/app/scheduler.py` — APScheduler에 `rotate_credentials_to_active_key` 잡 등록. 주 1회 (설정 가능). `key_id != active_key_id`인 모든 row 배치 재암호화. 실패 시 다음 회차 재시도, audit log `rotate`.

**테스트 회귀**:
- `backend/tests/` — `naver_tools` 등 직접 import 제거, fixture 신규 구조로 갱신
- `uv run pytest tests/ -v` 전체 통과

**검증**: 채팅 → 도구 호출 → executor 정상. 트리거 1회 수동 실행. cron 수동 트리거 후 모든 row `key_id` 변경 확인.

### M6. 프론트엔드 (디자인 시스템 → 페이지)

**공통 컴포넌트** — `frontend/src/components/`:
- `ui/data-table.tsx` (shadcn data-table 베이스) — 정렬/검색/페이지네이션/필터
- `shared/status-chip.tsx` — variants: `active|auth_needed|expired|disabled|error|unknown`. 자체 디자인 토큰.
- `shared/icon.tsx` — Lucide + 자체 SVG.
- `shared/empty-state.tsx`
- `shared/dynamic-fields-form.tsx` — **`FieldDef[]` → React 폼**:
  - 타입별 렌더러: string / password(마스킹+토글) / number / select / multiline / json / oauth_button / toggle / collection
  - `display_options.show`로 조건부 렌더
  - `type_options.password = true` → 항상 마스킹
  - `type_options.expirable = true` → 만료 시각 표시
  - 검증: required, regex, length
  - dirty 상태, 저장 전 confirm

**Credentials 페이지** — `frontend/src/app/credentials/page.tsx`:
- DataTable: 이름 / 정의(아이콘+이름) / 상태 칩 / 마지막 사용 / 마지막 테스트 / 액션
- 정렬, 검색, 정의 필터, 상태 필터
- 행 클릭 → Sheet (`credential-detail-sheet.tsx`)
- 상단 "+ Credential" → `credential-create-modal.tsx` (Step: 정의 카탈로그 → 동적 폼 → Test → 저장)

**컴포넌트** — `frontend/src/components/credential/`:
- `credential-create-modal.tsx`, `credential-detail-sheet.tsx`, `credential-picker.tsx`, `credential-test-button.tsx`

**Tools 페이지** — `frontend/src/app/tools/page.tsx` (재작성):
- 탭: Catalog / Manage
- Catalog: 카테고리 필터 + 카드 그리드 → 클릭 시 인스턴스 생성 다이얼로그
- Manage: DataTable

**컴포넌트** — `frontend/src/components/tool/`:
- `tool-catalog.tsx`, `tool-create-dialog.tsx`, `tool-detail-sheet.tsx`, `tool-run-panel.tsx`

**MCP 페이지** — `frontend/src/app/mcp-servers/page.tsx` + `frontend/src/components/mcp/`:
- DataTable + 4단계 wizard (기본정보 → 인증 → 도구검색 → 확인)
- `mcp-server-wizard.tsx`, `mcp-server-detail-sheet.tsx`, `mcp-tool-table.tsx`

**Skills 페이지** — `frontend/src/app/skills/page.tsx` (재작성) + `frontend/src/components/skill/`:
- DataTable + Grid 토글, kind 배지
- `skill-detail-sheet.tsx` (text 에디터 / package 트리), `skill-upload-dialog.tsx`, `skill-package-tree.tsx`

**API 클라이언트 & hooks**:
- `frontend/src/lib/api/{credentials,tools,mcp,skills}.ts` (신규)
- `frontend/src/lib/hooks/{use-credentials,use-tools,use-mcp-servers,use-skills,use-credential-test}.ts` (신규)
- `frontend/src/lib/types/{credential,tool,mcp,skill}.ts` (신규)

**삭제**:
- `frontend/src/app/connections/page.tsx`
- `frontend/src/components/{tool,connection,skill}/` 기존 폴더 전체
- `frontend/src/lib/api/{tools,connections,credentials,skills}.ts` 옛 파일
- `frontend/src/lib/hooks/use-{tools,connections,credentials,skills}*.ts` 옛 파일

**네비게이션**:
- `frontend/src/components/layout/sidebar.tsx` 수정 — Agents / Tools / MCP Servers / Skills / Credentials / Usage. Connections 항목 제거.
- `frontend/src/app/agents/*` 도구·스킬 선택 UI 신규 hooks/api로 재배선

**E2E**:
- `frontend/e2e/credentials.spec.ts`, `tools-catalog.spec.ts`, `mcp-server-wizard.spec.ts`, `skills-management.spec.ts` (신규)

**검증**: `pnpm build && pnpm lint && pnpm exec playwright test`

## 브랜딩 검증 (CI 게이트)

`scripts/check_branding.py` (M1):
- 설정된 금지 식별자 패턴이 소스 트리(`backend/`, `frontend/src`, `*.md` 일부)에 매치되면 실패
- `pyproject.toml`, `package.json`에 설정된 금지 패키지 prefix 발견 시 실패
- `frontend/public`, `frontend/src/assets`의 SVG/PNG 파일 SHA-256이 블랙리스트와 일치 시 실패

CI 파이프라인:
1. `python scripts/check_branding.py`
2. `cd backend && uv run ruff check . && uv run pytest tests/ -v`
3. `cd frontend && pnpm lint && pnpm build && pnpm exec playwright test`

## 리스크 & 대응

| 리스크 | 대응 |
|---|---|
| 공개 의존성 라이선스 | 외부 배포 전 의존성 라이선스 검토. |
| 브랜딩 정책 위반 | `scripts/check_branding.py` CI 게이트 + 머지 차단 |
| OAuth refresh 동시성 | `oauth2_base`에서 `SELECT ... FOR UPDATE`로 토큰 갱신 직렬화 |
| Vault 의존성 | feature flag 기본 off, env_provider로 폴백. dev 컨테이너로 통합 테스트 |
| 채팅 회귀 | M5에서 chat_service 재작성 후 즉시 채팅 + 도구 호출 + 트리거 + MCP 통합 시나리오 회귀 테스트 우선 통과 |
| dev DB 데이터 소실 | PoC 단계라 수용. README에 m13 마이그레이션 시 초기화됨 명기 |
| 단일 PR 리뷰 부담 | M1~M6 마일스톤 각각 별도 커밋으로 쪼개 리뷰 가독성 확보 (PR은 1회) |

## Verification (E2E)

```bash
# 브랜딩
python scripts/check_branding.py

# 백엔드
cd backend
uv run pytest tests/test_cipher.py tests/test_branding.py -v          # M1
uv run pytest tests/test_credentials.py tests/test_oauth2.py tests/test_tester.py tests/test_external_secrets.py -v  # M2
uv run pytest tests/test_tools.py tests/test_mcp.py -v                # M3
uv run pytest tests/test_skills.py -v                                 # M4
uv run pytest tests/ -v                                               # 전체 회귀

# 마이그레이션 클린 재구성
docker-compose down -v && docker-compose up -d postgres
uv run alembic upgrade head

# 프론트엔드
cd ../frontend
pnpm lint && pnpm build
pnpm exec playwright test
```

### 수동 시나리오 (머지 전)
1. 빌드 산출물에서 설정된 금지 식별자 grep 0건. 금지 자산 검색 결과 없음.
2. `ENCRYPTION_KEYS` 비우면 부팅 실패.
3. Credential 카탈로그 → 정의 선택 → 동적 폼 → Test → 저장 → DataTable 표시.
4. Google Workspace OAuth2 등록 후 토큰 만료 시뮬레이션 → 도구 호출 시 자동 refresh, audit log `refresh` 기록.
5. HTTP Request 도구 인스턴스화 → Credential 선택 → 에이전트 추가 → 채팅에서 호출.
6. MCP 4단계 wizard 통과 → 도구 자동 import → "Test connection" 동작.
7. Skill package 업로드 → 트리/README 미리보기 → 에이전트 attach → deep agent 실행.
8. `ENCRYPTION_KEYS=new,old` 변경 후 cron 수동 트리거 → 모든 row `key_id`가 활성 키로 변경. audit log `rotate`.
9. Vault feature flag on, dev Vault에 secret 저장 → `__external__` 마커 credential로 도구 호출 → 정상.
10. `SELECT * FROM credential_audit_logs ORDER BY created_at DESC LIMIT 50` — 모든 시나리오 이벤트 기록.
11. 상태 칩이 Credentials/Tools/MCP/Skills 4페이지에서 동일 시각.

## 핵심 파일 경로 요약

### 신규 (백엔드 ~50, 프론트 ~30, 스크립트/문서 2)
- `backend/app/security/{cipher,key_provider}.py`
- `backend/app/credentials/{domain,field,authenticate,interpolation,registry,oauth2_base,tester}.py`
- `backend/app/credentials/external_secrets/{base,env_provider,vault_provider,proxy}.py`
- `backend/app/credentials/definitions/*.py` (~11)
- `backend/app/tools/{domain,registry,runner,parameters}.py` + `definitions/*.py` (~6)
- `backend/app/mcp/{domain,client,discovery,oauth}.py`
- `backend/app/skills/{service,packager,inspector,runtime}.py`
- `backend/app/models/{credential,credential_audit_log,credential_default,tool,mcp_server,mcp_tool,skill}.py`
- `backend/app/routers/{credentials,tools,mcp,skills}.py`
- `backend/app/seed/bootstrap_from_env.py`
- `backend/alembic/versions/m13_greenfield_credentials.py`
- `backend/tests/test_{cipher,credentials,oauth2,tester,external_secrets,tools,mcp,skills,branding}.py`
- `frontend/src/app/{credentials,mcp-servers,tools,skills}/page.tsx`
- `frontend/src/components/ui/data-table.tsx`
- `frontend/src/components/shared/{status-chip,icon,dynamic-fields-form,empty-state}.tsx`
- `frontend/src/components/{credential,tool,mcp,skill}/*.tsx`
- `frontend/src/lib/api/{credentials,tools,mcp,skills}.ts`
- `frontend/src/lib/hooks/use-{credentials,tools,mcp-servers,skills,credential-test}.ts`
- `frontend/src/lib/types/{credential,tool,mcp,skill}.ts`
- `frontend/e2e/{credentials,tools-catalog,mcp-server-wizard,skills-management}.spec.ts`
- `scripts/check_branding.py`
- `NOTICES.md`

### 수정
- `backend/app/main.py`, `app/config.py`, `app/scheduler.py`
- `backend/app/services/chat_service.py` (전면 재작성)
- `backend/app/agent_runtime/{executor,tool_factory,model_factory,trigger_executor,creation_agent,mcp_client}.py`
- `backend/.env.example`, `backend/pyproject.toml`(hvac 추가)
- `frontend/src/components/layout/sidebar.tsx`
- `frontend/src/app/agents/*` (도구·스킬 선택 UI)
- `frontend/package.json`

### 폐기 (전부 삭제)
- `backend/app/services/{encryption,credential_service,credential_registry,connection_service}.py`
- `backend/app/models/connection.py`
- `backend/app/routers/connections.py`
- `backend/app/agent_runtime/{naver_tools,google_tools,google_workspace_tools,env_var_resolver}.py`
- `backend/app/agent_runtime/tool_factory.py`의 auth 분기 (재작성으로 흡수)
- `backend/app/seed/prebuilt_connections.py`
- `backend/scripts/google_oauth_setup.py`
- `frontend/src/app/connections/page.tsx`
- `frontend/src/components/{tool,connection,skill}/` 기존 폴더 전체
- `frontend/src/lib/api/{tools,connections,credentials,skills}.ts` (옛)
- `frontend/src/lib/hooks/use-{tools,connections,credentials,skills}*.ts` (옛)
