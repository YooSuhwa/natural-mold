# 작업 인계 문서

## 최근 완료 (2026-04-24)

**백로그 E M6: Cleanup (축소 스코프 — legacy 컬럼 drop + runtime 정리) — 커밋 대기**
worktree `.claude/worktrees/backlog-e-m6` / 브랜치 `feature/backlog-e-m6` / base main@`ad8c0fd` / HEAD `52c79d6`

## 완료 작업

- [x] TTH 사일로 Ralph Loop S0~S6 (피차이/베조스/젠슨/저커버그 병렬 실행)
- [x] m12 migration: `tools.auth_config` / `tools.credential_id` / `agent_tools.config` drop
- [x] Runtime: CUSTOM bridge override 제거, `_resolve_legacy_tool_auth` 삭제, fail-closed 일관화
- [x] Frontend thin cleanup (type/API dead 삭제, MCP 경계 보존)
- [x] 리뷰 5회차 반영 (code-reviewer 1 + codex-review 2 + codex-adversarial 5 + simplify 1)
- [x] `app/services/legacy_invariants.py` 공용 모듈로 m12 preflight + startup guard 통합
- [x] `useFindOrCreateCustomConnection` 훅으로 CUSTOM connection N:1 find-or-create 집중화
- [x] `get_usage_count` PREBUILT 경로 + `agent_tools` JOIN 포함
- [x] ADD tool dialog: credential 미선택 시 submit disabled + amber hint
- [x] Startup guard: m12 preflight와 동일 invariant, 긴급 bypass `ALLOW_DIRTY_AGENT_TOOLS_CONFIG=1`

## M6 실제 스코프 (MCP 관련 전부 M6.1 이월)

**DROP**: `tools.auth_config`, `tools.credential_id` + FK, `agent_tools.config`
**유지**: `mcp_servers`, `tools.mcp_server_id`, `resolve_server_auth`, MCP legacy fallback, MCPServer CRUD 라우터

## 검증
- ruff clean · **pytest 624/624 PASS** · pnpm lint 0 warnings · pnpm build 15 routes PASS
- alembic round-trip docker PG PASS
- legacy grep 0 / MCP 유지 스코프 보존

## 사용자 다음 작업

1. 브라우저 E2E 5항목 (`tasks/manual-e2e-e-m6.md §시나리오`)
2. `git push -u origin feature/backlog-e-m6` + PR 생성

## 다음 마일스톤 — **M6.1: MCP cleanup + 옵션 D**

**스코프**:
- `PATCH /api/tools/{id}`에 `connection_id` 필드 추가 (옵션 D)
- 프론트 MCP 경로 re-wire (updateMCPServer → tool.connection_id PATCH)
- 프론트 Custom "first-bind" 경로 활성화 (현재 `needsOptionDFirstBind`로 차단)
- Alembic `m13_drop_mcp_legacy`: `mcp_servers` drop, `tools.mcp_server_id` drop
- `resolve_server_auth` + chat_service MCP fallback 제거
- UI 리팩토링: `triggerContext` mode 일원화, `BindingDialogShell` 추출

**M5.5** (M6.1 이후): `agent_tools.connection_id` override

## 주의사항 / invariant

### Breaking API changes (M6)
외부 API client는 다음 필드 제거 반영 필요 (pydantic `extra='forbid'` 422 rejection):
- `POST /api/agents` / `PATCH /api/agents/{id}`: `tool_configs` 필드 제거
- `POST /api/tools/custom`: `credential_id`/`auth_config` 제거, `connection_id` 필수
- `PATCH /api/tools/{id}/auth-config`: 엔드포인트 삭제
- `ToolResponse.auth_config`/`credential_id`, `AgentResponse.tools[].agent_config` 응답 필드 제거

### 정책
- CUSTOM `connection_id` 없으면 `ToolConfigError` fail-closed (legacy 위임 없음)
- PREBUILT `provider_name IS NULL` → `cred_auth = {}` (env fallback과 동치)
- MCP fallback 경로는 여전히 라이브 (M6.1 대상)
- Startup legacy guard는 PostgreSQL 전용. sqlite(테스트) silent skip

### drive-by 금지
- `mcp-server-rename-dialog.tsx`, `connection-binding-dialog.tsx` MCP body, MCP API/hook 전량 — M6.1 전까지 유지

## 관련 파일 (M6 핵심)
- `backend/alembic/versions/m12_drop_legacy_columns.py` — migration + preflight
- `backend/app/services/legacy_invariants.py` — dirty-row invariant 정의 (공용)
- `backend/app/main.py::_enforce_m6_legacy_invariants` — startup guard
- `backend/app/services/credential_service.py::get_usage_count` — PREBUILT + agent_tools JOIN
- `backend/app/services/chat_service.py::_resolve_custom_auth` — fail-closed
- `frontend/src/lib/hooks/use-connections.ts::useFindOrCreateCustomConnection` — N:1 공용 훅
- `frontend/src/components/connection/connection-binding-dialog.tsx` — first-bind guard
- `frontend/src/components/tool/add-tool-dialog.tsx` — credential 필수 UX

## 마지막 상태
- 브랜치: `feature/backlog-e-m6` (PR 미생성, user 진행)
- Base: main @ `ad8c0fd` (PR #58 M5 머지)
- DB head: `m12_drop_legacy_columns`
- HEAD: `52c79d6`
- 변경량: 46 files, +2369/-1575
- 보존 worktree: `backlog-e-m0~m3`, `backlog-e-m6`

## 마일스톤 진행
| M0 | M1 | M2 | M3 | M4 | M5 | **M6** | M6.1 | M5.5 |
|---|---|---|---|---|---|---|---|---|
| PR #52 | PR #53 | PR #54 | PR #55 | PR #56 | PR #58 | **커밋 대기** | 다음 | M6.1 이후 |
