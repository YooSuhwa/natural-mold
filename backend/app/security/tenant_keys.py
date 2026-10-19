"""Per-company data keys stored wrapped in ``tenants.data_keys`` (B-16, plan 8.9 / Q18).

Layout of ``tenants.data_keys``::

    {"active_key_id": "<12 hex>" | None,
     "destroyed_at": "<iso>" | None,
     "keys": {"<key id>": {"version": 1, "status": "active|retired|destroyed",
                           "wrapped": "<b64>", "wrap_key_id": "<instance key id>",
                           "created_at": "<iso>", "retired_at": None, "destroyed_at": None}}}

``legacy_01_closed_at`` marks that the company has no format 0x01 credential
rows left; from then on (with ``TENANT_DATA_KEYS_ENABLED`` on) 0x01 ciphertext on
its rows is rejected, so an instance-key blob cannot be transplanted into them.

Raw key material never leaves this module except inside ``TenantDataKey`` (whose
repr hides it); it is never logged. Unwrapped keys are cached per process for at
most ``tenant_key_cache.TTL_SECONDS`` keyed by (company, key id, wrapped digest); every
lookup still reads ``tenants.data_keys`` first, so a destroy in any process takes
effect on the next read everywhere, and a local destroy also evicts the cache.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Tenant
from app.security import key_provider, tenant_key_cache
from app.security.cipher import AES_KEY_LEN, TENANT_KEY_ID_LEN, TenantKeyError

__all__ = [
    "TenantDataKey",
    "TenantKeyDestroyedError",
    "TenantKeyError",
    "TenantKeyNotFoundError",
    "close_legacy_01",
    "destroy_tenant_keys",
    "ensure_active_key",
    "get_key",
    "legacy_01_closed",
    "reopen_legacy_01",
    "rewrap_all_tenants",
    "rewrap_keys",
    "rotate_key",
]


logger = logging.getLogger(__name__)

_KEY_ID_ATTEMPTS = 8
clear_unwrap_cache = tenant_key_cache.clear
cached_key_count = tenant_key_cache.count


class TenantKeyNotFoundError(TenantKeyError):
    """The company has no data key with the requested id."""


class TenantKeyDestroyedError(TenantKeyError):
    """The company data key was destroyed (company permanently deleted)."""


@dataclass(frozen=True)
class TenantDataKey:
    key_id: str
    raw: bytes = field(repr=False)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _state(raw: object) -> dict[str, Any]:
    state: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    keys = state.get("keys")
    state["keys"] = {k: dict(v) for k, v in keys.items()} if isinstance(keys, dict) else {}
    return state


async def _read_state(db: AsyncSession, tenant_id: UUID) -> dict[str, Any]:
    result = await db.execute(select(Tenant.id, Tenant.data_keys).where(Tenant.id == tenant_id))
    row = result.first()
    if row is None:
        raise TenantKeyNotFoundError("company has no data keys")
    return _state(row.data_keys)


async def _lock(db: AsyncSession, tenant_id: UUID) -> Tenant:
    tenant = await db.scalar(
        select(Tenant)
        .where(Tenant.id == tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if tenant is None:
        raise TenantKeyNotFoundError("company has no data keys")
    return tenant


async def _store(db: AsyncSession, tenant: Tenant, state: dict[str, Any]) -> None:
    tenant.data_keys = state  # reassign so the JSON column change is tracked
    await db.flush([tenant])


def _unwrap(tenant_id: UUID, key_id: str, entry: dict[str, Any]) -> TenantDataKey:
    wrapped = str(entry["wrapped"])
    cached = tenant_key_cache.get(tenant_id, key_id, wrapped)
    if cached is not None:
        return TenantDataKey(key_id=key_id, raw=cached)
    raw = key_provider.unwrap_data_key(
        wrapped,
        wrap_key_id=str(entry["wrap_key_id"]),
        tenant_id=tenant_id,
        key_id=key_id,
        version=int(entry["version"]),
    )
    tenant_key_cache.put(tenant_id, key_id, wrapped, raw)
    return TenantDataKey(key_id=key_id, raw=raw)


def _usable_entry(state: dict[str, Any], key_id: str) -> dict[str, Any]:
    entry = state["keys"].get(key_id)
    if state.get("destroyed_at") or (entry is not None and entry.get("status") == "destroyed"):
        raise TenantKeyDestroyedError("company data key was destroyed")
    if entry is None or "wrapped" not in entry:
        raise TenantKeyNotFoundError("company has no data key with this id")
    return entry


def _new_key(tenant_id: UUID, state: dict[str, Any]) -> TenantDataKey:
    if state.get("destroyed_at"):
        raise TenantKeyDestroyedError("company data keys were destroyed")
    key_id = next(
        (
            k
            for _ in range(_KEY_ID_ATTEMPTS)
            if (k := secrets.token_hex(TENANT_KEY_ID_LEN)) not in state["keys"]
        ),
        None,
    )
    if key_id is None:
        raise TenantKeyError("could not allocate a unique company key id")
    raw = secrets.token_bytes(AES_KEY_LEN)
    version = max((int(e.get("version", 0)) for e in state["keys"].values()), default=0) + 1
    wrapped, wrap_key_id = key_provider.wrap_data_key(
        raw, tenant_id=tenant_id, key_id=key_id, version=version
    )
    state["keys"][key_id] = {
        "version": version,
        "status": "active",
        "wrapped": wrapped,
        "wrap_key_id": wrap_key_id,
        "created_at": _now(),
        "retired_at": None,
        "destroyed_at": None,
    }
    state["active_key_id"] = key_id
    return TenantDataKey(key_id=key_id, raw=raw)


async def get_key(db: AsyncSession, tenant_id: UUID, key_id: str) -> TenantDataKey:
    """Resolve one of the company's (active or retired) data keys for decryption."""

    state = await _read_state(db, tenant_id)
    return _unwrap(tenant_id, key_id, _usable_entry(state, key_id))


