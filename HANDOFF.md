# 작업 인계 — main (PR #86 머지)

> 새 세션 시작 시: 본 파일 + `progress.txt` 마지막 4-5 섹션만 읽으면 충분.
> 자세한 결정/oklch/스펙은 `docs/design-docs/ADR-010-ui-tokens-and-dialog-shell.md`.

## 마지막 상태

- 브랜치: `main` (origin과 sync, clean)
- 최신 커밋: `52b78b7 PR #86 (project cleanup 12 커밋)`
- 직전 머지: `5abe5d4 PR #85 (MIT 라이선스)` ← `2be2b81 PR #83 (M-CHAT2)`
- alembic head: **m31** (사용자 환경에선 `cd backend && uv run alembic upgrade head` 필수)
- backend: ruff clean, **pytest 709 passed**
- frontend: tsc clean, lint clean, build PASS (17 라우트)
- dev server: backend 8002 (m31 적용), frontend 3000

## 머지된 마일스톤

- [x] **M-UI1 + 핫픽스** — DialogShell + 디자인 토큰(emerald) + Sheet→Dialog 4종
- [x] **M-MCP1c/M-SKILL1c** — stdio + import/export + health polling + Skill multi-file editor (m26)
- [x] **M-CHAT1** (9/11) — Right Rail + 메시지 액션·검색·HITL countdown·Mermaid·파일첨부 (m27/m28)
- [x] **M-CHAT1b** — LangGraph fork + BranchPicker `<N/M>` (m29)
- [x] **/simplify P0+P1** — `_sse_handler`, `_run_agent_stream`, thread tree O(L×D)+O(L×M)
- [x] **M-UI1b** — 잔여 16개 DialogContent → DialogShell. `srOnly` prop 추가, FormFooter 재사용
- [x] **M-CHAT2** — `share_links` (m30/m31 partial unique index) + 공개 read-only `/shared/[shareId]` (LambChat editorial layout) + race-safe (IntegrityError → re-fetch)
- [x] **MIT 라이선스 + 프로젝트 정리** (PR #85, #86) — LICENSE, SECURITY/CONTRIBUTING/.dockerignore, README 전면 개편 + README_EN, mascot WebP, .gitignore 보강, orjson SSE hot path, tasks/ archive 정리

## 진행 중인 통합 브랜치 — `feature/handoff-improvements`

main 위에 누적된 작업(이 한 브랜치를 PR로):

- ✅ W1 HITL countdown — user-input/clarifying에 `useApprovalDeadline` 확장
- ✅ W2 공개 share rate limit + snapshot 캐시 (slowapi + TTL cache)
- ✅ W3-1 SSE event id 발행 + dedup helper
- ✅ W3-2 streamSSEPost를 `@microsoft/fetch-event-source` 기반으로 교체
  (탭 백그라운드 안정화, openWhenHidden=true). POST 한계로 자동 재시도는 비활성
- ✅ W3-3 caller-side stream guard (stale/duplicate event 폐기, fork race 차단)
- ✅ 테스트 부채 정리 — 67 fail → 0 fail (mock 보강 + 페이지 슬림화)
- ✅ husky pre-push hook (backend pytest + frontend vitest 게이트화)

## 다음 작업 후보 (우선순위 순)

1. **W3-out — 백엔드 GET-based stream resume endpoint** (W3 시리즈 마무리, **분량 큼**)
   - 현재 SSE는 POST 기반이라 진짜 자동 재연결 불가. fetch-event-source 도입(W3-2)으로
     탭 백그라운드는 안정화됐지만 **네트워크 단절 시 끊긴 지점부터 이어받기**가 안 됨.
   - 필요 컴포넌트:
     - **Run을 background task로 분리** (`asyncio.create_task` 또는 Celery/arq)
     - **Event broker** — `dict[run_id, asyncio.Queue]` 또는 Redis Pub/Sub
     - **Trace storage** (W5와 통합) — 모든 event를 DB에 append, replay용
     - **`GET /api/conversations/:id/stream?run_id=&last_event_id=`** endpoint —
       last_event_id 이후 events DB replay → broker subscribe로 실시간 stream
     - 프론트는 `Last-Event-ID` 헤더 자동 송신 (fetch-event-source 옵션)
   - 분량 5-7일. **W5(TraceStorage)와 짝**이라 묶어 처리 권장.
   - 시너지: M-CHAT2 공유 페이지(W6 도구 칩 렌더)도 trace events에서 재구성 가능.
2. **shared 페이지 도구 호출/Skill 칩 렌더** — LambChat은 샌드박스/Todo/Skill 칩까지 노출. 우리는 user/assistant text만. assistant-ui ChatMessage 위에 read-only 변형 필요. **W3-out/W5 trace storage 의존**.
3. **TraceStorage (W5)** — turn별 trace_id 1 doc에 events array (PostgreSQL JSONB).
   디버깅 + 공유 페이지 재구성 + W3-out 토대.
4. **Token 4종 추적 + 메시지별 메타 표시** — input/output/cache_creation/cache_read
   분리. 어시스턴트 메시지 푸터 hover 팝오버.
5. **deepagents 0.4.12 → 0.5.3 업그레이드** — LambChat 비교 시 발견. breaking change 확인 후 적용.
6. **CollapsiblePill 통일 컴포넌트** — LambChat `common/CollapsiblePill` 패턴
   (loading/success/error/cancelled 4상태). tool/subagent/thinking 모두에 적용.
   의존성 0, 1일.
7. **MarkdownContent 강화** — mermaid + katex + remarkBreaks. 1일.
8. **M-MCP2** — System/User 컬렉션 분리, Fernet+PBKDF2, Redis hybrid + Pub/Sub, Lua quota.
9. **M-SKILL2** — Marketplace, sandbox CompositeBackend, requirements 자동 설치, 버전 히스토리.
10. **Sprint 2** — 5 page.tsx → PageShell + raw color 58회 토큰화 + i18n 한/영.
11. **Sprint 3** — `agents/[agentId]/settings/page.tsx` (518줄) → React Hook Form + Zod.
12. **Sprint 4** — bundle splitting + Suspense + key={i}.

## 주의사항 (코드 컨벤션)

- **Sheet 사용 금지** — 모바일 사이드바 + 대화 목록 2곳만 예외. 우측 패널은 ChatRightRail
- **`text-primary`는 옅은 emerald (surface)** — 강조 의도면 `text-primary-strong`
- **`bg-primary-strong text-white` 흰 버튼 보일 때** → `rm -rf .next && pnpm dev` (Turbopack 캐시 stale)
- **Edit/Regenerate는 LangGraph time-travel** — `astream(None, config={checkpoint_id})`. user content 재전송 금지
- **alembic 새 마이그레이션 시** 사용자에게 `uv run alembic upgrade head` 안내
- **`page-header.tsx`는 9페이지에서 사용** — 삭제 금지
- 새 다이얼로그 → `DialogShell` + 토큰(`DIALOG_SIZE`/`DIALOG_HEIGHT`)
- chrome 없는 다이얼로그(lightbox / 커스텀 헤더) → `<DialogShell.Header srOnly title="..." />`
- 폼 다이얼로그 footer → `FormFooter` 재사용 (Cancel+Save 직접 작성 금지)
- 한국어 날짜 → `formatLongDate` / `formatMediumDate` (KST 고정). `toLocaleDateString` 직접 호출 금지
- 공개 페이지 → `AppLayout BARE_ROUTE_PREFIXES`에 prefix 등록 → bare 모드
- conversation 소유권 체크 → `chat_service.get_owned_conversation` (다른 user 소유 시 `None` → 404 통일)
- SSE chunk 직렬화 → `orjson.dumps` (FastAPI default response는 그대로 두기, 0.115+에서 ORJSONResponse는 deprecated)
- **SSE 이벤트는 백엔드에서 unique id 발행** — `streaming.py`의 `emit()` 헬퍼 사용
  (`{msg_id}-{seq}` 형식). caller가 W3-1 dedup helper / W3-3 stream guard로 활용.
- **SSE consumer는 `useChatRuntime` 경유** — 자체 SSE 핸들링 작성 시 `createStreamGuard()`를
  직접 사용해 Edit/Regenerate fork race 차단할 것.
- main 직접 커밋 금지 — feature/fix/chore 브랜치 + PR
- **`git push` 시 husky pre-push가 backend pytest + frontend vitest 자동 실행**.
  WIP 브랜치 한정 `--no-verify` 우회 가능. 머지 PR에선 절대 우회 금지.
- 라이선스: MIT (Copyright 2026 Moldy contributors)

## 검증 명령

```bash
cd backend && uv run ruff check . && uv run pytest tests/ --tb=short
cd frontend && pnpm lint && pnpm test --run && pnpm build
```

> `git push`로도 동일한 게이트가 자동 실행 (`.husky/pre-push`).

## 핵심 파일

- 디자인 토큰: `frontend/src/app/globals.css`, `lib/design-tokens.ts`
- DialogShell: `components/shared/dialog-shell.tsx` (`srOnly` prop) / FormFooter: `components/shared/form-footer.tsx`
- 날짜 유틸: `lib/utils/format-relative-time.ts` (`formatLongDate`/`formatMediumDate`/`formatRelativeShort` — KST)
- M-CHAT2 Share: `backend/app/{models/share_link.py, schemas/share.py, services/share_service.py, routers/shares.py}` + `frontend/src/{lib/api/shares.ts, lib/hooks/use-share.ts, components/chat/share-dialog.tsx, app/shared/[shareId]/page.tsx}` + `AppLayout` BARE_ROUTE_PREFIXES
- Share rate limit: `backend/app/{rate_limit.py, services/share_cache.py}`, `slowapi` (W2)
- ChatRightRail: `lib/stores/chat-right-rail.ts`, `components/chat/right-rail/*`
- LangGraph fork: `backend/app/services/thread_branch_service.py`, `agent_runtime/{executor,streaming}.py`
- BranchPicker: `components/chat/assistant-thread.tsx`, `lib/chat/{convert-message,use-chat-runtime}.ts`
- HITL countdown: `lib/hooks/use-approval-deadline.ts`, `components/chat/tool-ui/{approval-card,user-input-ui,clarifying-question-ui,countdown-badge}.tsx` (W1)
- SSE 인프라: `backend/app/agent_runtime/streaming.py` (event id), `frontend/src/lib/sse/{parse-sse,stream-guard}.ts` (fetch-event-source + dedup + stale guard) (W3-1/2/3)
- Pre-push hook: `.husky/pre-push` (backend pytest + frontend vitest 게이트)
- 메타 docs: `README.md` / `README_EN.md` / `CONTRIBUTING.md` / `SECURITY.md` / `.dockerignore`
- ADR: `docs/design-docs/ADR-010-ui-tokens-and-dialog-shell.md`
- Mascot: `docs/images/moldy-mascot.webp`
- Audit/팀 메모리: `AUDIT.log`, `progress.txt`, `tasks/archive/` (옛 milestone 자료)
