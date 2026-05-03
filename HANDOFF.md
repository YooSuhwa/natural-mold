# 작업 인계 — main (PR #101 머지 후 가정)

> 새 세션은 본 파일 + `progress.txt` 마지막 4-5 섹션만 읽으면 충분.
> 디자인 시스템은 `docs/design-docs/ADR-010-ui-tokens-and-dialog-shell.md`.
> ⚠️ 본 문서는 PR #101 머지 직후 시점 기준. PR #101이 아직 OPEN이라면 머지 후 사용.

## 마지막 상태

- 브랜치 **`main`** (PR #101 머지 가정)
- alembic head **m32** — `cd backend && uv run alembic upgrade head` 필수
- backend **733 pass** / frontend **249 pass** + 43 skip / lint·build clean
- pyright **0 errors / 0 warnings**, pytest 수집 경고 0건
- `uvx pip-audit` clean (CVE 0)
- `.husky/pre-push`가 backend pytest + frontend vitest 자동 게이트

## 최근 머지된 트랙 (이번 세션, 11 PR)

| PR | 트랙 | 임팩트 |
|----|------|--------|
| #90 | deepagents 0.5.6 + langchain 일괄 업그레이드 | 16 패키지 bump |
| #92 | pyright baseline 정리 (36/8 → 0/0) | **실제 버그 2건 수정** |
| #94 | CollapsiblePill 통일 컴포넌트 | UI 일관성 |
| #96 | **W5 TraceStorage** | 백엔드 토대 |
| #98 | **W6 공개 페이지 도구/Skill 칩 렌더** | **사용자 가시 개선** |
| #99 | 모델 카탈로그 자동 갱신 | 데이터 |
| #101 | **MarkdownContent 강화** | mermaid+KaTeX+디자인 통일 |
| #91, #93, #95, #97, #100 | HANDOFF 갱신 5건 | 문서 |

### PR #101 — MarkdownContent 강화 핵심
- `remark-breaks 4.0.0` 추가 (단일 newline → `<br>`로 LLM 줄바꿈 의도 보존)
- `MarkdownContent`(공유 페이지) + `assistant-thread`(라이브 채팅) 양쪽에 동일 적용
- **`buildMarkdownComponents` export로 통일** — 두 경로가 같은 components 사용. mermaid 다이어그램이 라이브 채팅에서도 SVG로 렌더, 코드 블록 디자인 일관
- `@streamdown/code` 제거 (자체 SyntaxHighlighter와 충돌). `math`만 유지
- KaTeX 크기 보정: `.katex` 1.1em / display 1.15em / display>katex 1.2em
- raw `text-emerald-500` → `text-status-success` 토큰화 (CodeBlock + CopyButton 양쪽)
- `REMARK_PLUGINS` 등 모듈 레벨 상수 추출 (reference 안정)

## 다음 작업 후보

1. **W3-out** GET-based stream resume — background task + event broker + 진행형 append + `GET /stream?run_id=&last_event_id=` (5-7일). W5 `last_event_id` 활용. **메인 UX 트랙**
2. **CollapsiblePill 추가 적용** (Sprint 2) — code-tool-ui / image-generation-ui / draft-config-ui / recommendation-approval-ui (1-2일)
3. **W6 정확도 개선** — backend가 turn당 langchain msg id 저장 → branch까지 정확 매핑 (1-2일)
4. **assistant-thread 라이브 mermaid 안전성** — 현재 `isStreaming: false` 고정. 부분 fence는 streamdown이 닫을 때까지 emit 안 하므로 실용 위험 낮으나 dynamic detection으로 보강 가능 (0.5-1일)
5. **Frontend 43 skipped 테스트 정리** (Sprint 2/3) — agent-settings 20 / tools 9 / models 8 / assistant-panel 4 / chat 2건. legacy: 접두
6. **그 외 outdated deps**: FastAPI 0.135→0.136 / Pydantic 2.12→2.13 (minor) / cryptography 46→47 / marshmallow 3→4 / protobuf 6→7 (major)
7. M-MCP2 / M-SKILL2 / Sprint 2~4

## 알려진 이슈 / 한계

- **base-ui 1.3.0 SliderThumb script 경고**: React 19 + Next 16.2의 새 정책. mui/base-ui#4373 패치 대기
- **W6 trace 매핑은 turn 순서 기반**: branch가 있는 conversation(edit/regenerate)은 active 외 trace 매핑 X (graceful)
- **assistant-thread mermaid streaming**: `isStreaming: false` 하드코딩이지만 streamdown이 fence 닫힘까지 기다려서 실용 위험 낮음. MermaidDiagram의 catch 블록이 raw fallback

## 코드 컨벤션 (PR #88·#90·#92·#94·#96·#98·#101 정착분)

- SSE 이벤트는 `emit()`으로 unique id, consumer는 `useChatRuntime`/`createStreamGuard()`
- 메시지 비용은 `MessagesEnvelope.total_estimated_cost`, 포맷은 `formatCostUsd` 단일 사용
- 공개 endpoint(`/api/shares/*`)는 slowapi 게이트 + `share_cache`
- `git push` 자동 게이트. main 직접 커밋 금지. WIP 한정 `--no-verify`
- **deps bump**: pyproject/uv.lock 별도 커밋, `uv lock --upgrade-package <name>` 단일 명시, `uvx pip-audit`로 CVE 확인
- **타입 좁히기**: `state.get("X")` 결과 변수 캐시. `db.get(Model, id)`는 `assert is not None`. TypedDict는 `cast()`. alembic은 `sa.TextClause` / `sa.types.TypeEngine`
- **chat tool 표현은 `CollapsiblePill`** — status 4종, kind 3종. raw `bg-emerald-*` 금지
- **Trace 영속화 (W5)**: 라우트가 `trace_sink: list[dict]` 만들어 executor에 전달, `_sse_handler(on_complete=...)`로 영속화. fresh `async_session()` 사용
- **공개 페이지 칩 (W6)**: `extractChips(turn)` ChipInfo[]. user→assistant 경계마다 traces 1:1. 본문 빈 assistant 필터, 연속 assistant 한 그룹
- **Markdown 렌더 통일 (W101)**: 공유/라이브 채팅 양쪽 `buildMarkdownComponents({isStreaming})` 재사용. plugin 배열은 모듈 상수. raw color 금지 — `text-status-{success,danger,...}` 토큰만

## 기존 컨벤션 (변경 없음, 요약)

Sheet 금지(모바일 사이드바·대화 목록만 예외) / `text-primary` ≠ 강조(`-strong`) / 흰 버튼 보이면 `.next` 캐시 stale → `rm -rf .next` / Edit·Regenerate는 time-travel(`astream(None, ...)`) / 다이얼로그는 `DialogShell` + 토큰 / 한국어 날짜는 `formatLongDate`·`formatMediumDate` / 공개 페이지는 `BARE_ROUTE_PREFIXES` / 소유권 체크 `get_owned_conversation` / SSE 직렬화 `orjson.dumps` / 라이선스 MIT 2026.

## 검증

```bash
cd backend && uv run ruff check . && uv run pytest tests/ && uv run pyright
cd frontend && pnpm lint && pnpm test --run && pnpm build
```

> integration 테스트 2건은 `pytest -m integration` 수동(라이브 PG 필요).

## 핵심 파일 (다음 작업 진입점)

- SSE: `backend/app/agent_runtime/{streaming,message_utils}.py`, `frontend/src/lib/sse/{parse-sse,stream-guard}.ts`
- Trace storage (W5): `backend/app/{models/message_event.py, services/trace_storage.py}` + `routers/conversations.py(_persist_trace, list_traces)`
- Public chips (W6): `frontend/src/{lib/share/extract-chips.ts, app/shared/[shareId]/page.tsx(groupMessagesIntoTurns)}` + `backend/app/routers/shares.py`
- Markdown (W101): `frontend/src/components/chat/{markdown-content.tsx, assistant-thread.tsx, markdown-styles.css}` — `buildMarkdownComponents` export, REMARK_PLUGINS 모듈 상수
- HITL: `frontend/src/components/chat/tool-ui/*.tsx`
- Tool 표현 통일: `frontend/src/components/chat/tool-ui/collapsible-pill.tsx`
- Agent runtime: `backend/app/agent_runtime/executor.py:19-20` (deepagents 진입점)
- 게이트: `.husky/pre-push`

## 새 트랙 시작 체크

1. `git checkout -b feature/<name>` (또는 `chore/<name>`, `fix/<name>`) — main 직접 커밋 금지
2. 작업마다 별도 commit, `git push`로 자동 게이트 통과 확인
3. 마지막에 `gh pr create`로 묶음 PR
