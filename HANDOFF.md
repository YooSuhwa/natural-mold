# 작업 인계 — main (PR #99 머지 후)

> 새 세션은 본 파일 + `progress.txt` 마지막 4-5 섹션만 읽으면 충분.
> 디자인 시스템은 `docs/design-docs/ADR-010-ui-tokens-and-dialog-shell.md`.

## 마지막 상태

- 브랜치 **`main`** (origin sync, working tree clean)
- 최신 커밋 `ebd77ee Merge pull request #99 — chore/catalog-auto-update`
- alembic head **m32** — `cd backend && uv run alembic upgrade head` 필수
- backend **733 pass** / frontend **245 pass** + 43 skip / lint·build clean
- pyright **0 errors / 0 warnings**, pytest 수집 경고 0건
- `uvx pip-audit` clean (CVE 0)
- `.husky/pre-push`가 backend pytest + frontend vitest 자동 게이트

## 최근 머지된 트랙 (이번 세션, 10 PR)

| PR | 트랙 | 임팩트 |
|----|------|--------|
| #90 | deepagents 0.5.6 + langchain 생태계 일괄 업그레이드 | 16개 패키지 bump, 코드 수정 0 |
| #92 | pyright baseline 정리 (36/8 → 0/0) | **실제 버그 2건 수정** |
| #94 | CollapsiblePill 통일 컴포넌트 | 4 사이트 적용 |
| #96 | **W5 TraceStorage** | message_events 테이블 + GET /traces |
| #98 | **W6 공개 페이지 도구/Skill 칩 렌더** | **사용자 가시 개선** |
| #99 | 모델 카탈로그 자동 갱신 | 데이터 fetch 1664 라인 |

### W6 핵심 (PR #98)
- `SharedConversationView.traces` 필드 + `GET /api/shares/{token}` 응답에 포함
- `extractChips`: tool_call_start + result FIFO 매칭, `task` → subagent, `write_todos` → Plan
- `groupMessagesIntoTurns`: 연속 assistant 메시지를 한 turn 그룹으로 묶음 (라이브 채팅 UX와 일치)
- 본문 비어있는 assistant placeholder 필터 (도구만 호출하는 AIMessage)
- trace 매칭은 user→assistant 경계마다 `traces[turnIdx]` chronological 1:1 (assistant_msg_id ≠ MessageResponse.id 한계 우회)

## 다음 작업 후보

1. **W3-out** GET-based stream resume — background task + event broker(asyncio.Queue/Redis Pub/Sub) + 진행형 append + `GET /stream?run_id=&last_event_id=` (5-7일). W5 `last_event_id` 활용. **메인 UX 트랙**
2. **MarkdownContent 강화** — mermaid + katex + remarkBreaks (1일)
3. **CollapsiblePill 추가 적용** (Sprint 2) — code-tool-ui / image-generation-ui / draft-config-ui / recommendation-approval-ui (1-2일)
4. **W6 정확도 개선** — backend가 turn별 langchain msg id를 함께 저장하면 branch가 있는 conversation에서도 정확한 trace 매핑 (1-2일)
5. **Frontend 43 skipped 테스트 정리** (Sprint 2/3) — agent-settings 20 / tools 9 / models 8 / assistant-panel 4 / chat 2건. 대부분 페이지 리팩터 후 셀렉터 무효 (`legacy:` 접두)
6. **그 외 outdated deps**: FastAPI 0.135→0.136 / Pydantic 2.12→2.13 (minor) / cryptography 46→47 / marshmallow 3→4 / protobuf 6→7 (major)
7. M-MCP2 / M-SKILL2 / Sprint 2~4

## 알려진 이슈 / 한계

- **base-ui 1.3.0 SliderThumb script 경고**: React 19 + Next 16.2의 새 정책. mui/base-ui#4373 패치 대기. 1.4+ 업그레이드로 해결 가능
- **W6 trace 매핑은 turn 순서 기반**: branch가 있는 conversation(edit/regenerate)은 active 브랜치 외 trace가 매핑되지 않을 수 있음 → 자연스럽게 칩 미표시 (graceful). 백엔드가 turn당 langchain msg id 저장하면 정확해짐 (후속 트랙)

