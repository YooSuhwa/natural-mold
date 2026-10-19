"""F-11 for MCP servers and OAuth fetches (plan 11.9, 11.4.1; F-15 review L2/L5)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.mcp_destination import mcp_destination_error, split_blocked
from app.agent_runtime.mcp_tool_loader import _plan_mcp_servers
from app.config import settings
from app.credentials import mcp_oauth_discovery
from app.credentials.allowed_hosts import default_allowed_hosts
from app.credentials.creation import CredentialCreation
from app.models.credential import Credential
from app.outbound import client as outbound_client
from app.outbound import transport as outbound_transport
from app.outbound.client import mcp_http_client_factory
from app.outbound.destination import OutboundBlockedError, owner_own_run
from app.schemas.mcp import McpServerCreate, McpServerUpdate
from app.services import mcp_service
from tests.authz.e01_support import add_credential, seed
from tests.authz.unit_scope_fixture import create_test_credential, unit_context
from tests.conftest import TEST_USER_ID

MCP_URL = "https://mcp.example.com/mcp"


def _config(url: str = MCP_URL, *, hosts: list[str], empty_ok: bool = False, **extra: Any):
    return {
        "name": "search",
        "mcp_tool_name": "search",
        "mcp_server_url": url,
        "mcp_transport_headers": {"Authorization": "Bearer t"},
        "credential_destination": {"allowed_hosts": hosts, "empty_ok": empty_ok},
        **extra,
    }


def test_mcp_call_time_checks_allowed_hosts_https_and_the_token_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "credential_empty_hosts_policy", "enforce")
    monkeypatch.setattr(settings, "app_env", "production")
    assert mcp_destination_error(_config(hosts=["mcp.example.com"])) is None
    assert "allowed hosts" in str(mcp_destination_error(_config(hosts=["other.example.com"])))
    assert "https" in str(
        mcp_destination_error(_config("http://mcp.example.com/mcp", hosts=["mcp.example.com"]))
    )
    assert "no allowed hosts" in str(mcp_destination_error(_config(hosts=[])))
    assert mcp_destination_error(_config(hosts=[], empty_ok=True)) is None
    issued_elsewhere = _config(
        hosts=["mcp.example.com"], credentials={"resource": "https://other.example.com/mcp"}
    )
    assert "another MCP server" in str(mcp_destination_error(issued_elsewhere))
    own_resource = _config(hosts=["mcp.example.com"], credentials={"resource": MCP_URL})
    assert mcp_destination_error(own_resource) is None
    assert mcp_destination_error({"mcp_server_url": "http://x"}) is None  # no credential
    missing = {"mcp_server_url": MCP_URL, "credentials": {"token": "t"}}
    assert "destination missing" in str(mcp_destination_error(missing))  # fail closed


async def test_blocked_mcp_tools_become_stubs_and_never_connect() -> None:
    allowed, stubs = split_blocked([_config(hosts=["elsewhere.example.com"])])
    assert allowed == []
    [stub] = stubs
    assert "cannot send its credential" in await stub.ainvoke({})


def test_credentialed_mcp_connections_use_the_guarded_client_factory() -> None:
    plan = _plan_mcp_servers([_config(hosts=["mcp.example.com"])])
    [server] = plan.servers.values()
    assert server["httpx_client_factory"] is mcp_http_client_factory
    bare = {"name": "x", "mcp_tool_name": "x", "mcp_server_url": "http://localhost:9/mcp"}
    [plain] = _plan_mcp_servers([bare]).servers.values()
    assert "httpx_client_factory" not in plain


async def test_the_mcp_client_factory_is_https_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    async with mcp_http_client_factory(headers={"Authorization": "Bearer t"}) as client:
        with pytest.raises(OutboundBlockedError, match="https"):
            await client.post("http://mcp.example.com/mcp")


def test_owner_own_run_needs_a_chat_by_the_owner_on_their_account() -> None:
    def ident(source: str, caller: object) -> Any:
        return SimpleNamespace(source=source, caller_user_id=caller)

    assert owner_own_run(ident("chat", "o"), "o", "o")
    assert owner_own_run(ident("subagent", "o"), "o", "o")
    assert not owner_own_run(ident("chat", "caller"), "o", "caller")  # shared agent
    assert not owner_own_run(ident("trigger", None), "o", "o")
    assert not owner_own_run(ident("channel", "o"), "o", "o")  # API
    assert not owner_own_run(None, "o", "o")


async def test_oauth_discovery_fetches_refuse_private_addresses_and_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def private(host: str, port: int) -> list[str]:
        return ["10.0.0.5"]

    monkeypatch.setattr(outbound_transport, "system_resolver", private)
    monkeypatch.setattr(outbound_client, "get_environment_proxies", dict)
    monkeypatch.setattr(settings, "app_env", "production")
    with pytest.raises(OutboundBlockedError, match="no public address"):
        await mcp_oauth_discovery.get_json("https://as.example.com/.well-known/x")
    client = mcp_oauth_discovery.new_http_client()
    async with client:
        with pytest.raises(OutboundBlockedError, match="https"):
            await client.post("http://as.example.com/token")
        assert client.follow_redirects is False


def test_definitions_declare_default_hosts() -> None:
    assert default_allowed_hosts("naver_search") == ["openapi.naver.com"]
    assert default_allowed_hosts("http_bearer") is None
    assert default_allowed_hosts("mcp_secret") is None


@pytest.fixture
async def _scoped(db: AsyncSession) -> None:
    await seed(db)


@pytest.mark.usefixtures("_scoped")
async def test_new_credentials_start_with_their_definition_hosts(db: AsyncSession) -> None:
    credential = await create_test_credential(
        db,
        CredentialCreation(definition_key="naver_search", name="n", data={"client_id": "x"}),
        unit_context(TEST_USER_ID),
    )
    assert credential.allowed_hosts == ["openapi.naver.com"]


@pytest.mark.usefixtures("_scoped")
async def test_mcp_registration_refuses_credentialed_http_and_remembers_https_hosts(
    db: AsyncSession,
) -> None:
    credential = await add_credential(db, owner=TEST_USER_ID, definition_key="mcp_secret")
    context = unit_context(TEST_USER_ID)
    data = McpServerCreate(name="s", transport="streamable_http", url="http://mcp.example.com/mcp")
    data.credential_id = credential.id
    with pytest.raises(HTTPException) as refused:
        await mcp_service.create_server(db, context=context, data=data)
    assert refused.value.status_code == 422

    plain = McpServerCreate(name="p", transport="streamable_http", url="http://localhost:9/mcp")
    await mcp_service.create_server(db, context=context, data=plain)  # no credential: as before

    data.url = MCP_URL
    server = await mcp_service.create_server(db, context=context, data=data)
    await db.refresh(credential)
    assert credential.allowed_hosts == ["mcp.example.com"]

    update = McpServerUpdate(url="http://mcp.example.com/other")
    with pytest.raises(HTTPException):
        await mcp_service.update_server(db, server=server, context=context, data=update)

    server.url = "http://legacy.example.com/mcp"  # registered before F-11
    await db.flush()
    renamed = McpServerUpdate(name="renamed")  # unrelated PATCH is not blocked
    await mcp_service.update_server(db, server=server, context=context, data=renamed)


@pytest.mark.usefixtures("_scoped")
async def test_someone_elses_credential_is_never_widened(db: AsyncSession) -> None:
    from app.credentials.allowed_hosts import remember_server_host
    from tests.authz.f05_support import OWNER

    credential = await add_credential(db, owner=OWNER, definition_key="mcp_secret")
    await remember_server_host(db, credential.id, MCP_URL, TEST_USER_ID)
    fetched = await db.get(Credential, credential.id)
    assert fetched is not None and fetched.allowed_hosts is None


@pytest.mark.parametrize(("env", "ok"), [("dev", True), ("production", False)])
async def test_openai_compatible_tester_follows_the_dev_http_default(
    monkeypatch: pytest.MonkeyPatch, env: str, ok: bool
) -> None:
    import httpx

    from app.credentials import tester as tester_module
    from app.credentials.registry import registry
    from app.credentials.tester import CredentialTester
    from app.outbound.client import outbound_http_client

    def wired(**options: Any) -> httpx.AsyncClient:
        mock = httpx.MockTransport(lambda request: httpx.Response(200, json={"data": []}))
        return outbound_http_client(transport=mock, **options)

    monkeypatch.setattr(settings, "app_env", env)
    monkeypatch.setattr(tester_module, "outbound_http_client", wired)
    definition = registry.get("openai_compatible")
    assert definition is not None
    data = {"base_url": "http://localhost:11434/v1", "api_key": "k"}
    result = await CredentialTester().run(definition, data)
    assert result.success is ok, result.message
    assert ok or "https" in result.message
