# 작업 인계 — `feature/handoff-improvements` (PR 대기)

> 새 세션은 본 파일 + `progress.txt` 마지막 4-5 섹션만 읽으면 충분.
> 디자인 시스템 결정은 `docs/design-docs/ADR-010-ui-tokens-and-dialog-shell.md`.

## 마지막 상태

- 브랜치 **`feature/handoff-improvements`** (main 위 18 커밋, working tree clean)
- alembic head **m31** — `cd backend && uv run alembic upgrade head` 필수
- backend **723 pass** / frontend **232 pass + 43 skip** / lint·build clean
- `.husky/pre-push`가 backend pytest + frontend vitest 자동 게이트

## 이 브랜치에 누적된 작업

- W1 HITL countdown 확장 (user-input/clarifying)
- W2 공개 share rate limit + snapshot 캐시 (slowapi + TTL)
- W3-1 SSE event id + dedup helper
- W3-2 `@microsoft/fetch-event-source` (탭 백그라운드 안정화). 자동 재시도는 비활성
- W3-3 stream guard (stale/duplicate 폐기, fork race 차단)
- W7~W7-5 메시지별 토큰 4종 + 비용. Composer 토큰 바 + hover 팝오버. 새로고침 회복
- 테스트 부채 67 fail → 0
- husky pre-push hook + simplify pass

## 다음 작업 후보

1. **W3-out** GET-based stream resume — 백엔드 background task + event broker + W5 trace storage + `GET /stream?run_id=&last_event_id=`. **5-7일, W5와 짝**. W6의 토대.
2. **W6 shared 페이지 도구/Skill 칩 렌더** (W3-out/W5 의존)
3. **W5 TraceStorage** — turn별 events array (PostgreSQL JSONB). 2-3일
4. **deepagents 0.4.12 → 0.5.3** 업그레이드 (1-2일)
5. **CollapsiblePill 통일 컴포넌트** (1일, 의존성 0)
6. **MarkdownContent 강화** — mermaid + katex + remarkBreaks (1일)
7. M-MCP2 / M-SKILL2 / Sprint 2~4 (UI 정리 트랙)

## 코드 컨벤션 (이 브랜치에서 새로 추가/강화된 것)

- **SSE 이벤트는 unique id 발행** — `streaming.py`의 `emit()` 사용. usage 평탄화는 `extract_usage_breakdown()` 재사용
- **SSE consumer는 `useChatRuntime` 경유** 또는 `createStreamGuard()` 직접 사용
- **메시지 비용은 `MessagesEnvelope.total_estimated_cost` 우선**. agent.model 단가로 derive (W7-5)
- 비용 포맷은 `components/usage/format.formatCostUsd` 단일 사용
- 공개 endpoint(`/api/shares/*`)는 slowapi 게이트 + `share_cache` 사용
- `git push` 자동 게이트. main 직접 커밋 금지. WIP 한정 `--no-verify` 가능

## 기존 컨벤션 (변경 없음, 요약)

Sheet 금지(모바일 사이드바·대화 목록만 예외) / `text-primary` ≠ 강조(`-strong`) / 흰 버튼 보이면 `.next` 캐시 stale → `rm -rf .next` / Edit·Regenerate는 time-travel(`astream(None, ...)`) / 다이얼로그는 `DialogShell` + 토큰(`DIALOG_SIZE`) / 한국어 날짜는 `formatLongDate`·`formatMediumDate` / 공개 페이지는 `BARE_ROUTE_PREFIXES` / 소유권 체크 `get_owned_conversation` / SSE 직렬화 `orjson.dumps` / 라이선스 MIT 2026.

## 검증

```bash
cd backend && uv run ruff check . && uv run pytest tests/
cd frontend && pnpm lint && pnpm test --run && pnpm build
```

## 핵심 파일 (이번 브랜치 산출)

- HITL: `frontend/src/components/chat/tool-ui/{countdown-badge,approval-card,user-input-ui,clarifying-question-ui}.tsx`
- Rate limit/cache: `backend/app/{rate_limit.py, services/share_cache.py}`
- SSE: `backend/app/agent_runtime/{streaming,message_utils}.py`, `frontend/src/lib/sse/{parse-sse,stream-guard}.ts`
- Token usage: `backend/app/{services/chat_service.py, routers/conversations.py, schemas/conversation.py}` + `frontend/src/{components/chat/token-usage-popover.tsx, lib/chat/{use-chat-runtime,convert-message}.ts, lib/hooks/use-conversations.ts}`
- 게이트: `.husky/pre-push`

## PR 머지 체크리스트

- [ ] `git push -u origin feature/handoff-improvements` (pre-push 게이트 통과)
- [ ] `gh pr create` — summary는 W1/W2/W3-1~3/W7~W7-5 + 테스트 부채 + pre-push + simplify
- [ ] 머지 후 `git branch -d feature/handoff-improvements`
- [ ] 보존된 cherry-pick 원본 정리: `feature/hitl-deadline-extension`, `feature/share-rate-limit`
