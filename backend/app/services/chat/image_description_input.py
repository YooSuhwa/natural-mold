"""Prepare bounded image content and immutable references for a single attached question."""

from __future__ import annotations

import hashlib
import io
from functools import lru_cache
from pathlib import Path
from uuid import UUID

from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import settings
from app.storage.paths import ForeignTenantPathError, upload_file_path

MAX_QUESTION_CHARS = 2000
MAX_PIXELS = 16_000_000
MAX_SIDE = 1568
_IMAGE_FORMATS = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "GIF": "image/gif",
    "WEBP": "image/webp",
}


class InvalidDescriptionInput(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def question_digest(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()


def scoped_image_path(storage_path: str, tenant_id: UUID) -> Path:
    path = upload_file_path(storage_path, tenant_id).resolve()
    if not path.is_relative_to(Path(settings.upload_dir).resolve()):
        raise ForeignTenantPathError("image is outside the upload directory")
    return path


def _file_version(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


@lru_cache(maxsize=256)
def _file_digest(path: Path, version: tuple[int, ...]) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        remaining = version[2]
        while remaining:
            data = source.read(min(remaining, 64 * 1024))
            if not data:
                raise InvalidDescriptionInput("unavailable")
            digest.update(data)
            remaining -= len(data)
    if _file_version(path) != version:
        raise InvalidDescriptionInput("unavailable")
    return digest.hexdigest()


def matches_version(path: Path, limit: int, digest: str) -> bool:
    """Cache only hashes of unchanged files. A read never grants access to a new actor."""
    try:
        version = _file_version(path)
        return 0 < version[2] <= limit and _file_digest(path, version) == digest
    except OSError:
        return False


def read_version(path: Path, limit: int) -> tuple[bytes, str]:
    try:
        with path.open("rb") as source:
            data = source.read(limit + 1)
    except OSError as exc:
        raise InvalidDescriptionInput("unavailable") from exc
    if len(data) > limit:
        raise InvalidDescriptionInput("size_limit")
    return data, hashlib.sha256(data).hexdigest()


def description_image(data: bytes, declared_mime: str, byte_limit: int) -> tuple[bytes, str]:
    """Re-encode the first frame. Metadata, embedded profiles and trailing bytes stay local."""
    try:
        with Image.open(io.BytesIO(data)) as source:
            mime = _IMAGE_FORMATS.get(source.format or "")
            if not mime or mime != declared_mime.partition(";")[0].strip().lower():
                raise InvalidDescriptionInput("invalid_image")
            if source.width * source.height > MAX_PIXELS:
                raise InvalidDescriptionInput("size_limit")
            image = ImageOps.exif_transpose(source)
            image.thumbnail((MAX_SIDE, MAX_SIDE))
            rgb = Image.new("RGB", image.size, "white")
            with image.convert("RGBA") as rgba:
                rgb.paste(rgba, mask=rgba.getchannel("A"))
            out = io.BytesIO()
            rgb.save(out, format="JPEG", quality=85)
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError, SyntaxError) as exc:
        raise InvalidDescriptionInput("invalid_image") from exc
    encoded = out.getvalue()
    if len(encoded) > byte_limit:
        raise InvalidDescriptionInput("size_limit")
    return encoded, "image/jpeg"
