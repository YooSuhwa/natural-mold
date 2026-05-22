# HANDOFF — Marketplace Resources Phase 1

**세션**: 2026-05-18 ~ 2026-05-22
**브랜치**: `worktree-marketplace-resources` (worktree)
**PR**: https://github.com/YooSuhwa/natural-mold/pull/162 (15 commits, latest `1d62d9c`)
**소스**: `docs/marketplace-resources-{prd,spec}.md`, `docs/design-docs/adr-017-marketplace-resources.md`

## 완료

- **Phase 1 출시 게이트 8개 PASS** (spec-verify 검증 완료) → Full GO
- backend pytest **1191 passed, 0 회귀** / ruff 0 / alembic m40~m43 reversible
- frontend pnpm lint 0 + build PASS (24 routes)
- 86 k-skill 카탈로그 등록 + first-wave sync 완료
- `/simplify` 적용 — `runMutation` helper / `_apply_visibility_change` 추출

## 진행 중

없음 — Phase 1 closure 상태, 머지 대기.

## 다음 (우선순위)

1. **PR #162 머지** → main checkout 에서 `git pull && cd backend && uv run alembic upgrade head`
2. **사용자 UI smoke test** (backend test 로는 보장됨):
   - PublishWizard 5-step (PRD §10.3): secret_scan 차단 / restricted ACL / public+approve
   - Agent 에 marketplace skill 연결 후 채팅 (PRD §10.1, §10.2)
3. **Phase 1 spec 한계 (별도 PR)**: ACL **user picker**, publish 전 **secret_scan preview**
4. **Polish**: 모바일/태블릿 적응형 (FilterBar Sheet, Wizard LineTabs)
5. **Phase 2**: MCP marketplace (mcp_servers publish/install)

## 주의사항

- **worktree** 진입 후 `bash scripts/worktree-setup.sh` 1회 — main `.env` symlink + 가이드
- **uvicorn** `--reload-dir app` 권장 — `data/` watch 가 publish/install 시 reload trigger
- **`is_listed`** 토글은 super_user 전용 endpoint — owner 의 public publish 는 카탈로그 미노출 (moderation approve 필요)
- **`(owner, slug)` 충돌** 은 stale item row 재사용 (commit `4be837f`) — `publication_link` CASCADE 후 marketplace_items 가 남는 케이스
- **Disable item** 은 영구 차단 아님 → Re-enable / Unpublish 별도 액션 (owner actions 카드)

## 핵심 파일

- Backend: `backend/app/marketplace/` (10 service 모듈), `models/marketplace.py`, `routers/{marketplace,skills}.py`, `alembic/versions/m40~m43_*.py`, `scripts/sync_k_skill.py`
- Frontend: `frontend/src/app/marketplace/**`, `components/marketplace/**`, `lib/{api,hooks,types}/marketplace.ts`
- 디자인: `docs/design-docs/{adr-017,marketplace-module-contracts,marketplace-ui-spec}.md`
- 테스트: `backend/tests/test_marketplace_*.py` + `test_{runtime_isolation,credential_injection,redaction,secret_scan,k_skill_importer}.py`

## Course corrections (commit history 요약)

OI-1 13 credential count / OI-4 secret_scan `\bsk-` boundary / OI-5 `origin_kind` strict-xfail
flow / M2.5 catalog default filter / OPEN-1 install_service selectinload / 본인 publish
Manage CTA / orphan owner Install fallback / importer instruction-only → ready_python /
(owner, slug) reuse / admin listing approval endpoint / Installed 탭 SQL EXISTS / Owner
visibility dropdown + Unpublish + Re-enable + ACL revoke / worktree-setup.sh.

자세한 내용은 PR #162 commit history + `tasks/lessons.md` Session 7.
