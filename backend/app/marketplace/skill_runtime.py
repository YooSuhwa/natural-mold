"""Skill runtime context — per-thread mount + credential injection plan.

Spec §8.2 + §9. ADR-017 Slice E surface that the executor consumes.

The four-level data shape (defined here, populated by ``build_skill_runtime_context``):

* ``ResolvedCredential``    — one decrypted secret + its env mapping.
* ``SkillRuntimeDescriptor``— a single attached skill: where it came from
                              on disk, where it lives in the per-thread
                              runtime root, and the resolved bindings.
* ``SkillToolContext``      — the bundle threaded into
                              ``_create_skill_execute_tool``. Encapsulates
                              ``thread_id``, ``output_dir``, ``runtime_root``,
                              and the slug → descriptor map.

Stage 1 (this file):
    * Dataclasses defined.
    * ``build_skill_runtime_context(cfg, db=None)`` populates the bundle
      with the *legacy* defaults so behaviour is bit-for-bit identical
      to the pre-Slice-E executor — that means ``runtime_root`` points
      at the global ``_DATA_DIR`` and ``descriptors`` is keyed by
      whatever slug the skill carries. The per-thread copytree + slug
      enforcement land in stage 2.

Stage 2 will:
    * Switch ``runtime_root`` to ``_DATA_DIR / "runtime" / thread_id / "skills"``.
    * ``shutil.copytree`` each attached skill into ``runtime_root / slug``.
    * Populate ``descriptor.runtime_storage_path``.
    * Empty descriptors → no skill is reachable (selected-skill mount).

Stage 3 will:
    * Resolve each requirement key → credential row → decrypted dict
      via ``credential_requirements`` + Cipher V2.
    * Fill ``descriptor.credential_bindings`` so the execute tool can
      compose the per-script env on the hot path without a DB round-trip.
"""

from __future__ import annotations

import logging
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.storage.paths import resolve_data_path

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.agent_runtime.executor import AgentConfig


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedCredential:
    """A decrypted credential ready for env-var injection.

    ``decrypted`` is in-memory only — never serialized, never logged. The
    redaction helper (Slice E §13.2) consumes the *mapped* env var names
    so that stdout/stderr replays don't leak values.
    """

    credential_id: uuid.UUID
    definition_key: str
    # ``env_map`` mirrors Spec §10.8 ``CredentialRequirementIn.env_map``:
    # ``{credential_field_name: env_var_name}``. Example:
    # ``{"username": "SRT_USERNAME", "password": "SRT_PASSWORD"}``.
    env_map: dict[str, str]
    decrypted: dict[str, str]


@dataclass
class SkillRuntimeDescriptor:
    """Per-skill view used by the execute tool to validate + inject env.

    * ``id`` / ``slug`` come from the DB row.
    * ``original_storage_path`` is the canonical user-owned location.
    * ``runtime_storage_path`` is where the executor mounts a *copy*
      for this thread. Stage 1 holds the same value as
      ``original_storage_path`` (no per-thread copy yet); stage 2
      switches to ``runtime_root / slug``.
    * ``credential_bindings`` maps a requirement key to the resolved
      credential — only populated in stage 3.
    """

    id: uuid.UUID
    slug: str
    name: str
    description: str
    original_storage_path: Path
    runtime_storage_path: Path
    execution_profile: dict[str, Any] | None = None
    credential_bindings: dict[str, ResolvedCredential] = field(default_factory=dict)


@dataclass
class SkillToolContext:
    """Container the executor passes to ``_create_skill_execute_tool``.

    Carries every piece of state the per-script subprocess needs:

    * ``thread_id``    — used to scope output URLs (``/api/conversations/<id>/files/``)
                         and the runtime root.
    * ``output_dir``   — concrete path where script outputs land.
                         Stays mounted at ``data/conversations/<thread_id>``.
    * ``runtime_root`` — the directory the LLM is allowed to ``cd`` into.
                         Stage 1: ``_DATA_DIR`` (legacy). Stage 2: per-thread.
    * ``descriptors``  — slug → ``SkillRuntimeDescriptor``. Stage 2 uses
                         this map to enforce "selected skills only" —
                         an unknown slug returns the documented error.
                         Stage 1 leaves the map empty so existing tests
                         that hit the broad-mount surface keep passing.
    """

    thread_id: str
    output_dir: Path
    runtime_root: Path
    descriptors: dict[str, SkillRuntimeDescriptor] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def _per_thread_runtime_root(data_dir: Path, thread_id: str) -> Path:
    """Slice E stage 2 — per-thread mount root.

    Layout::

        data/
        ├── runtime/
        │   └── <thread_id>/
        │       └── skills/
        │           ├── <slug-A>/
        │           │   └── SKILL.md
        │           └── <slug-B>/
        │               └── SKILL.md
        ├── skills/                # user-owned canonical storage
        └── conversations/<thread_id>/   # script outputs

    LangGraph ``thread_id`` is reused so the cleanup job can correlate
    runtime roots with active checkpoints (Spec §9 / progress.txt gotcha).
    """

    return (data_dir / "runtime" / thread_id / "skills").resolve()


