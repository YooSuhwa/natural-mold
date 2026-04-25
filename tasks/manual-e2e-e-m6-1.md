# M6.1 수동 E2E 시나리오

**작성자**: 베조스 (QA DRI)
**작성일**: 2026-04-25
**브랜치**: `feature/backlog-e-m6-1` (4 commits: `10c55dc` M2 / `87b173e` M4 / `7d3fef0` M3 / `b24ef1b` M5)
**대상**: `tool.connection_id` single source of truth + MCP legacy drop

## 사전 준비

```bash
# DB 상태 확인 (m13 적용 완료 기대)
docker exec natural-mold-postgres-1 psql -U moldy -d moldy -c "SELECT to_regclass('mcp_servers');"
# → NULL (table dropped)

docker exec natural-mold-postgres-1 psql -U moldy -d moldy -c "\d tools" | grep -c mcp_server_id
# → 0

# Backend + Frontend 기동
cd backend && uv run uvicorn app.main:app --reload --port 8001 &
cd frontend && pnpm dev &
# http://localhost:3000
```

---

## 시나리오 1: CUSTOM first-bind (M4 핵심 흐름)

**목표**: CUSTOM tool 생성 시점에 connection_id 없이도 이후 Binding dialog에서 credential 선택으로 self-serve 수리 가능.

**전제**:
- CUSTOM tool 생성은 **현재 경로에서 connection_id required** (schemas/tool.py L31) — 이 시나리오는 기존 fail-closed tool 혹은 drive-by로 생성된 tool을 Binding dialog로 수리하는 케이스 대상.
- 테스트 목적으로 직접 tool row를 생성: `psql -c "INSERT INTO tools (id, user_id, type, name, created_at) VALUES (gen_random_uuid(), (SELECT id FROM users LIMIT 1), 'custom', 'Test First-Bind', now());"`

**단계**:
1. `/tools` 페이지 → "Test First-Bind" tool 카드가 "연결 없음" 상태로 표시
2. Binding 버튼 클릭 → Credential Select dialog 오픈 (`<CustomBody>`)
3. 기존 credential 선택 OR "새 credential 만들기" → 저장
4. Network 탭 확인:
   - (a) `POST /api/connections` OR `GET /api/connections?type=custom` → 기존 connection 재사용 / 신규 생성 (useFindOrCreateCustomConnection, ADR-008 N:1)
   - (b) `PATCH /api/tools/{tool_id}` body: `{"connection_id": "<new-conn-id>"}` → 200
5. Dialog close, tool 카드 상태 "연결됨" 갱신 (invalidateQueries `['tools']`)
6. 에이전트에 해당 tool 바인딩 후 채팅 실행 → CUSTOM tool call 성공

**Pass 기준**:
- ✅ `PATCH /api/tools/{id}`가 `{connection_id}` 단일 필드로 호출됨
- ✅ tool.connection_id가 응답에 반영
- ✅ UI에서 `needsOptionDFirstBind` alert/가드 **더이상 표시되지 않음** (M4에서 제거)
- ✅ 채팅 실행 시 runtime은 `tool.connection.credential` 경로로 decrypt (chat_service `_resolve_custom_auth`)

**Fail 기준**:
- ❌ PATCH 응답이 400/422/404
- ❌ tool.connection_id가 null 그대로
- ❌ 에이전트 실행 시 `ToolConfigError: has no connection_id`

---

## 시나리오 2: MCP credential rotate (M5 핵심 흐름)

**목표**: 기존 MCP tool의 credential을 `ConnectionBindingDialog type="mcp"`로 교체. M5에서 `useUpdateMCPServer` 제거 후 `useUpdateConnection` 단일 경로.

**전제**: 기존 MCP connection + tool이 최소 1개 존재. dev DB에는 현재 0건 (Hancom-GW 삭제됨). 이 시나리오는 **코드 경로 유효성** 확인용 → staging 환경 or seed 데이터로 커버.

**대안 검증 (로컬)**: 직접 seed
```sql
INSERT INTO credentials (id, user_id, name, credential_type, provider_name, data_encrypted, field_keys, created_at)
VALUES (gen_random_uuid(), (SELECT id FROM users LIMIT 1), 'test-mcp-cred', 'key-value', 'mcp_custom', '...'::bytea, '["api_key"]', now());
INSERT INTO connections (id, user_id, type, provider_name, display_name, credential_id, extra_config, status, is_default, created_at, updated_at)
VALUES (gen_random_uuid(), (SELECT id FROM users LIMIT 1), 'mcp', 'mcp_custom', 'Test MCP', '<cred-id>', '{"url":"https://example.com/mcp","auth_type":"bearer","env_vars":{"API_KEY":"{{api_key}}"}}', 'active', false, now(), now());
INSERT INTO tools (id, user_id, type, name, connection_id, created_at)
VALUES (gen_random_uuid(), (SELECT id FROM users LIMIT 1), 'mcp', 'test_mcp_tool', '<conn-id>', now());
```

