# Manual E2E — 백로그 E M6 (축소 cleanup)

**작성자**: 베조스 (QA DRI, S5 통합 검증)
**일자**: 2026-04-21
**기반**: S3 젠슨 + S4 저커버그 산출 + S5 자체 검증
**검증 방식**: (A) 자동 회귀 전수 + (B) grep 유지/삭제 불변식 + (C) alembic round-trip docker PG + (D) 수동 E2E 5 시나리오 (사용자 실행 체크리스트)
**브라우저 실측**: **미수행** — 사티아 S6 또는 사용자 PR 리뷰 단계에서 수행. 정적 경로 추적 + pytest fail-closed 커버리지로 증명.

---

## 0. 자동 검증 결과 (PASS 전수)

| 항목 | 기대 | 실제 | 소요 | 판정 |
|------|------|------|------|------|
| Backend ruff | 0 error | `All checks passed!` | 0.14s | PASS |
| Backend pytest | 624 pass (S3 감소치) | `624 passed, 1 deselected, 3 warnings` | 81.21s | PASS |
| Frontend lint | 기존 1건(use-chat-runtime.ts:74) | `1 problem (0 errors, 1 warning)` 동일 위치 | 4.40s | PASS (신규 0) |
| Frontend build | 15 routes | `✓ 15 routes` Turbopack | 10.19s | PASS |
| Alembic upgrade | m12_drop_legacy_columns | PG docker round-trip PASS | — | PASS |
| Alembic downgrade -1 | m11_custom_connection | PG docker PASS | — | PASS |
| Alembic re-upgrade | m12_drop_legacy_columns | PG docker PASS | — | PASS |

**실행 커맨드 (베조스 S5)**:
```bash
cd backend && uv run ruff check .                  # PASS
cd backend && uv run pytest -q                     # 624 passed
cd frontend && pnpm lint                           # 1 warning (pre-existing)
cd frontend && pnpm build                          # 15 routes PASS
# Postgres round-trip (external docker natural-mold-postgres-1, fresh DB moldy_m6verify)
DATABASE_URL=postgresql+asyncpg://moldy:moldy@localhost:5432/moldy_m6verify uv run alembic upgrade head    # m12 head
DATABASE_URL=... uv run alembic downgrade -1                                                                # m11
DATABASE_URL=... uv run alembic upgrade head                                                                # m12 head
```

---

## 1. grep 불변식 (삭제 0 / 유지 존재)

### 1-1. 삭제 스코프 — app 코드 내 0건 기대

| 패턴 | 대상 | 실제 | 판정 |
|------|------|------|------|
| `rg "\.auth_config" backend/app/` (py) | Tool/AgentToolLink | 4건 — 전부 **MCPServer.auth_config** (M6.1 이월) | PASS |
| `rg "link\.config\|AgentToolLink.*config" backend/app/` | agent_tools.config | **0** | PASS |
| `rg "tool\.credential_id\|Tool\.credential_id" backend/app/` | Tool.credential_id | **0** (chat_service.py:299 docstring 제외) | PASS |
| `rg "ToolConfigEntry\|tool_configs\|ToolAuthConfigUpdate" backend/app/` | legacy schema | **0** | PASS |
| `rg "_mask_auth_config\|AUTH_CONFIG_MASK" backend/app/` | masking util | **0** | PASS |
| `rg "_resolve_legacy_tool_auth" backend/app/` | legacy resolver | **0** | PASS |
| `rg "\.auth_config" frontend/src --ts/tsx` | FE Tool.auth_config | 3건 — **MCPServer** 관련 (types 347/364 + hooks 주석 102) | PASS |
| `rg "tool_configs\|agent_config" frontend/src` | FE dead | **0** | PASS |
| `rg "updateAuthConfig\|useUpdateToolAuthConfig" frontend/src` | FE dead API | **0** | PASS |

### 1-2. 유지 스코프 — M6.1 이월 (존재해야 함)

| 패턴 | 위치 | 결과 | 판정 |
|------|------|------|------|
| `mcp_server_id` | `backend/app/models/tool.py:71` | 존재 | PASS |
| `class MCPServer` | `backend/app/models/tool.py:34` | 존재 | PASS |
| `resolve_server_auth` | `backend/app/services/credential_service.py:111` | 존재 | PASS |
| `useUpdateMCPServer` | `frontend/src/lib/hooks/use-tools.ts:97` | 존재 | PASS |

---

## 2. DB 스키마 직접 확인 (docker PG, fresh upgrade head)

