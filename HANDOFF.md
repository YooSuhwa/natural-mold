# 작업 인계 — feature/dialog-shell-and-mcp-skill-base

> 새 세션 시작 시: 본 파일 + `progress.txt` 마지막 4-5 섹션만 읽으면 충분.
> 자세한 결정/oklch/스펙은 `docs/design-docs/ADR-010-ui-tokens-and-dialog-shell.md`.

## 마지막 상태

- 브랜치: `feature/dialog-shell-and-mcp-skill-base`
- 마지막 커밋: `7f35667` (M-MCP1c/M-SKILL1c)
- working tree: M-CHAT1 + M-CHAT1b + 핫픽스 3건 + 시각/회귀 fix들 (uncommitted, 약 50 파일)
- backend: ruff clean, **pytest 701 passed** (회귀 0)
- frontend: tsc clean, lint clean, build PASS (16 라우트)
- alembic head: m29 (m26 health/system → m27 feedback → m28 attachments → m29 active_branch)
- dev server: backend 8002 (`/tmp/moldy-backend.log`), frontend 3000

## 완료된 마일스톤

- [x] **M-UI1** — DialogShell + 디자인 토큰(emerald primary) + Sheet→Dialog 4종
- [x] **M-MCP1c/M-SKILL1c** — stdio + import/export + health polling + Skill file-level CRUD + frontmatter sync + multi-file editor + MCP wizard 재설계
- [x] **M-CHAT1** (9/11 항목) — Right Rail + 메시지 액션·검색·HITL countdown·Mermaid·파일첨부·skill row·tool result·outline. P0-3 user-cut, P2-8 risk-defer
- [x] **M-CHAT1b + 핫픽스 3건** — LangGraph fork + BranchPicker `<N/M>` 동작
- [x] 시각 회귀 fix (text-primary→strong, slider use-client, SelectValue UUID, agent image 204, usage summary daily_spend, sidebar last_used_at)

## 다음 PR 후보 (우선순위 순)

1. **현재 working tree 커밋** — model_catalog/* 5개 자동갱신물 제외 후 단일 커밋
2. **#10 M-UI1b** — 잔여 ~18-20개 DialogContent → DialogShell 마이그레이션 (DialogShell 인프라 검증 끝)
3. **#36 M-CHAT2** — Share Dialog + read-only `app/shared/[shareId]/page.tsx` (LambChat 패턴)
4. **HITL countdown** user-input-ui / clarifying-question-ui에도 동일 패턴 적용 (현재 approval-card만)
5. **M-MCP2** — System/User 컬렉션 분리, Fernet+PBKDF2 암호화, Redis hybrid cache, Lua quota
6. **M-SKILL2** — Marketplace, sandbox CompositeBackend, requirements 자동 설치, 바이너리 S3 분류
7. **Sprint 2** — 5 page.tsx → PageShell + raw color 58회 토큰화 + i18n 한/영 라벨 정리
8. **Sprint 3** — agents settings page (518줄, useState 21개) → React Hook Form + Zod
9. **Sprint 4** — bundle splitting (xyflow / syntax-highlighter `next/dynamic`) + Suspense + key={i} 안티패턴

## 주의사항

- **Sheet 사용 금지** — 모바일 사이드바 + 대화 목록 2곳만 예외. 우측 패널은 ChatRightRail (`lib/stores/chat-right-rail.ts` jotai atom + inline split layout)
- **`text-primary`는 옅은 emerald (surface)** — 강조 의도면 `text-primary-strong` 사용
- **`bg-primary-strong text-white`가 dev server 캐시로 흰 버튼 보일 때** → `pnpm dev` 재시작 + `.next` 클리어
- **Edit/Regenerate는 LangGraph time-travel** — `astream(None, config={checkpoint_id})` 패턴. user content를 또 보내지 마라 (B5 회귀 위험)
- **assistant-ui ThreadPrimitive.Messages는 가상화 미지원** — Virtuoso 교체는 별도 마일스톤
- **alembic 새 마이그레이션 추가 시** 사용자에게 `uv run alembic upgrade head` 안내 필수 (M-CHAT1 시작 시 미적용으로 500 회귀 발생함)
- **`page-header.tsx`는 9개 페이지에서 사용 중** — 삭제 금지

## 검증 명령

```bash
cd backend && uv run ruff check . && uv run pytest tests/ --tb=short
cd frontend && pnpm lint && pnpm build
```

## 핵심 파일

- 디자인 토큰: `frontend/src/app/globals.css`, `frontend/src/lib/design-tokens.ts`
- DialogShell: `frontend/src/components/shared/dialog-shell.tsx`
- ChatRightRail: `frontend/src/lib/stores/chat-right-rail.ts`, `frontend/src/components/chat/right-rail/*`
- LangGraph fork: `backend/app/services/thread_branch_service.py`, `backend/app/agent_runtime/{executor,streaming}.py`
- BranchPicker wire: `frontend/src/components/chat/assistant-thread.tsx` (BranchPicker), `frontend/src/lib/chat/{convert-message,use-chat-runtime}.ts`
- Plan: `~/.claude/plans/buzzing-prancing-cloud.md`
- ADR: `docs/design-docs/ADR-010-ui-tokens-and-dialog-shell.md`
- Bezos sheet 분석: `tasks/sheet-deletion-analysis.md`
- Audit/팀 메모리: `AUDIT.log`, `progress.txt`
