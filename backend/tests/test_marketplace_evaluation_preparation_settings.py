from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.skills import service as skill_service
from tests.test_marketplace_install import _ensure_test_user, _make_published_skill_item


@pytest.mark.asyncio
async def test_marketplace_install_skips_llm_generation_when_evaluation_disabled(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    await _ensure_test_user(db)
    generator = AsyncMock()

    with (
        patch.object(skill_service.settings, "data_root", str(tmp_path)),
        patch.object(skill_service.settings, "skill_evaluation_enabled", False),
        patch(
            "app.services.skill_evaluation_set_preparation.generate_skill_smoke_eval_payload",
            generator,
        ),
    ):
        version_dir = tmp_path / "marketplace-versions" / "v1"
        item, _version = await _make_published_skill_item(
            db,
            storage_path=version_dir,
            requirements=None,
        )
        await db.commit()

        response = await client.post(
            f"/api/marketplace/items/{item.id}/install",
            json={"install_mode": "reuse_or_update"},
        )

    assert response.status_code == 201, response.text
    generator.assert_not_awaited()
