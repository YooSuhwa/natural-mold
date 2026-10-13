"""Canonical-lane access for B11 migration and constraint tests."""

from __future__ import annotations

import os
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Connection, Engine, make_url
from sqlalchemy.exc import IntegrityError

from alembic import command

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class CanonicalLaneRequiredError(RuntimeError):
    """Raised when a migration test is not running in the canonical owned lane."""


@dataclass(frozen=True, slots=True)
class CompanyMigrationLane:
    engine: Engine
    config: Config


@dataclass(frozen=True, slots=True)
class LaneTarget:
    username: str
    password: str
    host: str
    port: int
    database: str


@dataclass(frozen=True, slots=True)
class RunnerDsns:
    async_url: str
    sync_url: str
    integration_url: str


@dataclass(frozen=True, slots=True)
class CompanySeed:
    tenant_id: UUID
    org_id: UUID
    user_id: UUID


type InvitationValue = UUID | str | datetime | None


@dataclass(frozen=True, slots=True)
class InvalidInvitationCase:
    constraint: str
    changes: tuple[tuple[str, InvitationValue], ...]


INSERT_INVITATION = text(
    "INSERT INTO tenant_invitations "
    "(id,tenant_id,email,role,status,token_hash,created_by,created_at,expires_at,"
    "accepted_by,accepted_at,revoked_by,revoked_at) VALUES "
    "(:id,:tenant,:email,:role,:status,:token_hash,:created_by,:created_at,:expires_at,"
    ":accepted_by,:accepted_at,:revoked_by,:revoked_at)"
)


def _lane_target(raw: str, expected_driver: str) -> LaneTarget:
    url: URL = make_url(raw)
    if (
        url.drivername != expected_driver
        or url.host != "127.0.0.1"
        or url.port is None
        or url.username is None
        or url.password is None
        or url.database is None
        or not url.database.startswith("moldy_pg_lane_")
        or url.query
    ):
        raise CanonicalLaneRequiredError("invalid_lane_dsn")
    return LaneTarget(url.username, url.password, url.host, url.port, url.database)


def _runner_dsns() -> RunnerDsns:
    required = (
        "DATABASE_URL",
        "DATABASE_URL_SYNC",
        "INTEGRATION_DATABASE_URL",
        "MOLDY_PG_TEST_RECEIPT",
        "MOLDY_TEST_RUN_ROOT",
    )
    if any(name not in os.environ for name in required):
        raise CanonicalLaneRequiredError("canonical_runner_required")
    dsns = RunnerDsns(
        os.environ["DATABASE_URL"],
        os.environ["DATABASE_URL_SYNC"],
        os.environ["INTEGRATION_DATABASE_URL"],
    )
    targets = (
        _lane_target(dsns.async_url, "postgresql+asyncpg"),
        _lane_target(dsns.sync_url, "postgresql"),
        _lane_target(dsns.integration_url, "postgresql+psycopg"),
    )
    if len(set(targets)) != 1:
        raise CanonicalLaneRequiredError("lane_dsn_mismatch")
    return dsns


def seed_company(connection: Connection, *, archived: bool = False) -> CompanySeed:
    seed = CompanySeed(tenant_id=uuid4(), org_id=uuid4(), user_id=uuid4())
    status = "archived" if archived else "active"
    connection.execute(
        text("INSERT INTO users (id,email,name,created_at) VALUES (:id,:email,'Actor',now())"),
        {"id": seed.user_id, "email": f"{seed.user_id}@example.test"},
    )
    connection.execute(
        text(
            "INSERT INTO tenants "
            "(id,name,slug,status,limits,settings,secrets_namespace,data_keys,"
            "created_at,updated_at) "
            "VALUES (:id,'Company',:slug,'active','{}','{}',:namespace,'{}',now(),now())"
        ),
        {
            "id": seed.tenant_id,
            "slug": f"company-{seed.tenant_id}",
            "namespace": f"tenants/{seed.tenant_id}",
        },
    )
    connection.execute(
        text(
            "INSERT INTO organizations "
            "(id,tenant_id,name,slug,status,is_default,settings,authz_epoch,created_at,updated_at) "
            "VALUES (:id,:tenant,'Organization',:slug,:status,false,'{}',0,now(),now())"
        ),
        {
            "id": seed.org_id,
            "tenant": seed.tenant_id,
            "slug": f"org-{seed.org_id}",
            "status": status,
        },
    )
    return seed


def invitation_values(seed: CompanySeed) -> MutableMapping[str, InvitationValue]:
    created_at = datetime.now(UTC)
    return {
        "id": uuid4(),
        "tenant": seed.tenant_id,
        "email": "invitee@example.test",
        "role": "admin",
        "status": "pending",
        "token_hash": uuid4().hex + uuid4().hex,
        "created_by": seed.user_id,
        "created_at": created_at,
        "expires_at": created_at + timedelta(days=7),
        "accepted_by": None,
        "accepted_at": None,
        "revoked_by": None,
        "revoked_at": None,
    }


def rejects_invitation(
    connection: Connection,
    seed: CompanySeed,
    case: InvalidInvitationCase,
) -> None:
    values = invitation_values(seed)
    values.update(case.changes)
    with pytest.raises(IntegrityError, match=case.constraint), connection.begin_nested():
        connection.execute(INSERT_INVITATION, values)


@contextmanager
def company_migration_lane() -> Iterator[CompanyMigrationLane]:
    """Use the runner-owned database directly and restore its head on exit."""
    dsns = _runner_dsns()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.attributes["database_url"] = dsns.async_url
    engine = create_engine(dsns.integration_url)
    try:
        yield CompanyMigrationLane(engine=engine, config=config)
    finally:
        command.upgrade(config, "head")
        engine.dispose()
