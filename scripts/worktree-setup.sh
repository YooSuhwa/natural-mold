#!/usr/bin/env bash
# worktree-setup.sh — git worktree 진입 후 1회 실행.
#
# 매번 .env 를 따로 만들지 않도록 main checkout 의 backend/.env 를 symlink
# 로 연결한다. main 의 .env 가 ground truth — JWT_SECRET / ENCRYPTION_KEYS
# / DATABASE_URL 이 같아야 세션 공유 + 기존 credential 복호화가 정상이다.
#
# 사용 예 (worktree 안에서):
#   bash scripts/worktree-setup.sh
#
# 멱등 — 여러 번 실행해도 안전.

set -euo pipefail

# 현재 cwd 가 git worktree 인지 확인
toplevel=$(git rev-parse --show-toplevel 2>/dev/null || true)
if [[ -z "$toplevel" ]]; then
  echo "✗ git repository 가 아닙니다. cwd 를 worktree 안으로 옮겨서 다시 실행하세요." >&2
  exit 1
fi

# main checkout 의 경로 — git worktree list 에서 첫 번째 (= bare/main) 항목.
# 일반적으로 prunable 가 아닌 첫 항목이 main.
main_path=$(git worktree list --porcelain | awk '/^worktree /{print $2; exit}')
if [[ -z "$main_path" ]]; then
  echo "✗ 'git worktree list' 결과가 비어있습니다." >&2
  exit 1
fi

if [[ "$main_path" == "$toplevel" ]]; then
  echo "ℹ main checkout 입니다 — symlink 셋업이 필요 없습니다."
  exit 0
fi

main_env="$main_path/backend/.env"
worktree_env="$toplevel/backend/.env"

if [[ ! -f "$main_env" ]]; then
  echo "✗ main 의 backend/.env 가 없습니다: $main_env" >&2
  echo "  먼저 main 에서 'cp backend/.env.example backend/.env' + JWT_SECRET / ENCRYPTION_KEYS 채워주세요." >&2
  exit 1
fi

# 기존 .env 가 일반 파일이면 — 사용자가 직접 만든 worktree-local 파일.
# 충돌을 피하려 백업 후 symlink 로 교체.
if [[ -f "$worktree_env" && ! -L "$worktree_env" ]]; then
  backup="$worktree_env.bak-$(date +%s)"
  echo "⚠ 기존 backend/.env 는 일반 파일입니다 — $backup 으로 백업 후 symlink 로 교체"
  mv "$worktree_env" "$backup"
fi

# Relative symlink — main checkout 이 이동하지 않는 한 안전. worktree 가
# repository 어디에 있든 정확히 main backend/.env 를 가리킨다.
mkdir -p "$(dirname "$worktree_env")"
rel_target=$(python3 -c "import os.path; print(os.path.relpath('$main_env', start='$(dirname "$worktree_env")'))")
ln -sf "$rel_target" "$worktree_env"

# 결과 검증
if [[ ! -f "$worktree_env" ]]; then
  echo "✗ symlink 가 resolve 되지 않습니다: $(readlink "$worktree_env")" >&2
  exit 1
fi

echo "✓ backend/.env → $(readlink "$worktree_env")"
echo "  resolved → $(python3 -c "import os; print(os.path.realpath('$worktree_env'))")"

# --- ADR-018 — backend/data symlink ---------------------------------------
# DB는 main과 공유되는데 backend/data/ 가 worktree마다 별도면 publish/install
# 시 본문 파일이 worktree 안에만 생성된다. worktree를 정리하면 DB에는 row만
# 남고 파일이 사라지는 사고가 2026-05-23에 발생. 같은 .env symlink 패턴을
# data/ 에도 적용해 main backend/data 를 직접 가리키게 한다.
main_data="$main_path/backend/data"
worktree_data="$toplevel/backend/data"

mkdir -p "$main_data"

# worktree에 일반 디렉토리가 이미 있으면 비어있을 때만 자동 제거 후 symlink.
# 안에 데이터가 있으면 — 사용자가 worktree 안에서 publish/install 한 결과 —
# 자동 제거 위험하니 수동 조치 안내만 출력.
if [[ -d "$worktree_data" && ! -L "$worktree_data" ]]; then
  if [[ -z "$(ls -A "$worktree_data" 2>/dev/null)" ]]; then
    rmdir "$worktree_data"
  else
    backup="$worktree_data.bak-$(date +%s)"
    echo
    echo "⚠ 기존 backend/data/ 는 일반 디렉토리이고 내용물이 있습니다."
    echo "  worktree 안 데이터가 main 과 분리되어 storage_path 가 깨질 수 있습니다."
    echo "  수동 조치:"
    echo "    1) worktree 안에서 띄운 dev server 가 있으면 종료"
    echo "    2) mv '$worktree_data' '$backup'   # 백업"
    echo "    3) bash scripts/worktree-setup.sh   # 재실행"
    echo
  fi
fi

if [[ ! -e "$worktree_data" ]]; then
  rel_data_target=$(python3 -c "import os.path; print(os.path.relpath('$main_data', start='$(dirname "$worktree_data")'))")
  ln -sf "$rel_data_target" "$worktree_data"
  echo "✓ backend/data → $(readlink "$worktree_data")"
elif [[ -L "$worktree_data" ]]; then
  echo "✓ backend/data → $(readlink "$worktree_data") (이미 symlink)"
fi

echo
echo "다음 가이드:"
echo "  1) backend 실행: cd backend && uv run uvicorn app.main:app --reload --port 8001 --reload-dir app"
echo "     (--reload-dir app — publish/install 시 data/ 변경이 reload 트리거하는 것 방지)"
echo "  2) frontend 실행: cd frontend && pnpm dev"
echo
echo "참고: ADR-018 — storage_path 는 settings.data_root 기준 상대경로로 저장됩니다."
echo "       backend/data symlink + 상대경로 컬럼의 이중 방어로 worktree 정리 후에도"
echo "       main 데이터가 보존됩니다."
