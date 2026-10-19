"""I-09 TLS lane: real Postgres/OpenFGA over verified TLS (plan 15.2.1, 20.3 "TLS 설정 실행").

Run with ``bash scripts/run-internal-tls-lane.sh`` (Docker + the
docker-compose.tls.yml overlay); it exports the ``I09_TLS_*`` variables. The
module skips everywhere else: it needs its own TLS stack, not the shared
PostgreSQL integration lanes, so it carries no ``integration`` marker.
"""

from __future__ import annotations

import os
import time

import anyio
import psycopg
import pytest
from openfga_sdk.client import OpenFgaClient
from openfga_sdk.models.create_store_request import CreateStoreRequest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.authz.client import sdk_configuration
from app.authz.config import AuthzSettings
from app.config import Settings
from app.security.internal_tls_check import collect_internal_tls_issues

pytestmark = pytest.mark.skipif(
    not os.environ.get("I09_TLS_CA_CERT"), reason="TLS lane only: scripts/run-internal-tls-lane.sh"
)

_SSL_IN_USE = text("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} unset: run scripts/run-internal-tls-lane.sh")
    return value


def test_lane_settings_pass_the_production_tls_check() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=_env("I09_TLS_DATABASE_URL"),
        database_url_sync=_env("I09_TLS_DATABASE_URL_SYNC"),
        openfga_api_url=_env("I09_TLS_OPENFGA_URL"),
        openfga_ca_cert=_env("I09_TLS_CA_CERT"),
        migration_database_url="",
        vault_url="",
        vault_ca_cert="",
        internal_plaintext_hosts="",
    )
    assert collect_internal_tls_issues(settings) == []


async def test_asyncpg_verify_full_connects_over_tls() -> None:
    engine = create_async_engine(_env("I09_TLS_DATABASE_URL"))
    try:
        async with engine.connect() as conn:
            assert (await conn.execute(_SSL_IN_USE)).scalar_one() is True
    finally:
        await engine.dispose()


async def test_plaintext_tcp_client_is_rejected_by_the_server() -> None:
    plain = _env("I09_TLS_DATABASE_URL").replace("ssl=verify-full", "ssl=disable")
    engine = create_async_engine(plain)
    try:
        with pytest.raises(Exception, match="(?i)pg_hba|no encryption|rejects"):
            async with engine.connect():
                pass
    finally:
        await engine.dispose()


def test_psycopg_checkpointer_url_connects_over_tls() -> None:
    with psycopg.connect(_env("I09_TLS_DATABASE_URL_SYNC")) as conn:
        row = conn.execute("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()").fetchone()
    assert row == (True,)


async def test_openfga_https_with_private_ca() -> None:
    config = AuthzSettings(
        openfga_api_url=_env("I09_TLS_OPENFGA_URL"),
        openfga_api_token=SecretStr(_env("I09_TLS_OPENFGA_TOKEN")),
        openfga_ca_cert=_env("I09_TLS_CA_CERT"),
        openfga_timeout_ms=5000,
    )
    deadline = time.monotonic() + 30
    while True:
        try:
            async with OpenFgaClient(sdk_configuration(config)) as sdk:
                store = await sdk.create_store(CreateStoreRequest(name="i09-tls-lane"))
            break
        except Exception:  # noqa: BLE001 — OpenFGA may still be starting; re-raised at deadline
            if time.monotonic() > deadline:
                raise
            await anyio.sleep(0.5)
    assert store.id
