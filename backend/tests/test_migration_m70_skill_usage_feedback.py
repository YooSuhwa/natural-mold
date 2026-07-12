"""m70(skill usage ledger + human feedback + run.usage) 마이그레이션 검증.

m64/m68 테스트와 동일한 sqlite 라운드트립 방식 — 리비전 체인과 업/다운
그레이드 실행 가능성만 가드한다 (실배포는 PostgreSQL).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import inspect

_VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"
_FILENAME = "m70_skill_usage_and_feedback.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"{_FILENAME.removesuffix('.py')}_test_load",
        _VERSIONS / _FILENAME,
    )
    assert spec is not None
    loader = spec.loader
    assert loader is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_m70_revision_chain() -> None:
    mod = _load_module()
    assert mod.revision == "m70_skill_usage_and_feedback"
    assert mod.down_revision == "m69_skill_builder_sessions_v2"
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)


@pytest.mark.asyncio
async def test_m70_upgrade_downgrade_roundtrip_sqlite() -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine

    from alembic import op as alembic_op

    mod = _load_module()
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.exec_driver_sql("CREATE TABLE users (id TEXT PRIMARY KEY)")
            conn.exec_driver_sql("CREATE TABLE skills (id TEXT PRIMARY KEY)")
            conn.exec_driver_sql("CREATE TABLE conversations (id TEXT PRIMARY KEY)")
            conn.exec_driver_sql("CREATE TABLE agents (id TEXT PRIMARY KEY)")
            conn.exec_driver_sql("CREATE TABLE skill_evaluation_runs (id TEXT PRIMARY KEY)")

            ctx = MigrationContext.configure(conn)
            alembic_op._proxy = Operations(ctx)

            mod.upgrade()
            inspector = inspect(conn)
            table_names = set(inspector.get_table_names())
            assert {
                "skill_usage_events",
                "skill_feedbacks",
                "skill_evaluation_case_feedbacks",
            } <= table_names

            run_columns = {
                column["name"] for column in inspector.get_columns("skill_evaluation_runs")
            }
            assert "usage" in run_columns

            usage_indexes = {index["name"] for index in inspector.get_indexes("skill_usage_events")}
            assert "ix_skill_usage_events_skill_created" in usage_indexes
            assert "ix_skill_usage_events_evaluation_run" in usage_indexes

            feedback_uniques = {
                constraint["name"]
                for constraint in inspector.get_unique_constraints("skill_feedbacks")
            }
            assert "uq_skill_feedback_skill_user" in feedback_uniques
            case_uniques = {
                constraint["name"]
                for constraint in inspector.get_unique_constraints(
                    "skill_evaluation_case_feedbacks"
                )
            }
            assert "uq_skill_eval_case_feedback_run_user_case" in case_uniques

            mod.downgrade()
            inspector = inspect(conn)
            table_names = set(inspector.get_table_names())
            assert "skill_usage_events" not in table_names
            assert "skill_feedbacks" not in table_names
            assert "skill_evaluation_case_feedbacks" not in table_names
            run_columns = {
                column["name"] for column in inspector.get_columns("skill_evaluation_runs")
            }
            assert "usage" not in run_columns
    finally:
        engine.dispose()
