"""Tests for scripts/backfill_message_event_redaction.py (ADR-021 §9 Q5).

Covers the heuristics-only backfill: a stored event whose ``data`` contains a
heuristically-detectable secret gets masked in apply mode, an already-clean row is
left byte-identical, and a second pass is a no-op (idempotent).
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.message_event import MessageEvent

# scripts/ isn't a package on the import path — load the module by file path.
_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
_SCRIPT_PATH = _SCRIPTS_DIR / "backfill_message_event_redaction.py"
_spec = importlib.util.spec_from_file_location("backfill_message_event_redaction", _SCRIPT_PATH)
assert _spec and _spec.loader
backfill = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = backfill
_spec.loader.exec_module(backfill)


def _event(name: str, data: dict) -> dict:
    return {"id": f"{name}-1", "event": name, "data": data}


async def _make_row(db, *, events: list[dict]) -> uuid.UUID:
    row = MessageEvent(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),  # FK not enforced under aiosqlite test engine
        assistant_msg_id=uuid.uuid4().hex,
        events=events,
    )
    db.add(row)
    await db.flush()
    return row.id


async def _reload_events(db, row_id: uuid.UUID) -> list[dict]:
    db.expire_all()
    result = await db.execute(select(MessageEvent.events).where(MessageEvent.id == row_id))
    return result.scalar_one()


@pytest.mark.asyncio
async def test_backfill_masks_secret_and_leaves_clean_row_untouched(db) -> None:
    # A genuine residual leak: an OpenAI-style key the improved heuristics catch.
    leaky_events = [
        _event(
            "tool_call_result",
            {
                "tool_name": "http_request",
                "result": "called with api_key=sk-ABCDEFGHIJ0123456789XYZ done",
            },
        )
    ]
    # Already clean: no secret, must be returned byte-identical.
    clean_events = [_event("message_end", {"content": "all good", "usage": {"total_tokens": 12}})]

    leaky_id = await _make_row(db, events=leaky_events)
    clean_id = await _make_row(db, events=list(clean_events))

    report = await backfill.run_backfill(apply=True, session=db)

    # Reported one row needing change across the scanned tables.
    me_stats = next(s for s in report.stats if s.table == "message_events")
    assert me_stats.rows_changed == 1
    assert me_stats.rows_written == 1

    # The secret value is gone from the stored row and replaced with the marker.
    stored_leaky = await _reload_events(db, leaky_id)
    result_text = stored_leaky[0]["data"]["result"]
    assert "sk-ABCDEFGHIJ0123456789XYZ" not in result_text
    assert "<redacted>" in result_text

    # The clean row is untouched (idempotent on already-clean data).
    stored_clean = await _reload_events(db, clean_id)
    assert stored_clean == clean_events


@pytest.mark.asyncio
async def test_backfill_is_idempotent_second_pass_no_change(db) -> None:
    leaky_events = [
        _event(
            "tool_call_result",
            {"result": "Authorization: Bearer abcdef0123456789ABCDEF0123456789"},
        )
    ]
    await _make_row(db, events=leaky_events)

    first = await backfill.run_backfill(apply=True, session=db)
    assert first.total_rows_changed == 1
    assert first.total_rows_written == 1

    # Re-running over the now-clean data changes nothing.
    second = await backfill.run_backfill(apply=True, session=db)
    assert second.total_rows_changed == 0
    assert second.total_rows_written == 0


@pytest.mark.asyncio
async def test_dry_run_does_not_write(db) -> None:
    leaky_events = [_event("tool_call_result", {"result": "api_key=sk-ABCDEFGHIJ0123456789XYZ"})]
    row_id = await _make_row(db, events=list(leaky_events))

    report = await backfill.run_backfill(apply=False, session=db)

    # Dry-run detects the change but writes nothing.
    assert report.total_rows_changed == 1
    assert report.total_rows_written == 0
    stored = await _reload_events(db, row_id)
    assert stored == leaky_events
