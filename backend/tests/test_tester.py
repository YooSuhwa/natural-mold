"""Tests for ``CredentialTester`` — request building and rule evaluation."""

from __future__ import annotations

import pytest
import httpx

from app.credentials.authenticate import GenericAuth
from app.credentials.domain import CredentialDefinition, TestRequestSpec
from app.credentials.field import FieldDef, FieldKind
from app.credentials.tester import CredentialTester


def _http_mock_definition(
    *,
    test_url: str = "https://example.com/ping",
    rules: list[dict] | None = None,
    auth: GenericAuth | None = None,
) -> CredentialDefinition:
    return CredentialDefinition(
        key="mock",
        display_name="Mock",
        properties=[
            FieldDef(name="api_key", display_name="API Key", kind=FieldKind.PASSWORD),
        ],
        authenticate=auth,
        test=TestRequestSpec(
            request={"method": "GET", "url": test_url},
            rules=rules or [],
        ),
    )


@pytest.mark.asyncio
async def test_tester_returns_failure_when_definition_lacks_test_recipe() -> None:
    definition = CredentialDefinition(
        key="no_test", display_name="x", properties=[]
    )
    result = await CredentialTester().run(definition, {})
    assert result.success is False
    assert "no test recipe" in result.message


@pytest.mark.asyncio
async def test_tester_default_rule_treats_2xx_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = _http_mock_definition()

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None: ...

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *exc_info) -> None:
            return None

        async def request(self, **kwargs) -> httpx.Response:
            request = httpx.Request(kwargs["method"], kwargs["url"])
            return httpx.Response(204, request=request)

    monkeypatch.setattr("app.credentials.tester.httpx.AsyncClient", FakeAsyncClient)
    result = await CredentialTester().run(definition, {"api_key": "k"})
    assert result.success is True
    assert result.http_status == 204


@pytest.mark.asyncio
async def test_tester_response_code_rule_with_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = _http_mock_definition(
        rules=[{"type": "responseCode", "value": [200, 400]}]
    )

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None: ...

        async def __aenter__(self): return self
        async def __aexit__(self, *exc_info): return None

        async def request(self, **kwargs):
            request = httpx.Request(kwargs["method"], kwargs["url"])
            return httpx.Response(400, request=request)

    monkeypatch.setattr("app.credentials.tester.httpx.AsyncClient", FakeAsyncClient)
    result = await CredentialTester().run(definition, {"api_key": "k"})
    assert result.success is True


@pytest.mark.asyncio
async def test_tester_response_success_body_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = _http_mock_definition(
        rules=[{"type": "responseSuccessBody", "key": "status", "value": "ok"}]
    )

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None: ...

        async def __aenter__(self): return self
        async def __aexit__(self, *exc_info): return None

        async def request(self, **kwargs):
            request = httpx.Request(kwargs["method"], kwargs["url"])
            return httpx.Response(
                200, json={"status": "ok"}, request=request
            )

    monkeypatch.setattr("app.credentials.tester.httpx.AsyncClient", FakeAsyncClient)
    result = await CredentialTester().run(definition, {"api_key": "k"})
    assert result.success is True


@pytest.mark.asyncio
async def test_tester_response_success_body_rule_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = _http_mock_definition(
        rules=[{"type": "responseSuccessBody", "key": "status", "value": "ok"}]
    )

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None: ...

        async def __aenter__(self): return self
        async def __aexit__(self, *exc_info): return None

        async def request(self, **kwargs):
            request = httpx.Request(kwargs["method"], kwargs["url"])
            return httpx.Response(
                200, json={"status": "down"}, request=request
            )

    monkeypatch.setattr("app.credentials.tester.httpx.AsyncClient", FakeAsyncClient)
    result = await CredentialTester().run(definition, {"api_key": "k"})
    assert result.success is False
    assert "status" in result.message


@pytest.mark.asyncio
async def test_tester_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    definition = _http_mock_definition()

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None: ...

        async def __aenter__(self): return self
        async def __aexit__(self, *exc_info): return None

        async def request(self, **kwargs):
            raise httpx.ConnectError("dns failure")

    monkeypatch.setattr("app.credentials.tester.httpx.AsyncClient", FakeAsyncClient)
    result = await CredentialTester().run(definition, {"api_key": "k"})
    assert result.success is False
    assert "network" in result.message


@pytest.mark.asyncio
async def test_tester_applies_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authentication recipe is applied — header gets injected from credentials."""

    captured: dict = {}
    auth = GenericAuth(
        properties={
            "headers": {"Authorization": "=Bearer {{ $credentials.api_key }}"}
        }
    )
    definition = _http_mock_definition(auth=auth)

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None: ...

        async def __aenter__(self): return self
        async def __aexit__(self, *exc_info): return None

        async def request(self, **kwargs):
            captured.update(kwargs)
            request = httpx.Request(kwargs["method"], kwargs["url"], headers=kwargs.get("headers"))
            return httpx.Response(200, request=request)

    monkeypatch.setattr("app.credentials.tester.httpx.AsyncClient", FakeAsyncClient)
    await CredentialTester().run(definition, {"api_key": "k-123"})
    assert captured["headers"]["Authorization"] == "Bearer k-123"


@pytest.mark.asyncio
async def test_tester_missing_field_returns_interpolation_error() -> None:
    auth = GenericAuth(
        properties={
            "headers": {"X-Token": "={{ $credentials.missing_field }}"}
        }
    )
    definition = _http_mock_definition(auth=auth)
    result = await CredentialTester().run(definition, {})
    assert result.success is False
    assert "missing field" in result.message.lower() or "missing" in result.message.lower()