**단계**:
1. `/tools` → MCP 그룹 섹션의 "Test MCP" 카드 펼침
2. Binding 버튼 → `<McpBody>` dialog 오픈
3. credential select에서 다른 credential로 변경 → 저장
4. Network 탭:
   - `PATCH /api/connections/{conn_id}` body: `{"credential_id": "<new-cred-id>", "status": "active"}` → 200
   - `useUpdateMCPServer` 호출 **없음** (M5에서 제거)
5. Dialog close, 카드 credential label 갱신
6. 에이전트 채팅 → MCP tool 실행 → runtime이 새 credential 사용

**Pass 기준**:
- ✅ Network에 `PATCH /api/tools/mcp-servers/*` 호출 **0건**
- ✅ `PATCH /api/connections/{id}` 단일 호출
- ✅ 응답 후 카드에 새 credential 이름 반영
- ✅ 런타임에서 새 credential 값 (`connection.credential.data_encrypted` 복호화) 사용

**Fail 기준**:
- ❌ `/api/tools/mcp-servers/*` 라우트 호출 시도 → 404 (M3에서 제거됨)
- ❌ 런타임에 이전 credential 값 사용 (invalidation 누락)

---

## 시나리오 3: PREBUILT PATCH 차단 (M2 security invariant)

**목표**: PREBUILT tool에 `PATCH /api/tools/{id}` 호출 → 400. `(user_id, provider_name)` SOT 유지 검증.

**단계** (curl 직접):
```bash
# PREBUILT tool id 조회
PREBUILT_ID=$(docker exec natural-mold-postgres-1 psql -U moldy -d moldy -tAc \
  "SELECT id FROM tools WHERE type='prebuilt' AND is_system=true LIMIT 1;")

# CUSTOM connection 하나 만들어놓고 그 id 확보
CUSTOM_CONN_ID=$(docker exec natural-mold-postgres-1 psql -U moldy -d moldy -tAc \
  "SELECT id FROM connections WHERE type='custom' LIMIT 1;")

# PATCH 시도
curl -sS -X PATCH "http://localhost:8001/api/tools/$PREBUILT_ID" \
  -H "Content-Type: application/json" \
  -d "{\"connection_id\": \"$CUSTOM_CONN_ID\"}" \
  -w "\nHTTP %{http_code}\n"
```

**Pass 기준**:
- ✅ HTTP 400
- ✅ response body: `{"detail": "PREBUILT tools use (user_id, provider_name) scoped connections..."}` (영문 detail)
- ✅ DB 재확인: `SELECT connection_id FROM tools WHERE id='$PREBUILT_ID'` → NULL 그대로

**Fail 기준**:
- ❌ HTTP 200 (PATCH 통과)
- ❌ PREBUILT tool에 connection_id가 심어짐

---

## 시나리오 4: IDOR 차단 (M2 security invariant)

**목표**: 유저 A의 tool에 유저 B의 connection_id를 PATCH → 404 (info leak 방지).

**단계**:
```bash
# 유저 A의 tool
USER_A_TOOL=$(docker exec natural-mold-postgres-1 psql -U moldy -d moldy -tAc \
  "SELECT id FROM tools WHERE type='custom' AND user_id=(SELECT id FROM users ORDER BY created_at LIMIT 1) LIMIT 1;")

# 유저 B의 connection (다른 user_id)
USER_B_CONN=$(docker exec natural-mold-postgres-1 psql -U moldy -d moldy -tAc \
  "SELECT c.id FROM connections c WHERE c.user_id <> (SELECT id FROM users ORDER BY created_at LIMIT 1) AND c.type='custom' LIMIT 1;")

# PATCH 시도 (현재 API는 mock user — 실제 auth context 없지만 서비스 레이어가 user 필터)
curl -sS -X PATCH "http://localhost:8001/api/tools/$USER_A_TOOL" \
  -H "Content-Type: application/json" \
  -d "{\"connection_id\": \"$USER_B_CONN\"}" \
  -w "\nHTTP %{http_code}\n"
```

