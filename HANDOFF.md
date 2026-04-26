# 작업 인계 문서

## 최근 완료 (2026-04-25)

**백로그 E M6.1: 옵션 D + MCP legacy 완전 제거 + 신규 등록 경로 복원 — 커밋 대기**
worktree `.claude/worktrees/backlog-e-m6-1` / 브랜치 `feature/backlog-e-m6-1` / base main@`18d98be` (PR #59 M6 머지)

### 8 커밋
- `10c55dc` [feat] M6.1 M2 — PATCH /api/tools/{id} connection_id
- `87b173e` [feat] M6.1 M4 — 프론트 옵션 D + CUSTOM first-bind
- `7d3fef0` [feat] M6.1 M3 — MCP legacy drop (m13)
- `b24ef1b` [feat] M6.1 M5 — 프론트 MCP re-wire + BindingDialogShell
- `1c04481` [docs] M6.1 — HANDOFF + 검증 리포트 + E2E + 삭제 분석
- `0dc1610` [feat] M6.1 M7 — Backend: MCP connection discovery 엔드포인트
- `5e872e2` [feat] M6.1 M7 — Frontend: MCP 서버 신규 등록 경로 복원
- `b40e399` [docs] M6.1 — HANDOFF M7 반영

### 완료 작업
- [x] TTH 사일로 M1~M6 (베조스 M1/M6, 젠슨 M2/M3, 저커버그 M4/M5, 사티아 PO)
- [x] `PATCH /api/tools/{id}` 신설 — `ToolUpdate(connection_id)` 단일 필드, `extra="forbid"`
- [x] m13 migration: `tools.mcp_server_id` FK + 컬럼 drop, `mcp_servers` 테이블 drop
- [x] `MCPServer` 모델/스키마 전량 제거, `resolve_server_auth` 제거
- [x] `chat_service.build_tools_config` MCP legacy fallback → **fail-closed `ToolConfigError`**
- [x] `POST /api/tools/{tool_id}/test` 신설 (기존 `/api/tools/mcp-server/{id}/test` 재작성 — connection.extra_config 경유)
- [x] `/api/tools/mcp-server*` 4 라우트 삭제 (POST register, GET list, PATCH update, DELETE)
- [x] 프론트 CUSTOM first-bind 활성화 (`needsOptionDFirstBind` 가드 제거, `useFindOrCreateCustomConnection → useUpdateTool` 체인)
- [x] 프론트 McpBody re-wire: `useUpdateMCPServer` 제거 → `useUpdateConnection` 단일 경로
- [x] `BindingDialogShell` 공용 컴포넌트 추출 (UI chrome만)
- [x] `mcp-server-rename-dialog.tsx` 파일 삭제
- [x] `/tools` 페이지 grouping: `mcp_server_id` → `connection_id` (UX 유지, 내부 키만 교체)
- [x] `credential_service.get_usage_count` 반환에서 `mcp_server_count` 제거 + 프론트 `CredentialUsage` 타입 정리
- [x] i18n: `unsupportedFirstBindM6` / MCP rename 키 제거, `connections.mcpCreateDialog` 블록 신설
- [x] **M7 신규 등록 경로 복원**: `POST /api/connections/{id}/discover-tools` + `/tools` AddToolDialog **MCP 탭** (M5 이전 위치로 환원) — URL + display_name 2필드 입력 → connection 생성 → 자동 discovery → 도구 upsert. `/connections` McpSection은 조회 전용

## M6.1 스코프 vs 실제

| 영역 | 계획 | 실제 |
|------|------|------|
| `PATCH /api/tools/{id}` | ✅ | connection_id 단일 필드, PREBUILT 400, IDOR 404, 타입 불일치 422, unknown field 422 |
| `mcp_servers` drop | ✅ | m13 upgrade + downgrade 양방향 검증 |
| `MCPServer` 모델/스키마 | ✅ | 전량 제거 |
| `resolve_server_auth` | ✅ | 제거, test route는 `resolve_env_vars(extra.env_vars, conn.credential)` 재사용 |
| CUSTOM first-bind | ✅ | 프론트 가드 제거 + 체인 연결 |
| BindingDialogShell | ✅ | UI chrome만 추출 (hydration은 body 소유) |
| `/api/tools/mcp-server*` 삭제 | ✅ | 4 라우트 제거 |
| MCP **신규 등록** UI | ✅ | **M7에서 복원 완료**. `POST /api/connections/{id}/discover-tools` + `/tools` AddToolDialog의 **MCP 탭** (M5 이전 위치로 환원). 공개 MCP + 인증 MCP(credential + 헤더 매핑) 둘 다 지원. probe transport는 mcp library `streamablehttp_client` 사용 — Hancom-GW 등 SSE 강제 서버에서 정상 동작. `/connections` McpSection은 조회/관리 전용 |

## 검증

### 자동화
- backend `uv run ruff check .` → **clean**
- backend `uv run pytest` → **629 passed, 1 deselected** (M6 baseline 624 → M2 633 → M3 621 → M7 629. +8 MCP discovery 테스트 추가)
- frontend `pnpm lint` → **0 warnings / 0 errors**
- frontend `pnpm build` → **14 pages** PASS

### DB round-trip (docker PG)
```
m12_drop_legacy_columns → m13_drop_mcp_legacy → m12 → m13
```
- `\d tools`: `mcp_server_id` 컬럼 없음, `connection_id` 존재
- `to_regclass('mcp_servers')` = **NULL**
- FK 확정: `tools_user_id_fkey` + `fk_tools_connection_id` (**`tools_mcp_server_id_fkey` 없음**)

### 잔재 grep (residue)
- backend: `rg "mcp_server_id|MCPServer|resolve_server_auth|mcp-server" backend/app/` → **6건 모두 의도된 잔재**
  - `main.py:94` + `legacy_invariants.py:73-79` — m13 startup preflight SQL 문자열 (실 코드 참조 아님)
  - `schemas/connection.py:125, 186` — docstring 이력 코멘트
- frontend: `rg "mcp_server_id|updateMCPServer|useUpdate/Register/Delete MCPServer|useMCPServers|registerMCPServer|listMCPServers|deleteMCPServer|mcp-server-rename"` → **0건**
- `MCPServerGroupCard` 컴포넌트명은 기능적 네이밍 유지 (residue 아님)

## 사용자 다음 작업

1. 브라우저 E2E 수동 확인 — `tasks/manual-e2e-e-m6-1.md` §시나리오 1, 2 (코드경로 PASS, 브라우저 검증만 대기)
2. `git push -u origin feature/backlog-e-m6-1` + PR 생성

## 다음 마일스톤 — **M5.5: `agent_tools.connection_id` override** (멀티 유저 인증 도입 후)

스코프:
- `agent_tools` 테이블에 `connection_id: UUID | None` 컬럼 추가
- 에이전트별 tool connection override 경로 (tool.connection_id는 default, agent_tools.connection_id가 우선)
- `build_tools_config`에서 override 우선순위 반영
- ADR-008 §5 참조

**우선순위**: 파워 유저 기능 (멀티 유저 실사용 패턴 확인 후 결정). 기본 UX는 이미 온전.

## 주의사항 / invariant

### Breaking API changes (M6.1)

외부 API client는 다음 필드/라우트 제거 반영 필요:
- `POST /api/tools/mcp-server` (register) — **삭제됨**
- `GET /api/tools/mcp-servers` (list) — **삭제됨**
- `PATCH /api/tools/mcp-servers/{id}` (update) — **삭제됨**
- `DELETE /api/tools/mcp-servers/{id}` (delete) — **삭제됨**
- `POST /api/tools/mcp-server/{id}/test` — **`POST /api/tools/{tool_id}/test`로 이전** (파라미터 의미 server_id → tool_id)
- `POST /api/connections/{id}/discover-tools` — **신규** (M7, MCP 서버 tool discovery)
- `ToolResponse.mcp_server_id` 필드 제거
- `MCPServerResponse` / `MCPServerListItem` / `MCPServerCreate` / `MCPServerUpdate` 스키마 전량 제거
- `CredentialUsage.mcp_server_count` 반환 필드 제거

### 정책 (신규)
- PREBUILT tool에 `PATCH /api/tools/{id}` 호출 → **400** (user_id × provider_name SOT 유지)
- `PATCH /api/tools/{id}` body는 `connection_id` 단일 필드만 허용 — 그 외 → **422**
- 타 유저 connection_id PATCH → **404** (info leak 방지 위해 구별 없음)
- tool.type != connection.type → **422**
- MCP tool이 connection 없이 실행 시도 → `ToolConfigError` (fail-closed)
- `startup guard` m13 invariant: `tools.mcp_server_id` 컬럼 존재 시점에 `WHERE type='mcp' AND mcp_server_id IS NOT NULL AND connection_id IS NULL` rows 0건 강제 (prod 배포 전 safety net)

### drive-by 금지 (다음 PR로 이월)
- `agent_tools.connection_id` override (M5.5 — 멀티 유저 인증 도입 후)

## 관련 파일 (M6.1 핵심)

### 신규
- `backend/alembic/versions/m13_drop_mcp_legacy.py` — MCP drop migration + SQLite skip FK
- `backend/app/schemas/tool.py::ToolUpdate` / `DiscoverToolsResponse` (M7)
- `backend/app/services/tool_service.py::update_tool` + `discover_mcp_tools` (M7)
- `backend/app/routers/tools.py::test_tool_connection` — connection.extra_config 경유 MCP test
- `backend/app/routers/connections.py::discover_tools` (M7)
- `backend/tests/test_connection_discover_tools.py` (M7, 8 테스트)
- `frontend/src/components/connection/binding-dialog-shell.tsx` — 공용 Dialog chrome
- `frontend/src/lib/hooks/use-tools.ts::useUpdateTool` — PATCH 훅
- `frontend/src/lib/hooks/use-connections.ts::useDiscoverMcpTools` (M7)

### 수정
- `backend/app/services/chat_service.py::build_tools_config` — MCP fallback → fail-closed
- `backend/app/services/credential_service.py` — `resolve_server_auth` 제거, `get_usage_count` shape 정리
- `backend/app/services/legacy_invariants.py` — m13 invariant 추가
- `backend/app/main.py` — startup column_exists cache에 mcp_server_id 추가
- `frontend/src/components/connection/connection-binding-dialog.tsx` — 가드 제거 + McpBody re-wire + triggerContext 제거 + PrebuiltProps.createNew 신설
- `frontend/src/components/tool/mcp-server-group-card.tsx` — Connection 기반 재작성
- `frontend/src/app/tools/page.tsx` — `useMCPServers()` → `useConnections({type:'mcp'})`
- `frontend/src/app/connections/page.tsx` — McpSection 조회 전용 (M7 후속에서 신규 등록 진입점은 `/tools`로 환원)
- `frontend/src/components/tool/add-tool-dialog.tsx` — Tabs 구조 (MCP 탭 + CUSTOM 탭). MCP 탭은 `useCreateConnection` + `useDiscoverMcpTools` 체인
- `frontend/src/lib/api/connections.ts::discoverTools` (M7)
- `frontend/src/lib/types/index.ts` — `ConnectionCreateRequest.extra_config` + `DiscoverToolsResponse` (M7)

### 삭제
- `frontend/src/components/tool/mcp-server-rename-dialog.tsx` (파일 전체)

## 마지막 상태

- 브랜치: `feature/backlog-e-m6-1` (PR 미생성, user 진행)
- Base: main @ `18d98be` (PR #59 M6 머지)
- DB head: `m13_drop_mcp_legacy` (docker PG 적용 완료)
- HEAD: `b40e399` (8 커밋)
- 보존 worktree: `backlog-e-m0~m3`, `backlog-e-m6`, `backlog-e-m6-1`

## 마일스톤 진행

| M0 | M1 | M2 | M3 | M4 | M5 | M6 | **M6.1** | M5.5 |
|---|---|---|---|---|---|---|---|---|
| PR #52 | PR #53 | PR #54 | PR #55 | PR #56 | PR #58 | PR #59 | **커밋 대기** | 다음 |
