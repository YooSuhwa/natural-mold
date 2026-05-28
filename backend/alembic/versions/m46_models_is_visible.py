"""M46: ``models.is_visible`` — hide non-openai_compatible providers by default.

Revision ID: m46_models_is_visible
Revises: m45_system_llm_settings
Create Date: 2026-05-28

사내 환경에서는 당분간 ``openai_compatible`` provider 한 종류로만 운영하지만
시드로 들어온 ``anthropic`` / ``openai`` / ``google`` 모델이 모델 페이지와
에이전트 생성 셀렉터에서 그대로 노출되어 혼선을 일으킨다. 모델 row 자체는
나중에 다시 켤 가능성이 있으므로 삭제하지 않고 visibility 플래그로 가린다.

마이그레이션은 ``is_visible BOOL NOT NULL DEFAULT true`` 컬럼을 추가하고, 기존
row 중 ``provider <> 'openai_compatible'`` 인 것을 일괄 ``false`` 로 내린다.
숨겨지는 row 가 우연히 ``is_default=true`` 였다면 default 도 함께 해제해
"기본 모델이 숨겨져 있다"는 모순 상태를 막는다.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m46_models_is_visible"
down_revision = "m45_system_llm_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "models",
        sa.Column(
            "is_visible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )

    bind = op.get_bind()
    bind.execute(
        sa.text("UPDATE models SET is_visible = false WHERE provider <> 'openai_compatible'")
    )
    # 기본 모델이 숨김 처리되면 모델 셀렉터가 비어버린다. 두 플래그가
    # 함께 true 인 상태만 유효하도록 default 를 함께 해제.
    bind.execute(
        sa.text(
            "UPDATE models SET is_default = false WHERE is_default = true AND is_visible = false"
        )
    )


def downgrade() -> None:
    op.drop_column("models", "is_visible")
