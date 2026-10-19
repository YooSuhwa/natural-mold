"""F-11: definition-host backfill (Q14) and the outbound client ratchet (11.9)."""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine

from alembic import op as alembic_op
from app.credentials.registry import registry

BACKEND = Path(__file__).resolve().parents[2]
VERSIONS = BACKEND / "alembic" / "versions"

# Direct ``httpx.AsyncClient(...)``/``httpx.Client(...)`` left outside ``app/outbound``
# (no credential on these calls). The count may only go down. The zero-retry model lease
# transports (``lease_owned_model_client``) keep their own pinned trust store.
RATCHET: dict[str, int] = {
    "app/agent_runtime/builder_v3/image_gen.py": 1,
    "app/observability/langfuse.py": 1,
    "app/services/image_service.py": 1,
    "app/services/model_catalog/loaders.py": 2,
    "app/services/model_discovery.py": 4,
}


def _load() -> ModuleType:
    path = VERSIONS / "f11_credential_definition_hosts.py"
    spec = importlib.util.spec_from_file_location("f11_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backfill_adds_definition_hosts_and_keeps_existing_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load()
    assert module.down_revision == "f01_connection_delegation"
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE credentials (id CHAR(32) PRIMARY KEY, definition_key VARCHAR(80), "
            "allowed_hosts JSON)"
        )
        connection.exec_driver_sql(
            "INSERT INTO credentials VALUES ('c1', 'naver_search', NULL), "
            "('c2', 'google_search', '[\"proxy.example.com\"]'), "
            "('c3', 'http_bearer', NULL), ('c4', 'mcp_secret', '[\"mcp.example.com\"]'), "
            "('c5', 'http_api_key', NULL)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE tools (id CHAR(32) PRIMARY KEY, credential_id CHAR(32), "
            "definition_key VARCHAR(80), parameters JSON)"
        )
        connection.exec_driver_sql(
            "INSERT INTO tools VALUES "
            "('t1', 'c5', 'http_request', '{\"url\": \"https://API.Example.com/v1\"}'), "
            "('t2', 'c5', 'http_request', '{\"url\": \"https://10.0.0.5/x\"}'), "
            "('t3', 'c3', 'google_chat_message', '{\"url\": \"https://x.example\"}')"
        )
        monkeypatch.setattr(
            alembic_op, "_proxy", Operations(MigrationContext.configure(connection)), raising=False
        )
        module.upgrade()
        rows = connection.exec_driver_sql("SELECT id, allowed_hosts FROM credentials").all()
    hosts = {key: json.loads(value) if value else None for key, value in rows}
    assert hosts == {
        "c1": ["openapi.naver.com"],
        "c2": ["proxy.example.com", "www.googleapis.com"],
        "c3": None,  # generic HTTP without an HTTP tool URL stays empty (Q14)
        "c4": ["mcp.example.com"],
        "c5": ["api.example.com"],  # from the HTTP tool URL; the IP literal is skipped
    }


def test_backfill_snapshot_matches_the_definitions() -> None:
    declared = {d.key: d.hosts for d in registry.all() if d.hosts}
    assert declared == _load().DEFINITION_HOSTS


def _constructions(tree: ast.AST) -> tuple[int, bool]:
    count, verify_off = 0, False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in {"AsyncClient", "Client"}
            and isinstance(func.value, ast.Name)
            and func.value.id == "httpx"
        ):
            count += 1
        for keyword in node.keywords:
            if keyword.arg == "verify" and isinstance(keyword.value, ast.Constant):
                verify_off = verify_off or keyword.value.value is False
    return count, verify_off


def test_http_clients_are_built_by_outbound_http_client_only() -> None:
    found: dict[str, int] = {}
    for path in sorted((BACKEND / "app").rglob("*.py")):
        relative = path.relative_to(BACKEND).as_posix()
        count, verify_off = _constructions(ast.parse(path.read_text(encoding="utf-8")))
        assert not verify_off, f"{relative}: verify=False is not allowed"
        if count and not relative.startswith("app/outbound/"):
            found[relative] = count
    grown = {p: n for p, n in found.items() if n > RATCHET.get(p, 0)}
    assert not grown, f"build httpx clients with outbound_http_client(): {grown}"
