from __future__ import annotations

from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.model_factory import create_chat_model
from app.services.system_credential_resolver import resolve_system_model


@dataclass(frozen=True, slots=True)
class SkillBuilderChatModel:
    model: BaseChatModel
    model_name: str


async def build_skill_builder_chat_model(db: AsyncSession) -> SkillBuilderChatModel:
    resolved = await resolve_system_model(db, "text_primary")
    return SkillBuilderChatModel(
        model=create_chat_model(
            resolved.provider,
            resolved.model_name,
            api_key=resolved.api_key,
            base_url=resolved.base_url,
        ),
        model_name=resolved.model_name,
    )


async def build_skill_builder_model(db: AsyncSession) -> BaseChatModel:
    return (await build_skill_builder_chat_model(db)).model
