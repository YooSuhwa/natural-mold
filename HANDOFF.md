# HANDOFF — ADR-018 Relative storage_path

**세션**: 2026-05-23 ~ 2026-05-26
**브랜치**: `worktree-adr-018-relative-storage-path` (worktree, 미머지)
**worktree 경로**: `.claude/worktrees/adr-018-relative-storage-path/`
**소스**: `docs/design-docs/adr-018-relative-storage-path.md`

## 사건 (재발 방지 대상)

2026-05-23: 이전 worktree 정리하면서 ranian963 skill 2개 + marketplace_versions 93개 **본문 lost**. 원인 — `storage_path`가 worktree 절대경로(`.../worktrees/marketplace-resources/backend/data/skills/...`)로 박혀있어 worktree 삭제 시 dangling.

## 완료

- ADR-018 작성 — 절대→상대 경로 + worktree-setup data symlink 이중방어
- `app/storage/paths.py` — `resolve_data_path`, `ensure_relative` helper
- 저장 사이트 5곳 모두 상대화 (skills/marketplace publish/install/k_skill_importer/runtime)
- `settings.data_root="./data"` 단일 source, `skill_storage_dir` 등은 deprecated
- Alembic **M44** — 깨진 row wipe (marketplace 5테이블 + worktree-prefixed skills)
- `scripts/worktree-setup.sh` — `backend/data` symlink 추가
- 회귀 테스트 2개 (`test_storage_paths.py`, `test_storage_path_relative_invariant.py`)
- `/simplify` 적용 — 미사용 helper 제거, 1회용 `_rel_*` 인라인
- 검증: ruff 통과 / pytest **1199 passed, 0 회귀**

## 진행 중

없음. 모든 코드 변경 완료.

## 다음 (우선순위)

1. **PR 생성 + 머지** — worktree에서 commit + push + PR. 머지 후 main `git pull`
2. **M44 적용** ⚡ destructive — main에서 `uv run alembic upgrade head` (사용자가 직접). 깨진 95개 row wipe
3. **k-skill sync 재실행** — `uv run python -m app.scripts.sync_k_skill --ref main`. 86개 marketplace_versions 복원
4. **ranian963 skill 재설치** — UI에서: korean-spell-check Install + seating-guide `.skill` 재import (원본 보유)
5. **재발 검증 시나리오** — 새 worktree에서 publish → worktree 정리 → main에서 본문 살아있는지
6. **CLAUDE.md 환경변수 표 정정** — `OPENAI_API_KEY` 등 `O (필수)` → `X (선택, UI 등록 가능)`. ADR-013/016 이후 outdated

## 주의사항

- **M44 = mass delete** — marketplace_items/versions/acl/installations/publication_links + worktree-prefixed skills 전부 wipe. 실행 전 백업 권장
- **worktree 작업 시** `bash scripts/worktree-setup.sh` 1회 — `.env` + `backend/data` symlink 둘 다 만들어줌 (ADR-018 핵심)
- **uvicorn `--reload-dir app`** 필수 — data/ watch가 publish/sync 시 reload 유발해 세션 끊김
- **`storage_path` 신규 저장은 항상 상대** — `ensure_relative()` guardrail + 회귀 테스트가 막아줌
- **legacy 절대경로 row** — helper가 fallback으로 통과시키지만 M44가 미리 wipe하므로 production에서는 안 보일 것

## 핵심 파일

- ADR: `docs/design-docs/adr-018-relative-storage-path.md`
- Helper: `backend/app/storage/paths.py` (53줄)
- 저장 사이트: `backend/app/skills/service.py:136,178`, `app/marketplace/{publish,install}_service.py`, `app/marketplace/k_skill_importer.py:572`
- Read 사이트: `app/skills/service.py` (8곳), `app/marketplace/skill_runtime.py:215`
- Alembic: `backend/alembic/versions/m44_relative_storage_path.py`
- 테스트: `backend/tests/test_storage_paths.py`, `test_storage_path_relative_invariant.py`
- Worktree fix: `scripts/worktree-setup.sh`

## Course corrections

ENV LLM key 필수성 재검증 → 코드는 default `""` 허용 + ADR-013 fallback이라 **선택**. CLAUDE.md만 outdated (#6에서 정정). simplify 단계에서 `relative_to_data_root` (미사용) + 3개 1회용 helper 인라인, paths.py docstring 단축.