```
-- \d tools (존재 컬럼)
id, user_id, type, mcp_server_id, name, description, parameters_schema,
api_url, http_method, auth_type, created_at, is_system, tags,
connection_id, provider_name

-- ★ 부재 컬럼 (M6 drop 확인) ★
auth_config, credential_id   -- 존재하지 않음

-- FK
fk_tools_connection_id → connections(id) ON DELETE SET NULL
tools_mcp_server_id_fkey → mcp_servers(id)   -- M6.1까지 유지
tools_user_id_fkey → users(id)

-- \d agent_tools (존재 컬럼)
agent_id, tool_id

-- ★ 부재 컬럼 (M6 drop 확인) ★
config   -- 존재하지 않음

-- mcp_servers 테이블
SELECT to_regclass('mcp_servers')   -- → mcp_servers (존재, M6.1 이월)
```

**판정**: PASS — M6 축소 스코프의 3개 drop 전부 반영, MCP 경로는 완전 보존.

---

## 3. 잠재 회귀 포인트 조사 (S1 베조스 경고점 재검증)

### 3-1. `credential_service.get_usage_count` — Tool.credential_id 제거 후에도 정상 작동하는가

**조사**:
- 호출처 grep: `rg "get_usage_count"` → 1개 (`routers/credentials.py:93`)
- 신규 쿼리: `SELECT count(*) FROM tools JOIN connections ON tools.connection_id = connections.id WHERE connections.credential_id = X` (credential_service.py:130-136)
- `mcp_server_count` 쿼리는 MCPServer.credential_id 그대로 사용 (M6.1 이월 스코프)
- pytest `test_credentials.py` 5/5 PASS

**판정**: PASS — 회귀 없음. credential 사용처 집계 기능 유지. UI 호출처 `/api/credentials/{id}/usage` 정상 동작.

### 3-2. `_resolve_legacy_tool_auth` 삭제 후 CUSTOM null connection_id fail-closed 커버리지

**조사**:
- `chat_service.py:302-306` — CUSTOM tool에서 `tool.connection_id is None` → `ToolConfigError` raise
- pytest 커버리지:
  - `test_custom_resolves_raises_when_connection_id_is_null` PASS (신규 M6 시나리오)
  - `test_custom_resolves_raises_when_connection_missing_despite_fk` PASS
  - `test_custom_disabled_connection_fails_closed` PASS
  - `test_custom_connection_with_null_credential_fails_closed` PASS
- 총 12/12 PASS in `test_connection_custom_resolve.py`

**판정**: PASS — fail-closed 4개 시나리오 전수 커버. legacy resolver 삭제로 인한 회귀 없음.

### 3-3. `_mask_auth_config` 제거 후 masking 누수 가능성

**조사**:
- `ToolResponse`는 M6 이후 `auth_config` 필드 자체를 가지지 않음 — masking 대상 소멸
- `MCPServerResponse.auth_config` (M6.1 이월)은 별도 영역이며 내부 CRUD 전용, 기존 masking 정책은 raw 반환 (기존 동작 유지)
- `rg "_mask\|AUTH_CONFIG_MASK\|mask_auth"` backend/app/ → **0**

**판정**: PASS — masking 누수 0건. Tool 경로에서 민감 데이터 노출 경로 없음.

---

## 4. 수동 E2E 시나리오 (사용자 실행 체크리스트)

**준비**:
```bash
docker compose up -d postgres   # 또는 기존 natural-mold-postgres-1 재사용
cd backend && uv run alembic upgrade head     # m12 head 확인
cd backend && uv run uvicorn app.main:app --reload --port 8001
cd frontend && pnpm dev
# → http://localhost:3000
```

### 시나리오 1 — PREBUILT 경로 회귀 없음 (Naver)

**목적**: M3/M5 회귀 없음 증명.

1. [ ] `/connections` 진입 → "Prebuilt" 섹션 → "Naver" 카드 → "연결 추가"
2. [ ] ConnectionBindingDialog 오픈 → 신규 Credential 선택(또는 생성) → "연결"
3. [ ] `connections` 테이블에 `type=prebuilt, provider_name='naver', is_default=true, status='active'` row 생성 확인 (개발자도구 Network 탭 POST /api/connections 201)
4. [ ] `/tools` 이동 → "Naver 웹 검색" 등 Naver provider tool 카드가 **"인증됨(녹색)"** 배지 표시 확인
5. [ ] "도구 추가" → 에이전트에 Naver 검색 도구 바인딩
6. [ ] 에이전트 채팅 → "최근 AI 뉴스 검색해줘" 같은 쿼리 → 정상 응답 스트림 확인 (도구 호출 성공)

**기대 결과**: 모든 단계 정상. 도구 실행 시 `cred_auth` = connection.credential.data 해석, PREBUILT auto-match 유지.

### 시나리오 2 — CUSTOM 경로 회귀 없음

**목적**: M4 커스텀 connection 자동 생성 회귀 없음.

