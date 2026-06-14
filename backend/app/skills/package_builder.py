from __future__ import annotations

import io
import zipfile
from collections.abc import Sequence
from pathlib import PurePosixPath

from app.schemas.skill_builder import SkillDraftFile
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
