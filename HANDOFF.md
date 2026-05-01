# 작업 인계 — main (PR #81 merged)

> 새 세션 시작 시: 본 파일 + `progress.txt` 마지막 4-5 섹션만 읽으면 충분.
> 자세한 결정/oklch/스펙은 `docs/design-docs/ADR-010-ui-tokens-and-dialog-shell.md`.

## 마지막 상태

- 브랜치: `main` (uncommitted: M-UI1b + /simplify 픽스 17 파일)
- 마지막 커밋: `7cb42cd Merge pull request #81 ...`
- alembic head: **m29** (사용자 환경에선 `cd backend && uv run alembic upgrade head` 필수)
- backend: ruff clean, **pytest 701 passed**
- frontend: tsc clean, lint clean, build PASS (16 라우트)
- stash: `stash@{0} model_catalog autoupdate before sync` (보통 무시 OK)
- dev server: backend 8002 (`/tmp/moldy-backend.log`), frontend 3000 (`/tmp/moldy-frontend.log`, `.next` 방금 클리어 후 재시작)

## 머지된 마일스톤

- [x] **M-UI1 + M-UI1 핫픽스** — DialogShell + 디자인 토큰(emerald primary) + Sheet→Dialog 4종 + 시각 일관성 fix
- [x] **M-MCP1c/M-SKILL1c** — stdio + import/export + health polling + Skill file-level CRUD + frontmatter sync + multi-file editor + MCP wizard 재설계 (alembic m26)
- [x] **M-CHAT1** (9/11 항목) — Right Rail + 메시지 액션·검색·HITL countdown·Mermaid·파일첨부·skill row·tool result·outline (alembic m27/m28)
- [x] **M-CHAT1b + 핫픽스 3건** — LangGraph fork + BranchPicker `<N/M>` (alembic m29)
- [x] 시각 회귀 fix (text-primary→strong, slider use-client, SelectValue UUID, agent image 204, usage summary daily_spend, sidebar last_used_at)
- [x] **/simplify P0+P1** — `_sse_handler` factory, `_run_agent_stream`, `_runStream`, thread tree O(L×D×M)→O(L×D)+O(L×M)
- [x] **M-UI1b** — 잔여 16개 DialogContent → DialogShell 마이그레이션 완료. `grep DialogContent` 0건. AlertDialog 별도 primitive로 제외.
- [x] **M-UI1b /simplify 픽스** — `DialogShell.Header`에 `srOnly` prop 추가 (markdown-content lightbox + agent-node 인라인 헤더), model-dialog redundant wrapper 제거 + `DialogClose` 단순화, agent-node prettier 정리, model-add inline footer pt-2 제거, FormFooter 재사용 3곳 (credential-create / model-edit / tool-create).

## 다음 작업 후보 (우선순위 순)

1. **#36 M-CHAT2** — Share Dialog + read-only `app/shared/[shareId]/page.tsx` (LambChat 패턴, share_links 테이블)
2. **HITL countdown 확장** — `useApprovalDeadline`을 `user-input-ui.tsx`/`clarifying-question-ui.tsx`에도 적용 (현재 approval-card만)
3. **P0-3 Composer Model 토글** — 사용자 결정으로 이전 PR 보류, 재검토 시 진행
4. **M-MCP2** — System/User 컬렉션 분리, Fernet+PBKDF2 암호화, Redis hybrid cache + Pub/Sub, Lua quota
5. **M-SKILL2** — Marketplace, sandbox CompositeBackend, requirements 자동 설치, 바이너리 S3 분류, 버전 히스토리
6. **Sprint 2** — 5 page.tsx → PageShell + raw color 58회 토큰화 + i18n 한/영 라벨
7. **Sprint 3** — `agents/[agentId]/settings/page.tsx` (518줄, useState 21개) → React Hook Form + Zod
8. **Sprint 4** — bundle splitting (xyflow / syntax-highlighter `next/dynamic`) + Suspense + key={i}

## 주의사항 (코드 컨벤션)

- **Sheet 사용 금지** — 모바일 사이드바 + 대화 목록 2곳만 예외. 우측 패널은 ChatRightRail (`lib/stores/chat-right-rail.ts` jotai atom + inline split layout)
- **`text-primary`는 옅은 emerald (surface)** — 강조 의도면 `text-primary-strong` 사용
- **`bg-primary-strong text-white` 흰 버튼 보일 때** → `pnpm dev` 재시작 + `rm -rf .next` (Turbopack 캐시 stale, 본 세션에서도 발생). 코드 변경 후 dev 안에서 발생하면 거의 항상 이거.
- **Edit/Regenerate는 LangGraph time-travel** — `astream(None, config={checkpoint_id})` 패턴. user content를 또 보내지 마라 (B5 회귀 위험)
- **assistant-ui ThreadPrimitive.Messages는 가상화 미지원** — Virtuoso 교체는 별도 마일스톤
- **alembic 새 마이그레이션 시** 사용자에게 `uv run alembic upgrade head` 안내 필수
- **`page-header.tsx`는 9개 페이지에서 사용 중** — 삭제 금지
- 새 다이얼로그 신설 시 직접 `DialogContent` 쓰지 말고 `DialogShell` + 토큰(`DIALOG_SIZE`/`DIALOG_HEIGHT`)
- 헤더 chrome 없는 다이얼로그(lightbox / 커스텀 인라인 헤더)는 `<DialogShell.Header srOnly title="..." />` 사용 — a11y DialogTitle 유지하면서 시각 영역 0
- 폼 다이얼로그 footer는 `FormFooter` 재사용 (`onCancel`, `onSubmit`, `pending`, `extraActions`) — Cancel+Save 직접 작성 금지
- `frontend/AGENTS.md`에 Tailwind cn 함정 / react-19 setState / DialogShell 사용 가이드 정리됨

## 검증 명령

```bash
cd backend && uv run ruff check . && uv run pytest tests/ --tb=short
cd frontend && pnpm lint && pnpm build
```

## 핵심 파일

- 디자인 토큰: `frontend/src/app/globals.css`, `frontend/src/lib/design-tokens.ts`
- DialogShell: `frontend/src/components/shared/dialog-shell.tsx` (이제 `srOnly` prop 지원)
- FormFooter: `frontend/src/components/shared/form-footer.tsx`
- ChatRightRail: `frontend/src/lib/stores/chat-right-rail.ts`, `frontend/src/components/chat/right-rail/*`
- LangGraph fork: `backend/app/services/thread_branch_service.py`, `backend/app/agent_runtime/{executor,streaming}.py`
- BranchPicker wire: `frontend/src/components/chat/assistant-thread.tsx`, `frontend/src/lib/chat/{convert-message,use-chat-runtime}.ts`
- HITL countdown: `frontend/src/lib/hooks/use-approval-deadline.ts`, `frontend/src/components/chat/tool-ui/approval-card.tsx`
- ADR: `docs/design-docs/ADR-010-ui-tokens-and-dialog-shell.md`
- Bezos sheet 분석: `tasks/sheet-deletion-analysis.md`
- Plan: `~/.claude/plans/buzzing-prancing-cloud.md`
- Audit/팀 메모리: `AUDIT.log`, `progress.txt`
