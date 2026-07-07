from collections.abc import AsyncGenerator

import anyio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class ShieldedAsyncSession(AsyncSession):
    async def __aexit__(self, type_: object, value: object, traceback: object) -> None:
        await close_session_shielded(self)


# Pool knobs apply only to postgresql URLs — sqlite's NullPool/StaticPool
# rejects them (dev/test envs may point DATABASE_URL at sqlite).
_pool_kwargs: dict[str, int] = (
    {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout,
        "pool_recycle": settings.db_pool_recycle,
    }
    if settings.database_url.startswith("postgresql")
    else {}
)
engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True, **_pool_kwargs)
async_session = async_sessionmaker(engine, class_=ShieldedAsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    session = async_session()
    try:
        yield session
    finally:
        await close_session_shielded(session)


async def close_session_shielded(session: AsyncSession) -> None:
    with anyio.CancelScope(shield=True):
        await session.close()


def is_postgres(db: AsyncSession) -> bool:
    """True when the session's engine is Postgres.

    Fallback to ``False`` (= SQLite test path) when ``db.bind`` is
    ``None`` so the helper is safe to call from any service.
    """

    return db.bind is not None and db.bind.dialect.name == "postgresql"
