# ADR-018 — Relative `storage_path` for Skills & Marketplace Versions

## 1. Status & Date

- **Status**: Proposed
- **Date**: 2026-05-23
- **Owner**: chester
- **Branch**: `worktree-adr-018-relative-storage-path`
- **Relates / Depends on**:
  - ADR-003 (Skills Memory) — 본 ADR이 도입한 `data/skills/<id>/` 레이아웃 그대로 유지
  - ADR-017 (Marketplace Resources) — `marketplace_versions.storage_path` 도입 ADR. 본 ADR은 그 컬럼 의미만 변경
- **Supersedes**: 없음

---

## 2. Context

### 2.1 사건 — 2026-05-23 데이터 손실

`worktree-marketplace-resources` 작업 종료 후 worktree 디렉토리를 정리하자 다음이 발생:

- `skills` 테이블 — ranian963 사용자의 2개 row (`seating-guide`, `korean-spell-check`) 본문 파일 lost
- `marketplace_versions` 테이블 — 93개 row 전체 본문 파일 lost
- DB row의 metadata(name/description/size/hash)는 남아있지만 `storage_path`가 가리키는 파일/디렉토리는 worktree와 함께 삭제됨

조사 결과:

```sql
SELECT storage_path FROM skills WHERE user_id='ranian963';
-- /Users/chester/dev/ref/natural-mold/.claude/worktrees/marketplace-resources/backend/data/skills/<id>
```

DB에 **worktree 절대경로**가 박혀있었다.

### 2.2 근본 원인 — 두 가지 mismatch

**원인 1 — `_storage_root()`가 CWD 기준 절대경로로 resolve** (`app/skills/service.py:46-51`):

```python
def _storage_root() -> Path:
    return Path(settings.skill_storage_dir).resolve()  # CWD 의존
```

`settings.skill_storage_dir = "./data/skills"`는 상대경로지만, `.resolve()`가 CWD(=서버를 띄운 디렉토리)를 기준으로 절대경로화. worktree에서 dev server를 띄우면 worktree 경로가 박힘.

같은 패턴: `publish_service._versions_storage_root()`, `k_skill_importer` 의 `builtin_storage_dir`.

**원인 2 — `scripts/worktree-setup.sh`가 `.env`만 symlink, `data/`는 분리**:

`.env`는 main으로 symlink되어 같은 PostgreSQL을 공유하지만 `backend/data/`는 worktree마다 별도 디렉토리. 결과적으로 **DB는 공유, storage는 분리** — worktree에서 만든 skill의 본문은 worktree 안에만 존재하면서 DB에는 worktree 절대경로가 저장됨. worktree가 정리되면 dangling reference만 남음.

### 2.3 영향 범위

| 대상 | 깨진 row 수 | 정상 row 수 |
|---|---|---|
| skills | 2 | 0 |
| marketplace_versions | 93 | 0 |

100% 깨짐. 즉 이 프로젝트의 모든 publish/install/k-skill sync 트랜잭션이 worktree에서 일어났고 main 컬렉션은 비어있었다.

### 2.4 왜 단순히 worktree-setup.sh에 `data/` symlink만 추가하면 안 되는가

filesystem symlink만 추가하면 worktree 안 `backend/data` → main으로 link되어 파일은 공유되지만, DB의 `storage_path`는 여전히 **worktree 절대경로**로 박힘 (`.claude/worktrees/.../backend/data/skills/<id>`). worktree 자체를 삭제하면 그 절대경로도 더 이상 traverse 불가 (symlink 자체가 없어짐).

→ **두 레이어 모두 fix 필요**:
1. DB 차원 — `storage_path`를 상대 경로로 저장 (어디서 띄우든 동일하게 해석)
2. Filesystem 차원 — worktree-setup.sh에 `backend/data` symlink 가이드 (publish/install 시 main data에 쓰이도록)

---

## 3. Decision

### 3.1 `storage_path`는 항상 `settings.data_root` 기준 상대경로로 저장

- `settings.data_root: str = "./data"` 신설 (기존 `*_dir` 설정과 충돌 없음, derived 개념)
- 모든 저장 사이트가 컬럼 값으로 **`./data` 하위의 상대경로**만 저장
- 절대경로 저장 금지. CI/테스트에서 `is_absolute()` 단언으로 회귀 가드

**상대경로 schema**:

| 컬럼 | 패턴 | 예시 |
|---|---|---|
| `skills.storage_path` (text) | `skills/<skill_id>/SKILL.md` | `skills/abc.../SKILL.md` |
| `skills.storage_path` (package) | `skills/<skill_id>` | `skills/abc...` |
| `marketplace_versions.storage_path` (k-skill) | `marketplace/k-skill/<version_id>` | `marketplace/k-skill/def...` |
| `marketplace_versions.storage_path` (publish) | `skills/_marketplace_versions/<version_id>` | `skills/_marketplace_versions/def...` |

기존 `data/skills/<id>` 레이아웃과 `data/marketplace/k-skill/<vid>` 레이아웃은 그대로 유지. 변경은 **컬럼 값**만.

### 3.2 단일 helper `resolve_data_path(rel) -> Path`

`app/storage/paths.py` 신설:

```python
def resolve_data_path(rel: str | os.PathLike[str]) -> Path:
    """Return an absolute path for a value stored relative to ``settings.data_root``.

    - Empty/None → ValueError
    - Absolute input → returned as-is (legacy fallback; logged as deprecation)
    - Relative input → ``(settings.data_root / rel).resolve()``
    """
```

