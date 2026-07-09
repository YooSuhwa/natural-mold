"""스킬 빌더 테스트 공용 셋업.

start v2가 히든 빌더 에이전트를 lazy-seed하면서 ``models`` 카탈로그 행이
필요해졌다 — system LLM 설정과 Model row를 한 번에 만들어, 모듈마다 복제된
헬퍼가 서로 어긋나는 드리프트를 막는다 (공유 mock 규칙과 동일 취지).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.model import Model
from app.models.system_llm_setting import SystemLlmSetting

SYSTEM_MODEL_NAME = "gpt-5.4"


async def configure_system_llm(db: AsyncSession) -> None:
    """text_primary system LLM + 매칭되는 models 카탈로그 행을 구성한다."""

    credential = await credential_service.create(
        db,
        user_id=None,
        definition_key="openai",
        name="builder-key",
        data={"api_key": "sk-test"},
        is_system=True,
    )
    db.add(
        SystemLlmSetting(
            role="text_primary",
            credential_id=credential.id,
            model_name=SYSTEM_MODEL_NAME,
        )
    )
    db.add(
        Model(
            provider="openai",
            model_name=SYSTEM_MODEL_NAME,
            display_name="GPT-5.4",
        )
    )
    await db.commit()
