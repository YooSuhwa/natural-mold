"""Operator settings for the guardrail model service (guardrail-minimum-plan.md §2.1)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.platform_scope import PlatformScope
from app.config import settings
from app.credentials.creation import CredentialCreation
from app.guardrails import model_pack, model_settings
from app.guardrails.model_pack import MODEL_PACK, detect_texts
from app.models.audit_event import AuditEvent
from app.models.guardrail_model_setting import SINGLETON_ID, GuardrailModelSetting
from tests.authz.unit_scope_fixture import create_test_credential
from tests.conftest import TestSession

BASE = "/api/guardrail-model-settings"
ENV_URL = "http://pii-detector:8095"
TEXT = "담당자 김민수 과장의 번호는 010-2345-6789입니다"


async def _system_credential(db: AsyncSession, definition_key: str, data: dict) -> uuid.UUID:
    credential = await create_test_credential(
        db,
        CredentialCreation(definition_key=definition_key, name=definition_key, data=data),
        PlatformScope(),
    )
    await db.commit()
    return credential.id


def _route(monkeypatch: pytest.MonkeyPatch, module: object, handler) -> list[httpx.Request]:
    """Send ``module``'s outbound HTTP to ``handler``; returns the requests."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    real = module.outbound_http_client  # type: ignore[attr-defined]
    monkeypatch.setattr(
        module,
        "outbound_http_client",
        lambda **kw: real(transport=httpx.MockTransport(record), **kw),
    )
    return seen


def _detector(request: httpx.Request) -> httpx.Response:
    texts = json.loads(request.content)["texts"]
    results = [[{"entity": "PERSON", "start": 0, "end": 3, "score": 0.99}] for _ in texts]
    return httpx.Response(
        200, json={"model": "schift-io/schift-ko-pii-v7@9d9bc145", "results": results}
    )


async def _events(db: AsyncSession, action: str) -> list[AuditEvent]:
    found = await db.scalars(select(AuditEvent).where(AuditEvent.action == action))
    return list(found)


# --- reading and saving --------------------------------------------------------------


async def test_without_a_saved_row_the_environment_applies(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "guardrail_pii_model_url", ENV_URL)

    body = (await client.get(BASE)).json()

    assert body == {
        "enabled": True,
        "url": ENV_URL,
        "source": "environment",
        "environment_url": ENV_URL,
        "credential_id": None,
        "credential_name": None,
        "updated_at": None,
    }
    assert await model_settings.current() == model_settings.ModelService(
        True, ENV_URL, "environment"
    )


async def test_saving_overrides_the_environment_and_is_audited(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "guardrail_pii_model_url", ENV_URL)
    assert (await model_settings.current()).url == ENV_URL  # cached before the change

    resp = await client.put(BASE, json={"enabled": True, "url": " http://10.0.0.5:8095/ "})

    assert resp.status_code == 200, resp.text
    assert (resp.json()["source"], resp.json()["url"]) == ("saved", "http://10.0.0.5:8095/")
    # Saving clears the cache: the next check already uses the new address.
    assert await model_settings.current() == model_settings.ModelService(
        True, "http://10.0.0.5:8095/", "saved"
    )
    [event] = await _events(db, "guardrail_model_setting.update")
    assert (event.outcome, event.target_type) == ("success", "guardrail_model_setting")
    assert event.event_metadata == {
        "before": {
            "source": "environment",
            "enabled": True,
            "url": ENV_URL,
            "credential_bound": False,
        },
        "after": {
            "source": "saved",
            "enabled": True,
            "url": "http://10.0.0.5:8095/",
            "credential_bound": False,
        },
    }


async def test_switching_off_runs_the_rules_pack_even_with_a_service(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "guardrail_pii_model_url", ENV_URL)
    seen = _route(monkeypatch, model_pack, _detector)

    resp = await client.put(BASE, json={"enabled": False, "url": ENV_URL})
    [findings] = await detect_texts([TEXT], [MODEL_PACK])

    assert resp.status_code == 200
    assert seen == []
    assert findings and {f.pack for f in findings} == {"kr"}


async def test_a_bearer_credential_is_sent_to_the_service(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    credential_id = await _system_credential(db, "http_bearer", {"token": "tok-123"})
    seen = _route(monkeypatch, model_pack, _detector)

    resp = await client.put(
        BASE, json={"enabled": True, "url": ENV_URL, "credential_id": str(credential_id)}
    )
    [findings] = await detect_texts([TEXT], [MODEL_PACK])

    assert resp.json()["credential_name"] == "http_bearer"
    assert seen[0].headers["Authorization"] == "Bearer tok-123"
    assert {f.pack for f in findings} == {MODEL_PACK}
    assert "tok-123" not in resp.text


async def test_reset_returns_to_the_environment(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "guardrail_pii_model_url", ENV_URL)
    await client.put(BASE, json={"enabled": False, "url": ""})

    resp = await client.delete(BASE)

    assert resp.status_code == 204
    assert (await client.get(BASE)).json()["source"] == "environment"
    assert (await model_settings.current()).enabled is True
    [event] = await _events(db, "guardrail_model_setting.reset")
    assert event.event_metadata is not None
    assert (
        event.event_metadata["before"]["enabled"],
        event.event_metadata["after"]["enabled"],
    ) == (
        False,
        True,
    )


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("", "missing"),
        ("ftp://pii-detector:8095", "scheme_or_host"),
        ("pii-detector:8095", "scheme_or_host"),
        ("http://user:pw@pii-detector:8095", "credentials_in_url"),
        ("http://pii-detector:8095/?x=1", "query_or_fragment"),
        ("http://pii-detector:99999", "malformed"),
    ],
)
async def test_an_enabled_setting_needs_a_valid_url(
    client: AsyncClient, url: str, reason: str
) -> None:
    resp = await client.put(BASE, json={"enabled": True, "url": url})

    assert resp.status_code == 422
    assert resp.json()["error"] == {"code": "GUARDRAIL_MODEL_INVALID_URL", "message": reason}


