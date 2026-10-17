"""Storage path resolution (ADR-018).

Persisted ``storage_path`` columns hold values *relative to*
``settings.data_root``. This module is the single place that resolves them
back to absolute paths at read time. Direct ``Path(skill.storage_path)`` use
in service code is a regression — always go through :func:`resolve_data_path`.

Legacy absolute paths (M44 sweeps these out, but defence-in-depth) are
returned as-is so a partially-migrated DB still reads correctly.

Company layout (I-08, plan 8.9 "파일 저장 경로"): new company files go under
``tenants/<tenant_id>/...`` of their storage root, and a read is allowed only
when the path's company prefix matches the row's ``tenant_id``. Existing files
are not moved: an unprefixed (legacy) path counts as the default company's —
in a single-tenant installation that is the only company; in a multi-tenant one
it is m82's default company (and platform rows, which never carry a prefix).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Final
from uuid import NAMESPACE_URL, UUID, uuid5

from app.config import settings

logger = logging.getLogger(__name__)

TENANTS_DIR: Final = "tenants"
# Same id as ``app.migrations.org_authz_v1.contracts.TENANT`` (m82 default company).
DEFAULT_TENANT_ID: Final = uuid5(NAMESPACE_URL, "moldy/default-tenant")


class ForeignTenantPathError(ValueError):
    """The stored path belongs to another company; callers answer 404."""


def _data_root() -> Path:
    return Path(settings.data_root).resolve()


def resolve_data_path(value: str | os.PathLike[str]) -> Path:
    """Return an absolute path for a ``storage_path`` column value.

    Relative input is resolved against ``settings.data_root``. Absolute
    input is returned as-is (legacy / external mount fallback). Raises
    ``ValueError`` on empty input.
    """

    if not value:
        raise ValueError("empty storage_path")
    path = Path(value)
    if path.is_absolute():
        logger.debug("resolve_data_path got absolute input: %s", path)
        return path
    root = _data_root()
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("relative storage_path cannot escape data_root")
    return resolved


def ensure_relative(value: str) -> str:
    """Guardrail before assigning to a ``storage_path`` column — reject
    empty/absolute values so ADR-018's invariant holds at the write site."""

    if not value:
        raise ValueError("empty storage_path")
    if Path(value).is_absolute():
        raise ValueError(f"storage_path must be relative to data_root, got absolute: {value}")
    sentinel_root = Path("/moldy-data-root")
    if not (sentinel_root / value).resolve().is_relative_to(sentinel_root):
        raise ValueError("relative storage_path cannot escape data_root")
    return value


def tenant_relative(tenant_id: UUID | None, relative: str) -> str:
    """Location of a new file: ``tenants/<id>/<relative>``; platform rows keep ``relative``."""

    if tenant_id is None:
        return ensure_relative(relative)
    return ensure_relative(f"{TENANTS_DIR}/{tenant_id}/{relative}")


def skill_storage_dir(skill_id: UUID, tenant_id: UUID | None) -> str:
    """Directory of a new skill (package root, or the folder holding a text SKILL.md)."""

    return tenant_relative(tenant_id, f"skills/{skill_id}")


def path_company(path: Path, root: Path) -> UUID | None:
    """Company prefix of ``path`` under ``root``; ``None`` for the legacy/platform layout."""

    resolved, base = path.resolve(), root.resolve()
    if not resolved.is_relative_to(base):
        return None
    parts = resolved.relative_to(base).parts
    if not parts or parts[0].lower() != TENANTS_DIR:
        return None
    if parts[0] != TENANTS_DIR or len(parts) < 2:  # case variants fail closed
        raise ForeignTenantPathError("malformed company prefix")
    try:
        return UUID(parts[1])
    except ValueError as exc:
        raise ForeignTenantPathError("malformed company prefix") from exc


def _legacy_path_allowed(tenant_id: UUID | None) -> bool:
    if tenant_id is None or settings.deployment_mode == "single_tenant":
        return True
    return tenant_id == DEFAULT_TENANT_ID


def check_company_path(path: Path, root: Path, tenant_id: UUID | None) -> Path:
    """Return ``path`` when it belongs to ``tenant_id`` (``None`` = platform row)."""

    company = path_company(path, root)
    if company is None and _legacy_path_allowed(tenant_id):
        return path
    if company is not None and company == tenant_id:
        return path
    raise ForeignTenantPathError("storage path belongs to another company")


def resolve_company_data_path(value: str | os.PathLike[str], tenant_id: UUID | None) -> Path:
    """:func:`resolve_data_path` plus the company check against the row's ``tenant_id``."""

    return check_company_path(resolve_data_path(value), _data_root(), tenant_id)


def upload_file_path(storage_path: str, tenant_id: UUID | None) -> Path:
    """Chat upload location (absolute, under ``settings.upload_dir``) after the company check."""

    return check_company_path(Path(storage_path), Path(settings.upload_dir), tenant_id)


def upload_data_path(storage_path: str, tenant_id: UUID | None) -> Path:
    """Upload read that resolves a relative value against ``data_root`` (skill drafts)."""

    return check_company_path(resolve_data_path(storage_path), Path(settings.upload_dir), tenant_id)


def company_storage_value(value: str, tenant_id: UUID | None) -> str:
    """``value`` unchanged once it passed the company check (for call sites keyed on it)."""

    resolve_company_data_path(value, tenant_id)
    return value


def run_data_path_or_none(value: str, run_tenant_id: UUID | None) -> Path | None:
    """Run-time read of a skill attached to a run of ``run_tenant_id``.

    The descriptor carries no row company, so a company prefix must equal the run's
    company; unprefixed paths (platform/system skills, legacy) pass. Mismatch → ``None``.
    """

    path = resolve_data_path(value)
    try:
        company = path_company(path, _data_root())
    except ForeignTenantPathError:
        return None
    return path if company is None or company == run_tenant_id else None
