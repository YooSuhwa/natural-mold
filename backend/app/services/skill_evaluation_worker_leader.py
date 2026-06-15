from __future__ import annotations

import logging
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.database import engine

logger = logging.getLogger(__name__)

_SKILL_EVALUATION_WORKER_LOCK_ID: Final = 0x4D4F4C445945564C
_leader_connection: AsyncConnection | None = None


async def try_acquire_skill_evaluation_worker_leader() -> bool:
    global _leader_connection
    if _leader_connection is not None:
        return True
    if engine.dialect.name != "postgresql":
        logger.info(
            "Skill evaluation worker leader lock skipped for dialect=%s",
            engine.dialect.name,
        )
        return True

    conn = await engine.connect()
    acquired = bool(
        (
            await conn.execute(
                text("select pg_try_advisory_lock(:lock_id)"),
                {"lock_id": _SKILL_EVALUATION_WORKER_LOCK_ID},
            )
        ).scalar()
    )
    if not acquired:
        await conn.close()
        logger.warning("Skill evaluation worker leader lock not acquired; skipping worker")
        return False
    _leader_connection = conn
    logger.info("Skill evaluation worker leader lock acquired")
    return True


async def release_skill_evaluation_worker_leader() -> None:
    global _leader_connection
    conn = _leader_connection
    _leader_connection = None
    if conn is None:
        return
    try:
        if engine.dialect.name == "postgresql":
            await conn.execute(
                text("select pg_advisory_unlock(:lock_id)"),
                {"lock_id": _SKILL_EVALUATION_WORKER_LOCK_ID},
            )
    finally:
        await conn.close()
