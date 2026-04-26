# 작업 인계 문서

## 최근 완료 (2026-04-26 후속 2) — 채팅 UX/HiTL/메시지 표시 버그 정리

main HEAD: `e49d979` (PR #66 머지). 5개 PR + 1개 핫픽스.

### PR (시간순)
- **#62** [docs] PR #61 후속 — HANDOFF + tasks/full-verification + .gitignore lock
- **#63** [fix] Anthropic multi-block content가 message 응답에 raw 노출
  - `langchain_messages_to_response`가 list-of-blocks을 `str()` 직렬화 → text 블록만 concat하는 `_content_to_text` helper 추가
  - 부수: PR #61의 stale `test_api_key_none_when_not_provided` 갱신
  - backend pytest 656 passed (648 → +8)
- **#64** [fix] 채팅 입력창이 viewport 밖으로 짤리는 layout 버그
  - `page.tsx`의 두 ancestor flex 컨테이너에 `min-h-0` 누락 → 추가
- **#65** [fix] streaming loading indicator를 도구 박스 위로 이동
  - `MessagePrimitive.Content`의 `Empty` 슬롯 제거 → 별도 `StreamingLoadingIndicator` (status==='running' 시 노출)
- **#66** [fix] HiTL interrupt 직후 ask_user tool_call이 화면에서 사라지는 버그
  - `consumeStream` finally에서 `if (!interrupted) onStreamEnd?.()` → 항상 호출. interrupt 시 messages query invalidate 누락이 원인. unused `interrupted` 플래그 제거

### 핫픽스 (코드 변경 없음, DB)
- agent `0515042d-...`의 `model_params.top_p` 제거 (claude-sonnet-4-6은 temperature/top_p 동시 거부)

### 이번 세션 follow-up 후보
- **이미지를 DB BYTEA로 저장** (사용자 제안, 미진행) — 현재 `backend/data/agents/{id}/avatar.jpg` 로컬 저장이라 worktree 간 공유 안 됨, orphan 발생
- **agent 생성 시 default `top_p:1` 차단** — 위 핫픽스의 근본 원인. seed/agent 생성 default + model_factory 호환성 체크 + 마이그레이션 필요

### 검증
backend pytest 656 / pyright 0 / ruff clean / frontend tsc·lint·build·vitest·Playwright 모두 clean

---

## 최근 완료 (2026-04-26 후속) — 테스트 인프라 + 채팅 버그 클린업

**PR #61 머지** (main `bcb3a35`). 전체 검증 후 누적 회귀 일괄 정리.

### 핵심 변경 (4 commits)
- `[chore]` ruff format + prettier 일괄 (82 files, 자동 포맷)
- `[fix]` vitest/Playwright 회귀 정리 — connection refactor 후 누적된 인프라 회귀
  - 구현체 사라진 6개 테스트 파일 삭제 (chat-store atoms, ChatInput, MessageBubble, StreamingMessage, ToolCallDisplay, PrebuiltAuthDialog)
  - mocks/fixtures.ts: dead `MCPServer` 타입 제거 → Connection/Credential mock 추가
  - mocks/handlers.ts: legacy `mcp-server*`/`auth-config` 제거 → connections/credentials/discover-tools 추가
  - tests/setup.ts: ResizeObserver/IntersectionObserver/matchMedia/scrollIntoView/pointerCapture jsdom 폴리필
  - 페이지 테스트 모킹 보강 (`conversationKeys`, `useGenerateAgentImage`, `useConnections*`)
  - assistant-ui 통합으로 의미 잃은 8개 케이스 `it.skip` (e2e/manual QA로 대체)
  - Playwright smoke spec 새 UI 반영 (`/agents/new` hero, Models 탭, settings AssistantPanel 통합)
- `[fix]` pyright 58 → 0 errors — Optional 가드 + 라이브러리 stub mismatch 정리
- `[fix]` **production 버그**: `create_chat_model`이 `api_key=None`을 ChatAnthropic에 그대로 전달 → 새 langchain-anthropic의 pydantic strict가 거부 → 채팅 시작 즉시 실패. `PROVIDER_API_KEY_MAP[provider]` settings로 fallback

### 검증 (`tasks/full-verification-2026-04-26.md`)
backend pytest **648 passed** / pyright **0 errors** / ruff clean / alembic m14 round-trip ✅
frontend vitest **261 passed / 8 skipped / 0 fail** / build 14 pages / Playwright smoke 13 pass / 2 skip
실 SSE 채팅 호출 → message_start → content_delta → message_end 정상 stream

---

## 최근 완료 (2026-04-26) — 백로그 E **종료**

**M6.1 PR #60 머지** (main `16d9f27`). 백로그 E (Connection 엔티티 통합) 전체 클로즈.

### 핵심 변경
- `PATCH /api/tools/{id}` 신설 (옵션 D, connection_id 단일 필드)
- m13: `mcp_servers` 테이블 + `tools.mcp_server_id` drop
- m14: partial unique `(user_id, connection_id, name) WHERE type='mcp'`
- `POST /api/connections/{id}/discover-tools` 신설 (MCP server tool discovery)
- `MCPServer` 모델/스키마 + `resolve_server_auth` + MCP CRUD 라우터 4종 제거
- AddToolDialog MCP 탭 복원 (공개 + 인증 MCP 둘 다 지원)
- MCP probe transport를 mcp library `streamablehttp_client`로 통일 → Hancom-GW 등 SSE 강제 서버 정상 동작
- chat_service MCP fallback → fail-closed `ToolConfigError`
- backend invariants 강화: delete_connection 가드, ToolUpdate status/credential 검증, savepoint 격리, ORM partial unique

### 외부 리뷰 통과
code-reviewer 1차 + codex review 2차 + codex adversarial 3차 + /simplify

### 검증
backend pytest **648 passed** (baseline 624 → +24 회귀 방어) · ruff clean · frontend lint 0 / build 14 pages · alembic m12↔m13↔m14 round-trip · 실 Hancom-GW 도구 7개 발견 ✅

## 마일스톤 진행

| M0 | M1 | M2 | M3 | M4 | M5 | M6 | M6.1 | M5.5 |
|---|---|---|---|---|---|---|---|---|
| PR #52 | PR #53 | PR #54 | PR #55 | PR #56 | PR #58 | PR #59 | **PR #60** | 다음 |

## 다음 후보 (우선순위)

### 🟢 LOW (멀티 유저 인증 도입 후 재평가)
**M5.5** — `agent_tools.connection_id` override (ADR-008 §5)
- agent별로 tool의 default connection을 다른 것으로 override
- 파워 유저 기능 — PoC mock user 1명에선 효익 없음

### 🟡 MEDIUM (사용자 운영 편의)
**`/connections` UX 강화**
- CUSTOM vs MCP 시각 구분 (현재 카드 모양 비슷해 헷갈림)
- 그림자 connection 정리 (사용자 시도하다 만든 dead row)

### 🟡 MEDIUM (legacy 정비)
**Credential provider 재설계**
- legacy `Custom API Key` provider의 `field_keys=["header_name", "api_key"]` 분리 깨짐
- → `field_keys=["api_key"]`만 + 헤더 이름은 connection에서 입력
- m15 마이그레이션: 기존 `header_name` 필드 데이터 이전 + credential dialog UI 정리
- 단기 fix는 M6.1에서 적용됨 (메타 필드 select 숨김)

### 🟢 LOW
**MCP 인증 방식 확장**
- 멀티 헤더 매핑 / OAuth2 / Basic 인증 등 (현재 단일 헤더만)

## 진행 중 작업 — 없음

## 주의사항

- DB head: `m14_uniq_mcp_tool_per_conn`
- mock user 1명 (PoC), 멀티 유저 인증 미도입
- `chat_service`는 `MultiServerMCPClient` (langchain_mcp_adapters) 사용 — `mcp_client.test_mcp_connection`과 별개 transport
- env_vars 템플릿은 **전체 매칭만** 지원 (부분 치환 X) — Bearer는 credential 값에 `Bearer xxx` 통째로 저장
- 보존 worktree: `backlog-e-m6-1` (PR 머지됨, 정리 가능 — `git worktree remove .claude/worktrees/backlog-e-m6-1`)

## 관련 파일

- `docs/design-docs/adr-008-connection-entity.md` — Connection 엔티티 설계 결정
- `docs/exec-plans/active/backlog-e-connection-refactor.md` — 백로그 E 실행 계획 (전체 완료)
- `tasks/manual-e2e-e-m6-1.md` — M6.1 6개 시나리오 (시나리오 1·2·6 브라우저 검증 필요)
- `tasks/verification-report-e-m6-1.md` — 베조스 GREEN 판정 보고서

## 마지막 상태

- 브랜치: `main` (sync 완료)
- HEAD: `16d9f27` (PR #60 머지 commit)
- 다음 시작 시: `git worktree add .claude/worktrees/<next-milestone> -b feature/<next>` 로 새 worktree 만들고 작업
