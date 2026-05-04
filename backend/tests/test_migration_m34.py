"""m34 alembic migration — round-trip + SQLite batch_alter_table 회귀 가드.

W3-out M2 — ``status`` + ``updated_at`` 컬럼 추가 + ``idx_message_events_status``
인덱스 + CHECK 제약. SQLite는 ALTER TABLE DROP CONSTRAINT/COLUMN 미지원이라
downgrade 가 ``op.batch_alter_table`` 우회 없이는 깨진다. 본 테스트가 그
회귀 가드.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import inspect

from app.database import Base

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "m34_message_events_streaming_status.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "m34_message_events_streaming_status_test_load", _MIGRATION
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_module_imports_with_expected_revision_chain() -> None:
    mod = _load_module()
    assert mod.revision == "m34_message_events_status"
    assert mod.down_revision == "m33_add_linked_message_ids"
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)


def test_metadata_includes_status_and_updated_at_columns() -> None:
    table = Base.metadata.tables.get("message_events")
    assert table is not None
    assert "status" in table.columns
    assert "updated_at" in table.columns


@pytest.mark.asyncio
async def test_upgrade_downgrade_roundtrip_sqlite() -> None:
    """``upgrade`` → ``downgrade`` → ``upgrade`` against in-memory SQLite.

    핵심 회귀 가드: SQLite 는 ``ALTER TABLE DROP CONSTRAINT`` 와
    ``ALTER TABLE DROP COLUMN`` 을 native 로 지원하지 않으므로, downgrade
    가 ``batch_alter_table`` 로 우회하지 않으면 OperationalError 가 난다.
    """
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine

    mod = _load_module()
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as conn:
            # m33 상태 — message_events 테이블 + linked_message_ids 까지.
            # m34 가 추가하는 status / updated_at 은 아직 없음.
            conn.exec_driver_sql(
                "CREATE TABLE message_events ("
                "  id TEXT PRIMARY KEY,"
                "  conversation_id TEXT NOT NULL,"
                "  assistant_msg_id TEXT NOT NULL UNIQUE,"
                "  events JSON NOT NULL,"
                "  last_event_id TEXT,"
                "  linked_message_ids JSON,"
                "  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                "  completed_at TIMESTAMP"
                ")"
            )
            ctx = MigrationContext.configure(conn)
            op = Operations(ctx)
            from alembic import op as alembic_op

            alembic_op._proxy = op  # type: ignore[attr-defined]

            # 1) upgrade — status + updated_at + index 추가
            mod.upgrade()
            inspector = inspect(conn)
            cols = {c["name"] for c in inspector.get_columns("message_events")}
            assert "status" in cols
            assert "updated_at" in cols
            indexes = {ix["name"] for ix in inspector.get_indexes("message_events")}
            assert "idx_message_events_status" in indexes

            # 2) downgrade — status, updated_at, index, CHECK 모두 제거.
            #    batch_alter_table 우회가 동작 안 하면 여기서 OperationalError.
            mod.downgrade()
            inspector = inspect(conn)
            cols = {c["name"] for c in inspector.get_columns("message_events")}
            assert "status" not in cols
            assert "updated_at" not in cols
            indexes = {ix["name"] for ix in inspector.get_indexes("message_events")}
            assert "idx_message_events_status" not in indexes

            # 3) re-upgrade — m34 가 멱등하게 다시 적용되는지
            mod.upgrade()
            inspector = inspect(conn)
            cols = {c["name"] for c in inspector.get_columns("message_events")}
            assert "status" in cols
            assert "updated_at" in cols
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_upgrade_preserves_existing_rows_with_default_status() -> None:
    """m33 이전 row(status 컬럼 없음) → upgrade 후 status='completed' 자동 채움.

    DEFAULT 'completed' NOT NULL 의 backfill 동작 검증. PoC 에서는 message_events
    가 빈 상태이지만, 운영 DB에 존재하는 m33 이전 row 의 회귀 신호로 활용.
    """
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, text

    mod = _load_module()
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE message_events ("
                "  id TEXT PRIMARY KEY,"
                "  conversation_id TEXT NOT NULL,"
                "  assistant_msg_id TEXT NOT NULL UNIQUE,"
                "  events JSON NOT NULL,"
                "  last_event_id TEXT,"
                "  linked_message_ids JSON,"
                "  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                "  completed_at TIMESTAMP"
                ")"
            )
            # m33 시점 row 1건 시드
            conn.exec_driver_sql(
                "INSERT INTO message_events "
                "(id, conversation_id, assistant_msg_id, events) "
                "VALUES ('e1', 'c1', 'msg-1', '[]')"
            )
            conn.commit()

            ctx = MigrationContext.configure(conn)
            op = Operations(ctx)
            from alembic import op as alembic_op

            alembic_op._proxy = op  # type: ignore[attr-defined]

            mod.upgrade()

            row = conn.execute(
                text("SELECT status, updated_at FROM message_events WHERE id='e1'")
            ).first()
            assert row is not None
            assert row[0] == "completed"  # DEFAULT backfill
            assert row[1] is not None  # updated_at server_default(now)
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_check_constraint_rejects_invalid_status() -> None:
    """``status IN ('streaming','completed','failed')`` CHECK 제약이 적용되는지."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine
    from sqlalchemy.exc import IntegrityError

    mod = _load_module()
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE message_events ("
                "  id TEXT PRIMARY KEY,"
                "  conversation_id TEXT NOT NULL,"
                "  assistant_msg_id TEXT NOT NULL UNIQUE,"
                "  events JSON NOT NULL,"
                "  last_event_id TEXT,"
                "  linked_message_ids JSON,"
                "  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                "  completed_at TIMESTAMP"
                ")"
            )
            ctx = MigrationContext.configure(conn)
            op = Operations(ctx)
            from alembic import op as alembic_op

            alembic_op._proxy = op  # type: ignore[attr-defined]

            mod.upgrade()

            # 유효한 status 는 OK
            conn.exec_driver_sql(
                "INSERT INTO message_events "
                "(id, conversation_id, assistant_msg_id, events, status) "
                "VALUES ('e1', 'c1', 'msg-1', '[]', 'streaming')"
            )
            # 유효하지 않은 status 는 거부
            with pytest.raises(IntegrityError):
                conn.exec_driver_sql(
                    "INSERT INTO message_events "
                    "(id, conversation_id, assistant_msg_id, events, status) "
                    "VALUES ('e2', 'c1', 'msg-2', '[]', 'invalid')"
                )
    finally:
        engine.dispose()
