"""H-08 ``h08_sso_hardening``: verified-only domain uniqueness, new columns, reversibility."""

import importlib.util
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Connection, create_engine, inspect
from sqlalchemy.exc import IntegrityError

from alembic import op as alembic_op

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
PRIOR = (
    "CREATE TABLE tenant_domains (id CHAR(32) PRIMARY KEY, tenant_id CHAR(32) NOT NULL, "
    "domain VARCHAR(253) NOT NULL, txt_token VARCHAR(64) NOT NULL, "
    "status VARCHAR(20) NOT NULL DEFAULT 'pending', "
    "CONSTRAINT uq_tenant_domains_domain UNIQUE (domain))",
    "CREATE TABLE sso_connections (id CHAR(32) PRIMARY KEY)",
    "CREATE TABLE refresh_tokens (id CHAR(32) PRIMARY KEY)",
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("h08_test", VERSIONS / "h08_sso_hardening.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def lane(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Connection, ModuleType]]:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        for statement in PRIOR:
            connection.exec_driver_sql(statement)
        monkeypatch.setattr(
            alembic_op, "_proxy", Operations(MigrationContext.configure(connection)), raising=False
        )
        yield connection, _load()


def _claim(connection: Connection, tenant: str, status: str = "pending") -> None:
    connection.exec_driver_sql(
        "INSERT INTO tenant_domains (id, tenant_id, domain, txt_token, status) "
        "VALUES (?, ?, 'corp.example', 't', ?)",
        (uuid.uuid4().hex, tenant, status),
    )


def test_pending_claims_compete_but_one_verified_claim_wins(lane) -> None:
    connection, module = lane
    module.upgrade()
    a, b = uuid.uuid4().hex, uuid.uuid4().hex
    _claim(connection, a)
    _claim(connection, b)
    with pytest.raises(IntegrityError), connection.begin_nested():
        _claim(connection, a)  # one claim per company and domain
    connection.exec_driver_sql(
        "UPDATE tenant_domains SET status='verified' WHERE tenant_id=?", (a,)
    )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.exec_driver_sql(
            "UPDATE tenant_domains SET status='verified' WHERE tenant_id=?", (b,)
        )
    columns = {c["name"] for c in inspect(connection).get_columns("sso_connections")}
    assert "trust_email_claim" in columns
    assert "auth_method" in {c["name"] for c in inspect(connection).get_columns("refresh_tokens")}


def test_downgrade_keeps_only_verified_claims(lane) -> None:
    connection, module = lane
    assert (module.revision, module.down_revision) == ("h08_sso_hardening", "h02_notifications")
    module.upgrade()
    _claim(connection, uuid.uuid4().hex, "verified")
    _claim(connection, uuid.uuid4().hex)
    module.downgrade()
    rows = connection.exec_driver_sql("SELECT status FROM tenant_domains").fetchall()
    assert rows == [("verified",)]
    assert "auth_method" not in {
        c["name"] for c in inspect(connection).get_columns("refresh_tokens")
    }
