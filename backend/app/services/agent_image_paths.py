from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from app.config import settings

_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _dedupe(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _configured_dir(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (_backend_root() / path).resolve()


def _data_root_candidates() -> list[Path]:
    raw = Path(settings.data_root)
    candidates = (
        [raw]
        if raw.is_absolute()
        else [(_backend_root() / raw).resolve(), raw.resolve()]
    )
    return _dedupe(candidates)


def _stored_path_candidates(value: str) -> list[Path]:
    raw = Path(value)
    if raw.is_absolute():
        return [raw]

    candidates: list[Path] = [raw.resolve(), (_backend_root() / raw).resolve()]
    for data_root in _data_root_candidates():
        candidates.append((data_root / raw).resolve())
        parts = raw.parts
        if parts and parts[0] == "data" and len(parts) > 1:
            candidates.append((data_root / Path(*parts[1:])).resolve())
    return _dedupe(candidates)


def agent_image_dir() -> Path:
    return _configured_dir(settings.agent_image_dir)


def find_agent_avatar_path(agent_id: uuid.UUID) -> Path | None:
    image_dir = agent_image_dir() / str(agent_id)
    for suffix in _IMAGE_SUFFIXES:
        candidate = image_dir / f"avatar{suffix}"
        if candidate.is_file():
            return candidate
    if not image_dir.is_dir():
        return None
    for candidate in sorted(image_dir.glob("avatar.*")):
        if candidate.suffix.lower() in _IMAGE_SUFFIXES and candidate.is_file():
            return candidate
    return None


def resolve_agent_image_path(
    stored_path: str | None,
    *,
    agent_id: uuid.UUID | None = None,
) -> Path | None:
    if stored_path:
        for candidate in _stored_path_candidates(stored_path):
            if candidate.is_file():
                return candidate
    if agent_id is None:
        return None
    return find_agent_avatar_path(agent_id)


def build_agent_image_url(
    agent_id: uuid.UUID,
    *,
    updated_at: datetime,
    image_path: str | None,
) -> str | None:
    if resolve_agent_image_path(image_path, agent_id=agent_id) is None:
        return None
    return f"/api/agents/{agent_id}/image?t={int(updated_at.timestamp())}"
