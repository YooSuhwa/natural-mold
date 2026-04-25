# progress.txt — 백로그 E M6.1

## 프로젝트 컨텍스트
- **목표**: `tool.connection_id` single source of truth 확정 + MCP legacy 완전 제거
- **Base**: main @ 18d98be (M6 PR #59 머지)
- **브랜치**: feature/backlog-e-m6-1
- **Worktree**: `.claude/worktrees/backlog-e-m6-1`
- **계획**: `/Users/chester/.claude/plans/m6-1-spicy-kurzweil.md`

## M6 상속 자산 (그대로 사용)
- m12 migration 패턴 (`backend/alembic/versions/m12_drop_legacy_columns.py`) — m13 템플릿으로 활용
- `services/legacy_invariants.py` — m13 preflight 헬퍼 추가 지점
- `main.py::_enforce_m6_legacy_invariants` — startup guard 확장
- `useFindOrCreateCustomConnection` — 프론트 CUSTOM N:1 find-or-create
- `frontend/src/lib/types/index.ts` Tool 타입 (M6에서 이미 auth_config/credential_id 제거됨)
- pytest baseline: **624 passing** (M6 완료 시점)

## M6.1 제거 대상 (파일:라인 기준)

### Backend
- `backend/app/models/tool.py:34` — `class MCPServer`
- `backend/app/models/tool.py:71` — `mcp_server_id` FK 컬럼
- `backend/app/models/tool.py` — `Tool.mcp_server` relationship
- `backend/app/services/credential_service.py:111` — `resolve_server_auth(server: MCPServer)`
- `backend/app/services/chat_service.py:384-392` — MCP fallback 분기 (`# Legacy fallback — M3~M6 이행기... M6.1에서 제거` 주석 있음)
- `backend/app/services/tool_service.py` — MCPServer CRUD 함수 6종 (`list_mcp_servers`, `register_mcp_server`, `update_mcp_server`, `delete_mcp_server`, 등)
- `backend/app/routers/tools.py` — `/api/tools/mcp-server*` 4개 라우트 + `test_mcp_connection` 재작성
- `backend/app/schemas/tool.py` — `MCPServerCreate` / `MCPServerResponse` / `MCPServerUpdate` + `ToolResponse.mcp_server_id`

### Frontend
- `frontend/src/lib/hooks/use-tools.ts:97` — `useUpdateMCPServer` (유사하게 useRegisterMCPServer, useMCPServers, useDeleteMCPServer, useToolsByConnection의 mcp_server_id 참조)
- `frontend/src/lib/api/tools.ts` — `updateMCPServer` + MCP server CRUD 4종
- `frontend/src/lib/types/index.ts` — `MCPServer*` 타입 전부 + `Tool.mcp_server_id`
- `frontend/src/components/tool/mcp-server-rename-dialog.tsx` — 파일 전체 삭제
- `frontend/src/components/connection/connection-binding-dialog.tsx:339-345, 421-429` — `needsOptionDFirstBind` 가드 제거
- `frontend/src/components/connection/connection-binding-dialog.tsx:479` — `useUpdateMCPServer` 호출 제거 (MCP body)
- `frontend/src/components/tool/mcp-server-group-card.tsx:143-151` — triggerContext 정리

## M6.1 신설 대상

### Backend
- `backend/app/schemas/tool.py::ToolUpdate` — `connection_id: UUID | None` 한 필드, `extra="forbid"`
- `backend/app/services/tool_service.py::update_tool(db, tool_id, user_id, payload)`
- `backend/app/routers/tools.py` — `PATCH /api/tools/{id}`
- `backend/alembic/versions/m13_drop_mcp_legacy.py`

### Frontend
- `frontend/src/lib/api/tools.ts::toolsApi.update(id, { connection_id })`
- `frontend/src/lib/hooks/use-tools.ts::useUpdateTool`
- `frontend/src/components/connection/binding-dialog-shell.tsx` — 공용 Shell 추출

## Invariants (변경 금지)
- PREBUILT는 `(user_id, provider_name)` 스코프. `tool.connection_id` 사용하지 않음. PATCH 요청 오면 **400**.
- PREBUILT `provider_name IS NULL` → `cred_auth = {}` (env fallback과 동치)
- CUSTOM `connection_id` NULL 시 `ToolConfigError` fail-closed (legacy 위임 없음)
- aiosqlite 테스트는 모델 기반 create_all. alembic round-trip은 docker PG에서만.
- `extra="forbid"` 전파 — 프론트/테스트에서 새 필드 쓸 때 실제로 스키마에 반영

## Codebase Patterns (M6 확인)
- Alembic revision naming: `m{N}_<snake_case>`. 최신 head = `m12_drop_legacy_columns`. 다음 = `m13_drop_mcp_legacy`.
- FK constraint naming: 명시적이면 `fk_<table>_<col>`, 없으면 PG 기본 `<table>_<col>_fkey`
- 서비스 레이어: Router → Service → Model, async SQLAlchemy 2.0, select() 구문
- 테스트 관례: `backend/tests/test_<resource>.py`, aiosqlite in-memory conftest

## Gotchas (M6 상속)
- `tools.mcp_server_id` FK 이름 `tools_mcp_server_id_fkey`는 initial migration이 이름 없이 생성해서 PG 기본 네이밍. 배포 전 `\d tools` 실측 필수 (`m12-cleanup-migration-spec.md §8.2`).
- `useUpdateTool` 네이밍 충돌 — use-tools.ts에 이미 `useUpdateToolAuthConfig`(M6에서 제거됨) 등 선례 있음. grep 후 결정.
- `test_mcp_connection` 라우터는 connection entity 경유로 완전 재작성. mcp URL/auth는 `connection.extra_config`에서 읽음.
- `mcp-server-rename-dialog.tsx`는 M6에서 "drive-by 금지"로 보존됐음 — M6.1에서 삭제 대상. connection rename은 `/connections` 페이지에서 PATCH로 대체 가능 (M5 UI 통합분).
- `triggerContext` prop은 PrebuiltBody L178-180에서만 쓰인다 (탐색 결과). 제거 또는 일원화 택1.
- `connection-binding-dialog.tsx`의 CustomBody/McpBody/PrebuiltBody는 hydration 패턴(hydrationKey, hydratedFor, mode state) 동일 → Shell 추출 가능.

## 팀원 간 계약
- **베조스 M1 → 전원**: 제거 대상 확정 보고서 (GREEN 판정) 나오면 M2/M4 병렬 착수
- **젠슨 M2 → 저커버그 M4**: `ToolUpdate` 스키마 + `PATCH /api/tools/{id}` 라우트 커밋되면 프론트 API 호출 가능 (저커버그는 타입만 앞당겨 정의 가능)
- **젠슨 M3 → 저커버그 M5**: `mcp_servers` drop + `/api/tools/mcp-server*` 제거 커밋 후 프론트 MCP re-wire
- **베조스 M6**: 모든 구현 완료 후 통합 회귀 + 수동 E2E

## 실패 교훈
(아직 없음)

## M1 베조스 발견 (2026-04-24)

### 신규 Gotcha
- `credential_service.get_usage_count` 반환에 `mcp_server_count` 키 존재 (L172). M3에서 제거 시 프론트 `CredentialUsage` 타입(lib/types/index.ts L210-213) + `/connections` 페이지 사용량 배지까지 전파. **M3 착수 전 `CredentialUsage` 소비처 grep 필수**.
- `test_mcp_connection` 라우터는 `POST /api/tools/mcp-server/{id}/test` 경로. 재작성 시 `POST /api/tools/{tool_id}/test`로 경로 의미 변경 (server_id → tool_id). 프론트 `toolsApi.testMCPConnection(serverId)` 호출처 전수 확인 필요 (grep 결과 현재 사용처 0건일 가능성).
- `frontend/src/app/tools/page.tsx` L472-495의 mcp grouping이 `mcp_server_id` 기반. M5에서 grouping key를 `tool.connection_id`로 재매핑할 때 UX(그룹 카드 섹션) 유지하며 내부 키만 교체 — scope creep 금지.
- `connection-binding-dialog.tsx` McpBody(L456-516)는 이중 PATCH(server + connection) 패턴. M5 재배선 시 `useUpdateConnection`(extra_config) + `useUpdateTool`(connection rebind) 조합으로 축소 예정 — server PATCH는 완전 제거.

### 확정 결정사항
- **`useUpdateTool` 네이밍 충돌 없음** (grep 확인). 해당 훅 이름 사용 가능.
- `executor.py` L263-267, L401의 `mcp_server_url`/`mcp_tool_name`/`mcp_transport_headers` 키는 **connection 경로에서도 그대로 사용** — 제거 대상 아님 (K).
- `schemas/connection.py` L125, L186의 `resolve_server_auth`/`MCPServerResponse` 주석은 docstring 참조만 — L186은 정리 (D), L125는 유지 (K).
- `chat_service.py` L393-394 `else: cred_auth = {}` MCP fallback 분기는 제거 후 **`ToolConfigError` fail-closed로 전환** (CUSTOM 경로와 정합).
- BindingDialogShell은 **UI chrome(Dialog/Header/Footer/Credential section)만** 이관. hydration 로직은 body 소유 — 추상화 비용 방지.
- `tests/integration/test_m9_pg_roundtrip.py`의 MCPServer 참조 5건은 **보존** (m9 migration round-trip 테스트 — M6.1에서 건드리지 말 것).

### Scope creep 경고
- `agent_tools.connection_id` override는 M5.5 — M6.1 범위 아님.
- `ToolUpdate` 필드는 `connection_id` 단 하나. `name`/`description` 등 확장 금지.
- `/tools` 페이지 MCP 그룹 섹션 UX 유지 — grouping key만 `mcp_server_id` → `connection_id` 교체.
- PREBUILT PATCH 400 원칙 유지 — `is_system=True` PREBUILT 행에 connection_id 심는 유혹 금지.

### 판정: GREEN
- M2/M4 즉시 착수 가능. M3 착수 전 `CredentialUsage.mcp_server_count` 프론트 소비처만 한 번 확인.

## M2 젠슨 결정 (2026-04-24)

### 구현 위치
- `schemas/tool.py` — `ToolUpdate(BaseModel)` (`model_config = ConfigDict(extra="forbid")`, `connection_id: uuid.UUID | None = None`). pydantic v2 ConfigDict import 추가.
- `services/tool_service.py::update_tool` — `delete_tool` 직전에 배치. `selectinload(Tool.connection)` + `selectinload(Connection.credential)` 사용.
- `routers/tools.py` — `PATCH /{tool_id}` (response_model=ToolResponse). `DELETE /{tool_id}` 바로 위에 배치 (FastAPI 경로 우선순위 안전, `/mcp-server/*`와 충돌 없음).

### 검증 순서 (HTTP 코드 매핑)
1. tool 미존재 → `tool_not_found()` (404)
2. user-tool & 타 유저 소유 → `tool_not_found()` (404, 정보 노출 방지)
3. PREBUILT(ToolType.PREBUILT) → 400 with English detail "PREBUILT tools use (user_id, provider_name) scoped connections..."
   - is_system PREBUILT 행도 동일 — global asset이므로 ownership 체크 우회 후 PREBUILT 분기에서 거부
4. payload.connection_id is not None:
   - connection 미존재 또는 타 유저 → 404 "Connection not found" (IDOR)
   - connection.type != tool.type → 422 with detail
5. payload.connection_id is None → 단순 `tool.connection_id = None` 할당 후 commit (해제)
6. `await db.refresh(tool, attribute_names=["connection"])` — eager refresh

### 테스트 (9건 추가, baseline 624 → 633)
- `test_patch_tool_connection_id_custom_success` — CUSTOM 정상 200
- `test_patch_tool_connection_id_mcp_success` — MCP 정상 200 (mcp_server_id 없이 type='mcp' tool 직접 생성)
- `test_patch_tool_connection_id_prebuilt_400` — is_system=True PREBUILT (provider="naver") → 400, detail에 "PREBUILT" 포함
- `test_patch_tool_connection_id_other_user_connection_404` — 다른 유저 connection_id → 404
- `test_patch_tool_connection_id_type_mismatch_422` — CUSTOM tool + MCP connection → 422, detail에 "does not match"
- `test_patch_tool_connection_id_none_clears_binding` — None 전송 → 200 + null
- `test_patch_tool_nonexistent_404` — 존재하지 않는 tool_id → 404
- `test_patch_tool_unknown_field_422` — `{"name": "renamed"}` extra=forbid → 422
- `test_patch_tool_other_user_owned_tool_404` — 타 유저 user-tool PATCH → 404 (info leak 방지)
- 신규 helper `_seed_credential_and_connection(db, conn_type, provider_name, display_name)` — 테스트 픽스처 재사용성 확보

### 후속 (저커버그 M4용)
- `useUpdateTool` 훅 구현 시 `PATCH /api/tools/{id}` body는 `{connection_id: string | null}` 단일 필드만 — extra 필드는 422
- 응답은 `ToolResponse` (기존 GET /api/tools 응답과 동일 shape, `connection_id` 포함)
- `connection_id=null` 전송 시 200 응답 + `connection_id: null` — 해제 동작 보장

## M4 저커버그 결정 (2026-04-24)

### 파일:라인 수정 요약
- `frontend/src/lib/api/tools.ts:25-29` — `toolsApi.update(id, { connection_id })` 추가. PATCH `/api/tools/${id}`, body single field, 응답 `Tool`.
- `frontend/src/lib/hooks/use-tools.ts:55-67` — `useUpdateTool()` 훅. `invalidateQueries(['tools'])` + `['agents']`. 인자 시그니처 `{id, data: {connection_id?: string | null}}`.
- `frontend/src/components/connection/connection-binding-dialog.tsx`
  - L36 — import에 `useUpdateTool` 추가 (기존 `useUpdateMCPServer` 동일 라인)
  - L314 — `const updateTool = useUpdateTool()` 추가
  - L335 — `isPending`에 `updateTool.isPending` OR 추가
  - L337-341 (구) — `needsOptionDFirstBind` 상수 + `saveDisabled` 계산 완전 제거
  - L343-345 (구) — `handleSave` 가드/토스트 제거
  - L352-360 (신) — CUSTOM `handleSave` else 브랜치: `findOrCreate.run` 반환 후 `tool && !tool.connection_id`일 때 `updateTool.mutateAsync({id: tool.id, data: {connection_id: result.id}})` 체인
  - L421-429 (구) — 경고 UI 블록 제거
  - L435 (구→신) — `disabled={saveDisabled}` → `disabled={isPending}` 단순화
- `AlertTriangleIcon` import은 McpBody(L526)가 여전히 사용 → 보존.

### Gotchas
- `pnpm lint` 예상 1건(use-chat-runtime.ts:74) 실제로는 **0 warnings** 출력됨. eslint config가 해당 규칙 exempt 중이거나 이미 해결된 것으로 보임 — 베조스 M6 회귀 검증에서 재확인 대상.
- `useFindOrCreateCustomConnection().run`은 cached scope에서 credential_id 매칭 connection을 재사용 (ADR-008 N:1). 즉 기존 connection을 공유할 때도 PATCH /api/tools는 새 binding만 생성 — safe. 다른 tool의 connection_id를 덮어쓰지 않는다.
- 체인 순서: (1) findOrCreate → (2) updateTool. updateTool 실패 시 findOrCreate가 만든 connection은 "orphaned" 가능성 있으나 N:1 재사용 패턴상 재시도 시 재활용돼 누수 없음 — 베조스 M6 수동 E2E에서 실패 시나리오 커버.

### 스코프 경계 준수 (베조스 경고 반영)
- McpBody(`updateMCPServer` 사용) 변경 **없음** — M5
- `BindingDialogShell` 추출 **없음** — M5
- `mcp-server-rename-dialog.tsx` 삭제 **없음** — M5
- `triggerContext` 일원화 **없음** — M5
- `Tool.mcp_server_id` 타입 정리 **없음** — M5 (`MCPServer*` 타입 삭제와 같은 커밋에서)

### 검증 결과
- `pnpm lint`: **PASS** (0 warnings, 0 errors)
- `pnpm build`: **PASS** (15 routes 전부 컴파일 성공, TypeScript 3.9s)

## M3 젠슨 결정 (2026-04-24)

### 파일:라인 수정 요약
- `backend/alembic/versions/m13_drop_mcp_legacy.py` (신규) — preflight (`_assert_no_stale_legacy_rows()` → `collect_legacy_checks`) + `tools_mcp_server_id_fkey` drop + `tools.mcp_server_id` 컬럼 drop + `fk_mcp_servers_credential_id` drop + `mcp_servers` 테이블 drop. SQLite 분기로 FK drop skip (구조만 round-trip). down_revision=`m12_drop_legacy_columns`.
- `backend/app/models/tool.py` — `class MCPServer` 전체 + `Tool.mcp_server_id` + `Tool.mcp_server` 관계 제거. `Credential` TYPE_CHECKING import도 정리.
- `backend/app/models/__init__.py` — `MCPServer` import + `__all__` 제거.
- `backend/app/schemas/tool.py` — `MCPServerCreate`/`MCPServerResponse`/`CredentialBrief`/`MCPServerListItem`/`MCPServerUpdate` + `ToolResponse.mcp_server_id` 제거. `Field` import 제거.
- `backend/app/services/credential_service.py` — `resolve_server_auth(server: MCPServer)` 함수 + `MCPServer` import + mcp_count subquery + 반환 dict의 `mcp_server_count` 키 제거. 반환은 `{"tool_count": tool_count}`.
- `backend/app/services/tool_service.py` — `register_mcp_server`/`get_mcp_servers`/`list_mcp_server_items`/`_apply_credential_update`/`update_mcp_server`/`delete_mcp_server` 6개 함수 제거. `MCPServer`/`MCPServerCreate`/`credential_service` import 제거. `update_tool` (M2)/`delete_tool` 보존.
- `backend/app/services/chat_service.py` — `selectinload(Tool.mcp_server).selectinload(MCPServer.credential)` prefetch 체인 제거. L384-394 elif/else legacy fallback 통째로 fail-closed `ToolConfigError`로 교체 (`MCP tool '{name}' has no connection — execution blocked`).
- `backend/app/routers/tools.py` — `POST /mcp-server` (register), `GET /mcp-server` (list), `PATCH /mcp-server/{id}` (update), `DELETE /mcp-server/{id}` (delete), `POST /mcp-server/{id}/test` 4+1개 라우트 제거. `POST /{tool_id}/test` 신설 — `selectinload(Tool.connection).selectinload(Connection.credential)` + `extra_config.url`/`env_vars` 경로로 `mcp_client.test_mcp_connection` 호출.
- `backend/app/error_codes.py` — `mcp_server_not_found()` factory 제거.
- `backend/app/services/legacy_invariants.py` — m13 invariant 추가: `WHERE type='mcp' AND mcp_server_id IS NOT NULL AND connection_id IS NULL` (m9-skip된 stale rows 차단).
- `backend/app/main.py` — startup guard column_exists cache loop에 `("tools", "mcp_server_id")` 추가.
- `backend/app/routers/credentials.py` — `CredentialUsageResponse(mcp_server_count=...)` 인자 제거.
- `backend/app/schemas/credential.py` — `CredentialUsageResponse.mcp_server_count` 필드 제거.
- `backend/app/schemas/connection.py:186` — docstring 업데이트 ("M6.1에서 mcp_servers drop").
- `frontend/src/lib/types/index.ts` — `CredentialUsage.mcp_server_count` 제거. `Tool.mcp_server_id`는 M5 정리용으로 옵셔널 보존.
- 테스트: `test_tools.py`/`test_tools_router_extended.py`/`test_connection_mcp_resolve.py` — MCPServer 참조 헬퍼/케이스 12개 제거. `test_m9_pg_roundtrip.py` 보존(m9 history).

### 신규 Gotcha
- **dev PG 마이그레이션 차단**: `alembic upgrade head` 실행 시 `RuntimeError: M13 preflight failed — 7 row(s)` (Hancom-GW MCP 서버 ID `0578a536...`). 이 7건은 m9 단계에서 `credential_auth_recoverable=False`로 스킵된 row → `connection_id IS NULL AND mcp_server_id IS NOT NULL`. 코드 경로상 이미 죽은 row지만 DROP 전 사티아 결정 필요 (delete vs manual connection 생성).
- `routers/tools.py::test_tool_connection`에서 `selectinload(Tool.connection).selectinload(Connection.credential)` 체인을 위해 `Connection` 직접 import 필요 (`Tool.connection.property.mapper.class_` 식의 우회는 가독성 떨어짐).
- `legacy_invariants.py`에 `mcp_server_id` 문자열 grep 매치는 의도된 SQL — 실제 코드 참조 아님.
- `frontend/src/app/tools/page.tsx`/`use-tools.ts`의 `mcp_server_id` 사용처는 M5 (저커버그) 스코프 — M3에선 타입만 옵셔널 유지하여 빌드 통과.

### 검증 결과
- `uv run pytest`: **621 PASS** (M2 baseline 633 → 12 MCP-only test 제거 = 621)
- `uv run ruff check .`: **clean**
- alembic preflight: dev PG에서 정확히 7건 abort (의도된 동작)

### Scope creep 회피
- `agent_tools.connection_id` (M5.5) 미터치
- 프론트 MCP UI/훅/카드 (M5) 미터치 — `mcp_server_id` TS 필드만 옵셔널로 보존
- `tests/integration/test_m9_pg_roundtrip.py` MCPServer 참조 보존 (m9 round-trip 보호)

## M5 (저커버그, 2026-04-25)

### 완료 작업
- `frontend/src/lib/api/tools.ts` — `registerMCPServer`/`listMCPServers`/`updateMCPServer`/`deleteMCPServer` 4종 + MCP type import 전부 제거. `list`/`createCustom`/`update`/`delete`만 유지.
- `frontend/src/lib/hooks/use-tools.ts` — `useRegisterMCPServer`/`useMCPServers`/`useUpdateMCPServer`/`useDeleteMCPServer` + `invalidateMCPAndTools` 제거. `useToolsByConnection`에서 MCP 분기를 `tool.connection_id` 단일 매칭으로 축소.
- `frontend/src/lib/types/index.ts` — `Tool.mcp_server_id` 제거(M3에서 옵셔널로 보존하던 필드). `MCPServer*` 타입 전부(`MCPServer`/`MCPServerListItem`/`MCPServerUpdateRequest`/`MCPServerCreateRequest`) + `CredentialBrief` 제거.
- `frontend/src/components/connection/binding-dialog-shell.tsx` (신규) — Prebuilt/Custom/Mcp body가 공유하는 UI chrome(Header/Footer/Credential section + CredentialFormDialog)만 추출. hydration/save 로직은 각 body 유지 — 추상화 비용 최소화.
- `frontend/src/components/connection/connection-binding-dialog.tsx` — 재작성. `triggerContext` prop 제거, PrebuiltProps에 `createNew?: boolean` 신설(/connections `+ add` vs tool-edit rotate 구분). McpProps는 `mcpServerId` → `connectionId`로 완전 교체, `connectionName`/`currentCredentialId`로 재설계. McpBody: 기존 dual PATCH(`useUpdateMCPServer` + `useUpdateConnection`) → 단일 `useUpdateConnection`. N:1 공유 경고는 `mcpConnections` sibling 검사로 재정의.
- `frontend/src/components/tool/mcp-server-group-card.tsx` — `MCPServerListItem` 기반 → `Connection` 기반 재작성. rename 메뉴 + `MCPServerRenameDialog` import 제거. delete는 `useDeleteConnection({id, type, provider_name})`. credential label은 `useCredentials` lookup.
- `frontend/src/components/tool/mcp-server-rename-dialog.tsx` — 파일 삭제.
- `frontend/src/app/tools/page.tsx` — `useMCPServers()` → `useConnections({type:'mcp'})`. grouping key `mcp_server_id` → `connection_id`. `filteredMCPServers` → `filteredMCPConnections`. `MCPServerGroupCard` prop `server` → `connection`. `triggerContext="tool-edit"` 3곳 제거.
- `frontend/src/app/connections/page.tsx` — `triggerContext="standalone"` → `createNew` (PrebuiltSection), CustomSection은 prop 삭제만. McpSection "연결 추가" 버튼은 disabled + `addDisabledHint` tooltip.
- `frontend/src/components/connection/connection-detail-sheet.tsx` — `triggerContext` prop 사용 2곳 제거(prebuilt는 `connectionId` 명시로, custom은 `currentConnectionId` 명시로 대체 의미 유지).
- `frontend/src/components/tool/add-tool-dialog.tsx` — **MCP 탭 전체 제거**. Tabs → 단일 Custom form. `useRegisterMCPServer`/`discoveredTools` state/`handleMCPSubmit`/MCP form fields 전부 삭제.
- `frontend/messages/ko.json` — `connections.bindingDialog.custom.unsupportedFirstBindM6`/`toast.unsupportedFirstBindM6` 제거. `tool.mcpServer.menu.rename`/`rename` 블록 제거. `connections.sections.mcp.addDisabledHint` 신설.

### 검증 결과
- `pnpm lint`: clean
- `pnpm build`: PASS (14 pages, TypeScript clean)
- grep residue 체크 (`mcp_server_id|MCPServer|useUpdate/Register/Delete MCPServer|registerMCPServer|listMCPServers|deleteMCPServer|mcp-server-rename|triggerContext|unsupportedFirstBindM6`): **0건**
- `MCPServerGroupCard` 컴포넌트 이름만 남음 — MCP 서버 그룹을 보여주는 UI 컴포넌트라는 기능적 네이밍이라 residue 아님.

### 결정/이월 (사티아/베조스 M6에서 릴리스 노트로 반영)
- **MCP 서버 신규 등록 경로는 M6.1 이후 이월**. Jensen M3가 backend `/mcp-server/*` 4 routes를 drop하면서 신규 등록 endpoint가 사라졌음 → AddToolDialog MCP 탭은 dead code. 팀 리드 옵션 1 승인.
- 사용자 Impact: 기존 MCP tool의 credential rotate는 가능(`mcp-server-group-card` → `ConnectionBindingDialog type="mcp"`), 신규 MCP 서버 등록 UI는 `/connections` McpSection에서 tooltip으로 안내하며 비활성화.
- 신규 등록 경로 설계 방향: `POST /api/connections (type=mcp, extra_config={url, env_vars})` + backend discovery로 MCP tool 자동 생성. backend 신규 엔드포인트 + agent_runtime discovery helper 필요 → 별도 스코프.

### Scope creep 회피
- BindingDialogShell은 UI chrome만 추출. PrebuiltBody/CustomBody/McpBody의 hydration (hydrationKey + hydratedFor pattern), 409 handling, save path는 각 body 보존 → 추상화 비용/리스크 최소화.
- `MCPServerGroupCard` 파일명/컴포넌트명 리네임은 미실행 — UX 유지 최우선. 기능적 네이밍이라 스코프 외 정리 불필요.

## M6 (베조스, 2026-04-25) — 통합 검증 완료

### 검증 결과 전수 PASS
- backend ruff: clean
- backend pytest: 621 passed, 1 deselected (M2 baseline 624 → M2 결과 633 → M3 drop 후 621. MCPServer CRUD 전용 테스트 12건 삭제 기인, 회귀 0)
- frontend pnpm lint: 0 warnings / 0 errors (M4 시점 예측 1 warning은 false — 실제 0)
- frontend pnpm build: 14 pages PASS (M5에서 MCP register 탭 제거로 15→14)

### DB round-trip (docker PG)
- m12 → m13 → m12 → m13 가역성 확인
- `\d tools`: mcp_server_id 없음, connection_id 존재
- to_regclass('mcp_servers') = NULL
- FK 2건 only: tools_user_id_fkey, fk_tools_connection_id (tools_mcp_server_id_fkey 없음)

### 잔재 grep
- backend: 6건 의도된 잔재 (main.py:94 column_exists cache, legacy_invariants.py:73-79 preflight SQL 리터럴, schemas/connection.py:125+186 docstring)
- frontend: 0건 (MCPServerGroupCard 컴포넌트명은 기능적 네이밍 유지)

### 회귀 테스트 포인트
- PATCH tool 9건 (CUSTOM/MCP success, PREBUILT 400, IDOR 404, type mismatch 422, none clears, nonexistent 404, extra forbid 422, 타 유저 tool 404)
- chat_service fail-closed 2건 (MCP missing connection raises, MCP with connection succeeds)

### Gotcha (M6 검증 중 확인)
- dev DB에 MCP tool 0건 → 시나리오 2 (MCP credential rotate) 브라우저 확인 불가. staging seed 또는 PR review 시 재현 필요
- m13 downgrade 시 mcp_servers 테이블 + FK 복원 정상 → PROD 머지 직전 safety net 확보
- legacy_invariants.py m13 preflight는 PROD 배포 전 stale row 차단용 — 사용자 Hancom-GW cleanup 완료로 현재 clean

### 산출물
- `tasks/verification-report-e-m6-1.md` (본 판정)
- `tasks/manual-e2e-e-m6-1.md` (5 시나리오)
- `HANDOFF.md` 전면 갱신

### 최종 판정
🟢 GREEN — 커밋/PR 가능. 사용자 브라우저 확인 + push + PR 생성 대기.