@pytest.mark.parametrize("kind", ["missing", "other_type"])
async def test_only_a_system_bearer_credential_is_accepted(
    client: AsyncClient, db: AsyncSession, kind: str
) -> None:
    credential_id = (
        uuid.uuid4()
        if kind == "missing"
        else await _system_credential(db, "http_basic", {"username": "u", "password": "p"})
    )

    resp = await client.put(
        BASE, json={"enabled": True, "url": ENV_URL, "credential_id": str(credential_id)}
    )

    assert resp.status_code == 404
    assert await db.get(GuardrailModelSetting, SINGLETON_ID) is None


# --- connection test ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("handler", "expected"),
    [
        (
            _detector,
            {"status": "ok", "findings": 1, "model": "schift-io/schift-ko-pii-v7@9d9bc145"},
        ),
        (lambda _r: httpx.Response(401), {"status": "unauthorized"}),
        (
            lambda _r: httpx.Response(503, json={"error": "model_missing"}),
            {"status": "model_missing"},
        ),
        (
            lambda _r: httpx.Response(200, text="<html>internal page</html>"),
            {"status": "bad_response"},
        ),
        (
            lambda _r: httpx.Response(302, headers={"Location": "http://x"}),
            {"status": "bad_response"},
        ),
    ],
    ids=["ok", "unauthorized", "model-missing", "not-json", "redirect"],
)
async def test_the_probe_reports_an_outcome_not_the_body(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
    expected: dict,
) -> None:
    seen = _route(monkeypatch, model_settings, handler)

    resp = await client.post(f"{BASE}/test", json={"url": "http://10.0.0.5:8095"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {k: body[k] for k in expected} == expected
    assert "internal page" not in resp.text
    assert seen[0].url == "http://10.0.0.5:8095/detect"
    [event] = await _events(db, "guardrail_model_setting.test")
    assert event.outcome == ("success" if expected["status"] == "ok" else "failure")
    assert event.event_metadata == {
        "status": expected["status"],
        "host": "10.0.0.5",
        "credential_bound": False,
    }


async def test_the_probe_reports_an_unreachable_service(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _route(monkeypatch, model_settings, refuse)

    resp = await client.post(f"{BASE}/test", json={"url": ENV_URL})

    assert resp.json()["status"] == "unreachable"


async def test_the_probe_uses_the_chosen_credential_and_does_not_save(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    credential_id = await _system_credential(db, "http_bearer", {"token": "tok-9"})
    seen = _route(monkeypatch, model_settings, _detector)

    await client.post(f"{BASE}/test", json={"url": ENV_URL, "credential_id": str(credential_id)})

    assert seen[0].headers["Authorization"] == "Bearer tok-9"
    assert await db.get(GuardrailModelSetting, SINGLETON_ID) is None


# --- access and the cache -------------------------------------------------------------


@pytest.fixture
async def non_super_client():
    from httpx import ASGITransport

    from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
    from app.main import create_app
    from tests.conftest import override_get_db

    async def regular() -> CurrentUser:
        return CurrentUser(
            id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            email="user@test.com",
            name="Regular User",
            is_super_user=False,
        )

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = regular
    app.dependency_overrides[verify_csrf] = lambda: None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_operator_only(non_super_client: AsyncClient) -> None:
    calls = [
        non_super_client.get(BASE),
        non_super_client.put(BASE, json={"enabled": False, "url": ""}),
        non_super_client.delete(BASE),
        non_super_client.post(f"{BASE}/test", json={"url": ENV_URL}),
    ]
    for call in calls:
        assert (await call).status_code == 403


async def test_other_workers_see_a_change_within_the_cache_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert (await model_settings.current()).source == "environment"
    async with TestSession() as db:  # another worker saved it
        db.add(GuardrailModelSetting(id=SINGLETON_ID, enabled=False, url=""))
        await db.commit()

    assert (await model_settings.current()).source == "environment"
    monkeypatch.setattr(model_settings, "CACHE_SECONDS", 0.0)
    assert (await model_settings.current()).source == "saved"


async def test_an_unreadable_setting_falls_back_to_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "guardrail_pii_model_url", ENV_URL)

    def broken(_reason: str):
        raise RuntimeError("database down")

    monkeypatch.setattr(model_settings, "platform_session", broken)

    assert await model_settings.current() == model_settings.ModelService(
        True, ENV_URL, "environment"
    )
