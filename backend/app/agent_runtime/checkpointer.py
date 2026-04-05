from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

_pool: AsyncConnectionPool | None = None
_checkpointer: AsyncPostgresSaver | None = None


async def init_checkpointer(conn_string: str) -> None:
    """앱 시작 시 checkpointer 초기화. lifespan에서 호출."""
    global _pool, _checkpointer

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

    _pool = AsyncConnectionPool(
        conninfo=conn_string,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0},
    )
    await _pool.open()
    _checkpointer = AsyncPostgresSaver(conn=_pool)
    await _checkpointer.setup()
    logger.info("Checkpointer initialized (PostgreSQL)")


async def shutdown_checkpointer() -> None:
    """앱 종료 시 connection pool 정리. lifespan에서 호출."""
    global _pool, _checkpointer
    if _pool:
        await _pool.close()
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
