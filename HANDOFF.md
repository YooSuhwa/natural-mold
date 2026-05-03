# 작업 인계 — main (PR #90 머지 후)

> 새 세션은 본 파일 + `progress.txt` 마지막 4-5 섹션만 읽으면 충분.
> 디자인 시스템은 `docs/design-docs/ADR-010-ui-tokens-and-dialog-shell.md`.

## 마지막 상태

- 브랜치 **`main`** (origin sync, working tree clean)
- 최신 커밋 `c6db561 Merge pull request #90 — chore/deps-deepagents-langchain-upgrade`
- alembic head **m31** — `cd backend && uv run alembic upgrade head` 필수
- backend 723 pass / frontend 232 pass + 43 skip / lint·build clean
- pyright 36 errors / 8 warnings — pre-existing baseline (회귀 0)
- `uvx pip-audit` clean (CVE 0)
- `.husky/pre-push`가 backend pytest + frontend vitest 자동 게이트

## PR #90 — deepagents/langchain 생태계 업그레이드

- **deepagents 0.4.12 → 0.5.6** (harness profiles, async subagents, backend protocol v2)
- langchain 1.2.14→1.2.17 / langchain-core 1.2.23→1.3.2 / langgraph 1.1.4→1.1.10
- langsmith 0.7.23→0.8.0 / mcp 1.26.0→1.27.0 / anthropic 0.87.0→0.97.0
- 신규 transitive: `langchain-protocol 0.0.15`
- **앱 코드 수정 0건** (`from deepagents.backends import FilesystemBackend` 경로 그대로 유효)
- D+2 신규 릴리스 → 24h LangSmith trace 모니터링 권장

## PR #88 트랙 (이전 세션 완료)

W1 HITL countdown / W2 share rate-limit + cache / W3-1~3 SSE id+dedup / W7~7-5 메시지별 토큰+비용 / husky pre-push 게이트.

## 다음 작업 후보

1. **pyright baseline 정리** (1-2일, 회귀 그물망 보강)
   - 실제 버그 2건: `routers/agents.py:104` (존재하지 않는 `last_used_at`), alembic m7/m9 (deleted `app.services.encryption` import)
   - Optional/Literal 좁히기: model_catalog/normalize.py 4건, model_discovery 1건, builder_v3/phase2_intent 1건, sub_agents/helpers 2건
   - 테스트 타입: test_message_utils 5건, test_health_check 7건, test_skills 4건
2. **W3-out** GET-based stream resume (W5와 짝, 5-7일) — W6 잠금 해제
3. **W6 shared 페이지 도구/Skill 칩 렌더** (W3-out/W5 의존, 2일)
4. **W5 TraceStorage** — turn별 events array (PostgreSQL JSONB), 2-3일
5. **CollapsiblePill 통일 컴포넌트** — loading/success/error/cancelled 4상태, 1일, 의존성 0
6. **MarkdownContent 강화** — mermaid + katex + remarkBreaks, 1일
7. **그 외 outdated deps**: FastAPI 0.135→0.136 / Pydantic 2.12→2.13 / cryptography 46→47 (major) / marshmallow 3→4 (major) / protobuf 6→7 (major) — 위험도별 분리 PR
8. M-MCP2 / M-SKILL2 / Sprint 2~4

## 코드 컨벤션 (PR #88·#90 정착분)

- SSE 이벤트는 `emit()`으로 unique id, consumer는 `useChatRuntime`/`createStreamGuard()`
- 메시지 비용은 `MessagesEnvelope.total_estimated_cost`, 포맷은 `formatCostUsd` 단일 사용
- 공개 endpoint(`/api/shares/*`)는 slowapi 게이트 + `share_cache`
- `git push` 자동 게이트. main 직접 커밋 금지. WIP 한정 `--no-verify`
- **deps bump**: pyproject/uv.lock 별도 커밋, `uv lock --upgrade-package <name>` 단일 명시, `uvx pip-audit`로 CVE 확인, pyright는 main과 비교

## 기존 컨벤션 (변경 없음, 요약)

Sheet 금지(모바일 사이드바·대화 목록만 예외) / `text-primary` ≠ 강조(`-strong`) / 흰 버튼 보이면 `.next` 캐시 stale → `rm -rf .next` / Edit·Regenerate는 time-travel(`astream(None, ...)`) / 다이얼로그는 `DialogShell` + 토큰(`DIALOG_SIZE`) / 한국어 날짜는 `formatLongDate`·`formatMediumDate` / 공개 페이지는 `BARE_ROUTE_PREFIXES` / 소유권 체크 `get_owned_conversation` / SSE 직렬화 `orjson.dumps` / 라이선스 MIT 2026.

## 검증

```bash
cd backend && uv run ruff check . && uv run pytest tests/
cd frontend && pnpm lint && pnpm test --run && pnpm build
```

> `git push` 시 husky pre-push가 동일 게이트 자동 실행.

## 핵심 파일 (다음 작업 진입점)

- SSE: `backend/app/agent_runtime/{streaming,message_utils}.py`, `frontend/src/lib/sse/{parse-sse,stream-guard}.ts`
- Token usage: `backend/app/services/chat_service.py`, `frontend/src/lib/chat/use-chat-runtime.ts`
- HITL: `frontend/src/components/chat/tool-ui/*.tsx`
- Share/rate-limit: `backend/app/{rate_limit.py, services/share_cache.py}`
- Agent runtime: `backend/app/agent_runtime/executor.py:19-20` (deepagents 진입점)
- 게이트: `.husky/pre-push`

## 새 트랙 시작 체크

1. `git checkout -b feature/<name>` (또는 `chore/<name>`, `fix/<name>`) — main 직접 커밋 금지
2. 작업마다 별도 commit, `git push`로 자동 게이트 통과 확인
3. 마지막에 `gh pr create`로 묶음 PR
