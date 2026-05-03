# 작업 인계 — main (PR #105~#108·#110 머지 후)

> 새 세션은 본 파일 + `progress.txt` 마지막 4-5 섹션만 읽으면 충분.
> 디자인 시스템은 `docs/design-docs/ADR-010-ui-tokens-and-dialog-shell.md`.

## 마지막 상태

- 브랜치 **`main`** (HEAD `8373004`, PR #110 머지 commit)
- alembic head **m33** — `cd backend && uv run alembic upgrade head` 필수
- backend **735 pass** / frontend **249 pass** + 43 skip / lint·build clean
- pyright **0 errors / 0 warnings**, pytest 수집 경고 0건
- `uvx pip-audit` clean (CVE 0)
- `.husky/pre-push`가 backend pytest + frontend vitest 자동 게이트

## 이번 세션 추가 머지된 PR

| PR | 트랙 | 임팩트 |
|----|------|--------|
| **#110** | **HITL 컴포넌트 zinc → 토큰화** (Sprint 3) | 5개 파일 53/53줄. raw zinc 제거 |

### #110 핵심
- 대상: `approval-footer / image-generation-ui / draft-config-ui / prompt-approval-ui / recommendation-approval-ui`
- 매핑: `border-zinc-*` → `border-border`, `bg-white dark:bg-zinc-900` → `bg-card`, `bg-zinc-50/100 dark:bg-zinc-800` → `bg-muted`, `text-zinc-500/400` → `text-muted-foreground`, `text-zinc-700 dark:text-zinc-300` → `text-foreground`, `bg-zinc-900 text-white dark:bg-white dark:text-zinc-900` → `bg-foreground text-background`, `focus:ring-zinc-400` → `focus:ring-2 focus:ring-ring`
- approval-footer accent type: `'zinc'` → `'neutral'` (의미 정정. 기존 사용처는 default라 무영향)
- prompt-approval "전체 보기": `text-blue-*` → `text-primary-strong` (ADR-010 매핑 따름)
- 제외: `code-tool-ui` (의도된 다크 코드 블록), `phase-timeline-ui` (HITL 외)

## 직전 세션 머지된 트랙 (참고)

| PR | 트랙 |
|----|------|
| #90/#92/#94/#96/#98/#99/#101/#103 | deepagents bump, pyright 정리, CollapsiblePill, W5 TraceStorage, W6 칩 렌더, 모델 카탈로그, MarkdownContent 강화, code-tool 토큰화 |
| #105 | CollapsiblePill leadingIcon (code-tool file 아이콘) |
| #106 | tool-ui status 매핑 통합 (`pillStatusFromAssistantUi`) |
| #107 | mermaid streaming 안전성 (isStreaming 동적 detect) |
| #108 | W6 정확도 (linked_message_ids, alembic m33) |

## 다음 작업 후보

1. **W3-out** GET-based stream resume — background task + event broker + `GET /stream?run_id=&last_event_id=` (5-7일). W5 활용. **메인 UX 트랙**
2. ~~**HITL 컴포넌트 zinc → border/muted 토큰화**~~ → **PR #110에서 처리**
3. **phase-timeline-ui zinc 토큰화** (0.5일) — HITL 외이지만 같은 패턴, 별도 PR
4. **approval-card raw color 매핑** — emerald/blue/red/amber → primary/status-* (ADR-010 매핑표 미적용 잔여)
5. **Frontend 43 skipped 테스트 정리** (Sprint 2/3) — agent-settings 20 / tools 9 / models 8 / assistant-panel 4 / chat 2건
6. **Outdated deps**: FastAPI 0.135→0.136 / Pydantic 2.12→2.13 (minor) / cryptography 46→47 / marshmallow 3→4 / protobuf 6→7 (major) — 위험도별 분리 PR
7. **mermaid 다크모드** — `mermaid-diagram.tsx`의 `theme: 'default'` → `useTheme` 동적
8. M-MCP2 / M-SKILL2 / Sprint 2~4

## 알려진 이슈 / 한계

- **base-ui 1.3.0 SliderThumb script 경고**: React 19 + Next 16.2 새 정책. mui/base-ui#4373 패치 대기
- **W6 trace 매핑**: PR #108로 정확도 개선됐으나, m32 이전 row는 `linked_message_ids = NULL`이라 chronological 폴백 (graceful)
- **assistant-thread mermaid streaming**: PR #107로 streaming 중 raw 표시 → 메시지 종료 후 SVG 변환

## 코드 컨벤션 (정착분)

- SSE: `emit()` unique id / consumer는 `useChatRuntime`+`createStreamGuard()` / 직렬화 `orjson.dumps`
- 비용: `MessagesEnvelope.total_estimated_cost` + `formatCostUsd`
- 공개 endpoint(`/api/shares/*`): slowapi + `share_cache`. `BARE_ROUTE_PREFIXES`
- **deps bump**: `uv lock --upgrade-package <name>` 단일, lockfile 별도 커밋, `uvx pip-audit`
- **타입 좁히기**: `state.get(...)` 캐시 / `db.get(...)`는 `assert is not None` / TypedDict는 `cast()` / alembic은 `sa.TextClause`
- **chat tool**은 `CollapsiblePill` (kind/status 4종/leadingIcon). raw `bg-{emerald,violet,amber,zinc}-*` 금지 → `text-status-{success,danger,warn,accent,info}` / 디자인 토큰
- **status 매핑**: `pillStatusFromAssistantUi(status.type)` 단일 헬퍼. `incomplete = cancelled`
- **HITL 양식**은 CollapsiblePill 강제 X. 카드 셸 = `rounded-xl border border-border bg-card shadow-sm`, 헤더 `border-b border-border`, 입력 `bg-muted`, 보조 텍스트 `text-muted-foreground`, 본문 `text-foreground` (PR #110)
- **Trace 영속화 (W5+W6)**: 라우트가 `trace_sink`+`msg_id_sink` 생성→executor 전달, `_sse_handler(on_complete=...)`로 record. `record_turn(raw_msg_ids=...)`
- **공개 페이지 칩 (W6)**: `extractChips(turn)`. `groupMessagesIntoTurns`는 `linked_message_ids` 우선 → chronological 폴백
- **Markdown 통일**: `buildMarkdownComponents({isStreaming})` 양쪽 재사용. 라이브는 `useAssistantState`로 isRunning 동적

## 기존 컨벤션 (요약)

Sheet 금지(모바일 사이드바·대화 목록만) / `text-primary` ≠ 강조(`-strong`) / `.next` stale → `rm -rf .next` / Edit·Regenerate = time-travel `astream(None, ...)` / 다이얼로그는 `DialogShell` + 토큰 / 한국어 날짜 `formatLongDate`·`formatMediumDate` / 소유권 `get_owned_conversation` / 라이선스 MIT 2026.

## 검증

```bash
cd backend && uv run ruff check . && uv run pytest tests/ && uv run pyright
cd frontend && pnpm lint && pnpm test --run && pnpm build
```

> integration 테스트 2건은 `pytest -m integration` 수동(라이브 PG 필요).

## 핵심 파일 (다음 작업 진입점)

- SSE: `backend/app/agent_runtime/{streaming,message_utils}.py`, `frontend/src/lib/sse/{parse-sse,stream-guard}.ts`
- Trace storage (W5+W6): `backend/app/{models/message_event.py, services/trace_storage.py}` + `routers/conversations.py(_persist_trace, list_traces)`
- Public chips (W6): `frontend/src/{lib/share/extract-chips.ts, app/shared/[shareId]/page.tsx(groupMessagesIntoTurns)}` + `backend/app/routers/shares.py`
- Markdown 통일: `frontend/src/components/chat/{markdown-content.tsx, assistant-thread.tsx}` — `buildMarkdownComponents` export
- Tool 표현: `frontend/src/components/chat/tool-ui/collapsible-pill.tsx` (`pillStatusFromAssistantUi` 헬퍼)
- HITL 양식: `frontend/src/components/chat/tool-ui/{approval-footer, image-generation-ui, draft-config-ui, prompt-approval-ui, recommendation-approval-ui}.tsx` (PR #110 토큰 패턴)
- Agent runtime: `backend/app/agent_runtime/executor.py:19-20` (deepagents 진입점)
- 게이트: `.husky/pre-push`

## HITL 시각 검증 경로 (PR #110)

1. `cd backend && uv run uvicorn app.main:app --reload --port 8001`
2. `cd frontend && pnpm dev` → http://localhost:3000/agents/new/conversational
3. "회의 요약 에이전트 만들어줘" 등 입력 → Phase 4~8 진행하며 5개 카드 노출
4. 라이트/다크 토글로 카드 배경(card)·보더(border)·입력(muted)·텍스트 대비 확인

## 새 트랙 시작 체크

1. `git checkout -b feature/<name>` (또는 `chore/<name>`, `fix/<name>`) — main 직접 커밋 금지
2. 작업마다 별도 commit, `git push`로 자동 게이트 통과 확인
3. 마지막에 `gh pr create`로 묶음 PR
