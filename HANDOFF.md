# 작업 인계 — main (PR #94 머지 후)

> 새 세션은 본 파일 + `progress.txt` 마지막 4-5 섹션만 읽으면 충분.
> 디자인 시스템은 `docs/design-docs/ADR-010-ui-tokens-and-dialog-shell.md`.

## 마지막 상태

- 브랜치 **`main`** (origin sync, working tree clean)
- 최신 커밋 `c36f51a Merge pull request #94 — feature/collapsible-pill`
- alembic head **m31** — `cd backend && uv run alembic upgrade head` 필수
- backend 723 pass / frontend 232 pass + 43 skip / lint·build clean
- pyright **0 errors / 0 warnings**, pytest 수집 경고 0건
- `uvx pip-audit` clean (CVE 0)
- `.husky/pre-push`가 backend pytest + frontend vitest 자동 게이트

## 최근 머지된 트랙 (이번 세션)

| PR | 트랙 | 임팩트 |
|----|------|--------|
| #90 | deepagents 0.5.6 + langchain 생태계 일괄 업그레이드 | 16개 패키지 bump, 코드 수정 0 |
| #92 | pyright baseline 정리 (36/8 → 0/0) | **실제 버그 2건 수정** |
| #94 | CollapsiblePill 통일 컴포넌트 | 4 사이트 적용 (sub-agent / generic-tool / search / plan) |

### PR #92 발견한 실제 버그
- `AgentResponse.last_used_at` 필드 누락 → Pydantic이 silently drop, 사이드바 정렬이 `updated_at` 폴백으로만 동작 (사이드바 "최근 채팅" 정렬 복구)
- alembic m7/m9의 `app.services.encryption` dead import → fresh DB 롤업 깨짐 수정

## 다음 작업 후보

1. **W3-out** GET-based stream resume — POST 한계로 못 했던 자동 재연결. background task + event broker(asyncio.Queue/Redis Pub/Sub) + `GET /stream?run_id=&last_event_id=`. **W5와 짝, 5-7일**. W6 잠금 해제
2. **W6 shared 페이지 도구/Skill 칩 렌더** (W3-out/W5 의존, 2일) — CollapsiblePill 활용 가능
3. **W5 TraceStorage** — turn별 events array (PostgreSQL JSONB), 2-3일
4. **MarkdownContent 강화** — mermaid + katex + remarkBreaks, 1일
5. **CollapsiblePill 추가 적용** (Sprint 2) — code-tool-ui / image-generation-ui / draft-config-ui / recommendation-approval-ui 등
6. **그 외 outdated deps**: FastAPI 0.135→0.136 / Pydantic 2.12→2.13 / cryptography 46→47 (major) / marshmallow 3→4 (major) / protobuf 6→7 (major) — 위험도별 분리 PR
7. M-MCP2 / M-SKILL2 / Sprint 2~4

## 알려진 이슈 (우리 코드 무관)

- **base-ui 1.3.0 SliderThumb script 경고**: React 19 + Next 16.2의 새 정책으로 `<script>` 렌더 시 콘솔 노이즈. 기능 영향 없음. mui/base-ui#4373 패치 대기
- **422 일시 발생**: PR #94 머지 직후 `POST /api/conversations/.../messages`에서 1회 관찰됨. dev 서버 reload 타이밍 의심. 재현 불가 → 다시 보면 DevTools Network 탭의 detail JSON 공유 필요

## 코드 컨벤션 (PR #88·#90·#92·#94 정착분)

- SSE 이벤트는 `emit()`으로 unique id, consumer는 `useChatRuntime`/`createStreamGuard()`
- 메시지 비용은 `MessagesEnvelope.total_estimated_cost`, 포맷은 `formatCostUsd` 단일 사용
- 공개 endpoint(`/api/shares/*`)는 slowapi 게이트 + `share_cache`
- `git push` 자동 게이트. main 직접 커밋 금지. WIP 한정 `--no-verify`
- **deps bump**: pyproject/uv.lock 별도 커밋, `uv lock --upgrade-package <name>` 단일 명시, `uvx pip-audit`로 CVE 확인, pyright는 main과 비교
- **타입 좁히기**: `state.get("X")` 결과는 변수 캐시 후 좁힘. `db.get(Model, id)`는 `assert is not None`. TypedDict는 `cast()`. alembic은 `sa.TextClause`
- **chat tool 표현은 `CollapsiblePill`** (`components/chat/tool-ui/collapsible-pill.tsx`) — status: loading/success/error/cancelled, kind: tool/subagent/thinking. raw `bg-emerald-*` 금지 (시맨틱 status 토큰만)

## 기존 컨벤션 (변경 없음, 요약)

Sheet 금지(모바일 사이드바·대화 목록만 예외) / `text-primary` ≠ 강조(`-strong`) / 흰 버튼 보이면 `.next` 캐시 stale → `rm -rf .next` / Edit·Regenerate는 time-travel(`astream(None, ...)`) / 다이얼로그는 `DialogShell` + 토큰(`DIALOG_SIZE`) / 한국어 날짜는 `formatLongDate`·`formatMediumDate` / 공개 페이지는 `BARE_ROUTE_PREFIXES` / 소유권 체크 `get_owned_conversation` / SSE 직렬화 `orjson.dumps` / 라이선스 MIT 2026.

## 검증

```bash
cd backend && uv run ruff check . && uv run pytest tests/ && uv run pyright
cd frontend && pnpm lint && pnpm test --run && pnpm build
```

> `git push` 시 husky pre-push가 동일 게이트 자동 실행. pyright는 게이트 밖이지만 main에서 0/0 baseline.

## 핵심 파일 (다음 작업 진입점)

- SSE: `backend/app/agent_runtime/{streaming,message_utils}.py`, `frontend/src/lib/sse/{parse-sse,stream-guard}.ts`
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
