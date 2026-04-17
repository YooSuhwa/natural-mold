# CHECKPOINT — MCP 서버 단위 그룹화

**브랜치**: `feature/mcp-server-grouping`
**플랜**: `~/.claude/plans/mcp-idempotent-avalanche.md`
**ADR**: `docs/design-docs/2026-04-17-mcp-server-grouping.md`
**시작**: 2026-04-17

## M1: Backend — MCP 서버 CRUD + Runtime precedence

- [x] schemas/tool.py: MCPServerListItem, MCPServerUpdate, CredentialBrief 추가
- [x] services/tool_service.py: list_mcp_server_items, update_mcp_server, delete_mcp_server
- [x] routers/tools.py: GET/PATCH/DELETE /api/tools/mcp-servers[/{id}]
- [x] services/chat_service.py: build_tools_config MCP precedence 분리
- [x] tests/test_tools.py: list/update/delete/cascade/precedence 5건 추가
- 검증: `cd backend && uv run ruff check . && uv run pytest tests/test_tools.py -v`
- done-when: 신규 테스트 통과, 기존 회귀 없음
- 상태: done (ruff PASS, pytest 536 passed — 신규 5건 포함)
- 담당: 젠슨

## M2: Frontend — 데이터 레이어

- [x] lib/types/index.ts: MCPServerListItem, MCPServerUpdateRequest, CredentialBrief
- [x] lib/api/tools.ts: listMCPServers, updateMCPServer, deleteMCPServer
- [x] lib/hooks/use-tools.ts: useMCPServers, useUpdateMCPServer, useDeleteMCPServer
- 검증: `cd frontend && pnpm lint`
- done-when: 타입 에러 0
- 상태: done (lint PASS, 0 errors)
- 담당: 저커버그

## M3: Frontend — UI 컴포넌트 (신규 3개)

- [x] components/tool/mcp-server-group-card.tsx (Collapsible + DropdownMenu)
- [x] components/tool/mcp-server-auth-dialog.tsx (서버 단위)
- [x] components/tool/mcp-server-rename-dialog.tsx
- 검증: `cd frontend && pnpm lint`
- done-when: 타입 에러 0, 컴포넌트 import 가능
- 상태: done (lint PASS, tsc PASS)
- 담당: 저커버그 (M2 완료 후)
- blockedBy: M2

## M4: Frontend — 페이지 통합 + 정리 + i18n

- [x] app/tools/page.tsx: 4 섹션 렌더 (builtin/prebuilt/mcp/custom), MCP는 그룹 카드
- [x] components/tool/mcp-auth-dialog.tsx 삭제
- [x] messages/ko.json: tool.mcpServer.* 추가, tool.mcpAuth.* 정리
- 검증: `cd frontend && pnpm lint && pnpm build`
- done-when: 빌드 성공, MCP 도구 그룹으로 표시
- 상태: done (build PASS 3.7s, lint PASS 0 errors)
- 담당: 저커버그 (M3 완료 후)
- blockedBy: M3

## M5: 통합 검증

- [x] 백엔드 전체 회귀: `cd backend && uv run pytest` → 536 passed
- [x] 프론트 풀빌드: `cd frontend && pnpm build` → PASS (14 routes)
- [x] 백엔드 ruff: PASS
- [x] 프론트 lint: PASS (0 errors, 1 pre-existing warning)
- [x] HANDOFF.md 업데이트 (수동 E2E 가이드 + 변경 사항)
- 검증: 위 모두 통과
- done-when: 회귀 없음, HANDOFF 업데이트 완료
- 상태: done (2026-04-17T14:45)
- 담당: 베조스 (M4 완료 후)
- blockedBy: M4
