from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

EXCLUDED_FILE_NAMES: Final[frozenset[str]] = frozenset({".DS_Store"})
EXCLUDED_DIR_NAMES: Final[frozenset[str]] = frozenset(
    {".moldy-output", ".moldy-runtime", "__moldy_outputs__", "__moldy_runtime__"}
)
EXCLUDED_FILE_SUFFIXES: Final[tuple[str, ...]] = ("~", ".swp", ".swo")


@dataclass(frozen=True, slots=True)
class PackageHashError(Exception):
    path: str
    reason: str

    def __str__(self) -> str:
        return f"{self.path}: {self.reason}"


def compute_package_tree_hash(root: Path) -> str:
    root_path = root.resolve()
    if not root_path.is_dir():
        raise PackageHashError(path=root.as_posix(), reason="not a directory")

    hasher = hashlib.sha256()
    for entry in _iter_hashable_files(root_path):
        rel = entry.relative_to(root_path).as_posix()
        data = entry.read_bytes()
        file_digest = hashlib.sha256(data).hexdigest()
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(str(len(data)).encode("ascii"))
        hasher.update(b"\0")
        hasher.update(file_digest.encode("ascii"))
        hasher.update(b"\0")
    return hasher.hexdigest()


def _iter_hashable_files(root_path: Path) -> list[Path]:
    entries = sorted(root_path.rglob("*"), key=lambda path: path.relative_to(root_path).as_posix())
    files: list[Path] = []
    for entry in entries:
        rel = entry.relative_to(root_path)
        if _is_excluded(rel):
            continue
        if entry.is_symlink():
            raise PackageHashError(path=rel.as_posix(), reason="symlink not allowed")
        if entry.is_file():
            files.append(entry)
    return files


def _is_excluded(rel: Path) -> bool:
    name = rel.name
    return (
        name in EXCLUDED_FILE_NAMES
        or name.endswith(EXCLUDED_FILE_SUFFIXES)
        or any(part in EXCLUDED_DIR_NAMES for part in rel.parts)
    )