1. [ ] `/tools` → "도구 추가" → Custom 탭 → 이름/URL/메서드 입력 → 신규 Credential 선택 → "생성"
2. [ ] 백엔드: `POST /api/tools/custom` → body에 `credential_id: null` 필드 **없음** 확인 (저커버그 S4 변경)
3. [ ] `tools` 테이블에 row 생성, `tools.connection_id IS NOT NULL` (m11 migration 호환), `tools.credential_id` **컬럼 자체 부재** (m12)
4. [ ] tool 카드 "인증됨" 상태
5. [ ] 에이전트에 바인딩 → 채팅에서 도구 호출 → custom API 실행 성공

**기대 결과**: `chat_service.build_tools_config` → Gate A: connection active → Gate B: credential resolve → merged_auth = cred_auth (agent_tools.config merge 제거됨). 200 응답.

### 시나리오 3 — MCP 경로 회귀 없음 (M6.1 이월 스코프 보존)

**목적**: MCP 관련 코드(live)가 M6에 의해 훼손되지 않음 증명.

1. [ ] `/tools` → "도구 추가" → MCP 탭 → URL/이름 + Credential 선택 → "등록"
2. [ ] `POST /api/tools/mcp-server` → 201. `mcp_servers` 테이블 row 생성, `tools.mcp_server_id` 세팅
3. [ ] `/connections` → MCP 섹션 → 해당 서버 카드 → "연결 관리" → ConnectionBindingDialog
4. [ ] Credential 변경 → 저장 → `useUpdateMCPServer` → PATCH `/api/tools/mcp-servers/{id}` 200 확인
5. [ ] 에이전트 바인딩 → 채팅 → MCP 도구 호출 성공

**기대 결과**: MCPServer CRUD 4종 + resolve_server_auth + chat_service MCP fallback 전부 라이브. M6.1 승격 준비 완료.

### 시나리오 4 — Connection fail-closed (kill-switch 유지)

**목적**: `status='disabled'` kill-switch가 M6 이후에도 작동.

1. [ ] 시나리오 1 후속 — `/connections` → Naver 카드 → "비활성화" 토글 (PATCH status='disabled')
2. [ ] 에이전트 채팅 → "검색해줘" 쿼리
3. [ ] 도구 호출 시점에 **`ToolConfigError`** 발생 확인. SSE에러 이벤트에 "connection disabled" 류 메시지 노출
4. [ ] 백엔드 로그 확인 — `chat_service._resolve_custom_auth` or `_resolve_prebuilt` → `raise ToolConfigError`

**기대 결과**: pytest `test_custom_disabled_connection_fails_closed` + `test_prebuilt_disabled_fails_closed`가 이 경로를 자동 커버. 실제 UI에서도 동일 경로 재확인.

### 시나리오 5 — DB 구조 직접 확인 (m12 스키마 불변식)

**목적**: 프로덕션 배포 직후 DBA 체크리스트.

```sql
-- psql 또는 docker exec
\d tools
-- 기대:
--   auth_config 컬럼 부재
--   credential_id 컬럼 부재
--   mcp_server_id 컬럼 존재 (M6.1 이월)
--   connection_id 컬럼 존재

\d agent_tools
-- 기대: (agent_id, tool_id) 2컬럼만. config 컬럼 부재

SELECT to_regclass('mcp_servers');
-- 기대: 'mcp_servers' (M6.1 이월)

-- data 무결성
SELECT COUNT(*) FROM tools WHERE connection_id IS NULL AND type = 'custom';
-- 기대: 0 (m11 migration이 모두 이관 완료). 있으면 fail-closed로 떨어지고 사용자에게 재설정 요구

SELECT COUNT(*) FROM tools WHERE provider_name IS NULL AND type = 'prebuilt' AND is_system = true;
-- 기대: 0 (m10 백필 완료)
```

**판정 조건**: 위 모든 쿼리가 기대값과 일치.

---

## 5. 종합 판정

| 영역 | 결과 |
|------|------|
| 자동 회귀 (ruff/pytest/lint/build) | 전수 PASS |
| Alembic round-trip (docker PG) | PASS |
| grep 불변식 (삭제 0 / 유지 존재) | PASS |
| DB 스키마 실측 | PASS |
| 잠재 회귀 포인트 3개 | 전수 PASS |
| 수동 E2E 5 시나리오 | 문서화 완료 (사용자 실행 대기) |

**베조스 판정**: **그린** — M6 축소 스코프(auth_config + credential_id + agent_tools.config 3개 drop)의 통합 검증 완료. S3 젠슨 + S4 저커버그 산출물이 회귀 없이 통합됨. MCP 경계(M6.1 이월)는 침범 없음. 사티아 S6(commit) 진행 가능.
