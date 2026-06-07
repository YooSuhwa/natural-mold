from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.error_codes import file_not_found
from app.services.image_preview import get_or_create_image_preview_async


@dataclass(frozen=True)
class ResolvedConversationFile:
    path: Path
    media_type: str | None
    cache_control: str


async def resolve_conversation_file(
    base_dir: Path,
    conversation_id: uuid.UUID,
    file_path: str,
    variant: Literal["original", "preview"],
) -> ResolvedConversationFile:
    base = base_dir / str(conversation_id)
    resolved_base = base.resolve()
    target = (base / file_path).resolve()
    if not target.is_relative_to(resolved_base):
        raise file_not_found()
    target_exists = await asyncio.to_thread(target.is_file)
    if not target_exists:
        raise file_not_found()

    if variant == "preview":
        preview = await get_or_create_image_preview_async(
            target,
            cache_dir=resolved_base / ".previews",
            cache_name=target.relative_to(resolved_base).as_posix(),
        )
        if preview is not None:
            return ResolvedConversationFile(
                path=preview,
                media_type="image/webp",
                cache_control="public, max-age=31536000, immutable",
            )

    return ResolvedConversationFile(
        path=target,
        media_type=None,
        cache_control="public, max-age=3600",
    )
