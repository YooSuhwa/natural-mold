# HANDOFF — Marketplace Resources Phase 1 (closure 후)

**세션**: 2026-05-18 ~ 2026-05-22
**브랜치**: `main` (Phase 1 작업물 머지 완료)
**소스**: `docs/marketplace-resources-{prd,spec}.md`, `docs/design-docs/adr-017-marketplace-resources.md`
**머지된 PR**: https://github.com/YooSuhwa/natural-mold/pull/162 (16 commits)

## 완료

- **Phase 1 출시 게이트 8개 PASS** → Full GO (`/spec-verify` 확인)
- backend pytest **1191 passed, 0 회귀** / ruff 0 / alembic m40~m43 reversible
- frontend pnpm lint 0 / build PASS (24 routes)
- 86 k-skill 카탈로그 등록 + first-wave sync 완료
- `/simplify` 적용 — `runMutation` helper + `_apply_visibility_change` 추출
- main 머지 + sync 완료 (latest `f9c165c [docs] CLAUDE.md`)

## 진행 중

없음. 다만 worktree 디렉토리 잔존 — dev server 잠금으로 자동 삭제 실패:

```bash
# 사용자 액션: worktree 안 backend/frontend dev server (Ctrl+C) → 디렉토리 정리
cd /Users/chester/dev/ref/natural-mold
rm -rf .claude/worktrees/marketplace-resources
git branch -D worktree-marketplace-resources
```

## 다음 (우선순위)

1. **dev server를 main 디렉토리에서 재시작** — `cd backend && uv run uvicorn app.main:app --reload --port 8001 --reload-dir app` / `cd frontend && pnpm dev`
2. **사용자 UI smoke test** (backend test 로는 보장됨):
   - PublishWizard 5-step (PRD §10.3): secret_scan / restricted ACL / public+approve
   - Agent 에 marketplace skill 연결 후 채팅 (PRD §10.1, §10.2)
3. **Phase 1 spec 한계 (별도 PR)**: ACL **user picker** (현재 UUID 직접 입력), publish 전 **secret_scan preview**
4. **Polish (별도 PR)**: 모바일/태블릿 적응형 (FilterBar Sheet, Wizard LineTabs)
5. **Phase 2**: MCP marketplace (mcp_servers publish/install)

## 주의사항

- **새 worktree 생성** 시 `bash scripts/worktree-setup.sh` 1회 — main `.env` symlink + 가이드. CLAUDE.md "git worktree 에서 작업 시" 참조
- **uvicorn** 띄울 때 `--reload-dir app` — `data/` watch 가 publish/install 시 reload trigger 함 (사용자 세션 끊어짐)
- **`is_listed` 토글**은 super_user 전용 endpoint — owner 의 public publish 는 카탈로그 미노출 (`/marketplace/admin/moderation` 에서 Approve listing)
- **`(owner, slug)` 충돌**: 사용자가 publish→삭제→재publish 시 stale marketplace_items 재사용 (publish_service 자동 처리)
- **Disable 영구 차단 아님** → Re-enable / Unpublish 액션 분리 (owner actions 카드)
- **본인 publish item Install 시도** — orphan publication owner는 Install fallback, publication_link 살아있는 owner는 Manage CTA

## 핵심 파일

- Backend: `backend/app/marketplace/` (10 service 모듈), `models/marketplace.py`, `routers/{marketplace,skills}.py`, `alembic/versions/m40~m43_*.py`, `scripts/sync_k_skill.py`
- Frontend: `frontend/src/app/marketplace/**`, `components/marketplace/**`, `lib/{api,hooks,types}/marketplace.ts`
- 디자인: `docs/design-docs/{adr-017,marketplace-module-contracts,marketplace-ui-spec}.md`
- 테스트: `backend/tests/test_marketplace_*.py` + `test_{runtime_isolation,credential_injection,redaction,secret_scan,k_skill_importer}.py`
- 운영 자동화: `scripts/worktree-setup.sh` (.env symlink + uvicorn 가이드)

## Course corrections (16 commit history 요약)

OI-1 13 credential count / OI-4 secret_scan `\bsk-` boundary / OI-5 `origin_kind` strict-xfail
flow / M2.5 catalog default filter / OPEN-1 install_service selectinload / 본인 publish
Manage CTA + orphan owner Install fallback / importer instruction-only → ready_python /
(owner, slug) reuse / admin listing approval endpoint / Installed 탭 SQL EXISTS / Owner
visibility dropdown + Unpublish + Re-enable + ACL revoke / worktree-setup.sh / `/simplify` /
CLAUDE.md ADR-016 반영.

PR #162 commit history + `tasks/lessons.md` Session 7 참조.
