from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.config import settings

if TYPE_CHECKING:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

_pool: AsyncConnectionPool | None = None
_checkpointer: AsyncPostgresSaver | None = None


def _pool_size_kwargs(
    *,
    min_size: int | None = None,
    max_size: int | None = None,
) -> dict[str, int]:
    pool_min_size = max(
        1,
        settings.checkpointer_pool_min_size if min_size is None else min_size,
    )
    pool_max_size = max(
        pool_min_size,
        settings.checkpointer_pool_max_size if max_size is None else max_size,
    )
    return {"min_size": pool_min_size, "max_size": pool_max_size}


async def init_checkpointer(
    conn_string: str,
    *,
    min_size: int | None = None,
    max_size: int | None = None,
) -> None:
    """앱 시작 시 checkpointer 초기화. lifespan에서 호출."""
    global _pool, _checkpointer

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

    if _pool is not None or _checkpointer is not None:
        raise RuntimeError("Checkpointer already initialized")
    pool_size_kwargs = _pool_size_kwargs(min_size=min_size, max_size=max_size)
    candidate_pool: AsyncConnectionPool = AsyncConnectionPool(
        conninfo=conn_string,
        open=False,
        min_size=pool_size_kwargs["min_size"],
        max_size=pool_size_kwargs["max_size"],
        kwargs={"autocommit": True, "prepare_threshold": 0},
    )
    try:
        await candidate_pool.open()
        candidate_checkpointer = AsyncPostgresSaver(
            conn=candidate_pool  # type: ignore[arg-type]  # Pool도 Conn 인터페이스 호환
        )
        await candidate_checkpointer.setup()
    except BaseException:  # noqa: BLE001 - ownership boundary must close on cancellation too
        try:
            await candidate_pool.close()
        except Exception:  # noqa: BLE001 - preserve the original setup/open failure
            logger.exception("Failed to close unsuccessful checkpointer candidate")
        raise
    _pool = candidate_pool
    _checkpointer = candidate_checkpointer
    logger.info(
        "Checkpointer initialized (PostgreSQL, pool_min=%s, pool_max=%s)",
        pool_size_kwargs["min_size"],
        pool_size_kwargs["max_size"],
    )


async def shutdown_checkpointer() -> None:
    """앱 종료 시 connection pool 정리. lifespan에서 호출."""
    global _pool, _checkpointer
    pool = _pool
    if pool:
        await pool.close()
    _pool = None
    _checkpointer = None
    logger.info("Checkpointer shut down")


def get_checkpointer() -> AsyncPostgresSaver:
    """checkpointer 싱글턴 반환. 초기화 전 호출 시 RuntimeError."""
    if _checkpointer is None:
        raise RuntimeError("Checkpointer not initialized. Call init_checkpointer() first.")
    return _checkpointer


async def delete_thread(thread_id: str) -> None:
    """thread의 모든 checkpoint 데이터를 삭제."""
    if _pool is None:
        return
    async with _pool.connection() as conn, conn.transaction():
        await conn.execute("DELETE FROM checkpoint_writes WHERE thread_id = %s", (thread_id,))
        await conn.execute("DELETE FROM checkpoint_blobs WHERE thread_id = %s", (thread_id,))
        await conn.execute("DELETE FROM checkpoints WHERE thread_id = %s", (thread_id,))
