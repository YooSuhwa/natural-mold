"""Credential key rotation — re-encrypt rows whose ``key_id`` is stale (BE-S9).

Business logic moved out of ``app.scheduler``; the scheduler keeps a
same-named wrapper so the persisted APScheduler job reference
(``app.scheduler:rotate_credentials_to_active_key``) stays valid and the
test patch surface (``app.scheduler.async_session`` / ``_ROTATION_BATCH``)
is injected at call time.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.credential import Credential
from app.security.key_provider import get_active_key_id

logger = logging.getLogger(__name__)


async def rotate_credentials_to_active_key(
    *,
    session_factory: Callable[[], AsyncSession],
    batch_size: int,
) -> int:
    """Re-encrypt every credential whose ``key_id`` differs from the active key.

    Iterates in pages of ``batch_size`` so a large backlog doesn't OOM
    a single transaction. Each row writes a ``rotate`` audit log; failures
    log+continue so a single bad row can't stall the rotation.
    """

    active_key_id = get_active_key_id()
    rotated = 0
    # Rows that failed re-encryption keep their stale key_id, so without an
    # exclusion a batch of >=batch_size persistent failures would be
    # re-fetched forever (the len(rows) < batch guard never trips). Excluding
    # failed ids guarantees progress: every iteration either rotates a row
    # (drops out via key_id) or marks it failed (drops out via notin_).
    failed_ids: set[uuid.UUID] = set()

    while True:
        async with session_factory() as db:
            stmt = select(Credential).where(Credential.key_id != active_key_id)
            if failed_ids:
                stmt = stmt.where(Credential.id.notin_(failed_ids))
            result = await db.execute(stmt.limit(batch_size))
            rows = list(result.scalars().all())
            if not rows:
                break
            for cred in rows:
                try:
                    await credential_service.re_encrypt_with_active_key(db, cred)
                    rotated += 1
                except Exception:  # noqa: BLE001 — keep rotation moving
                    failed_ids.add(cred.id)
                    logger.exception("credential %s rotation failed; will retry next run", cred.id)
            await db.commit()
        if len(rows) < batch_size:
            break
    if failed_ids:
        logger.warning(
            "credential rotation finished with %d failed rows; they will be retried next run",
            len(failed_ids),
        )
    return rotated
