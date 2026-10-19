"""F-09 ``f09_agent_memo``: widened proposal scope, memo switch backfill, reversibility."""

import importlib.util
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Connection, create_engine
from sqlalchemy.exc import IntegrityError

from alembic import op as alembic_op

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
WITH_MEMO, EMPTY_MEMO, NO_MEMO, SETTINGS_ROW = (uuid.uuid4() for _ in range(4))
PRIOR = (
    "CREATE TABLE agents (id CHAR(32) PRIMARY KEY)",
    "CREATE TABLE memory_proposals (id CHAR(32) PRIMARY KEY, scope VARCHAR(20) NOT NULL, "
    "CONSTRAINT ck_memory_proposals_scope CHECK (scope in ('user', 'agent')))",
    "CREATE TABLE agent_memory_settings (agent_id CHAR(32) PRIMARY KEY, "
    "memory_policy_override VARCHAR(20) NOT NULL DEFAULT 'inherit', "
    "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)",
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("f09_test", VERSIONS / "f09_agent_memo.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def lane(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[tuple[Connection, ModuleType]]:
    for agent_id, text in ((WITH_MEMO, "# Team\n"), (EMPTY_MEMO, "  \n"), (SETTINGS_ROW, "x")):
        (tmp_path / "agents" / str(agent_id)).mkdir(parents=True)
        (tmp_path / "agents" / str(agent_id) / "AGENTS.md").write_text(text)
    (tmp_path / "agents" / "not-a-uuid").mkdir()
    (tmp_path / "agents" / "not-a-uuid" / "AGENTS.md").write_text("ignored")
    (tmp_path / "agents" / str(uuid.uuid4())).mkdir()  # memo of an agent row that is gone
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        for statement in PRIOR:
            connection.exec_driver_sql(statement)
        for agent_id in (WITH_MEMO, EMPTY_MEMO, NO_MEMO, SETTINGS_ROW):
            connection.exec_driver_sql("INSERT INTO agents VALUES (?)", (agent_id.hex,))
        connection.exec_driver_sql(
            "INSERT INTO agent_memory_settings VALUES (?, 'off', '2026-01-01', '2026-01-01')",
            (SETTINGS_ROW.hex,),
        )
        monkeypatch.setattr(
            alembic_op, "_proxy", Operations(MigrationContext.configure(connection)), raising=False
        )
        module = _load()
        monkeypatch.setattr(module, "data_dir", lambda: tmp_path)
        yield connection, module


def test_revision_chain() -> None:
    module = _load()
    assert (module.revision, module.down_revision) == (
        "f09_agent_memo",
        "f11_credential_definition_hosts",
    )


def _settings(connection: Connection) -> dict[str, tuple]:
    rows = connection.exec_driver_sql(
        "SELECT agent_id, shared_memo_enabled, memory_policy_override FROM agent_memory_settings"
    )
    return {row[0]: (bool(row[1]), row[2]) for row in rows}


def test_upgrade_widens_the_scope_and_switches_on_agents_with_memo_content(lane) -> None:
    connection, module = lane
    module.upgrade()
    connection.exec_driver_sql("INSERT INTO memory_proposals VALUES ('p1', 'agent_memo')")
    with pytest.raises(IntegrityError):
        connection.exec_driver_sql("INSERT INTO memory_proposals VALUES ('p2', 'other')")
    assert _settings(connection) == {
        WITH_MEMO.hex: (True, "inherit"),
        SETTINGS_ROW.hex: (True, "off"),  # existing row kept, only the switch set
    }


def test_downgrade_restores_the_narrow_scope(lane) -> None:
    connection, module = lane
    module.upgrade()
    connection.exec_driver_sql("INSERT INTO memory_proposals VALUES ('p1', 'agent_memo')")
    module.downgrade()
    assert connection.exec_driver_sql("SELECT count(*) FROM memory_proposals").scalar() == 0
    with pytest.raises(IntegrityError):
        connection.exec_driver_sql("INSERT INTO memory_proposals VALUES ('p3', 'agent_memo')")
    columns = [
        row[1] for row in connection.exec_driver_sql("PRAGMA table_info(agent_memory_settings)")
    ]
    assert "shared_memo_enabled" not in columns
