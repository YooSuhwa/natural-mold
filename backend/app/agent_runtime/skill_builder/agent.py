from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.model_factory import create_chat_model
from app.services.system_credential_resolver import resolve_system_model


async def build_skill_builder_model(db: AsyncSession) -> BaseChatModel:
    resolved = await resolve_system_model(db, "text_primary")
    return create_chat_model(
        resolved.provider,
        resolved.model_name,
        api_key=resolved.api_key,
        base_url=resolved.base_url,
    )