def _materialize_skill(
    descriptor: SkillRuntimeDescriptor, runtime_root: Path
) -> None:
    """Copy a single skill's bytes into ``runtime_root/<slug>``.

    Sync helper (caller wraps with ``asyncio.to_thread`` if needed).

    * package-kind skills: ``copytree`` the original dir.
    * text-kind skills: the original ``storage_path`` is a single
      ``SKILL.md`` file — recreate the wrapping dir + copy the file.
    * Existing target dir is wiped first so re-runs are idempotent.
    * ``symlinks=False`` (Spec §9 — writes must NOT flow back to the
      shared canonical storage).
    """

    target = runtime_root / descriptor.slug
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)

    src = descriptor.original_storage_path
    if not src.exists():
        # Caller treats this as a silently-skipped skill — the LLM will
        # see "skill not attached" rather than an unhandled exception.
        logger.warning(
            "skill_runtime materialize skipped — source missing: skill=%s path=%s",
            descriptor.slug,
            src,
        )
        return
    if src.is_file():
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target / "SKILL.md")
    else:
        shutil.copytree(src, target, symlinks=False)


def _build_descriptor_from_skill_dict(
    raw: dict[str, Any], runtime_root: Path
) -> SkillRuntimeDescriptor | None:
    """Translate a single ``agent_skills`` entry (see
    ``app.skills.service.to_runtime_dict``) into a runtime descriptor.

    Returns ``None`` when the entry is malformed — caller filters.
    """

    skill_id_raw = raw.get("id")
    slug = raw.get("slug") or ""
    storage_path = raw.get("storage_path") or ""
    if not skill_id_raw or not slug:
        return None
    try:
        skill_id = uuid.UUID(str(skill_id_raw))
    except (TypeError, ValueError):
        return None
    # ADR-018 — resolve column (relative) to absolute for materialize/copy.
    original = resolve_data_path(storage_path) if storage_path else runtime_root
    return SkillRuntimeDescriptor(
        id=skill_id,
        slug=str(slug),
        name=str(raw.get("name") or slug),
        description=str(raw.get("description") or ""),
        original_storage_path=original,
        # Stage 1 — no per-thread copy yet; runtime path == original.
        # Stage 2 overwrites with ``runtime_root / slug``.
        runtime_storage_path=original,
        execution_profile=(
            raw.get("execution_profile")
            if isinstance(raw.get("execution_profile"), dict)
            else None
        ),
    )


def build_skill_runtime_context(
    cfg: AgentConfig,
    *,
    data_dir: Path,
) -> SkillToolContext:
    """Synchronous part of the context build — per-thread mount only.

    Credential resolution lives in a separate async step
    (:func:`resolve_runtime_credentials`) so tests + non-DB callsites
    can exercise the mount path without spinning a session.

    Stage 2 — per-thread runtime root with ``copytree(symlinks=False)``
    of each attached skill. ``descriptors`` is keyed by slug;
    ``execute_in_skill`` rejects unknown slugs (selected-skill mount).
    """

    output_dir = (data_dir / "conversations" / cfg.thread_id).resolve()
    runtime_root = _per_thread_runtime_root(data_dir, cfg.thread_id)

    descriptors: dict[str, SkillRuntimeDescriptor] = {}
    if cfg.agent_skills:
        runtime_root.mkdir(parents=True, exist_ok=True)

        for raw in cfg.agent_skills:
            descriptor = _build_descriptor_from_skill_dict(raw, runtime_root)
            if descriptor is None:
                continue
            descriptor.runtime_storage_path = runtime_root / descriptor.slug
            _materialize_skill(descriptor, runtime_root)
            descriptors[descriptor.slug] = descriptor

    return SkillToolContext(
        thread_id=cfg.thread_id,
        output_dir=output_dir,
        runtime_root=runtime_root,
        descriptors=descriptors,
    )


