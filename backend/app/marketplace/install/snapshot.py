"""Storage / snapshot helpers for the marketplace install flow — BE-S3
split of ``install_service``. Function bodies are moved verbatim; only
import plumbing changed.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

from app.config import settings
from app.error_codes import marketplace_invalid_package
from app.marketplace.install.common import _payload_skill_kind
from app.models.marketplace import MarketplaceVersion
from app.models.skill import Skill
from app.storage.paths import resolve_data_path


def _skill_storage_root() -> Path:
    """Mirrors ``app.skills.service._skill_root`` without importing the
    module (file-boundary rule for Slice B — install_service must not
    modify skill service.py). ADR-018 — derived from ``data_root``."""

    return (Path(settings.data_root) / "skills").resolve()


def _target_for(skill_id: uuid.UUID) -> Path:
    return _skill_storage_root() / str(skill_id)


def _rel_install_storage(skill_id: uuid.UUID, version: MarketplaceVersion) -> str:
    """text-kind → ``skills/<id>/SKILL.md`` (file); package-kind →
    ``skills/<id>`` (dir). Relative to ``settings.data_root`` per ADR-018."""

    suffix = "/SKILL.md" if _payload_skill_kind(version) == "text" else ""
    return f"skills/{skill_id}{suffix}"


async def _copy_snapshot(version: MarketplaceVersion, target: Path) -> None:
    """Copy the version's on-disk snapshot to ``target``.

    Raises ``marketplace_invalid_package`` when the snapshot is missing
    or unreadable. Off the main event loop to keep the request hot path
    responsive on big packages.
    """

    if not version.storage_path:
        raise marketplace_invalid_package("version has no storage snapshot")

    src = resolve_data_path(version.storage_path)
    # ``ASYNC240`` — filesystem checks happen off the event loop. We
    # also return the is_file decision from the same probe so the copy
    # picks the correct branch without a second stat.
    exists, is_file = await asyncio.to_thread(_probe_path, src)
    if not exists:
        raise marketplace_invalid_package(
            f"version snapshot missing on disk: {version.storage_path}"
        )

    await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)

    try:
        if is_file:
            # text-kind version: SKILL.md file. Recreate ``<target>/SKILL.md``.
            await asyncio.to_thread(_copy_text_snapshot, src, target)
        else:
            await asyncio.to_thread(shutil.copytree, src, target)
    except (OSError, shutil.Error) as exc:
        # Cleanup partial dir then translate to a 400 the user can act on.
        await asyncio.to_thread(shutil.rmtree, target, ignore_errors=True)
        raise marketplace_invalid_package(f"copy failed: {exc}") from exc


def _probe_path(p: Path) -> tuple[bool, bool]:
    """Return ``(exists, is_file)`` in a single stat. Sync helper for
    ``asyncio.to_thread`` so async callers stay ASYNC240-clean."""

    if not p.exists():
        return False, False
    return True, p.is_file()


def _rmtree_skill_storage(p: Path) -> None:
    """Remove a skill storage path safely. For text skills ``p`` is the
    SKILL.md file path — climb one level to wipe the wrapping dir."""

    target = p.parent if p.is_file() else p
    shutil.rmtree(target, ignore_errors=True)


def _copy_text_snapshot(src: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, target / "SKILL.md")


async def _replace_skill_snapshot(version: MarketplaceVersion, skill: Skill) -> None:
    """Replace an installed skill's on-disk bytes while preserving its id."""

    target = _target_for(skill.id)
    tmp = target.with_suffix(".update.tmp")
    if tmp.exists():
        await asyncio.to_thread(shutil.rmtree, tmp, ignore_errors=True)

    await _copy_snapshot(version, tmp)
    try:
        if target.exists():
            await asyncio.to_thread(shutil.rmtree, target, ignore_errors=True)
        await asyncio.to_thread(tmp.rename, target)
    except Exception:
        await asyncio.to_thread(shutil.rmtree, tmp, ignore_errors=True)
        raise
