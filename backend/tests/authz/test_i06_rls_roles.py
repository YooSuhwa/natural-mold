"""I-06: app/migration role separation and the production start-up role check."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.config import settings
from app.migrations.org_authz_v1 import rls_activation
from app.security import rls_role_check
from app.security.rls_role_check import RoleFacts, enforce_rls_role_safety, rls_role_issues

SAFE = RoleFacts(superuser=False, bypass_rls=False, owned=(), unforced=())
PG = "postgresql+asyncpg://moldy_app:x@db/moldy"


def test_safe_app_role_has_no_issues() -> None:
    assert rls_role_issues(SAFE) == []


@pytest.mark.parametrize(
    ("facts", "needle"),
    [
        (RoleFacts(True, False, (), ()), "superuser"),
        (RoleFacts(False, True, (), ()), "BYPASSRLS"),
        (RoleFacts(False, False, ("agents",), ()), "by membership) Moldy tables (agents)"),
        (RoleFacts(False, False, (), ("conversations",)), "not enabled and forced"),
        (RoleFacts(False, False, (), (), ("conversations",)), "tenant_boundary policy is missing"),
    ],
)
def test_each_unsafe_fact_is_reported(facts: RoleFacts, needle: str) -> None:
    (issue,) = rls_role_issues(facts)
    assert needle in issue


def test_long_table_lists_are_sampled() -> None:
    names = tuple(f"t{i}" for i in range(8))
    (issue,) = rls_role_issues(RoleFacts(False, False, names, ()))
    assert "(+3 more)" in issue and "t7" not in issue


def _patch(monkeypatch: pytest.MonkeyPatch, facts: RoleFacts) -> list[object]:
    reads: list[object] = []

    async def read(db: object) -> RoleFacts:
        reads.append(db)
        return facts

    monkeypatch.setattr(rls_role_check, "read_role_facts", read)
    return reads


async def test_check_is_skipped_when_rls_is_off_or_not_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads = _patch(monkeypatch, RoleFacts(True, True, (), ()))
    await enforce_rls_role_safety(settings.model_copy(update={"db_rls_enabled": False}))
    await enforce_rls_role_safety(
        settings.model_copy(update={"db_rls_enabled": True, "database_url": "sqlite+aiosqlite://"})
    )
    assert reads == []


async def test_production_refuses_to_start_with_an_unsafe_app_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, RoleFacts(False, True, ("agents",), ()))
    unsafe = settings.model_copy(
        update={"db_rls_enabled": True, "database_url": PG, "app_env": "production"}
    )
    with pytest.raises(RuntimeError, match="BYPASSRLS"):
        await enforce_rls_role_safety(unsafe)


async def test_non_production_only_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _patch(monkeypatch, RoleFacts(False, False, ("agents",), ()))
    await enforce_rls_role_safety(
        settings.model_copy(update={"db_rls_enabled": True, "database_url": PG, "app_env": "dev"})
    )
    assert "Moldy tables (agents)" in caplog.text


async def test_production_with_a_safe_role_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    reads = _patch(monkeypatch, SAFE)
    safe = settings.model_copy(
        update={"db_rls_enabled": True, "database_url": PG, "app_env": "production"}
    )
    await enforce_rls_role_safety(safe)
    assert len(reads) == 1


def _connection(current_user: str, *, superuser: bool = False, bypass: bool = False) -> MagicMock:
    connection = MagicMock()
    connection.scalar.return_value = current_user
    connection.execute.return_value.one.return_value = SimpleNamespace(
        rolsuper=superuser, rolbypassrls=bypass
    )
    return connection


def test_app_role_is_granted_only_when_roles_are_separated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "database_url", PG)
    monkeypatch.setattr(settings, "migration_database_url", "")
    assert rls_activation.app_role(_connection("moldy_owner")) is None
    monkeypatch.setattr(settings, "migration_database_url", "postgresql+asyncpg://owner:x@db/m")
    assert rls_activation.app_role(_connection("moldy_owner")) == "moldy_app"
    assert rls_activation.app_role(_connection("moldy_app")) is None


def test_enabling_rls_requires_a_bypassing_migration_role() -> None:
    with pytest.raises(rls_activation.RlsRoleError, match="BYPASSRLS"):
        rls_activation.require_rls_bypass(_connection("owner"))
    rls_activation.require_rls_bypass(_connection("owner", bypass=True))
    rls_activation.require_rls_bypass(_connection("postgres", superuser=True))


def test_owning_app_role_gets_the_exact_reassign_instruction() -> None:
    connection = MagicMock()
    connection.dialect = postgresql.dialect()
    connection.scalar.side_effect = [1, "moldy_owner"]
    connection.scalars.return_value = ["agents", "users"]
    with pytest.raises(rls_activation.RlsRoleError) as caught:
        rls_activation.require_app_role_not_owner(connection, "moldy_app")
    message = str(caught.value)
    assert "REASSIGN OWNED BY moldy_app TO moldy_owner;" in message
    assert "ALTER TABLE agents OWNER TO moldy_owner" in message


def test_missing_app_role_and_non_owner_cases() -> None:
    connection = MagicMock()
    connection.scalar.return_value = None
    with pytest.raises(rls_activation.RlsRoleError, match="does not exist"):
        rls_activation.require_app_role_not_owner(connection, "moldy_app")
    connection.scalar.return_value = 1
    connection.scalars.return_value = []
    rls_activation.require_app_role_not_owner(connection, "moldy_app")


def test_enable_refreshes_policies_before_enable_and_force(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    connection = _connection("owner", bypass=True)
    connection.dialect = postgresql.dialect()
    connection.execute.side_effect = lambda statement, *a: (
        order.append(str(statement))
        or (MagicMock(one=lambda: SimpleNamespace(rolsuper=False, rolbypassrls=True)))
    )
    monkeypatch.setattr(settings, "migration_database_url", "")
    monkeypatch.setattr(
        rls_activation, "install_policies", lambda _c: order.append("install") or ("agents",)
    )
    assert rls_activation.set_rls(connection, enabled=True) == ("agents",)
    install = order.index("install")
    assert order[install + 1 :] == [
        "ALTER TABLE agents ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE agents FORCE ROW LEVEL SECURITY",
    ]


def test_cli_refuses_enable_until_the_app_flag_is_on(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(settings, "migration_database_url", PG)
    monkeypatch.setattr(settings, "db_rls_enabled", False)
    assert rls_activation.main(["enable"]) == 2
    assert "DB_RLS_ENABLED=true" in capsys.readouterr().out
