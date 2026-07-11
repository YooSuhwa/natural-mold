from __future__ import annotations

import io
import zipfile
from collections.abc import Collection, Sequence
from pathlib import Path, PurePosixPath

from app.config import settings
from app.schemas.skill_builder import SkillDraftFile
from app.skills.packager import PackageError
from app.skills.service import slugify

EXCLUDED_EXPORT_DIRS = frozenset({"evals"})


def normalize_draft_path(path: str) -> str:
    cleaned = path.strip().replace("\\", "/").lstrip("/")
    pure = PurePosixPath(cleaned)
    if not cleaned or ".." in pure.parts or "\x00" in cleaned:
        raise ValueError(f"invalid draft file path: {path!r}")
    return pure.as_posix()


def build_skill_zip_bytes(
    *,
    slug: str,
    files: Sequence[SkillDraftFile],
    include_evals: bool = False,
) -> bytes:
    folder = slugify(slug)
    by_path: dict[str, SkillDraftFile] = {}
    for draft_file in files:
        rel_path = normalize_draft_path(draft_file.path)
        top_level = rel_path.split("/", 1)[0]
        if not include_evals and top_level in EXCLUDED_EXPORT_DIRS:
            continue
        by_path[rel_path] = draft_file
    if "SKILL.md" not in by_path:
        raise ValueError("draft package must include SKILL.md")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for rel_path in sorted(by_path):
            archive.writestr(f"{folder}/{rel_path}", by_path[rel_path].content)
    return buffer.getvalue()


def build_skill_zip_bytes_from_dir(
    *,
    slug: str,
    root: Path,
    include_evals: bool = False,
    exclude_top_dirs: Collection[str] = (),
) -> bytes:
    """디스크 디렉토리 → ``.skill`` zip — 파일을 바이트 그대로 싣는다.

    text 어댑터(``SkillDraftFile.content``)는 바이너리를 표현할 수 없어
    finalize에서 asset이 조용히 누락됐다(Phase 1.5) — 이 경로는 디스크를 직접
    zip 소스로 써서 바이너리를 보존한다. symlink는 제외하고 경로는
    ``normalize_draft_path``로 방어한다. 최종 안전판은 어차피
    ``extract_package``의 zip-slip/symlink/size 가드가 다시 검증한다.

    크기 상한은 순회 중 ``st_size`` 누적으로 **읽기 전에** 검사한다 —
    ``extract_package``의 가드는 zip을 이미 메모리에 다 만든 뒤라, 여기서
    fail-fast하지 않으면 초대형 워크스페이스가 상한에 걸리기 전에 메모리를
    무제한 점유한다.
    """

    folder = slugify(slug)
    excluded = set(exclude_top_dirs)
    if not include_evals:
        excluded |= EXCLUDED_EXPORT_DIRS
    entries: dict[str, Path] = {}
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel_path = normalize_draft_path(path.relative_to(root).as_posix())
        if rel_path.split("/", 1)[0] in excluded:
            continue
        total_bytes += path.stat().st_size
        if total_bytes > settings.skill_max_package_bytes:
            raise PackageError(
                f"package too large: {total_bytes} bytes so far "
                f"(max {settings.skill_max_package_bytes})"
            )
        entries[rel_path] = path
    if "SKILL.md" not in entries:
        raise ValueError("draft package must include SKILL.md")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for rel_path in sorted(entries):
            archive.writestr(f"{folder}/{rel_path}", entries[rel_path].read_bytes())
    return buffer.getvalue()
