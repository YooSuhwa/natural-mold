from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session


def is_postgres(db: AsyncSession) -> bool:
    """True when the session's engine is Postgres.

    Fallback to ``False`` (= SQLite test path) when ``db.bind`` is
    ``None`` so the helper is safe to call from any service.
    """

    return db.bind is not None and db.bind.dialect.name == "postgresql"
