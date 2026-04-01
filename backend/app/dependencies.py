import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session


@dataclass
class CurrentUser:
    id: uuid.UUID
    email: str
    name: str


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session


async def get_current_user() -> CurrentUser:
    """Mock user for PoC. Replace with real auth later."""
    return CurrentUser(
        id=uuid.UUID(settings.mock_user_id),
        email=settings.mock_user_email,
        name=settings.mock_user_name,
    )
