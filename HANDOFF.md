# 작업 인계 — main (PR #103 머지 후 가정)

> 새 세션은 본 파일 + `progress.txt` 마지막 4-5 섹션만 읽으면 충분.
> 디자인 시스템은 `docs/design-docs/ADR-010-ui-tokens-and-dialog-shell.md`.
> ⚠️ 본 문서는 PR #103 머지 직후 시점 가정. PR #103이 OPEN이라면 머지 후 사용.

## 마지막 상태

- 브랜치 **`main`** (PR #103 머지 가정)
- alembic head **m32** — `cd backend && uv run alembic upgrade head` 필수
- backend **733 pass** / frontend **249 pass** + 43 skip / lint·build clean
- pyright **0 errors / 0 warnings**, pytest 수집 경고 0건
- `uvx pip-audit` clean (CVE 0)
- `.husky/pre-push`가 backend pytest + frontend vitest 자동 게이트

## 최근 머지된 트랙 (이번 세션, 12+ PR)

| PR | 트랙 | 임팩트 |
|----|------|--------|
| #90 | deepagents 0.5.6 + langchain 일괄 업그레이드 | 16 패키지 bump |
| #92 | pyright baseline 정리 (36/8 → 0/0) | **실제 버그 2건 수정** |
| #94 | CollapsiblePill 통일 컴포넌트 (sub-agent/generic/search/plan) | UI 일관성 |
| #96 | **W5 TraceStorage** | 백엔드 토대 |
| #98 | **W6 공개 페이지 도구/Skill 칩 렌더** | **사용자 가시 개선** |
| #99 | 모델 카탈로그 자동 갱신 | 데이터 |
| #101 | **MarkdownContent 강화** | mermaid+KaTeX+디자인 통일 |
| #103 | **CollapsiblePill Sprint 2** (code-tool-ui + tool-ui 토큰화) | UI 일관성 + 컨벤션 |
| #91, #93, #95, #97, #100, #102 | HANDOFF 갱신 6건 | 문서 |

### PR #103 — CollapsiblePill Sprint 2 핵심
- code-tool-ui (read_file/write_file/edit_file): 자체 FileToolWrapper 마크업 → CollapsiblePill 위임 (-49 LoC). status 4종 매핑(loading/success/error/cancelled)
- image-generation-ui / draft-config-ui: HITL 양식 구조 보존, raw color만 토큰화 (amber/red/violet → status-warn/danger/accent, primary-strong)
- DiffBlock: bg-red/emerald-950 → bg-status-danger·success/15 (다크 테마 호환)
- statusToPill: AssistantUiStatusType union으로 좁힘, requires-action도 loading 매핑

## 다음 작업 후보

1. **W3-out** GET-based stream resume — background task + event broker + `GET /stream?run_id=&last_event_id=` (5-7일). W5 활용. **메인 UX 트랙**
2. **tool-ui status 매핑 함수 통합** (Sprint 2 후속, 1일) — `code-tool-ui.statusToPill` / `sub-agent-ui.resolveStatus` / `generic-tool-ui.resolveStatus` 등 5개 파일에 흩어진 매핑 함수가 미세하게 다름 (특히 incomplete를 cancelled vs error로 다르게). 공통 헬퍼로 통일
3. **W6 정확도 개선** — backend가 turn당 langchain msg id 저장 → branch까지 정확 매핑 (1-2일)
4. **assistant-thread 라이브 mermaid 안전성 보강** — `isStreaming: false` 고정. dynamic detection으로 보강 (실용 위험은 낮음, 0.5-1일)
5. **Frontend 43 skipped 테스트 정리** (Sprint 2/3) — agent-settings 20 / tools 9 / models 8 / assistant-panel 4 / chat 2건. legacy 접두
6. **그 외 outdated deps**: FastAPI 0.135→0.136 / Pydantic 2.12→2.13 (minor) / cryptography 46→47 / marshmallow 3→4 / protobuf 6→7 (major) — 위험도별 분리 PR
7. M-MCP2 / M-SKILL2 / Sprint 2~4 (raw zinc 대신 border/muted 토큰화 같은 토큰 hygiene 후속)

## 알려진 이슈 / 한계

- **base-ui 1.3.0 SliderThumb script 경고**: React 19 + Next 16.2 새 정책. mui/base-ui#4373 패치 대기
- **W6 trace 매핑은 turn 순서 기반**: branch가 있는 conversation(edit/regenerate)은 active 외 trace 매핑 X (graceful)
- **assistant-thread mermaid streaming**: `isStreaming: false` 하드코딩이지만 streamdown이 fence 닫힘까지 emit 안 함 + catch 블록이 raw fallback
- **tool-ui status 매핑 함수 5개 파일에 분산** (PR #103 진단): 후속 트랙으로 통합 예정

## 코드 컨벤션 (PR #88·#90·#92·#94·#96·#98·#101·#103 정착분)

- SSE 이벤트는 `emit()`으로 unique id, consumer는 `useChatRuntime`/`createStreamGuard()`
- 메시지 비용은 `MessagesEnvelope.total_estimated_cost`, 포맷은 `formatCostUsd` 단일 사용
- 공개 endpoint(`/api/shares/*`)는 slowapi 게이트 + `share_cache`
- `git push` 자동 게이트. main 직접 커밋 금지
- **deps bump**: pyproject/uv.lock 별도 커밋, `uv lock --upgrade-package <name>` 단일 명시, `uvx pip-audit`로 CVE 확인
- **타입 좁히기**: `state.get("X")` 결과 변수 캐시. `db.get(Model, id)`는 `assert is not None`. TypedDict는 `cast()`. alembic은 `sa.TextClause` / `sa.types.TypeEngine`
- **chat tool 표현은 `CollapsiblePill`** — kind="tool|subagent|thinking", status 4종. raw `bg-emerald-*`/`bg-violet-*`/`bg-amber-*` 금지 — `text-status-{success,danger,warn,accent,info}` 토큰만
- **HITL 양식**(approval-card/clarifying/draft-approval/image-approval)은 CollapsiblePill 강제 X — 양식 구조 보존하되 status 토큰만 통일
- **Trace 영속화 (W5)**: 라우트가 `trace_sink: list[dict]` 만들어 executor에 전달, `_sse_handler(on_complete=...)`로 영속화. fresh `async_session()`
- **공개 페이지 칩 (W6)**: `extractChips(turn)` ChipInfo[]. user→assistant 경계마다 traces 1:1. 본문 빈 assistant 필터, 연속 assistant 한 그룹
- **Markdown 렌더 통일**: 공유/라이브 채팅 양쪽 `buildMarkdownComponents({isStreaming})` 재사용. plugin 배열은 모듈 상수

## 기존 컨벤션 (변경 없음, 요약)

Sheet 금지(모바일 사이드바·대화 목록만 예외) / `text-primary` ≠ 강조(`-strong`) / `.next` 캐시 stale → `rm -rf .next` / Edit·Regenerate는 time-travel(`astream(None, ...)`) / 다이얼로그는 `DialogShell` + 토큰 / 한국어 날짜는 `formatLongDate`·`formatMediumDate` / 공개 페이지는 `BARE_ROUTE_PREFIXES` / 소유권 체크 `get_owned_conversation` / SSE 직렬화 `orjson.dumps` / 라이선스 MIT 2026.

## 검증

```bash
cd backend && uv run ruff check . && uv run pytest tests/ && uv run pyright
cd frontend && pnpm lint && pnpm test --run && pnpm build
```

> integration 테스트 2건은 `pytest -m integration` 수동(라이브 PG 필요).

## 핵심 파일 (다음 작업 진입점)

- SSE: `backend/app/agent_runtime/{streaming,message_utils}.py`, `frontend/src/lib/sse/{parse-sse,stream-guard}.ts`
- Trace storage (W5): `backend/app/{models/message_event.py, services/trace_storage.py}` + `routers/conversations.py(_persist_trace)`
- Public chips (W6): `frontend/src/{lib/share/extract-chips.ts, app/shared/[shareId]/page.tsx}` + `backend/app/routers/shares.py`
- Markdown 통일: `frontend/src/components/chat/{markdown-content.tsx, assistant-thread.tsx}` — `buildMarkdownComponents` export
- Tool 표현: `frontend/src/components/chat/tool-ui/collapsible-pill.tsx` — 5개 파일에서 사용 (sub-agent / generic / search / plan / **code**). statusToPill 함수 통합 후속 필요
- Agent runtime: `backend/app/agent_runtime/executor.py:19-20` (deepagents 진입점)
- 게이트: `.husky/pre-push`

## 새 트랙 시작 체크

1. `git checkout -b feature/<name>` (또는 `chore/<name>`, `fix/<name>`) — main 직접 커밋 금지
2. 작업마다 별도 commit, `git push`로 자동 게이트 통과 확인
3. 마지막에 `gh pr create`로 묶음 PR