async def ensure_active_key(db: AsyncSession, tenant_id: UUID) -> TenantDataKey:
    """Return the company's active data key, creating the first one on demand."""

    state = await _read_state(db, tenant_id)
    active = state.get("active_key_id")
    if isinstance(active, str):
        return _unwrap(tenant_id, active, _usable_entry(state, active))
    if state.get("destroyed_at"):
        raise TenantKeyDestroyedError("company data keys were destroyed")
    tenant = await _lock(db, tenant_id)
    state = _state(tenant.data_keys)
    active = state.get("active_key_id")
    if isinstance(active, str):
        return _unwrap(tenant_id, active, _usable_entry(state, active))
    key = _new_key(tenant_id, state)
    await _store(db, tenant, state)
    return key


async def rotate_key(db: AsyncSession, tenant_id: UUID) -> TenantDataKey:
    """Create a new active data key; the previous one is retired (still decrypts)."""

    tenant = await _lock(db, tenant_id)
    state = _state(tenant.data_keys)
    previous = state.get("active_key_id")
    key = _new_key(tenant_id, state)
    if isinstance(previous, str) and previous in state["keys"]:
        state["keys"][previous].update(status="retired", retired_at=_now())
    await _store(db, tenant, state)
    return key


async def rewrap_keys(db: AsyncSession, tenant_id: UUID) -> int:
    """Re-wrap the company's keys with the active instance key; returns keys changed."""

    tenant = await _lock(db, tenant_id)
    state = _state(tenant.data_keys)
    active_instance = key_provider.get_active_key_id()
    changed = 0
    for key_id, entry in state["keys"].items():
        if "wrapped" not in entry or entry.get("wrap_key_id") == active_instance:
            continue
        key = _unwrap(tenant_id, key_id, entry)
        entry["wrapped"], entry["wrap_key_id"] = key_provider.wrap_data_key(
            key.raw, tenant_id=tenant_id, key_id=key_id, version=int(entry["version"])
        )
        changed += 1
    if changed:
        await _store(db, tenant, state)
    return changed


async def rewrap_all_tenants(session_factory: Callable[[], AsyncSession]) -> int:
    """Instance key rotation: re-wrap every company key still under an older instance key.

    Each company is re-wrapped in its own session/commit; a failing company is
    logged (by id only) and skipped so it cannot block the others.
    """

    active_instance = key_provider.get_active_key_id()
    async with session_factory() as db:
        rows = (await db.execute(select(Tenant.id, Tenant.data_keys))).all()
    pending = [
        row.id
        for row in rows
        if any(
            "wrapped" in e and e.get("wrap_key_id") != active_instance
            for e in _state(row.data_keys)["keys"].values()
        )
    ]
    changed = 0
    for tenant_id in pending:
        try:
            async with session_factory() as db:
                changed += await rewrap_keys(db, tenant_id)
                await db.commit()
        except Exception:  # noqa: BLE001 — isolate one company's failure
            logger.exception("company data key rewrap failed for tenant %s", tenant_id)
    return changed


async def legacy_01_closed(db: AsyncSession, tenant_id: UUID) -> bool:
    return bool((await _read_state(db, tenant_id)).get("legacy_01_closed_at"))


async def close_legacy_01(
    db: AsyncSession, tenant_id: UUID, has_legacy_rows: Callable[[AsyncSession], Awaitable[bool]]
) -> bool:
    """Set ``legacy_01_closed_at`` under the company row lock if no 0x01 rows remain."""

    tenant = await _lock(db, tenant_id)
    state = _state(tenant.data_keys)
    if state.get("legacy_01_closed_at"):
        return True
    if await has_legacy_rows(db):
        return False
    state["legacy_01_closed_at"] = _now()
    await _store(db, tenant, state)
    return True


async def reopen_legacy_01(db: AsyncSession, tenant_id: UUID) -> None:
    """A new 0x01 company row is being written (flag off): clear the closure marker."""

    raw = await db.scalar(select(Tenant.data_keys).where(Tenant.id == tenant_id))
    if not _state(raw).get("legacy_01_closed_at"):
        return
    tenant = await _lock(db, tenant_id)
    state = _state(tenant.data_keys)
    state.pop("legacy_01_closed_at", None)
    await _store(db, tenant, state)


async def destroy_tenant_keys(db: AsyncSession, tenant_id: UUID) -> int:
    """Crypto-shred all company data keys (permanent company deletion); returns keys destroyed.

    Unreadability is only as final as the database's backup/WAL/PITR retention:
    a restored pre-destroy snapshot still holds wrapped keys. Restored company
    key state must never be re-wrapped or reused; destroy again after restore.
    """

    tenant = await _lock(db, tenant_id)
    state = _state(tenant.data_keys)
    destroyed_at = _now()
    count = 0
    for entry in state["keys"].values():
        if entry.get("status") == "destroyed":
            continue
        entry.pop("wrapped", None)
        entry.pop("wrap_key_id", None)
        entry.update(status="destroyed", destroyed_at=destroyed_at)
        count += 1
    state["active_key_id"] = None
    state["destroyed_at"] = state.get("destroyed_at") or destroyed_at
    await _store(db, tenant, state)
    clear_unwrap_cache(tenant_id)
    return count
