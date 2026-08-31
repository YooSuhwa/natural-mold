"""Typed trust-boundary contracts for the disposable PostgreSQL runner."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote

from sqlalchemy.engine import URL, make_url


class LaneDsns(NamedTuple):
    async_url: str
    sync_url: str
    integration_url: str


@dataclass(frozen=True, slots=True)
class LaneTarget:
    user: str
    password: str
    host: str
    port: int
    database: str


class DsnContractError(RuntimeError):
    """Expose a stable reason code without reflecting credential-bearing input."""


_SAFE_DATABASE_PREFIX = "moldy_pg_lane_"
_EVIDENCE_PARTS = (".omo", "evidence", "project-restart-consolidated-roadmap")


def build_lane_dsns(*, user: str, password: str, port: int, database: str) -> LaneDsns:
    encoded_user = quote(user, safe="")
    encoded_password = quote(password, safe="")
    authority = f"{encoded_user}:{encoded_password}@127.0.0.1:{port}/{database}"
    return LaneDsns(
        async_url=f"postgresql+asyncpg://{authority}",
        sync_url=f"postgresql://{authority}",
        integration_url=f"postgresql+psycopg://{authority}",
    )


def _parse_url(raw: str, *, expected_driver: str, driver_reason: str) -> LaneTarget:
    try:
        url: URL = make_url(raw)
    except (TypeError, ValueError, AttributeError) as error:
        raise DsnContractError("malformed_url") from error
    if url.drivername != expected_driver:
        raise DsnContractError(driver_reason)
    if url.query or "#" in raw:
        raise DsnContractError("url_options")
    if url.host != "127.0.0.1" or url.port is None:
        raise DsnContractError("target_mismatch")
    if url.username is None or url.password is None or url.database is None:
        raise DsnContractError("missing_component")
    if not url.database.startswith(_SAFE_DATABASE_PREFIX):
        raise DsnContractError("unsafe_database")
    if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in url.database):
        raise DsnContractError("unsafe_database")
    return LaneTarget(
        user=url.username,
        password=url.password,
        host=url.host,
        port=url.port,
        database=url.database,
    )


def parse_lane_dsns(raw: LaneDsns) -> LaneTarget:
    async_target = _parse_url(
        raw.async_url,
        expected_driver="postgresql+asyncpg",
        driver_reason="async_driver",
    )
    sync_target = _parse_url(
        raw.sync_url,
        expected_driver="postgresql",
        driver_reason="sync_driver",
    )
    integration_target = _parse_url(
        raw.integration_url,
        expected_driver="postgresql+psycopg",
        driver_reason="integration_driver",
    )
    if async_target != sync_target or async_target != integration_target:
        raise DsnContractError("target_mismatch")
    return async_target


def validate_manifest_destination(destination: Path, evidence_root: Path) -> Path:
    root = evidence_root.resolve(strict=True)
    parent = destination.parent.resolve(strict=True)
    if parent != root or destination.name in {"", ".", ".."}:
        raise DsnContractError("manifest_boundary")
    try:
        os.lstat(destination)
    except FileNotFoundError:
        return destination
    raise FileExistsError(destination.name)


def ensure_evidence_root(repo_root: Path) -> Path:
    current = repo_root.resolve(strict=True)
    for component in _EVIDENCE_PARTS:
        current = current / component
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            metadata = os.lstat(current)
        if not current.is_dir() or current.is_symlink() or metadata.st_nlink < 1:
            raise DsnContractError("evidence_component")
    return current
