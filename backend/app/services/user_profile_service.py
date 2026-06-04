"""User profile settings and avatar image storage."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User
from app.schemas.auth import ProfileUpdateRequest

MAX_AVATAR_BYTES = 2 * 1024 * 1024
ALLOWED_AVATAR_MIME_TYPES = {"image/png", "image/jpeg", "image/webp"}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _avatar_dir(user_id: object) -> Path:
    return Path(settings.user_avatar_dir) / str(user_id)


def _avatar_path(user_id: object) -> Path:
    return _avatar_dir(user_id) / "avatar.webp"


def apply_profile_update(user: User, payload: ProfileUpdateRequest) -> None:
    fields = payload.model_fields_set
    if "display_name" in fields:
        user.display_name = payload.display_name
    if "avatar_mode" in fields and payload.avatar_mode is not None:
        user.avatar_mode = payload.avatar_mode
    if "avatar_initials" in fields:
        user.avatar_initials = payload.avatar_initials
    if "avatar_color" in fields and payload.avatar_color is not None:
        user.avatar_color = payload.avatar_color


def _center_crop_square(image: Image.Image) -> Image.Image:
    width, height = image.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    return image.crop((left, top, left + side, top + side))


def _write_avatar_image(path: Path, contents: bytes) -> None:
    try:
        with Image.open(BytesIO(contents)) as source:
            image = _center_crop_square(source.convert("RGBA"))
            image = image.resize((512, 512), Image.Resampling.LANCZOS)
            path.parent.mkdir(parents=True, exist_ok=True)
            image.save(path, format="WEBP", quality=88, method=6)
    except UnidentifiedImageError as exc:
        raise HTTPException(status_code=422, detail="invalid avatar image") from exc


async def save_avatar_image(user: User, file: UploadFile) -> None:
    mime = file.content_type or "application/octet-stream"
    if mime not in ALLOWED_AVATAR_MIME_TYPES:
        raise HTTPException(status_code=422, detail=f"unsupported avatar mime type: {mime}")

    contents = await file.read(MAX_AVATAR_BYTES + 1)
    if len(contents) > MAX_AVATAR_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"avatar exceeds max size {MAX_AVATAR_BYTES} bytes",
        )

    path = _avatar_path(user.id)
    await asyncio.to_thread(_write_avatar_image, path, contents)
    user.avatar_image_path = str(path)
    user.avatar_mode = "image"
    user.avatar_updated_at = _now()


async def delete_avatar_image(user: User) -> None:
    if user.avatar_image_path:
        path = Path(user.avatar_image_path)
        if await asyncio.to_thread(path.is_file):
            await asyncio.to_thread(path.unlink)
    user.avatar_image_path = None
    user.avatar_mode = "initials"
    user.avatar_updated_at = _now()


async def avatar_image_file(user: User) -> Path | None:
    if not user.avatar_image_path:
        return None
    path = Path(user.avatar_image_path)
    exists = await asyncio.to_thread(path.is_file)
    if not exists:
        user.avatar_image_path = None
        user.avatar_mode = "initials"
        user.avatar_updated_at = _now()
        return None
    return path


async def flush_profile(db: AsyncSession) -> None:
    await db.flush()
