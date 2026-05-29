from __future__ import annotations

import logging
from hashlib import sha256
from pathlib import Path

logger = logging.getLogger(__name__)

IMAGE_PREVIEW_MAX_EDGE = 768
IMAGE_PREVIEW_QUALITY = 82
IMAGE_PREVIEW_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def get_or_create_image_preview(
    target: Path,
    *,
    cache_dir: Path,
    cache_name: str,
) -> Path | None:
    """Return a cached WebP preview for ``target`` when it is a supported image."""

    if target.suffix.lower() not in IMAGE_PREVIEW_SUFFIXES:
        return None

    stat = target.stat()
    safe_name = cache_name.replace("/", "__")
    cache_key = sha256(
        f"{safe_name}:{stat.st_size}:{stat.st_mtime_ns}".encode()
    ).hexdigest()[:24]
    preview = cache_dir / f"{safe_name}.{cache_key}.webp"
    if preview.is_file():
        return preview

    try:
        from PIL import Image, ImageOps

        preview.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(target) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            image.thumbnail(
                (IMAGE_PREVIEW_MAX_EDGE, IMAGE_PREVIEW_MAX_EDGE),
                Image.Resampling.LANCZOS,
            )
            image.save(
                preview,
                format="WEBP",
                quality=IMAGE_PREVIEW_QUALITY,
                method=6,
            )
        return preview
    except Exception:  # noqa: BLE001
        logger.exception("Failed to create image preview for %s", target)
        return None
