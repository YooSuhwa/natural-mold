from uuid import UUID

import pytest
import sqlalchemy as sa

from app.migrations.org_authz_v1.contracts import M81_SCOPED, ORG, TENANT, TENANT_ONLY
from app.migrations.org_authz_v1.metadata import reflect_metadata
from app.migrations.org_authz_v1.settings import MigrationSettings


def test_scope_contract_has_exactly_twenty_scoped_and_three_tenant_only_tables() -> None:
    assert len(set(M81_SCOPED)) == len(M81_SCOPED) == 20
    assert TENANT_ONLY == ("models", "system_llm_settings", "audit_events")
    assert UUID("2dd14fba-c565-5cca-b356-08669f8d00a3") == TENANT
    assert UUID("634ca626-f90b-53a3-aaee-93b734df1b03") == ORG


def test_reflection_restores_frozen_uuid_semantics_on_sqlite() -> None:
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    users = sa.Table("users", metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    opaque = sa.Table("opaque", metadata, sa.Column("id", sa.String(), primary_key=True))
    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(users.insert().values(id=TENANT))
        connection.execute(opaque.insert().values(id="opaque-key"))
        reflected = reflect_metadata(connection)
        assert connection.scalar(sa.select(reflected.tables["users"].c.id)) == TENANT
        assert connection.scalar(sa.select(reflected.tables["opaque"].c.id)) == "opaque-key"
        assert isinstance(reflected.tables["users"].c.id.type, sa.Uuid)
    engine.dispose()


def test_migration_settings_reads_only_frozen_keys() -> None:
    class Restricted(dict[str, str]):
        def get(self, key: str, default: str = "") -> str:
            assert key in {"DEFAULT_TENANT_NAME", "DEFAULT_ORG_NAME", "TENANT_DEFAULT_LIMITS"}
            return super().get(key, default)

    settings = MigrationSettings.from_environ(Restricted(TENANT_DEFAULT_LIMITS='{"tokens": 5}'))
    assert settings.default_tenant_name == "기본 회사"
    assert settings.default_org_name == "기본 조직"
    assert settings.tenant_default_limits == {"tokens": 5}


@pytest.mark.parametrize("limits", ["[]", "null", "42", "not-json", '{"a":NaN}'])
def test_migration_settings_rejects_non_object_limits(limits: str) -> None:
    with pytest.raises(ValueError, match="validation error|Expecting value|Invalid JSON constant"):
        MigrationSettings.from_environ({"TENANT_DEFAULT_LIMITS": limits})