모든 읽기 사이트는 이 helper로 wrap. `Path(skill.storage_path)` 직접 사용 금지.

### 3.3 Alembic M44 — clean slate

사용자 동의(2026-05-23) 하에:

- `DELETE FROM marketplace_installations`
- `DELETE FROM marketplace_publication_links`
- `DELETE FROM marketplace_item_acl`
- `DELETE FROM marketplace_versions`
- `DELETE FROM marketplace_items`
- `DELETE FROM skill_credential_bindings`
- `DELETE FROM agent_skills WHERE skill_id IN (...broken skills...)`
- `DELETE FROM skills WHERE storage_path LIKE '%/worktrees/%' OR storage_path LIKE '/%'`
  (즉 worktree 경로 OR 모든 절대경로 row)

컬럼 코멘트도 갱신해서 미래 reader에게 schema 의미 전달.

Downgrade는 noop (데이터 복원 불가, schema 변경도 없음).

### 3.4 `scripts/worktree-setup.sh` — data symlink 추가

`.env` symlink와 동일 패턴으로 `backend/data` → main `backend/data` symlink. ADR-018 적용 후 storage_path는 상대지만 **filesystem 차원에서도** worktree와 main이 같은 데이터를 보게 해서 다음을 보장:

- worktree에서 publish한 skill을 main에서 즉시 read 가능
- worktree 삭제해도 main data는 그대로

---

## 4. Consequences

### 4.1 Positive

- worktree에서 띄운 서버가 만든 publish/install/k-skill sync 결과가 worktree 삭제 후에도 살아남음
- `storage_path` 가 deploy-portable — DB dump를 다른 환경에 import해도 그대로 동작 (단 `data_root` 만 맞추면 됨)
- 단일 helper로 read 사이트가 한곳에서 보호됨 (path traversal 등 추가 검증 추가 시 단일 진입점)

### 4.2 Negative

- 기존 절대경로 row와의 backward compat: helper에서 `is_absolute()` 통과시키지만 **신규 저장은 금지**. legacy 절대경로 row는 M44에서 삭제했으므로 production 코드 path에서는 보지 않음
- 사용자가 main이 아닌 환경에서 띄우거나 `data_root`를 잘못 설정하면 모든 skill이 broken으로 보임 — 이건 의도된 동작 (single source of truth)

### 4.3 마이그레이션 영향

- ranian963 의 2개 skill: M44가 row 삭제. 사용자가 직접 재install/재import
- 91개 k-skill marketplace_versions: M44가 row 삭제. super_user가 `sync_k_skill` 재실행
- 2개 user-publish marketplace_versions: M44가 row 삭제. 원본 skill 보유자가 재publish

---

## 5. Alternatives Considered

### 5.1 worktree-setup.sh data symlink만 (DB schema 무변경)

탈락. §2.4 설명 — DB storage_path가 worktree 절대경로로 박히는 문제 미해결.

### 5.2 storage_path에 canonical path (`os.path.realpath`) 저장

`_storage_root()` 에서 `.resolve(strict=False)` 후 `os.path.realpath`로 symlink follow → 결과 path가 main을 가리키게. 탈락 이유:

- worktree에 `data` symlink가 설정되어 있을 때만 동작. setup 안 한 worktree에서는 여전히 worktree 경로 박힘
- DB에 환경별 절대경로 저장 — deploy/clone 시 portability 없음

### 5.3 컬럼 폐기 후 `<id>` 만으로 경로 재구성

`storage_path` 컬럼 자체를 제거하고 `data/skills/<skill_id>/` 규약만 사용. 탈락 이유:

- marketplace publish snapshot은 `data/skills/_marketplace_versions/<vid>` 위치라 skill_id로는 재구성 불가
- k-skill builtin은 `data/marketplace/k-skill/<vid>` 위치 — 더 다양함
- 명시적 path 컬럼이 future Phase 2/3 (MCP/Agent marketplace) 확장 시 유연성 제공

---

## 6. Implementation Plan

1. ADR 작성 (본 문서)
2. `settings.data_root = "./data"` 추가
3. `app/storage/paths.py` — `resolve_data_path()` + 단언 헬퍼
4. 저장 사이트 변경:
   - `app/skills/service.py:118, 160` (text/package skill create)
   - `app/marketplace/publish_service.py:398` (publish snapshot)
   - `app/marketplace/install_service.py:363-365` (install copy)
   - `app/marketplace/k_skill_importer.py:571` (k-skill version)
5. 읽기 사이트 변경 — `Path(skill.storage_path)` / `Path(version.storage_path)` 모두 `resolve_data_path()` 경유
6. Alembic M44 — 깨진 row wipe + 컬럼 코멘트
7. 회귀 테스트:
   - `tests/test_storage_paths.py` — helper unit
   - `tests/test_skill_service_paths.py` — text/package create 후 컬럼 값이 상대인지 단언
   - `tests/test_marketplace_paths.py` — publish/install 후 동일 단언
8. `scripts/worktree-setup.sh` — `backend/data` symlink 추가
9. `CLAUDE.md` / `HANDOFF.md` 업데이트
10. ruff + pytest 전체 통과 검증 후 PR

---

## 7. Rollback

- M44는 데이터 삭제만 — schema 변경 없음. Downgrade가 noop이므로 rollback 어려움 (삭제된 row 복원 불가)
- 코드 변경은 helper 한 곳에서 absolute path도 허용하므로 partial rollback 가능 (M44만 안 돌리면 기존 절대경로 row도 그대로 읽힘)
- PR 단위 revert 가능