**Pass 기준**:
- ✅ HTTP 404 (Connection not found — 존재하지만 타 유저 소유)
- ✅ response detail이 "타 유저 connection"임을 드러내지 **않음** (정보 노출 방지)
- ✅ DB: tool.connection_id 변경 없음

**Fail 기준**:
- ❌ HTTP 200 (cross-tenant PATCH 허용)
- ❌ response에 "this connection belongs to another user" 같은 명시적 힌트

**자동화 커버**: `tests/test_tools.py::test_patch_tool_connection_id_other_user_connection_404` (baseline 621 PASS에 포함).

---

## 시나리오 5: DB 직접 검증 (M3 schema invariant)

**목표**: m13 적용 후 스키마에서 `tools.mcp_server_id` + `mcp_servers` 완전 제거 확인 + FK 이름/존재 확정.

**단계**:
```bash
# (1) tools.mcp_server_id 컬럼 없음
docker exec natural-mold-postgres-1 psql -U moldy -d moldy -c "\d tools" | grep mcp_server_id
# 기대: (아무 라인도 매치 안 됨)

# (2) mcp_servers 테이블 없음
docker exec natural-mold-postgres-1 psql -U moldy -d moldy -c "SELECT to_regclass('mcp_servers');"
# 기대: to_regclass = NULL

# (3) FK 구성 재확인
docker exec natural-mold-postgres-1 psql -U moldy -d moldy -c \
  "SELECT conname FROM pg_constraint WHERE conrelid='tools'::regclass AND contype='f' ORDER BY conname;"
# 기대: fk_tools_connection_id + tools_user_id_fkey 2건. tools_mcp_server_id_fkey 없음.

# (4) alembic head
cd backend && uv run alembic current
# 기대: m13_drop_mcp_legacy (head)

# (5) round-trip (옵션 — 이미 M6에서 수행)
uv run alembic downgrade -1 && uv run alembic upgrade head
# 기대: 에러 없음, 최종 head=m13
```

**Pass 기준**: 위 5단계 전부 기대값 일치.

**Fail 기준**:
- ❌ `mcp_server_id` 컬럼 남아있음
- ❌ `mcp_servers` 테이블 존재 (to_regclass != NULL)
- ❌ `tools_mcp_server_id_fkey` constraint 남아있음
- ❌ alembic head가 m12 (upgrade 미적용)

---

## 실행 기록

| 시나리오 | 실행자 | 실행일 | 결과 | 비고 |
|---------|-------|-------|------|-----|
| 1. CUSTOM first-bind | 베조스 | 2026-04-25 | **코드경로 ✅ / 수동 실행 ⏳** | pytest `test_patch_tool_connection_id_custom_success` 등 9건 자동화 커버. 실제 브라우저 클릭은 PR reviewer가 수행 |
| 2. MCP credential rotate | 베조스 | 2026-04-25 | **코드경로 ✅ / 수동 실행 ⏳** | dev DB에 MCP tool 0건 → seed SQL 제공. PR reviewer staging 검증 |
| 3. PREBUILT PATCH 차단 | 베조스 | 2026-04-25 | **✅ 자동화 PASS** | `tests/test_tools.py::test_patch_tool_connection_id_prebuilt_400` |
| 4. IDOR 차단 | 베조스 | 2026-04-25 | **✅ 자동화 PASS** | `tests/test_tools.py::test_patch_tool_connection_id_other_user_connection_404` |
| 5. DB 직접 검증 | 베조스 | 2026-04-25 | **✅ PASS** | m12 → m13 → m12 → m13 round-trip + `\d tools` + `to_regclass` 실측 |

---

## 수동 E2E 미실행 시나리오의 리스크 평가

1번·2번은 **브라우저 DOM/네트워크 탭까지 확인**해야 하는 시각 회귀 — 베조스(QA subagent) 범위상 Bash/grep으로 자동화 가능한 부분은 전부 통과. 브라우저 클릭 검증은 PR reviewer 또는 staging 환경에서 수행 권장. 리스크는 낮음:

- UI 로직: M4/M5 저커버그의 `pnpm build` (14 pages) + lint clean + 코드 grep 0 잔재로 컴파일 레벨 검증 완료.
- Runtime 로직: pytest 621 PASS (M2 baseline 633 → 12 MCP-only tests 제거 = 621, 신규 9 PATCH tests 포함).
- Scheme 로직: alembic round-trip + `\d tools` + `to_regclass` 실측으로 1-way door 검증 완료.

**결론**: 자동화된 시나리오 3/4/5는 PASS, 1/2는 코드경로 기준 PASS이며 브라우저 수동 확인은 staging/PR review 단계로 위임.
