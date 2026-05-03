# 작업 인계 — main (PR #88 머지 후)

> 새 세션은 본 파일 + `progress.txt` 마지막 4-5 섹션만 읽으면 충분.
> 디자인 시스템은 `docs/design-docs/ADR-010-ui-tokens-and-dialog-shell.md`.

## 마지막 상태

- 브랜치 **`main`** (origin sync, working tree clean)
- 최신 커밋 `3585a58 Merge pull request #88 — feature/handoff-improvements` (19 커밋 묶음)
- alembic head **m31** — `cd backend && uv run alembic upgrade head` 필수
- backend 723 pass / frontend 232 pass + 43 skip / lint·build clean
- `.husky/pre-push`가 backend pytest + frontend vitest 자동 게이트

## 최근 머지된 트랙 (PR #88)

- W1 HITL countdown 확장 (user-input/clarifying)
- W2 공개 share rate limit + snapshot 캐시 (slowapi + TTL)
- W3-1 SSE event id + dedup helper / W3-2 fetch-event-source / W3-3 stream guard
- W7~W7-5 메시지별 토큰 4종 + 비용. Composer 토큰 바 + hover 팝오버. 새로고침 회복
- 테스트 부채 67 → 0 (mock 보강 + 페이지 슬림화)
- husky pre-push + simplify pass

## 다음 작업 후보

1. **W3-out** GET-based stream resume — POST 한계로 자동 재연결 못 했던 부분 마무리. background task + event broker (asyncio.Queue 또는 Redis Pub/Sub) + W5 trace storage + `GET /stream?run_id=&last_event_id=`. **W5와 짝, 5-7일**. W6의 토대.
2. **W6 shared 페이지 도구/Skill 칩 렌더** (W3-out/W5 의존, 2일)
3. **W5 TraceStorage** — turn별 events array (PostgreSQL JSONB). 2-3일
4. **deepagents 0.4.12 → 0.5.3** 업그레이드 (1-2일, breaking change 확인)
5. **CollapsiblePill 통일 컴포넌트** — loading/success/error/cancelled 4상태. tool/subagent/thinking 적용. 1일, 의존성 0
6. **MarkdownContent 강화** — mermaid + katex + remarkBreaks (1일)
7. M-MCP2 / M-SKILL2 / Sprint 2~4 (UI 정리 트랙)

## 코드 컨벤션 (PR #88에서 새로 추가/강화된 것)

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

> `git push` 시 husky pre-push가 동일 게이트 자동 실행.

## 핵심 파일 (PR #88 산출 — 다음 작업 진입점)

- HITL: `frontend/src/components/chat/tool-ui/{countdown-badge,approval-card,user-input-ui,clarifying-question-ui}.tsx`
- Rate limit/cache: `backend/app/{rate_limit.py, services/share_cache.py}` (envelope key helper 캡슐화)
- SSE: `backend/app/agent_runtime/{streaming,message_utils}.py` (`emit()` + `extract_usage_breakdown()`), `frontend/src/lib/sse/{parse-sse,stream-guard}.ts`
- Token usage: `backend/app/{services/chat_service.py(_resolve_agent_model_pricing), routers/conversations.py(envelope total_estimated_cost), schemas/conversation.py(TokenUsageBreakdown)}` + `frontend/src/{components/chat/token-usage-popover.tsx, lib/chat/{use-chat-runtime,convert-message}.ts, lib/hooks/use-conversations.ts(useMessages/useMessagesEnvelope)}`
- 게이트: `.husky/pre-push`

## 정리 권장 (선택)

```bash
# PR #88로 머지된 로컬 브랜치 삭제
git branch -d feature/handoff-improvements 2>/dev/null
# cherry-pick 원본 (안전망 역할 끝났음)
git branch -D feature/hitl-deadline-extension feature/share-rate-limit 2>/dev/null
```

## 새 트랙 시작 체크

1. `git checkout -b feature/<name>` — main 직접 커밋 금지
2. 작업마다 별도 commit, `git push`로 자동 게이트 통과 확인
3. 마지막에 `gh pr create`로 묶음 PR
