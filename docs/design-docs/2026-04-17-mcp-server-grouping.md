# ADR: MCP 서버 단위 그룹화 + 서버 레벨 인증

**날짜**: 2026-04-17
**상태**: 결정됨
**컨텍스트**: feature/mcp-server-grouping
**상세 플랜**: `~/.claude/plans/mcp-idempotent-avalanche.md`

## 결정

`/tools` 페이지에서 MCP 도구를 평탄 리스트가 아닌 **MCP 서버 단위 그룹 카드**로 표시한다. 인증은 **서버 단위로 1회** 설정하고 소속 도구 전체에 적용한다.

## 트레이드오프

| 결정 | 채택 이유 | 대안 |
|---|---|---|
| Backend: 신규 `/api/tools/mcp-servers` 라우트 3개 (GET/PATCH/DELETE) | REST 컬렉션 관습. `tool_service.get_mcp_servers()` 이미 존재 | 기존 `/api/tools` 응답 확장 (서버 메타데이터 부족) |
| Runtime: MCP 도구는 server-level credential만 사용 (tool-level 무시) | UI 정책과 정합 — tool-level auth UI를 숨기므로 런타임도 분리 | 기존 fallback 체인 유지 (UI/런타임 정책 불일치) |
| Frontend: Collapsible 자체 구현 | 의존성 추가 회피, 30줄 미만 | shadcn-ui add accordion (불필요한 의존성) |
| 그룹 내 개별 도구 삭제 미지원 | MCP 도구는 서버 종속 — 개별 삭제해도 다음 fetch에 다시 생김 | 삭제 허용 (혼란 야기) |
| DB 마이그레이션 없음 | 스키마 변경 0. 기존 `tool.credential_id`는 런타임에서 자연스럽게 무시됨 | 자동 마이그레이션 스크립트 (수동 옵션으로 충분) |

## 영향 범위

- Backend: `schemas/tool.py`, `services/tool_service.py`, `services/chat_service.py`, `routers/tools.py`, `tests/test_tools.py`
- Frontend: `lib/types/index.ts`, `lib/api/tools.ts`, `lib/hooks/use-tools.ts`, `app/tools/page.tsx`, `components/tool/mcp-server-{group-card,auth-dialog,rename-dialog}.tsx` (신규), `components/tool/mcp-auth-dialog.tsx` (삭제), `messages/ko.json`

## 미해결 (백로그)

- 기존 `tool.credential_id` 데이터 정리 스크립트 (`scripts/migrate_mcp_credentials.py` 옵션)
- MCP 서버 활성/비활성 토글 (status 필드 유지만)
- `lazy="joined"` → `selectinload` 전환 (HANDOFF 백로그 D)
