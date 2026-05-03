# 작업 인계 — main (PR #105~#108 머지 후 가정)

> 새 세션은 본 파일 + `progress.txt` 마지막 4-5 섹션만 읽으면 충분.
> 디자인 시스템은 `docs/design-docs/ADR-010-ui-tokens-and-dialog-shell.md`.
> ⚠️ 본 문서는 PR #105/#106/#107/#108 머지 직후 시점 가정. 다 머지된 후 사용.

## 마지막 상태

- 브랜치 **`main`** (PR #105~#108 머지 가정)
- alembic head **m33** — `cd backend && uv run alembic upgrade head` 필수 (PR #108)
- backend **735 pass** / frontend **249 pass** + 43 skip / lint·build clean
- pyright **0 errors / 0 warnings**, pytest 수집 경고 0건
- `uvx pip-audit` clean (CVE 0)
- `.husky/pre-push`가 backend pytest + frontend vitest 자동 게이트

## 최근 머지된 트랙 (이번 세션, 16+ PR)

| PR | 트랙 | 임팩트 |
|----|------|--------|
| #90 | deepagents 0.5.6 + langchain 일괄 업그레이드 | 16 패키지 bump |
| #92 | pyright baseline 정리 (36/8 → 0/0) | **실제 버그 2건 수정** |
| #94 | CollapsiblePill 통일 컴포넌트 | UI 일관성 |
| #96 | **W5 TraceStorage** | 백엔드 토대 |
| #98 | **W6 공개 페이지 도구/Skill 칩 렌더** | 사용자 가시 개선 |
| #99 | 모델 카탈로그 자동 갱신 | 데이터 |
| #101 | **MarkdownContent 강화** | mermaid+KaTeX+디자인 통일 |
| #103 | **CollapsiblePill Sprint 2** | code-tool + tool-ui 토큰화 |
| #105 | **CollapsiblePill leadingIcon prop** | code-tool file 아이콘 복원 |
| #106 | **tool-ui status 매핑 통합** | 5개 파일 미스매치 정리 |
| #107 | **mermaid streaming 안전성** | 동적 isStreaming detect |
| #108 | **W6 정확도 개선 (linked_message_ids)** | branch까지 정확 매핑 |
| #91, #93, #95, #97, #100, #102, #104 | HANDOFF 갱신 7건 | 문서 |

### PR #105~#108 핵심 (이번 마지막 batch)
- **#105 leadingIcon**: CollapsiblePill에 옵션 prop. code-tool-ui의 Read/Write/Edit이 FileIcon/FilePlusIcon/FileEditIcon으로 시각 구분
- **#106 status 매핑**: `pillStatusFromAssistantUi(statusType)` 표준 헬퍼 + 5개 파일 통일. `incomplete = HiTL reject = cancelled` 의미 정정 (sub-agent의 incomplete=error 잘못 매핑 수정)
- **#107 mermaid streaming**: `useAssistantState`로 `isRunning` 동적 detect → 두 components(streaming/final) ref swap. 부분 mermaid가 SVG 시도해서 발생하던 시각 flicker 제거
- **#108 W6 정확도**: alembic m33로 `linked_message_ids` 컬럼 + streaming raw msg id 수집. branch가 있는 conversation에서도 trace ↔ message 직접 매칭

## 다음 작업 후보

1. **W3-out** GET-based stream resume — background task + event broker + `GET /stream?run_id=&last_event_id=` (5-7일). W5 활용. **메인 UX 트랙**
2. **HITL 컴포넌트 zinc → border/muted 토큰화** (1일) — image-generation-ui / draft-config-ui / approval-card 등의 raw zinc 클래스 정리 (Sprint 3)
3. **Frontend 43 skipped 테스트 정리** (Sprint 2/3) — agent-settings 20 / tools 9 / models 8 / assistant-panel 4 / chat 2건. legacy 접두
4. **그 외 outdated deps**: FastAPI 0.135→0.136 / Pydantic 2.12→2.13 (minor) / cryptography 46→47 / marshmallow 3→4 / protobuf 6→7 (major) — 위험도별 분리 PR
5. **mermaid 다크모드** — `mermaid-diagram.tsx`의 `theme: 'default'`를 `useTheme` 동적
6. M-MCP2 / M-SKILL2 / Sprint 2~4

## 알려진 이슈 / 한계

- **base-ui 1.3.0 SliderThumb script 경고**: React 19 + Next 16.2 새 정책. mui/base-ui#4373 패치 대기
- **W6 trace 매핑**: PR #108로 정확도 개선됐으나, m32 이전 row는 `linked_message_ids = NULL`이라 chronological 폴백 (graceful)
- **assistant-thread mermaid streaming**: PR #107로 streaming 중 raw 표시 → 메시지 종료 후 SVG 변환

## 코드 컨벤션 (PR #88·#90·#92·#94·#96·#98·#101·#103·#105·#106·#107·#108 정착분)

- SSE 이벤트는 `emit()`으로 unique id, consumer는 `useChatRuntime`/`createStreamGuard()`
- 메시지 비용은 `MessagesEnvelope.total_estimated_cost`, 포맷은 `formatCostUsd` 단일 사용
- 공개 endpoint(`/api/shares/*`)는 slowapi 게이트 + `share_cache`
- `git push` 자동 게이트. main 직접 커밋 금지
- **deps bump**: pyproject/uv.lock 별도 커밋, `uv lock --upgrade-package <name>` 단일 명시, `uvx pip-audit`로 CVE 확인
- **타입 좁히기**: `state.get("X")` 결과 변수 캐시. `db.get(Model, id)`는 `assert is not None`. TypedDict는 `cast()`. alembic은 `sa.TextClause` / `sa.types.TypeEngine`
- **chat tool 표현은 `CollapsiblePill`** — kind="tool|subagent|thinking", status 4종, leadingIcon으로 도구 종류 시각 구분 가능. raw `bg-emerald-*`/`bg-violet-*`/`bg-amber-*` 금지 — `text-status-{success,danger,warn,accent,info}` 토큰만
- **status 매핑 통일**: 항상 `pillStatusFromAssistantUi(status.type)` 사용 (`collapsible-pill.tsx`). `incomplete = cancelled` 의미상 정확
- **HITL 양식**(approval-card/clarifying/draft-approval/image-approval)은 CollapsiblePill 강제 X — 양식 구조 보존하되 status 토큰만 통일
- **Trace 영속화 (W5+W6)**: 라우트가 `trace_sink: list[dict]` + `msg_id_sink: list[str]` 만들어 executor에 전달, `_sse_handler(on_complete=...)`로 영속화. `record_turn(raw_msg_ids=...)`이 parse_msg_id로 UUID 변환
- **공개 페이지 칩 (W6)**: `extractChips(turn)`. `groupMessagesIntoTurns` 매칭 우선순위 — `linked_message_ids` 직접 매칭 → 폴백(chronological)
- **Markdown 렌더 통일**: 공유/라이브 채팅 양쪽 `buildMarkdownComponents({isStreaming})` 재사용. plugin 배열은 모듈 상수. 라이브 채팅은 `useAssistantState`로 isRunning 동적 (streaming/final 두 components ref swap)

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
- Trace storage (W5+W6): `backend/app/{models/message_event.py, services/trace_storage.py}` + `routers/conversations.py(_persist_trace, list_traces)`
- Public chips (W6): `frontend/src/{lib/share/extract-chips.ts, app/shared/[shareId]/page.tsx(groupMessagesIntoTurns)}` + `backend/app/routers/shares.py`
- Markdown 통일: `frontend/src/components/chat/{markdown-content.tsx, assistant-thread.tsx}` — `buildMarkdownComponents` export
- Tool 표현: `frontend/src/components/chat/tool-ui/collapsible-pill.tsx` (`pillStatusFromAssistantUi` 헬퍼) — 5개 파일에서 사용
- Agent runtime: `backend/app/agent_runtime/executor.py:19-20` (deepagents 진입점)
- 게이트: `.husky/pre-push`

## 새 트랙 시작 체크

1. `git checkout -b feature/<name>` (또는 `chore/<name>`, `fix/<name>`) — main 직접 커밋 금지
2. 작업마다 별도 commit, `git push`로 자동 게이트 통과 확인
3. 마지막에 `gh pr create`로 묶음 PR
