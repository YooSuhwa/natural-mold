# 작업 인계 문서

## 최근 완료 (2026-04-17)

**MCP 서버 단위 그룹화 (feature/mcp-server-grouping)**
- Backend: `GET/PATCH/DELETE /api/tools/mcp-servers[/{id}]` 신규 라우트 3개
- Backend: `chat_service.build_tools_config`의 MCP precedence 분리 (server-level credential/auth만, tool-level 무시)
- Backend: `tool_service.list_mcp_server_items / update_mcp_server / delete_mcp_server` 추가 (cascade는 ORM `delete-orphan`)
- Frontend: `MCPServerGroupCard` (Collapsible 자체 구현 + DropdownMenu) + 서버 단위 인증/이름변경 다이얼로그
- Frontend: `tools/page.tsx` 4 섹션 렌더 (builtin / prebuilt / mcp 그룹 / custom)
- Frontend: 기존 `mcp-auth-dialog.tsx` 삭제, `tool.mcpAuth.*` → `tool.mcpServer.*` i18n 정리
- DB 마이그레이션: 없음 (스키마 변경 0)
- 검증: backend `ruff` PASS, `pytest` 536 passed (신규 5건 포함); frontend `lint` PASS (0 errors, 1 pre-existing warning), `build` PASS (14 routes)

**수동 E2E 가이드** (백엔드 + 프론트엔드 실행 필요)
1. `/tools` → "도구 추가" → "MCP 서버" → URL 입력 → 등록 (N tools 발견)
2. MCP 섹션에 그룹 카드 1개 + "N개 도구 · 인증 미설정" 배지 표시
3. 카드 펼침 → 도구 N개 sub-card 표시 (인증/삭제 버튼 없음)
4. [⋯] → "인증 설정" → 크리덴셜 선택 또는 "+ 새로 만들기" → 저장 → 배지 갱신
5. 에이전트 채팅에서 이 MCP 도구 호출 → 새 credential로 인증 성공
6. [⋯] → "이름 변경" → 새 이름 반영
7. [⋯] → "삭제" → "N개의 도구가 함께 삭제됩니다" 경고 → 확인 → 그룹 카드 + N개 도구 모두 사라짐
8. `/connections`에서 credential 사용 카운트(`mcp_server_count`) 정확히 표시되는지

**회귀 확인 포인트**
- Prebuilt / Custom 도구 섹션 기존과 동일하게 동작
- `/connections` 페이지 변경 없음
- 채팅에서 prebuilt/custom 도구 호출 시 인증 정상

**PR #46 머지 완료 (이전 작업)** — 중앙 크리덴셜 관리 시스템 (n8n 스타일)
- credentials 테이블 + Fernet 암호화 + Provider 레지스트리
- `/connections` 페이지 + `CredentialSelect` 공통 컴포넌트
- 세 도구 다이얼로그(add-tool / mcp-auth / prebuilt-auth) credential Select 통합
- 중첩 다이얼로그 인라인 크리덴셜 생성 (`CredentialFormDialog`)
- PATCH IDOR 방지 + 부분 업데이트 시맨틱 수정
- ENCRYPTION_KEY 미설정 시 credential 생성 거부 (503)
- `test_mcp_connection` credential 반영 (`resolve_server_auth` 헬퍼)

## 다음 작업 — 커스텀 도구 탭 credential 통합 (백로그 B)

### 핵심 목표
- 커스텀 도구 추가/편집 다이얼로그에 `CredentialSelect` 통합
- 도구 카드에서 credential 인디케이터 표시
- credential UI 일관성 완성 (prebuilt / mcp 와 동일한 UX)

## 백로그 (추천 진행 순서)

| # | 항목 | 규모 | 비고 |
|---|------|------|------|
| **B** | **커스텀 도구 탭 credential 통합** (다음 작업) | 중 | credential UI 일관성 완성 |
| C | credentials list N+1 복호화 제거 | 작음 | `field_keys` 캐시 컬럼 추가 (Alembic 마이그레이션 필요). Agent 3 효율성 리뷰 지적 |
| D | `lazy="joined"` → `lazy="select"` + selectinload | 중 | 범용 성능 개선. `Tool.credential`, `MCPServer.credential` 등 |
| E | PREBUILT 공유 행 per-user credential binding | 큼 | ultrareview `bug_032`. 아키텍처 변경 필요 (새 매핑 테이블 또는 `AgentToolLink.config` 활용). PoC 단계라 우선순위 낮춤 |

## 남아있는 마이너 이슈 (우선순위 낮음)

- `bug_009` — PrebuiltAuthDialog 에서 바인딩된 credential 의 provider 가 `detectProvider` 결과와 다르면 Select 에 공란 표시. 엣지 케이스. 수정책: `matchingCredentials` 에 현재 바인딩된 cred 를 fallback 으로 포함
- `tests/components/chat/*`, `tests/pages/chat.test.tsx`, `tests/pages/agent-*` — 이전 리팩토링으로 깨진 dead test. 별도 정리 필요

## 주의사항

- **ENCRYPTION_KEY 필수** — `.env`에 설정되어야 credential 생성 가능
- **pre-existing 테스트 실패** — `tests/components/chat/*`, `tests/pages/chat.test.tsx`, `tests/pages/agent-*` 는 이전 리팩토링으로 깨진 dead test (별도 정리 필요)
- `.claude/worktrees/` 는 로컬 워킹 디렉토리 — 커밋 금지
- `link_credential_to_tool/mcp_server` 함수는 이미 제거됨 — MCP 그룹화 PR에서 새 `update_mcp_server` 서비스로 대체

## 관련 파일 (커스텀 도구 credential 통합 작업 시 참조)

| 목적 | 경로 |
|------|------|
| 커스텀 도구 추가 다이얼로그 | `frontend/src/components/tool/add-tool-dialog.tsx` |
| 도구 목록 페이지 | `frontend/src/app/tools/page.tsx` |
| 재사용 컴포넌트 | `frontend/src/components/tool/credential-select.tsx`, `credential-form-dialog.tsx`, `prebuilt-auth-dialog.tsx` |
| credential resolution | `backend/app/services/chat_service.py:build_tools_config` (비MCP 분기) |
| 커스텀 도구 등록 | `backend/app/routers/tools.py` (POST `/`), `backend/app/services/tool_service.py:create_custom_tool` |

## 마지막 상태

- **브랜치**: `feature/mcp-server-grouping` (커밋 대기, working tree에 변경 15 + 신규 4)
- **베이스 커밋**: `8685320` (main, PR #46 머지) — 아직 신규 커밋 없음
- **DB 상태**: 초기화 후 `alembic upgrade head` 까지 적용됨 (스키마 변경 없음)
- **검증**: Backend `ruff` PASS, `pytest` 536 passed (신규 5건 포함); Frontend `lint` PASS (0 errors, 1 pre-existing warning), `build` PASS (14 routes, 3.7s)
