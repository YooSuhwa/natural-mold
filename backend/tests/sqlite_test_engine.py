"""The shared in-memory SQLite engine of the unit suite.

A plain ``sqlite+aiosqlite://`` engine keeps the whole database in its single
StaticPool connection. When a task is cancelled mid-statement SQLAlchemy
invalidates that connection, the pool opens a new one — and with it a new, empty
database: every later test of the worker failed with ``no such table`` (PR #314 CI).

The database is a named shared-cache in-memory database instead, held open by an
anchor connection for the life of the process, so a replaced connection reattaches
to the same tables and rows — as a pooled PostgreSQL connection would. Each
replacement is logged with the test that caused it.
"""

from __future__ import annotations

import logging
import os
import sqlite3

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

logger = logging.getLogger(__name__)
_NAME = f"moldy-unit-{os.getpid()}"
_anchor = sqlite3.connect(f"file:{_NAME}?mode=memory&cache=shared", uri=True)

engine = create_async_engine(
    f"sqlite+aiosqlite:///file:{_NAME}?mode=memory&cache=shared&uri=true",
    echo=False,
    poolclass=StaticPool,
)
TestSession = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
_connects = 0


@event.listens_for(engine.sync_engine, "connect")
def _log_replaced_connection(_dbapi_connection: object, _record: object) -> None:
    global _connects
    _connects += 1
    if _connects > 1:
        logger.warning(
            "unit-test SQLite connection replaced (a task was cancelled mid-statement?) test=%s",
            os.environ.get("PYTEST_CURRENT_TEST", "?"),
        )
