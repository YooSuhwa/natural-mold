"""I-09 (plan 15.2.1): production boot refuses plaintext or unverified internal hops."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.security.production_check import (
    collect_production_warnings,
    enforce_production_safety,
)

_SECRET = "a" * 48
_TLS = {
    "database_url": "postgresql+asyncpg://moldy:s3cretpw@db.internal:5432/moldy?ssl=verify-full",
    "database_url_sync": (
        "postgresql://moldy:s3cretpw@db.internal:5432/moldy"
        "?sslmode=verify-full&sslrootcert=/certs/ca.crt"
    ),
    "openfga_api_url": "https://openfga.internal:8080",
}


def _make(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "app_env": "production",
        "jwt_secret": _SECRET,
        "api_key_hash_secret": _SECRET,
        "cookie_secure": True,
        "allow_first_user_as_admin": False,
        "cors_allowed_origins": "https://app.example.com",
        "encryption_keys": "00" * 32,
        "internal_plaintext_hosts": "",
        "vault_url": "",
        "migration_database_url": "",
        "openfga_ca_cert": "",
        "vault_ca_cert": "",
        **_TLS,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


def test_verified_tls_everywhere_boots() -> None:
    assert collect_production_warnings(_make()) == []
    enforce_production_safety(_make())


def test_plaintext_defaults_refuse_production_boot() -> None:
    settings = _make(
        database_url="postgresql+asyncpg://moldy:s3cretpw@postgres:5432/moldy",
        database_url_sync="postgresql://moldy:s3cretpw@postgres:5432/moldy",
        openfga_api_url="http://openfga:8080",
    )
    with pytest.raises(RuntimeError) as exc:
        enforce_production_safety(settings)
    message = str(exc.value)
    for name in ("DATABASE_URL connects", "DATABASE_URL_SYNC connects", "OPENFGA_API_URL"):
        assert name in message
    assert "ssl=verify-full" in message and "sslmode=verify-full" in message
    assert "INTERNAL_PLAINTEXT_HOSTS" in message
    assert "s3cretpw" not in message  # host:port only, never credentials


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        ({"database_url": "postgresql+asyncpg://u:p@db.internal/m?ssl=require"}, "'require'"),
        ({"database_url": "postgresql+asyncpg://u:p@db.internal/m?ssl=verify-ca"}, "'verify-ca'"),
        ({"database_url": "postgresql+asyncpg://u:p@db.internal/m?ssl=disable"}, "'disable'"),
        ({"database_url_sync": "postgresql://u:p@db.internal/m?sslmode=prefer"}, "'prefer'"),
        ({"migration_database_url": "postgresql+asyncpg://u:p@db.internal/m"}, "without TLS"),
    ],
)
def test_verification_disabled_modes_are_refused(override: dict[str, object], fragment: str):
    issues = collect_production_warnings(_make(**override))
    assert len(issues) == 1
    assert fragment in issues[0] and "verify-full" in issues[0]


def test_only_listed_hosts_may_stay_plaintext() -> None:
    settings = _make(
        internal_plaintext_hosts=" Postgres , openfga ",
        database_url="postgresql+asyncpg://u:p@postgres:5432/moldy",
        database_url_sync="postgresql://u:p@postgres:5432/moldy?sslmode=disable",
        openfga_api_url="http://OpenFGA:8080",
        migration_database_url="postgresql+asyncpg://u:p@other-db:5432/moldy",
    )
    issues = collect_production_warnings(settings)
    assert len(issues) == 1
    assert issues[0].startswith("MIGRATION_DATABASE_URL connects to other-db:5432")


@pytest.mark.parametrize(
    ("vault_url", "flagged"),
    [("", False), ("https://vault.internal:8200", False), ("http://vault.internal:8200", True)],
)
def test_vault_url_must_be_https_when_set(vault_url: str, flagged: bool) -> None:
    issues = collect_production_warnings(_make(vault_url=vault_url))
    assert any("VAULT_URL" in msg for msg in issues) is flagged


def test_ca_bundle_paths_must_exist(tmp_path: Path) -> None:
    ca = tmp_path / "ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\n")
    assert collect_production_warnings(_make(openfga_ca_cert=str(ca))) == []
    issues = collect_production_warnings(_make(vault_ca_cert=str(tmp_path / "missing.crt")))
    assert issues == [
        f"VAULT_CA_CERT points to a missing file ({tmp_path / 'missing.crt'}). "
        "Mount the CA bundle there."
    ]


def test_sqlite_is_not_an_internal_network_hop() -> None:
    assert collect_production_warnings(_make(database_url="sqlite+aiosqlite:///./x.db")) == []


def test_dev_defaults_only_warn(caplog: pytest.LogCaptureFixture) -> None:
    settings = _make(app_env="dev", openfga_api_url="http://openfga:8080")
    with caplog.at_level("WARNING", logger="app.security.production_check"):
        enforce_production_safety(settings)
    assert "OPENFGA_API_URL" in " ".join(r.message for r in caplog.records)


@pytest.mark.parametrize("url", ["postgres://u:p@db.internal/m", "cockroachdb://u:p@db.internal/m"])
def test_every_non_sqlite_scheme_is_checked(url: str) -> None:
    issues = collect_production_warnings(_make(database_url_sync=url))
    assert len(issues) == 1 and "DATABASE_URL_SYNC connects to db.internal" in issues[0]


@pytest.mark.parametrize(
    "query", ["host=db.other", "hostaddr=10.0.0.9", "host=/run/pg&hostaddr=10.0.0.9"]
)
def test_host_overrides_void_the_plaintext_exemption(query: str) -> None:
    settings = _make(
        internal_plaintext_hosts="postgres",
        database_url=f"postgresql+asyncpg://u:p@postgres:5432/moldy?{query}",
    )
    issues = collect_production_warnings(settings)
    assert len(issues) == 1 and "does not apply to URLs with host/hostaddr" in issues[0]


@pytest.mark.parametrize(
    ("override", "flagged"),
    [
        ({"database_url": "postgresql+asyncpg://u:p@/moldy?host=/run/postgresql"}, False),
        ({"database_url_sync": "postgresql://u:p@/moldy?host=/var/run/postgresql"}, False),
        ({"database_url_sync": "postgresql://u:p@/moldy?host=db.internal"}, True),
        ({"database_url_sync": "postgresql://u:p@db.internal/moldy?host=/run/pg"}, True),
    ],
)
def test_unix_socket_urls_need_no_tls(override: dict[str, object], flagged: bool) -> None:
    assert bool(collect_production_warnings(_make(**override))) is flagged
