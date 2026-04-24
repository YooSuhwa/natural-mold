# CHECKPOINT — 백로그 E M6.1

**브랜치**: `feature/backlog-e-m6-1`
**Worktree**: `.claude/worktrees/backlog-e-m6-1`
**Base**: `main @ 18d98be` (PR #59 M6 머지)
**계획**: `/Users/chester/.claude/plans/m6-1-spicy-kurzweil.md`
**PO**: 사티아

---

## 스코프 (사용자 승인 — 2026-04-24)

M6에서 M6.1로 이월된 MCP legacy 완전 제거 + PATCH tool.connection_id 신설.

| 항목 | 결정 |
|------|------|
| `PATCH /api/tools/{id}` | 신설, 필드는 `connection_id`만 (최소 스코프) |
| `mcp_servers` 테이블 | drop (m13) |
| `tools.mcp_server_id` 컬럼 + FK | drop (m13) |
| `MCPServer` 모델/스키마 | 전부 제거 |
| `resolve_server_auth` | 제거 |
| `chat_service` MCP fallback (L384-392) | 제거 |
| `routers/tools.py` `/api/tools/mcp-server*` 4종 | 제거 |
| 프론트 `updateMCPServer` + `useUpdateMCPServer` | 제거 |
| `mcp-server-rename-dialog.tsx` | 삭제 |
| `BindingDialogShell` 추출 + triggerContext 일원화 | 포함 |
| PATCH는 `tool.type in ('custom','mcp')`만 허용 | PREBUILT 400 |
| M5.5 (`agent_tools.connection_id` override) | M6.1 이후 별도 PR |

---

## M1: 삭제 분석 (베조스) [blockedBy: 없음]
- [ ] `tasks/deletion-analysis-e-m6-1.md` 작성
  - MCP legacy 제거 대상을 파일:라인으로 확정
  - `test_mcp_connection` 라우터 재작성 방안 제안 (connection.extra_config 경유)
  - `resolve_server_auth` 호출처 전수
  - 프론트 updateMCPServer 호출처 전수 + BindingDialogShell 후보 공통 라인 식별
  - 제거(D) / 재작성(R) / 유지(K) 태그 분류
- 검증: 보고서 존재, 파일:라인 명시
- done-when: 베조스 판정 `GREEN` (젠슨/저커버그 착수 가능)
- 상태: pending

## M2: 백엔드 옵션 D — PATCH tool.connection_id (젠슨) [blockedBy: M1]
- [ ] `schemas/tool.py` — `ToolUpdate` 추가 (connection_id만, `extra="forbid"`)
- [ ] `services/tool_service.py` — `update_tool()` 추가
  - connection.user_id == current_user.id (IDOR 방지, 남의 것이면 404)
  - connection.type == tool.type 정합성 (불일치 422)
  - tool.type == 'prebuilt'면 400 (PREBUILT는 (user_id, provider_name) 스코프)
  - connection_id=None 허용 (연결 해제)
- [ ] `routers/tools.py` — `PATCH /api/tools/{id}` 라우트
- [ ] 테스트: 정상 / 남의 connection 404 / 타입 불일치 422 / PREBUILT 400 / None 허용 / 시스템 도구(is_system) MCP 케이스
- 검증: `uv run ruff check . && uv run pytest tests/test_tools.py -v`
- done-when: 신규 테스트 PASS, 전체 pytest 회귀 0 (baseline 624)
- 상태: pending

## M3: 백엔드 MCP Legacy Drop (젠슨) [blockedBy: M2]
- [ ] `alembic/versions/m13_drop_mcp_legacy.py`
  - pre-check 쿼리 주석: `SELECT COUNT(*) FROM tools WHERE mcp_server_id IS NOT NULL AND connection_id IS NULL = 0`
  - upgrade: FK drop → tools.mcp_server_id drop → mcp_servers drop
  - downgrade: 구조만 복구, 데이터 상실 경고 주석
- [ ] `models/tool.py` — `MCPServer` 클래스 + `Tool.mcp_server_id` + `Tool.mcp_server` relationship 제거
- [ ] `schemas/tool.py` — `MCPServerCreate/Response/Update` 제거 + `ToolResponse.mcp_server_id` 제거
- [ ] `services/tool_service.py` — MCPServer CRUD 함수 6종 제거
- [ ] `services/credential_service.py` — `resolve_server_auth()` 제거
- [ ] `services/chat_service.py` — `build_tools_config` MCP fallback 분기 제거 (L384-392)
- [ ] `routers/tools.py` — `/api/tools/mcp-server*` 4개 라우트 제거 + `test_mcp_connection` 재작성 (connection.extra_config.url + credential.decrypt)
- [ ] `services/legacy_invariants.py` — m13 preflight 헬퍼 추가
- [ ] `main.py` — startup guard에 m13 invariant 통합
- [ ] 테스트: MCP legacy 시나리오 파일 정리, connection path 회귀 유지
- 검증:
  - `uv run ruff check . && uv run pytest`
  - `rg "mcp_server_id|MCPServer|resolve_server_auth" backend/app/` → 0
  - docker PG round-trip: upgrade → downgrade -1 → upgrade
- done-when: 전 테스트 PASS, grep 잔재 0, round-trip PASS
- 상태: pending

## M4: 프론트 옵션 D + first-bind 활성화 (저커버그) [blockedBy: M2]
- [ ] `lib/api/tools.ts` — `toolsApi.update(id, { connection_id })` 추가
- [ ] `lib/hooks/use-tools.ts` — `useUpdateTool` 훅 (invalidate `['tools']`, `['agents']`)
- [ ] `connection-binding-dialog.tsx` CustomBody
  - `needsOptionDFirstBind` 가드 제거 (L339-340, 343-345, 421-429)
  - handleSave에서 `useFindOrCreateCustomConnection` → `useUpdateTool` 체인
- [ ] `lib/types/index.ts` — 필요 시 Tool 타입 정리
- 검증: `pnpm lint && pnpm build`
- done-when: lint 0 warnings(기존 1건 허용), build PASS
- 상태: pending

## M5: 프론트 MCP re-wire + UI 리팩토링 (저커버그) [blockedBy: M3, M4]
- [ ] `connection-binding-dialog.tsx` McpBody — `updateMCPServer` 제거, `useUpdateConnection` + `useUpdateTool` 경로로 재배선
- [ ] `BindingDialogShell<T>` 공용 컴포넌트 추출 (open/title/isLoading/credentials/isPending/onSave)
- [ ] `triggerContext` prop 제거 (사용처 확인 후)
- [ ] `mcp-server-rename-dialog.tsx` 삭제
- [ ] `mcp-server-group-card.tsx` — triggerContext 사용 정리 (쓰이던 라인)
- [ ] `lib/hooks/use-tools.ts` — `useUpdateMCPServer` 제거
- [ ] `lib/api/tools.ts` — `updateMCPServer` + MCP server CRUD 4종 제거
- [ ] `lib/types/index.ts` — `MCPServer*` 타입 + `Tool.mcp_server_id` 제거
- 검증:
  - `pnpm lint && pnpm build`
  - `rg "mcp_server_id|MCPServer|updateMCPServer|mcp-server-rename|useUpdateMCPServer" frontend/src/` → 0
- done-when: lint/build PASS, 잔재 0
- 상태: pending

## M6: 통합 검증 + HANDOFF (베조스 + 사티아) [blockedBy: M3, M5]
- [ ] 전체 verify: ruff + pytest + pnpm lint + pnpm build
- [ ] docker PG alembic round-trip
- [ ] 잔재 grep 최종 (backend + frontend 합산 0)
- [ ] 수동 E2E 시나리오 문서 `tasks/manual-e2e-e-m6-1.md` (3개: CUSTOM first-bind / MCP re-wire / PATCH tool connection_id)
- [ ] HANDOFF.md 갱신 (M6.1 완료 → 다음 = M5.5)
- 검증: 사티아 최종 승인
- done-when: "스태프 엔지니어 승인 가능" 판정
- 상태: pending

---

## 리스크 요약

- R1: `tools_mcp_server_id_fkey` 이름 환경별 차이 → 배포 전 `\d tools` 실측 (m12 precedent)
- R2: `test_mcp_connection` 라우터가 resolve_server_auth 의존 → connection.extra_config + credential decrypt 직결 재작성
- R3: `useUpdateTool` 네이밍 충돌 → use-tools.ts 기존 훅 grep 후 naming 결정
- R4: PREBUILT PATCH 400 정책이 실제 운영 UX에 영향 없는지 확인 (PREBUILT connection 교체는 `/connections` 페이지 경유이므로 tool PATCH가 필요 없음)
- R5: `triggerContext` 제거 시 PrebuiltBody의 L178-180 분기 영향 검토

---

## 검증 커맨드 (최종)

```bash
cd backend
uv run ruff check .
uv run pytest
uv run alembic upgrade head
uv run alembic downgrade -1 && uv run alembic upgrade head

cd ../frontend
pnpm lint
pnpm build

rg "mcp_server_id|MCPServer|resolve_server_auth" backend/app/       # 0
rg "mcp_server_id|MCPServer|updateMCPServer|mcp-server-rename|useUpdateMCPServer" frontend/src/   # 0

# docker PG (round-trip 시)
psql postgresql://moldy:moldy@localhost:5432/moldy -c "\d tools"
psql ... -c "SELECT to_regclass('mcp_servers')"    # NULL 기대
```
