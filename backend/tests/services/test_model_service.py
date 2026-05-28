"""Regression tests for ``model_service.resolve_model``.

The ``Model`` table has no unique constraint on ``display_name`` /
``model_name`` / ``is_default``, so production data can hold duplicate rows
(e.g. the same Claude model registered both as ``anthropic`` and as
``openai_compatible``). The lookup must stay deterministic without raising
``MultipleResultsFound``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.model import Model
from app.services.model_service import resolve_model


def _make_model(
    *,
    provider: str,
    model_name: str,
    display_name: str,
    is_default: bool = False,
    created_at: datetime | None = None,
) -> Model:
    return Model(
        provider=provider,
        model_name=model_name,
        display_name=display_name,
        is_default=is_default,
        created_at=created_at or datetime.now(UTC).replace(tzinfo=None),
    )


@pytest.mark.asyncio
async def test_resolve_model_handles_duplicate_model_name_with_provider(db: AsyncSession):
    """``provider:model_name`` 입력은 provider 까지 매칭해 정확한 row 반환."""

    older = datetime(2026, 5, 26, 0, 0, 0)
    newer = datetime(2026, 5, 28, 0, 0, 0)
    db.add_all(
        [
            _make_model(
                provider="anthropic",
                model_name="claude-sonnet-4-6",
                display_name="Claude Sonnet 4.6",
                created_at=older,
            ),
            _make_model(
                provider="openai_compatible",
                model_name="claude-sonnet-4-6",
                display_name="Claude Sonnet 4.6 (Compatible)",
                is_default=True,
                created_at=newer,
            ),
        ]
    )
    await db.commit()

    resolved = await resolve_model(db, "openai_compatible:claude-sonnet-4-6", strict=True)
    assert resolved is not None
    assert resolved.provider == "openai_compatible"

    resolved = await resolve_model(db, "anthropic:claude-sonnet-4-6", strict=True)
    assert resolved is not None
    assert resolved.provider == "anthropic"


@pytest.mark.asyncio
async def test_resolve_model_tolerates_duplicate_display_name(db: AsyncSession):
    """display_name 중복 시 raise 대신 결정적 첫 행(``is_default`` 우선)을 반환."""

    older = datetime(2026, 5, 26, 0, 0, 0)
    newer = older + timedelta(days=2)
    db.add_all(
        [
            _make_model(
                provider="anthropic",
                model_name="claude-sonnet-4-6",
                display_name="Same Name",
                is_default=False,
                created_at=older,
            ),
            _make_model(
                provider="openai_compatible",
                model_name="claude-sonnet-4-6",
                display_name="Same Name",
                is_default=True,
                created_at=newer,
            ),
        ]
    )
    await db.commit()

    resolved = await resolve_model(db, "Same Name", strict=True)
    assert resolved is not None
    # is_default desc → openai_compatible row 가 먼저
    assert resolved.provider == "openai_compatible"


@pytest.mark.asyncio
async def test_resolve_model_default_fallback_tolerates_duplicates(db: AsyncSession):
    """``is_default=True`` row 가 여러 개여도 fallback 이 raise 하지 않음."""

    older = datetime(2026, 5, 26, 0, 0, 0)
    newer = older + timedelta(days=2)
    db.add_all(
        [
            _make_model(
                provider="anthropic",
                model_name="claude-a",
                display_name="A",
                is_default=True,
                created_at=older,
            ),
            _make_model(
                provider="openai",
                model_name="gpt-b",
                display_name="B",
                is_default=True,
                created_at=newer,
            ),
        ]
    )
    await db.commit()

    resolved = await resolve_model(db, "no-such-name", strict=False)
    assert resolved is not None
    # 결정적 순서: is_default desc + created_at asc → older row 선택
    assert resolved.provider == "anthropic"


@pytest.mark.asyncio
async def test_resolve_model_strict_returns_none_when_no_match(db: AsyncSession):
    """strict 모드에서 매칭 실패 시 fallback 없이 None."""

    db.add(
        _make_model(
            provider="anthropic",
            model_name="claude-sonnet-4-6",
            display_name="Claude Sonnet 4.6",
            is_default=True,
        )
    )
    await db.commit()

    assert await resolve_model(db, "no-such-provider:no-such-model", strict=True) is None


@pytest.mark.asyncio
async def test_resolve_model_falls_back_to_default_when_not_strict(db: AsyncSession):
    """비 strict 모드에서 매칭 실패 시 is_default row 로 fallback."""

    db.add_all(
        [
            _make_model(
                provider="anthropic",
                model_name="claude-sonnet-4-6",
                display_name="Claude Sonnet 4.6",
                is_default=True,
            ),
            _make_model(
                provider="openai",
                model_name="gpt-5.4",
                display_name="GPT-5.4",
                is_default=False,
            ),
        ]
    )
    await db.commit()

    resolved = await resolve_model(db, "", strict=False)
    assert resolved is not None
    assert resolved.provider == "anthropic"