async def resolve_runtime_credentials(
    ctx: SkillToolContext,
    *,
    db: AsyncSession,
    cfg: AgentConfig,
) -> None:
    """Stage 3 — populate ``descriptor.credential_bindings`` for each
    attached skill. Mutates ``ctx`` in place.

    Raises ``marketplace_credential_required`` when a required ``user``
    binding is missing for any attached skill (Spec §8.3 — fail-fast at
    agent build before any LLM token is spent). Skips when ``cfg.user_id``
    is empty (DB-free unit tests, trigger mode).
    """

    if not cfg.agent_skills or not cfg.user_id:
        return

    # Lazy imports — keep ``skill_runtime`` cheap on cold start.
    from app.error_codes import marketplace_credential_required
    from app.marketplace.credential_requirements import (
        resolve_credential_bindings,
    )
    from app.models.skill import Skill as _Skill

    try:
        user_uuid = uuid.UUID(str(cfg.user_id))
    except (TypeError, ValueError):
        return

    # Build a slug → raw-config map so we can pair descriptors with
    # their ``agent_skills.config`` overrides (Spec §3.8).
    raw_by_slug: dict[str, dict[str, Any]] = {
        str(r.get("slug") or ""): r for r in cfg.agent_skills if isinstance(r, dict)
    }

    aggregated_missing: list[str] = []
    for slug, descriptor in ctx.descriptors.items():
        raw = raw_by_slug.get(slug) or {}
        try:
            skill_uuid = descriptor.id
        except AttributeError:
            continue

        skill = await db.get(_Skill, skill_uuid)
        if skill is None:
            continue

        agent_config_override = (
            raw.get("config") if isinstance(raw.get("config"), dict) else None
        )

        resolved, missing = await resolve_credential_bindings(
            db,
            skill=skill,
            user_id=user_uuid,
            agent_skill_config=agent_config_override,
        )
        if missing:
            aggregated_missing.extend(f"{slug}/{k}" for k in missing)
            continue

        descriptor.credential_bindings = {
            key: ResolvedCredential(
                credential_id=entry.credential_id,
                definition_key=entry.definition_key,
                env_map=entry.env_map,
                decrypted=entry.decrypted,
            )
            for key, entry in resolved.items()
        }

    if aggregated_missing:
        raise marketplace_credential_required(
            "missing required credential bindings: "
            + ", ".join(aggregated_missing)
        )


# ---------------------------------------------------------------------------
# Cleanup (Spec §9.3 — stale runtime root retention)
# ---------------------------------------------------------------------------


def cleanup_stale_runtime_roots(
    data_dir: Path, *, retention_seconds: int = 3600
) -> int:
    """Remove ``data/runtime/<thread_id>/`` dirs older than the threshold.

    Returns the count of removed dirs (for logging).

    LangGraph thread_id is the per-conversation identifier — once a
    conversation is idle for ``retention_seconds`` (default 1h), the
    on-disk runtime root has nothing to protect. The cleanup is best-
    effort: per-dir failures get logged but don't abort the sweep.

    The function is sync so callers can run it from APScheduler (which
    expects a plain callable) or from a lifespan startup hook.
    """

    runtime_parent = data_dir / "runtime"
    if not runtime_parent.exists():
        return 0
    cutoff = time.time() - retention_seconds
    removed = 0
    for entry in runtime_parent.iterdir():
        if not entry.is_dir():
            continue
        try:
            # Use mtime — copytree refreshes the dir, so an actively
            # used root keeps a fresh mtime; idle ones grow stale.
            if entry.stat().st_mtime > cutoff:
                continue
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
        except OSError:
            logger.exception(
                "cleanup_stale_runtime_roots failed for entry=%s", entry
            )
    if removed:
        logger.info(
            "cleanup_stale_runtime_roots removed %d stale dir(s) under %s",
            removed,
            runtime_parent,
        )
    return removed


__all__ = [
    "ResolvedCredential",
    "SkillRuntimeDescriptor",
    "SkillToolContext",
    "build_skill_runtime_context",
    "cleanup_stale_runtime_roots",
    "resolve_runtime_credentials",
]