## 코드 컨벤션 (PR #88·#90·#92·#94·#96·#98 정착분)

- SSE 이벤트는 `emit()`으로 unique id, consumer는 `useChatRuntime`/`createStreamGuard()`
- 메시지 비용은 `MessagesEnvelope.total_estimated_cost`, 포맷은 `formatCostUsd` 단일 사용
- 공개 endpoint(`/api/shares/*`)는 slowapi 게이트 + `share_cache`
- `git push` 자동 게이트. main 직접 커밋 금지. WIP 한정 `--no-verify`
- **deps bump**: pyproject/uv.lock 별도 커밋, `uv lock --upgrade-package <name>` 단일 명시, `uvx pip-audit`로 CVE 확인
- **타입 좁히기**: `state.get("X")` 결과 변수 캐시. `db.get(Model, id)`는 `assert is not None`. TypedDict는 `cast()`. alembic은 `sa.TextClause` / `sa.types.TypeEngine`
- **chat tool 표현은 `CollapsiblePill`** — status 4종(loading/success/error/cancelled), kind 3종(tool/subagent/thinking). raw `bg-emerald-*` 금지
- **Trace 영속화 (W5)**: 라우트가 `trace_sink: list[dict]` 만들어 executor에 전달, `_sse_handler(on_complete=lambda: _persist_trace(...))`로 영속화. fresh `async_session()` 사용
- **공개 페이지 칩 (W6)**: trace events → `extractChips(turn)`로 ChipInfo[] 추출. user→assistant 경계마다 traces 1:1 매핑. 본문 빈 assistant는 필터, 연속 assistant는 한 그룹

## 기존 컨벤션 (변경 없음, 요약)

Sheet 금지(모바일 사이드바·대화 목록만 예외) / `text-primary` ≠ 강조(`-strong`) / 흰 버튼 보이면 `.next` 캐시 stale → `rm -rf .next` / Edit·Regenerate는 time-travel(`astream(None, ...)`) / 다이얼로그는 `DialogShell` + 토큰(`DIALOG_SIZE`) / 한국어 날짜는 `formatLongDate`·`formatMediumDate` / 공개 페이지는 `BARE_ROUTE_PREFIXES` / 소유권 체크 `get_owned_conversation` / SSE 직렬화 `orjson.dumps` / 라이선스 MIT 2026.

## 검증

```bash
cd backend && uv run ruff check . && uv run pytest tests/ && uv run pyright
cd frontend && pnpm lint && pnpm test --run && pnpm build
```

> `git push` 시 husky pre-push가 동일 게이트 자동 실행. pyright는 게이트 밖이지만 main에서 0/0 baseline. integration 테스트 2건은 `pytest -m integration`으로 수동 실행(라이브 PG 필요).

## 핵심 파일 (다음 작업 진입점)

- SSE: `backend/app/agent_runtime/{streaming,message_utils}.py`, `frontend/src/lib/sse/{parse-sse,stream-guard}.ts`
- Trace storage (W5): `backend/app/{models/message_event.py, services/trace_storage.py, schemas/conversation.py(TurnTraceResponse)}` + `routers/conversations.py(_persist_trace, list_traces)`
- Public chips (W6): `frontend/src/{lib/share/extract-chips.ts, app/shared/[shareId]/page.tsx(groupMessagesIntoTurns)}` + `backend/app/{schemas/share.py, routers/shares.py(traces=)}`
- Token usage: `backend/app/services/chat_service.py`, `frontend/src/lib/chat/use-chat-runtime.ts`
- HITL: `frontend/src/components/chat/tool-ui/*.tsx`
- Tool 표현 통일: `frontend/src/components/chat/tool-ui/collapsible-pill.tsx`
- Share/rate-limit: `backend/app/{rate_limit.py, services/share_cache.py}`
- Agent runtime: `backend/app/agent_runtime/executor.py:19-20` (deepagents 진입점)
- 게이트: `.husky/pre-push`

## 새 트랙 시작 체크

1. `git checkout -b feature/<name>` (또는 `chore/<name>`, `fix/<name>`) — main 직접 커밋 금지
2. 작업마다 별도 commit, `git push`로 자동 게이트 통과 확인
3. 마지막에 `gh pr create`로 묶음 PR
