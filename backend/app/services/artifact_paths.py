from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path

from app.storage.artifact_paths import ArtifactPathError, validate_artifact_relative_path

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")
_CUSTOM_MIME_TYPES = {
    "hwp": "application/x-hwp",
    "hwpx": "application/x-hwpx",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


@dataclass(frozen=True)
class NormalizedArtifactPath:
    logical_path: str
    display_name: str
    extension: str | None
    mime_type: str
    artifact_kind: str


def artifact_kind_for(mime_type: str, extension: str | None) -> str:
    ext = (extension or "").lower()
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type == "application/pdf" or ext == "pdf":
        return "pdf"
    if mime_type == "text/markdown" or ext in {"md", "markdown", "mmd", "mermaid"}:
        return "markdown"
    if mime_type == "text/html" or ext in {"html", "htm"}:
        return "html"
    if ext in {"py", "js", "ts", "tsx", "jsx", "css", "sql", "sh"}:
        return "code"
    if ext in {"csv", "tsv", "xlsx", "xls", "json", "yaml", "yml", "toml"}:
        return "data"
    if ext in {"doc", "docx", "ppt", "pptx", "hwp", "hwpx"}:
        return "document"
    if ext in {"dwg", "dxf", "step", "stp", "iges", "igs", "stl"}:
        return "cad"
    return "other"


def normalize_output_path(base_dir: Path, path: Path) -> NormalizedArtifactPath:
    if path.is_symlink():
        raise ArtifactPathError("artifact symlinks are not allowed")
    resolved_base = base_dir.resolve()
    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(resolved_base):
        raise ArtifactPathError("artifact path escapes output directory")
    if not resolved_path.is_file():
        raise ArtifactPathError("artifact path is not a file")
    if resolved_path.is_symlink():
        raise ArtifactPathError("artifact symlinks are not allowed")

    relative = resolved_path.relative_to(resolved_base)
    logical_path = relative.as_posix()
    validate_artifact_relative_path(logical_path)

    extension = relative.suffix.lower().lstrip(".") or None
    mime_type = _CUSTOM_MIME_TYPES.get(extension or "")
    if mime_type is None:
        mime_type = mimetypes.guess_type(relative.name)[0] or "application/octet-stream"
    return NormalizedArtifactPath(
        logical_path=logical_path,
        display_name=relative.name,
        extension=extension,
        mime_type=mime_type,
        artifact_kind=artifact_kind_for(mime_type, extension),
    )


def safe_storage_filename(display_name: str) -> str:
    cleaned = _SAFE_FILENAME_RE.sub("_", display_name).strip(" .")
    return cleaned[:160] or "artifact"
