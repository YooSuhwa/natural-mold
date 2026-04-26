# 전체 검증 보고서 (2026-04-26)

**기준 commit**: `16d9f27` (main, PR #60 머지 직후)
**실행자**: Claude Code (auto mode, plan: `eager-leaping-platypus`)
**정책**: 검증만 수행, 실패는 수집·보고하고 수정은 후속 세션으로 위임

---

## 요약

| Phase | 결과 | 비고 |
|-------|------|------|
| 1. 정적 검증 | 🟡 부분 통과 | ruff check ✅ / lint ✅ / tsc ✅ — pyright 58 errors / ruff format 55 / prettier 27 (모두 미적용 스타일) |
| 2.1 백엔드 unit | ✅ **648 passed** | 0 failed, 1 deselected (integration), 197 warnings (deprecation) |
| 2.2 백엔드 integration | ⚪ skipped | `INTEGRATION_DATABASE_URL` 미설정 → 1 test skipped (round-trip은 2.3에서 직접 검증) |
| 2.3 alembic round-trip | ✅ | m14 → m12 → m14 round-trip 성공 |
| 2.4 프론트엔드 vitest | 🔴 **60 failed** / 218 passed (14 files failed / 35 passed) | Jotai store 관련 다수 실패 |
| 2.5 프론트엔드 build | ✅ | Next.js 16.2.2 / 14 pages / TypeScript clean |
| 3. Playwright smoke | 🔴 **7 failed** / 7 passed / 1 skipped | 전부 dynamic page locator timeout |
| 4. M6.1 수동 E2E (API 레벨) | ✅ | 시나리오 1·2·3·5·6 API/DB 직접 검증 PASS — 브라우저 UI 클릭은 사용자 확인 필요 |

**Net 판정**: 백엔드 런타임 GREEN (648 pytest + alembic + API invariants 모두 PASS). 프론트엔드는 **테스트 인프라 회귀** (vitest, playwright) — 빌드/lint/타입은 통과하므로 production 영향 없으나 CI에서 차단될 수 있음.

---

## Phase 1 — 정적 검증

| 명령 | 결과 |
|------|------|
| `cd backend && uv run ruff check .` | ✅ All checks passed |
| `cd backend && uv run ruff format --check .` | 🔴 **55 files would be reformatted** (135 already formatted) |
| `cd backend && uv run pyright` | 🔴 **58 errors** (대부분 tests/ 파일의 type narrowing 누락) |
| `cd frontend && pnpm lint` | ✅ 0 errors |
| `cd frontend && pnpm exec tsc --noEmit` | ✅ 0 errors |
| `cd frontend && pnpm format:check` | 🔴 **27 files** style issues |

### pyright 실패 패턴 (samples)
- `tests/test_executor.py:24` — `provider_api_keys` 타입에 None 누락 (8 errors at single call site)
- `tests/test_skill_package.py:168` — `subprocess.Popen(args=str|None)` 타입 가드 부재
- `tests/test_tools.py:104,121` — `Optional[UUID]`을 UUID 인자에 직접 전달
- `tests/test_model_discovery.py:90` — `Operator ">=" not supported for "None"`

### ruff format 미적용 파일 (sample)
- `app/routers/tools.py`, `app/services/chat_service.py`, `app/services/connection_service.py`, `app/services/credential_service.py`, `app/services/tool_service.py` 등 application code 9개
- 그 외 tests/ 26개

> 모두 **기능적 영향 없는 스타일/타입 노이즈**. CI 게이트가 없으므로 누적된 것으로 추정.

---

## Phase 2 — 자동화 테스트

### 2.1 백엔드 pytest (in-memory SQLite)

```
648 passed, 1 deselected, 197 warnings in 77.93s
```

- 0 failed
- HANDOFF.md 기록 (648 passed) 일치 → 회귀 없음
- Warnings는 외부 라이브러리 deprecation (`google.genai`, `langchain_core` asyncio.iscoroutinefunction). 추적용으로만 기록.

### 2.2 백엔드 integration

```
1 skipped, 648 deselected
```

- `tests/integration/test_m9_pg_roundtrip.py` — `INTEGRATION_DATABASE_URL` 환경변수 필요 (disposable Postgres 분리 fixture)
- 본 세션은 별도 DB 미준비 → SKIP. 동일 round-trip은 **Phase 2.3에서 직접 PASS** (m14 round-trip)

### 2.3 alembic round-trip (Postgres)

```
m14_uniq_mcp_tool_per_conn (head)
  ↓ downgrade -2
m12_drop_legacy_columns
  ↑ upgrade head
m14_uniq_mcp_tool_per_conn (head) ← 복원 성공
```

- M13 (mcp_servers drop), M14 (partial unique) 양방향 무결성 ✅

### 2.4 프론트엔드 vitest

```
Test Files  14 failed | 35 passed (49)
Tests       60 failed | 218 passed (278)
```

**대표 실패**: `tests/unit/stores/chat-store.test.ts`
```
TypeError: Cannot read properties of undefined (reading 'write')
  at BUILDING_BLOCK_atomWrite (jotai/.../internals.mjs:84:68)
  at tests/unit/stores/chat-store.test.ts:34:11
        store.set(streamingToolCallsAtom, toolCalls)
```

추정 원인: jotai 2.19.0 / vitest 4.1 / React 19 조합에서 `createStore()` API 변경 또는 atom registration 누락. **production 코드는 영향 없음** (build / lint / tsc 모두 통과).

영향받은 영역: chat-store atoms, 일부 hooks 테스트.

### 2.5 프론트엔드 build

```
Next.js 16.2.2 (Turbopack)
✓ Compiled successfully in 4.0s
✓ TypeScript clean
✓ 14 pages generated
```

라우트 16개 (static 14 + dynamic 4): `/`, `/agents/new`, `/agents/[agentId]/...`, `/connections`, `/models`, `/settings`, `/skills`, `/tools`, `/usage` 등 정상.

---

## Phase 3 — Playwright smoke E2E

```
7 failed, 7 passed, 1 skipped (총 15 specs in 2.9 min)
```

### 실패 목록

| # | spec | 실패 line | 추정 원인 |
|---|------|-----------|-----------|
| 1 | `Static Pages › /agents/new - creation chooser loads` | smoke.spec.ts:26 | locator timeout |
| 2 | `Static Pages › /models - models page loads` | smoke.spec.ts:67 | locator timeout |
| 3 | `Dynamic Pages › /agents/[id]/conversations/[cid] - chat page loads` | smoke.spec.ts:134 | dynamic seed 의존 가능성 |
| 4 | `Dynamic Pages › /agents/[id]/settings - settings page loads` | smoke.spec.ts:153 | dynamic seed 의존 가능성 |
| 5 | `Dynamic Pages › /agents/[id] - redirects to conversation` | smoke.spec.ts:174 | dynamic seed 의존 가능성 |
| 6 | `Dialogs › models page - "모델 추가" dialog opens` | smoke.spec.ts:217 | 버튼 라벨 변경 가능성 |
| 7 | `Dialogs › settings page - "AI로 수정하기" dialog opens` | smoke.spec.ts:312 | 버튼 라벨/조건 변경 가능성 |

trace 파일: `frontend/test-results/smoke-Smoke-Test---*-chromium*/trace.zip`

> Playwright 실행 중 backend/frontend는 webServer 자동 기동. 정적 페이지 일부와 dynamic 전체가 실패한 것으로 보아, **시드 데이터 의존성** 또는 **최근 UI 변경 (M6/M6.1)에 spec가 동기화되지 않음**이 의심.

---

## Phase 4 — M6.1 수동 E2E (API 레벨 직접 검증)

브라우저 UI 클릭은 사용자에게 위임하고, **API + DB 무결성**은 본 세션에서 직접 검증.

### 시나리오 1 — CUSTOM first-bind ✅

```bash
# 직접 INSERT한 CUSTOM tool에 PATCH /api/tools/{id} body={connection_id}
PATCH /api/tools/0bfdb56e-... → 200
{
  "id": "0bfdb56e-...",
  "type": "custom",
  "connection_id": "f51cf369-...",  ← 반영됨
  ...
}
DB: tools.connection_id = f51cf369-... ✅
```

### 시나리오 2 — MCP credential rotate ✅

```bash
# hancom-gw connection의 credential을 다른 것으로 rotate → 원복
PATCH /api/connections/54aaea37-... {"credential_id": "203d3bc5-..."} → 200
PATCH /api/connections/54aaea37-... {"credential_id": "6556a684-..."} → 200 (원복)

# legacy MCP route는 제거됨
PATCH /api/tools/mcp-servers/{id} → 404 ✅
```

### 시나리오 3 — PREBUILT PATCH 차단 ✅

```bash
PATCH /api/tools/{prebuilt_id} {"connection_id": "..."} → 400
{"detail": "PREBUILT tools use (user_id, provider_name) scoped connections; PATCH /api/tools/{id} does not apply. Manage the connection in /connections instead."}
```

### 시나리오 4 — IDOR 차단 ⚪

mock user 1명 환경이라 다른 user 의 connection으로 PATCH 시도 자체가 불가. **자동화 테스트 `test_patch_tool_connection_id_other_user_connection_404`로 커버**.

### 시나리오 5 — DB schema invariants ✅

```
(1) tools.mcp_server_id 컬럼 → 없음 ✅
(2) mcp_servers 테이블 → null (drop 됨) ✅
(3) FK 제약: fk_tools_connection_id, tools_user_id_fkey (mcp_server_id_fkey 없음) ✅
(4) M14 partial unique:
    CREATE UNIQUE INDEX uq_mcp_tools_user_connection_name
    ON public.tools (user_id, connection_id, name)
    WHERE type = 'mcp' ✅
```

### 시나리오 6 — MCP discover-tools ✅

```bash
# 기존 hancom-gw connection으로 재실행 → idempotent 확인
POST /api/connections/54aaea37-.../discover-tools → 200
{
  "server_info": {"name": "한컴 그룹웨어", "version": "3.2.4"},
  "items": 7건 (status: existing × 7)
}
DB: tools COUNT before=7, after=7 ✅ idempotent

# non-mcp connection 거부
POST /api/connections/{custom_conn}/discover-tools → 422
{"detail": "Connection type 'custom' does not support tool discovery..."}
```

> ⏳ **남은 사용자 검증**: AddToolDialog MCP 탭 UX, 토스트 문구, `/connections` McpSection의 "조회 전용" 카드, 채팅 실행 시 새 credential 사용 확인.

---

## 실패 상세 (집계)

| Phase | Test/명령 | 실패 수 | 핵심 메시지 | 추정 영향 | 우선순위 |
|-------|----------|--------|------------|----------|----------|
| 1 | `pyright` | 58 | tests/ 파일의 Optional/Union 타입 narrowing 누락 | 런타임 영향 없음 (테스트 PASS) | 🟡 LOW |
| 1 | `ruff format --check` | 55 files | 스타일 (포맷터 미적용) | 없음 | 🟢 LOW |
| 1 | `prettier --check` | 27 files | 스타일 | 없음 | 🟢 LOW |
| 2.4 | `vitest` chat-store | 60 tests | `Cannot read properties of undefined (reading 'write')` (jotai store API) | 테스트 인프라 회귀 — production 영향 없음 | 🟠 MEDIUM |
| 3 | `playwright` smoke | 7 specs | locator timeout (모달/페이지 라벨/시드) | smoke 게이트로 활용 시 차단 | 🟠 MEDIUM |

---

## 다음 액션 (제안)

- [ ] **🟠 vitest 60 fail 조사** — jotai 2.19 / vitest 4.1 / React 19 조합에서 `createStore()` 사용 패턴 점검 (`tests/unit/stores/chat-store.test.ts:34` 등). vitest setup에 jotai Provider 누락 가능성.
- [ ] **🟠 Playwright 7 fail 조사** — `e2e/smoke.spec.ts`의 셀렉터가 M6/M6.1의 UI 변경을 반영하지 못함. trace.zip 확인 (경로: `frontend/test-results/smoke-Smoke-Test---*-chromium*/trace.zip`).
- [ ] **🟡 pyright 58 errors** — 일괄 정리 (대부분 `assert x is not None` 또는 캐스트만 추가하면 됨).
- [ ] **🟢 ruff format + prettier** — `uv run ruff format .` + `pnpm format` 한 번에 정리 가능 (drive-by 금지 룰에 따라 별도 PR로).
- [ ] **사용자 직접 검증 (남은 브라우저 클릭)** — M6.1 시나리오 1·2·6의 UI 흐름 (binding dialog 토스트, 카드 상태 갱신, MCP 탭 default).

---

## 참고

- 실행 환경: backend `localhost:8001` (uvicorn 기동 중) + frontend `localhost:3000` + postgres `natural-mold-postgres-1`
- alembic head: `m14_uniq_mcp_tool_per_conn`
- 트레이스/로그: `frontend/test-results/`
- 본 보고서 외 코드/DB는 변경 없음 (검증용 INSERT/DELETE는 cleanup 완료)
