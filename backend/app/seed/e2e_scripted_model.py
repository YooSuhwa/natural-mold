from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.model import Model

logger = logging.getLogger(__name__)

E2E_SCRIPTED_PROVIDER = "e2e_scripted"
E2E_SCRIPTED_MODEL_NAME = "document-artifact-scripted"
E2E_SCRIPTED_DISPLAY_NAME = "E2E Scripted Document Model"


async def seed_e2e_scripted_model(db: AsyncSession) -> Model | None:
    if settings.app_env == "production":
        logger.warning("skip seed_e2e_scripted_model: production environment")
        return None
    if not settings.e2e_scripted_model_enabled:
        return None

    model = (
        await db.execute(
            select(Model)
            .where(Model.provider == E2E_SCRIPTED_PROVIDER)
            .where(Model.model_name == E2E_SCRIPTED_MODEL_NAME)
            .limit(1)
        )
    ).scalar_one_or_none()

    if model is None:
        model = Model(
            provider=E2E_SCRIPTED_PROVIDER,
            model_name=E2E_SCRIPTED_MODEL_NAME,
            display_name=E2E_SCRIPTED_DISPLAY_NAME,
            is_default=False,
            is_visible=True,
            cost_per_input_token=Decimal("0"),
            cost_per_output_token=Decimal("0"),
            supports_function_calling=True,
            input_modalities=["text"],
            output_modalities=["text"],
            source="manual",
        )
        db.add(model)
        await db.flush()
        logger.info("seed_e2e_scripted_model: created %s", E2E_SCRIPTED_MODEL_NAME)
        return model

    model.display_name = E2E_SCRIPTED_DISPLAY_NAME
    model.is_visible = True
    model.supports_function_calling = True
    model.input_modalities = ["text"]
    model.output_modalities = ["text"]
    model.source = "manual"
    await db.flush()
    logger.info("seed_e2e_scripted_model: refreshed %s", E2E_SCRIPTED_MODEL_NAME)
    return model


__all__ = [
    "E2E_SCRIPTED_DISPLAY_NAME",
    "E2E_SCRIPTED_MODEL_NAME",
    "E2E_SCRIPTED_PROVIDER",
    "seed_e2e_scripted_model",
]
