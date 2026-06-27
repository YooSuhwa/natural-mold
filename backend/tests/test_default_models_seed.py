"""Phase 0 — default model context_window seed + idempotent backfill."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.model import Model
from app.seed.default_models import (
    DEFAULT_MODELS,
    backfill_default_model_context_windows,
)


def test_all_default_models_carry_positive_context_window() -> None:
    """게이지 + 자동압축 임계값의 단일 source — seed에서 빠지면 둘 다 깨진다."""
    for model_data in DEFAULT_MODELS:
        cw = model_data.get("context_window")
        assert isinstance(cw, int) and cw > 0, f"{model_data['model_name']} missing context_window"


class TestBackfill:
    @pytest.mark.asyncio
    async def test_fills_null_context_window_for_seeded_model(self, db: AsyncSession) -> None:
        sample = DEFAULT_MODELS[0]
        db.add(
            Model(
                provider=sample["provider"],
                model_name=sample["model_name"],
                display_name=sample["display_name"],
                context_window=None,
            )
        )
        await db.commit()

        await backfill_default_model_context_windows(db)
        await db.commit()

        row = (
            await db.execute(select(Model).where(Model.model_name == sample["model_name"]))
        ).scalar_one()
        assert row.context_window == sample["context_window"]

    @pytest.mark.asyncio
    async def test_preserves_operator_customised_window(self, db: AsyncSession) -> None:
        sample = DEFAULT_MODELS[0]
        db.add(
            Model(
                provider=sample["provider"],
                model_name=sample["model_name"],
                display_name=sample["display_name"],
                context_window=4096,  # operator override — must not be clobbered
            )
        )
        await db.commit()

        await backfill_default_model_context_windows(db)
        await db.commit()

        row = (
            await db.execute(select(Model).where(Model.model_name == sample["model_name"]))
        ).scalar_one()
        assert row.context_window == 4096

    @pytest.mark.asyncio
    async def test_idempotent_second_run_is_noop(self, db: AsyncSession) -> None:
        sample = DEFAULT_MODELS[0]
        db.add(
            Model(
                provider=sample["provider"],
                model_name=sample["model_name"],
                display_name=sample["display_name"],
                context_window=None,
            )
        )
        await db.commit()

        await backfill_default_model_context_windows(db)
        await db.commit()
        await backfill_default_model_context_windows(db)
        await db.commit()

        row = (
            await db.execute(select(Model).where(Model.model_name == sample["model_name"]))
        ).scalar_one()
        assert row.context_window == sample["context_window"]
