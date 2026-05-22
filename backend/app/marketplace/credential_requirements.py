"""Skill credential requirement handling + runtime env injection plan.

Spec §10.6 — the marketplace surfaces ``credential_requirements`` per
version. After install, the user must bind each requirement to a
concrete ``credentials`` row (or the install enters ``needs_setup``).

This module owns:

* ``CredentialRequirement`` — typed view over a single requirement entry
  in ``Skill.credential_requirements`` JSON.
* ``parse_requirements(skill)`` — JSON → list of dataclass instances.
* ``list_bindings(db, skill, user)`` — current user's binding rows.
* ``validate_binding(db, skill, user, requirement_key, credential_id)``
  — Spec §10.6 validation rules (ownership / definition_key / not-system).
* ``upsert_binding`` / ``delete_binding`` — mutate ``SkillCredentialBinding``.
* ``missing_required_keys(db, skill, user)`` — keys with required=True
  + no matching binding. Used by ``origin_service`` to flip the
  installation summary to ``needs_setup``.
* ``build_runtime_env(...)`` — *Slice E skeleton*. Returns the env-var
  plan (key → decrypted value) the executor will inject. Slice D leaves
  the body unimplemented except for the binding+env_map traversal — the
  Cipher V2 decrypt happens in Slice E.

Leaf-ish module: imports ``models`` + ``schemas`` only — no other
marketplace service modules.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.error_codes import (
    credential_not_found,
    marketplace_credential_mismatch,
    marketplace_credential_required,
    skill_not_found,
)
from app.models.credential import Credential
from app.models.marketplace import SkillCredentialBinding
from app.models.skill import Skill

if TYPE_CHECKING:
    from app.dependencies import CurrentUser


# ---------------------------------------------------------------------------
# Typed requirement view
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedCredentialEntry:
    """In-memory projection of a fully resolved + decrypted credential.

    Distinct from :class:`app.marketplace.skill_runtime.ResolvedCredential`
    so this leaf module doesn't depend on ``skill_runtime``. The
    runtime adapter wraps these into the descriptor shape.
    """

    credential_id: uuid.UUID
    definition_key: str
    env_map: dict[str, str]
    decrypted: dict[str, str]


@dataclass(frozen=True)
class _LightUser:
    """Tiny adapter so ``list_bindings`` (which takes a ``CurrentUser``)
    works when only a user_id is in scope (executor / runtime path)."""

    id: uuid.UUID


@dataclass(frozen=True)
class CredentialRequirement:
    """Typed projection of a single ``credential_requirements[]`` JSON entry.

    Field names mirror ``CredentialRequirementOut`` so a caller can do
    ``CredentialRequirementOut(**asdict(req))`` cheaply.
    """

    key: str
    definition_key: str
    required: bool
    label: str
    description: str | None
    fields: tuple[str, ...]
    injection: str  # 'env' | 'config'
    scope: str  # 'user' | 'system_dependency' | 'manual'
    env_map: dict[str, str] | None

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> CredentialRequirement:
        return cls(
            key=str(raw["key"]),
            definition_key=str(raw["definition_key"]),
            required=bool(raw.get("required", True)),
            label=str(raw.get("label", raw["key"])),
            description=raw.get("description"),
            fields=tuple(raw.get("fields") or []),
            injection=str(raw.get("injection", "env")),
            scope=str(raw.get("scope", "user")),
            env_map=raw.get("env_map") if isinstance(raw.get("env_map"), dict) else None,
        )


def parse_requirements(skill: Skill) -> list[CredentialRequirement]:
    """Convert ``Skill.credential_requirements`` JSON into typed objects.

    Returns an empty list when the column is NULL/empty/malformed — the
    runtime should never crash on a missing requirement list.
    """

    raw = skill.credential_requirements or []
    if not isinstance(raw, list):
        return []
    out: list[CredentialRequirement] = []
    for entry in raw:
        if not isinstance(entry, dict) or "key" not in entry or "definition_key" not in entry:
            continue
        try:
            out.append(CredentialRequirement.from_json(entry))
        except (KeyError, TypeError, ValueError):
            # Malformed entry — skip silently rather than 500.
            continue
    return out


# ---------------------------------------------------------------------------
# Binding CRUD
# ---------------------------------------------------------------------------


async def list_bindings(
    db: AsyncSession, *, skill: Skill, user: CurrentUser
) -> list[SkillCredentialBinding]:
    """Bindings owned by ``user`` for the given skill (``scope='skill'``)."""

    stmt = select(SkillCredentialBinding).where(
        SkillCredentialBinding.skill_id == skill.id,
        SkillCredentialBinding.user_id == user.id,
        SkillCredentialBinding.scope == "skill",
    )
    return list((await db.execute(stmt)).scalars().all())


async def validate_binding(
    db: AsyncSession,
    *,
    skill: Skill,
    user: CurrentUser,
    requirement_key: str,
    credential_id: uuid.UUID,
) -> tuple[CredentialRequirement, Credential]:
    """Spec §10.6 — verify a proposed binding before persistence.

    Rules:

    1. Skill is owned by the user (``skill.user_id == user.id``). Mismatch
       raises ``skill_not_found`` so we don't leak existence of someone
       else's skill (enumeration oracle — rules/security.md).
    2. ``requirement_key`` exists on the skill's ``credential_requirements``.
       Unknown key → ``MARKETPLACE_CREDENTIAL_MISMATCH`` (400).
    3. Credential exists *and* is owned by the user. Both "missing" and
       "owned by someone else" collapse to ``credential_not_found``.
    4. Credential is NOT a system credential. System credentials are
       operator-managed and must never be hand-bound by users — rejecting
       them keeps the surface area small. ``marketplace_credential_mismatch``.
    5. ``credential.definition_key`` matches the requirement's expected
       definition. Mismatch → ``marketplace_credential_mismatch`` (400).
    """

    if skill.user_id != user.id:
        # Ownership check first — enumeration-safe 404.
        raise skill_not_found()

    requirements = {r.key: r for r in parse_requirements(skill)}
    requirement = requirements.get(requirement_key)
    if requirement is None:
        raise marketplace_credential_mismatch(
            f"unknown requirement_key '{requirement_key}'"
        )

    credential = await db.get(Credential, credential_id)
    if credential is None or credential.user_id != user.id or credential.is_system:
        # ``credential_not_found`` and "not yours" collapse — same as
        # other resource endpoints (rules/security.md).
        if credential is not None and credential.is_system:
            # Differentiate the operator-misuse case in logs but not in
            # the response. Caller may raise mismatch instead so the
            # client gets a hint that the credential exists but isn't
            # bindable.
            raise marketplace_credential_mismatch(
                "system credentials cannot be bound to user skills"
            )
        raise credential_not_found()

    if credential.definition_key != requirement.definition_key:
        raise marketplace_credential_mismatch(
            f"credential definition '{credential.definition_key}' "
            f"does not match requirement '{requirement.definition_key}'"
        )

    return requirement, credential


async def upsert_binding(
    db: AsyncSession,
    *,
    skill: Skill,
    user: CurrentUser,
    requirement_key: str,
    credential_id: uuid.UUID,
) -> SkillCredentialBinding:
    """Validate + create/update a single binding. Caller commits."""

    await validate_binding(
        db,
        skill=skill,
        user=user,
        requirement_key=requirement_key,
        credential_id=credential_id,
    )

    stmt = select(SkillCredentialBinding).where(
        SkillCredentialBinding.skill_id == skill.id,
        SkillCredentialBinding.user_id == user.id,
        SkillCredentialBinding.requirement_key == requirement_key,
        SkillCredentialBinding.scope == "skill",
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        existing.credential_id = credential_id
        return existing

    row = SkillCredentialBinding(
        skill_id=skill.id,
        user_id=user.id,
        requirement_key=requirement_key,
        credential_id=credential_id,
        scope="skill",
    )
    db.add(row)
    await db.flush()
    return row


async def delete_binding(
    db: AsyncSession,
    *,
    skill: Skill,
    user: CurrentUser,
    requirement_key: str,
) -> bool:
    """Remove a binding. Returns True if a row was deleted."""

    if skill.user_id != user.id:
        raise skill_not_found()
    stmt = select(SkillCredentialBinding).where(
        SkillCredentialBinding.skill_id == skill.id,
        SkillCredentialBinding.user_id == user.id,
        SkillCredentialBinding.requirement_key == requirement_key,
        SkillCredentialBinding.scope == "skill",
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return False
    await db.delete(row)
    return True


# ---------------------------------------------------------------------------
# Status helpers (consumed by origin_service)
# ---------------------------------------------------------------------------


async def missing_required_keys(
    db: AsyncSession, *, skill: Skill, user: CurrentUser
) -> list[str]:
    """Requirement keys with ``required=True`` and no user binding.

    ``scope='system_dependency'`` and ``scope='manual'`` keys never count
    as missing — the operator/user handles them out-of-band.
    """

    requirements = parse_requirements(skill)
    user_required = [
        r for r in requirements if r.required and r.scope == "user"
    ]
    if not user_required:
        return []
    bindings = await list_bindings(db, skill=skill, user=user)
    bound_keys = {b.requirement_key for b in bindings}
    return [r.key for r in user_required if r.key not in bound_keys]


# ---------------------------------------------------------------------------
# Slice E entry point — skeleton, full decrypt path lands later
# ---------------------------------------------------------------------------


async def resolve_credential_bindings(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
    agent_skill_config: dict[str, Any] | None = None,
) -> tuple[dict[str, ResolvedCredentialEntry], list[str]]:
    """Slice E §8.2 — resolve each ``credential_requirements`` entry into
    a decrypted, env-mapped projection.

    Returns ``(resolved_by_key, missing_required_keys)``.

    Override priority (Spec §8.4):

    1. ``agent_skill_config['credential_bindings'][requirement_key]``
       — per-agent override from ``agent_skills.config`` JSON.
    2. ``SkillCredentialBinding`` row (``scope='skill'``) for the user.
    3. Otherwise — counted as missing if the requirement is
       ``required=True`` and ``scope='user'``.

    Decryption uses ``credential_service.decrypt_with_external`` which
    is Cipher V2 + ``__external__`` ref expansion (same path as the
    rest of the codebase). The decrypted dict is held in memory only —
    callers are responsible for not logging the returned object.
    """

    requirements = parse_requirements(skill)
    if not requirements:
        return {}, []

    bindings = await list_bindings(
        db, skill=skill, user=_LightUser(user_id)  # type: ignore[arg-type]
    )
    binding_by_key: dict[str, SkillCredentialBinding] = {
        b.requirement_key: b for b in bindings
    }
    override_raw = (agent_skill_config or {}).get("credential_bindings") or {}
    override: dict[str, uuid.UUID] = {}
    for key, raw in override_raw.items():
        try:
            override[key] = (
                raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))
            )
        except (TypeError, ValueError):
            continue

    resolved: dict[str, ResolvedCredentialEntry] = {}
    missing: list[str] = []
    for req in requirements:
        if req.injection != "env":
            continue
        if req.scope != "user":
            # ``system_dependency`` / ``manual`` — caller's responsibility.
            continue

        credential_id: uuid.UUID | None = override.get(req.key)
        if credential_id is None and req.key in binding_by_key:
            credential_id = binding_by_key[req.key].credential_id
        if credential_id is None:
            if req.required:
                missing.append(req.key)
            continue

        credential = await db.get(Credential, credential_id)
        if credential is None or credential.user_id != user_id:
            # Ownership lost (deleted / reassigned). Treat as missing —
            # the caller surfaces ``marketplace_credential_required``.
            if req.required:
                missing.append(req.key)
            continue
        if credential.definition_key != req.definition_key:
            # Drift since the binding was created. Same conservative
            # treatment.
            if req.required:
                missing.append(req.key)
            continue

        # Lazy import — keeps credential_requirements importable from
        # unit tests that don't need the cipher path.
        from app.credentials import service as credential_service

        try:
            decrypted = await credential_service.decrypt_with_external(
                credential.data_encrypted
            )
        except Exception:
            if req.required:
                missing.append(req.key)
            continue
        if not isinstance(decrypted, dict):
            if req.required:
                missing.append(req.key)
            continue

        resolved[req.key] = ResolvedCredentialEntry(
            credential_id=credential.id,
            definition_key=credential.definition_key,
            env_map=dict(req.env_map or {}),
            decrypted={k: str(v) for k, v in decrypted.items() if v is not None},
        )

    return resolved, missing


async def build_runtime_env(
    db: AsyncSession,
    *,
    skill: Skill,
    user: CurrentUser,
    agent_skill_config: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Plan the env vars to inject into the skill subprocess.

    Slice E §8.2 — full credential resolution + Cipher V2 decrypt.

    The function intentionally does *not* read os.environ — the
    executor injects this dict on top of a minimal base env (``PATH``,
    ``PYTHONPATH``, ``HOME``, ``SKILL_OUTPUT_DIR``, ``OUTPUTS_DIR``) so
    a stray host secret can't leak.

    Raises ``marketplace_credential_required`` when a required ``user``
    scope binding is missing — chat_service uses this for fail-fast on
    the way into the agent run.
    """

    resolved, missing = await resolve_credential_bindings(
        db,
        skill=skill,
        user_id=user.id,
        agent_skill_config=agent_skill_config,
    )
    if missing:
        raise marketplace_credential_required(
            f"missing required credential bindings: {', '.join(missing)}"
        )

    plan: dict[str, str] = {}
    for rc in resolved.values():
        # ``env_map`` shape per ADR-017 module-contracts §3.5 + Sat's
        # Slice E brief: ``{credential_field_name: env_var_name}``.
        for field, env_name in rc.env_map.items():
            value = rc.decrypted.get(field)
            if value is None:
                continue
            plan[env_name] = value
    return plan


__all__ = [
    "CredentialRequirement",
    "ResolvedCredentialEntry",
    "build_runtime_env",
    "delete_binding",
    "list_bindings",
    "missing_required_keys",
    "parse_requirements",
    "resolve_credential_bindings",
    "upsert_binding",
    "validate_binding",
]


# Silence ruff F401 for Iterable when it slips into edits via future hooks.
_ITERABLE_HINT: Iterable[Any] = ()
