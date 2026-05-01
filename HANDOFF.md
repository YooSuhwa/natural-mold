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

## 다음 작업 후보 (우선순위 순)

1. **shared 페이지 도구 호출/Skill 칩 렌더** — LambChat은 샌드박스/Todo/Skill 칩까지 노출. 우리는 user/assistant text만. assistant-ui ChatMessage 위에 read-only 변형 필요.
2. **공개 endpoint rate limit** — `/api/shares/*`는 unauth + checkpoint walk라 abuse 벡터. slowapi(IP) + share snapshot 캐싱(키: `(token, active_branch_checkpoint_id)`).
3. **HITL countdown 확장** — `useApprovalDeadline`을 `user-input-ui.tsx`/`clarifying-question-ui.tsx`에도 적용 (현재 approval-card만).
4. **deepagents 0.4.12 → 0.5.3 업그레이드** — LambChat 비교 시 발견. breaking change 확인 후 적용.
5. **M-MCP2** — System/User 컬렉션 분리, Fernet+PBKDF2, Redis hybrid + Pub/Sub, Lua quota.
6. **M-SKILL2** — Marketplace, sandbox CompositeBackend, requirements 자동 설치, 버전 히스토리.
7. **Sprint 2** — 5 page.tsx → PageShell + raw color 58회 토큰화 + i18n 한/영.
8. **Sprint 3** — `agents/[agentId]/settings/page.tsx` (518줄) → React Hook Form + Zod.
9. **Sprint 4** — bundle splitting + Suspense + key={i}.

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
- main 직접 커밋 금지 — feature/fix/chore 브랜치 + PR
- 라이선스: MIT (Copyright 2026 Moldy contributors)

## 검증 명령

```bash
cd backend && uv run ruff check . && uv run pytest tests/ --tb=short
cd frontend && pnpm lint && pnpm build
```

## 핵심 파일

- 디자인 토큰: `frontend/src/app/globals.css`, `lib/design-tokens.ts`
- DialogShell: `components/shared/dialog-shell.tsx` (`srOnly` prop) / FormFooter: `components/shared/form-footer.tsx`
- 날짜 유틸: `lib/utils/format-relative-time.ts` (`formatLongDate`/`formatMediumDate`/`formatRelativeShort` — KST)
- M-CHAT2 Share: `backend/app/{models/share_link.py, schemas/share.py, services/share_service.py, routers/shares.py}` + `frontend/src/{lib/api/shares.ts, lib/hooks/use-share.ts, components/chat/share-dialog.tsx, app/shared/[shareId]/page.tsx}` + `AppLayout` BARE_ROUTE_PREFIXES
- ChatRightRail: `lib/stores/chat-right-rail.ts`, `components/chat/right-rail/*`
- LangGraph fork: `backend/app/services/thread_branch_service.py`, `agent_runtime/{executor,streaming}.py`
- BranchPicker: `components/chat/assistant-thread.tsx`, `lib/chat/{convert-message,use-chat-runtime}.ts`
- HITL countdown: `lib/hooks/use-approval-deadline.ts`, `components/chat/tool-ui/approval-card.tsx`
- 메타 docs: `README.md` / `README_EN.md` / `CONTRIBUTING.md` / `SECURITY.md` / `.dockerignore`
- ADR: `docs/design-docs/ADR-010-ui-tokens-and-dialog-shell.md`
- Mascot: `docs/images/moldy-mascot.webp`
- Audit/팀 메모리: `AUDIT.log`, `progress.txt`, `tasks/archive/` (옛 milestone 자료)
