from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_provider import LLMProvider
from app.models.model import Model
from app.schemas.llm_provider import ProviderCreate, ProviderUpdate
from app.services.encryption import decrypt_api_key, encrypt_api_key


async def list_providers(db: AsyncSession) -> list[dict]:
    """List all providers with model_count and has_api_key."""
    stmt = (
        select(
            LLMProvider,
            func.count(Model.id).label("model_count"),
        )
        .outerjoin(Model, Model.provider_id == LLMProvider.id)
        .group_by(LLMProvider.id)
        .order_by(LLMProvider.name)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            **{c.key: getattr(row[0], c.key) for c in LLMProvider.__table__.columns},
            "has_api_key": row[0].api_key_encrypted is not None,
            "model_count": row[1],
        }
        for row in rows
    ]


async def get_provider(db: AsyncSession, provider_id: uuid.UUID) -> LLMProvider | None:
    result = await db.execute(select(LLMProvider).where(LLMProvider.id == provider_id))
    return result.scalar_one_or_none()


async def create_provider(db: AsyncSession, data: ProviderCreate) -> LLMProvider:
    provider = LLMProvider(
        name=data.name,
        provider_type=data.provider_type,
        base_url=data.base_url,
        api_key_encrypted=encrypt_api_key(data.api_key) if data.api_key else None,
    )
    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    return provider


_UPDATABLE_FIELDS = {"name", "base_url"}


async def update_provider(
    db: AsyncSession, provider_id: uuid.UUID, data: ProviderUpdate
) -> LLMProvider | None:
    provider = await get_provider(db, provider_id)
    if not provider:
        return None
    update_data = data.model_dump(exclude_unset=True)
    if "api_key" in update_data:
        key = update_data.pop("api_key")
        provider.api_key_encrypted = encrypt_api_key(key) if key else None
    for key, value in update_data.items():
        if key in _UPDATABLE_FIELDS:
            setattr(provider, key, value)
    await db.commit()
    await db.refresh(provider)
    return provider


async def delete_provider(db: AsyncSession, provider_id: uuid.UUID) -> tuple[bool, int]:
    """Delete a provider. Returns (deleted, model_count)."""
    provider = await get_provider(db, provider_id)
    if not provider:
        return False, 0
    # Count connected models before deletion
    count_result = await db.execute(
        select(func.count(Model.id)).where(Model.provider_id == provider_id)
    )
    model_count = count_result.scalar_one()
    await db.delete(provider)
    await db.commit()
    return True, model_count


async def get_provider_with_count(db: AsyncSession, provider_id: uuid.UUID) -> dict | None:
    """Get a single provider with model_count."""
    stmt = (
        select(
            LLMProvider,
            func.count(Model.id).label("model_count"),
        )
        .outerjoin(Model, Model.provider_id == LLMProvider.id)
        .where(LLMProvider.id == provider_id)
        .group_by(LLMProvider.id)
    )
    row = (await db.execute(stmt)).first()
    if not row:
        return None
    return {
        **{c.key: getattr(row[0], c.key) for c in LLMProvider.__table__.columns},
        "has_api_key": row[0].api_key_encrypted is not None,
        "model_count": row[1],
    }


def get_decrypted_api_key(provider: LLMProvider) -> str | None:
    """Decrypt API key from provider. Returns None if no key stored."""
    if not provider.api_key_encrypted:
        return None
    return decrypt_api_key(provider.api_key_encrypted)


async def load_all_provider_api_keys(db: AsyncSession) -> dict[str, str | None]:
    """모든 활성 프로바이더의 복호화된 API 키를 조회한다."""
    result = await db.execute(
        select(LLMProvider).where(LLMProvider.is_active.is_(True))
    )
    keys: dict[str, str | None] = {}
    for p in result.scalars():
        if p.api_key_encrypted:
            keys[p.provider_type] = decrypt_api_key(p.api_key_encrypted)
    return keys
