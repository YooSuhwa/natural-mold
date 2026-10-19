"""Plan 8.4 decision table: response codes and envelopes of AuthzService."""

from datetime import datetime
from uuid import uuid4

import pytest

from app.authz import denials
from app.authz.client import AuthzUnavailable
from app.authz.org_context import OrgContext
from app.authz.service import Verdict
from app.exceptions import AppError
from tests.authz.authz_service_testkit import (
    AGENT,
    ORG,
    OTHER_ORG,
    USER,
    RecordingClient,
    grants,
    require,
    service,
    user,
)


# 8.4 row 1: missing, foreign-organization, or not even min_visible -> 404 (existing code).
@pytest.mark.parametrize(
    ("resource_org", "allowed", "consulted"),
    [
        (None, grants("can_run", "can_edit"), False),  # resource does not exist
        (OTHER_ORG, grants("can_run", "can_edit"), False),  # other organization's resource
        (ORG, frozenset(), True),  # no minimum visibility
        (ORG, grants("can_edit"), True),  # action without visibility still hides existence
    ],
)
async def test_hidden_resource_is_existing_not_found(resource_org, allowed, consulted):
    client = RecordingClient(allowed)
    existing = AppError(code="agent_not_found", message="에이전트를 찾을 수 없습니다", status=404)
    with pytest.raises(AppError) as caught:
        await require(service(client), org=resource_org, not_found=existing)
    assert caught.value is existing
    assert bool(client.queries) is consulted


async def test_hidden_resource_defaults_to_generic_not_found_without_details():
    with pytest.raises(AppError) as caught:
        await require(service(RecordingClient()))
    assert (caught.value.status, caught.value.code, caught.value.details) == (
        404,
        "not_found",
        None,
    )


# 8.4 row 2: visible but action missing -> 403 permission_required with details.
@pytest.mark.parametrize("request_access", [True, False])
async def test_visible_without_action_is_permission_required(request_access):
    client = RecordingClient(grants("can_run"))
    with pytest.raises(AppError) as caught:
        await require(service(client), request_access=request_access)
    error = caught.value
    assert (error.status, error.code) == (403, "permission_required")
    assert error.details == {"required": "can_edit", "request_access": request_access}


async def test_visible_with_action_is_allowed_in_one_batch():
    client = RecordingClient(grants("can_run", "can_edit"))
    await require(service(client))
    assert [(q.relation, getattr(q, "object", None)) for q in client.queries] == [
        ("can_run", AGENT),
        ("can_edit", AGENT),
    ]


# 8.4 row 3: access request to an invisible resource -> 202 always; created only if visible.
@pytest.mark.parametrize(
    ("resource_org", "allowed", "unavailable", "expected"),
    [
        (ORG, grants("can_run"), False, True),
        (ORG, frozenset(), False, False),
        (OTHER_ORG, grants("can_run"), False, False),
        (None, grants("can_run"), False, False),
        (ORG, grants("can_run"), True, False),  # outage never creates a request
    ],
)
async def test_access_request_created_only_for_visible(
    resource_org, allowed, unavailable, expected
):
    client = RecordingClient(allowed, unavailable=unavailable)
    created = await service(client).can_request_access(
        user(), AGENT, min_visible="can_run", resource_org_id=resource_org, legacy_visible=False
    )
    assert created is expected


async def test_access_request_uses_legacy_visibility_when_not_enforced():
    client = RecordingClient(grants("can_run"))
    created = await service(client, "legacy").can_request_access(
        user(), AGENT, min_visible="can_run", resource_org_id=ORG, legacy_visible=True
    )
    assert created is True
    assert client.queries == []


# 8.4 row 4: missing organization capability -> 403 capability_required.
async def test_missing_capability_is_capability_required():
    ctx = OrgContext(org_id=ORG, tenant_id=uuid4(), user_id=USER)
    client = RecordingClient()
    with pytest.raises(AppError) as caught:
        await service(client).require_cap(ctx, "cap_create_agent", legacy=True)
    error = caught.value
    assert (error.status, error.code) == (403, "capability_required")
    assert error.details == {"capability": "cap_create_agent"}
    assert getattr(client.queries[0], "object", None) == f"organization:{ORG}"


async def test_granted_capability_passes():
    ctx = OrgContext(org_id=ORG, tenant_id=uuid4(), user_id=USER)
    client = RecordingClient(grants("cap_create_agent", obj=f"organization:{ORG}"))
    await service(client).require_cap(ctx, "cap_create_agent", legacy=False)


# 8.4 rows 4-7: remaining responses have the exact typed envelope.
@pytest.mark.parametrize(
    ("error", "status", "code", "details"),
    [
        (denials.capability_required("cap_x"), 403, "capability_required", {"capability": "cap_x"}),
        (denials.org_access_denied(), 403, "org_access_denied", None),
        (denials.tenant_suspended(), 403, "tenant_suspended", None),
        (
            denials.quota_exceeded("monthly_spend", "org"),
            429,
            "quota_exceeded",
            {"kind": "monthly_spend", "scope": "org"},
        ),
        (denials.authz_unavailable(), 503, "authz_unavailable", None),
    ],
)
def test_denial_envelopes(error, status, code, details):
    assert (error.status, error.code, error.details) == (status, code, details)


def test_quota_exceeded_carries_usage_details():
    resets = datetime(2026, 11, 1)
    error = denials.quota_exceeded("rate", "user", limit=10, used=11, resets_at=resets)
    assert error.details == {
        "kind": "rate",
        "scope": "user",
        "limit": 10,
        "used": 11,
        "resets_at": resets,
    }


# 8.4 row 8: OpenFGA outage in enforce mode -> 503 authz_unavailable (fail closed).
async def test_enforce_outage_is_authz_unavailable():
    with pytest.raises(AppError) as caught:
        await require(service(RecordingClient(unavailable=True)))
    assert (caught.value.status, caught.value.code) == (503, "authz_unavailable")


async def test_capability_outage_is_authz_unavailable():
    ctx = OrgContext(org_id=ORG, tenant_id=uuid4(), user_id=USER)
    with pytest.raises(AppError) as caught:
        await service(RecordingClient(unavailable=True)).require_cap(ctx, "cap_x", legacy=True)
    assert caught.value.status == 503


@pytest.mark.parametrize("operation", ["check", "batch_check", "list_objects"])
async def test_raw_operations_never_turn_outage_into_answer(operation):
    svc = service(RecordingClient(unavailable=True))
    calls = {
        "check": lambda: svc.check(user(), "can_run", AGENT),
        "batch_check": lambda: svc.batch_check(user(), [("can_run", AGENT)]),
        "list_objects": lambda: svc.list_objects(user(), "can_run", "agent"),
    }
    with pytest.raises(AuthzUnavailable):
        await calls[operation]()


async def test_error_envelope_renders_details_only_when_present(monkeypatch):
    import httpx

    from app.services import audit_service

    audited: list[dict[str, object]] = []

    async def record(**event: object) -> None:
        audited.append(event)

    monkeypatch.setattr(audit_service, "record_platform_event_best_effort", record)
    from fastapi import FastAPI

    from app.exception_handlers import register_exception_handlers

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/cap")
    async def cap() -> None:
        raise denials.capability_required("cap_create_agent")

    @app.get("/gone")
    async def gone() -> None:
        raise denials.authz_unavailable()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        cap_response = await http.get("/cap")
        gone_response = await http.get("/gone")
    assert cap_response.status_code == 403
    assert cap_response.json()["error"]["details"] == {"capability": "cap_create_agent"}
    assert gone_response.status_code == 503
    assert "details" not in gone_response.json()["error"]
    # capability denials are audited like permission_required; outages are not.
    assert [event["reason_code"] for event in audited] == ["capability_required"]


async def test_legacy_forbidden_override_keeps_route_403():
    existing = AppError(code="forbidden", message="권한이 없습니다", status=403)
    with pytest.raises(AppError) as caught:
        await require(
            service(RecordingClient(), "legacy"), legacy=Verdict.FORBIDDEN, forbidden=existing
        )
    assert caught.value is existing


async def test_enforced_forbidden_uses_8_4_shape_even_with_override():
    existing = AppError(code="forbidden", message="권한이 없습니다", status=403)
    with pytest.raises(AppError) as caught:
        await require(service(RecordingClient(grants("can_run"))), forbidden=existing)
    assert caught.value.code == "permission_required"


@pytest.mark.parametrize(
    ("error", "code", "message"),
    [
        (denials.not_found(), "not_found", "요청한 항목을 찾을 수 없습니다"),
        (
            denials.permission_required("can_edit", request_access=True),
            "permission_required",
            "조직 관리 권한이 필요합니다",
        ),
        (
            denials.org_access_denied(),
            "org_access_denied",
            "현재 조직에서만 대상을 검색할 수 있습니다",
        ),
        (denials.tenant_suspended(), "tenant_suspended", "회사가 정지되었습니다"),
    ],
)
def test_denials_share_registered_error_shapes(error, code, message):
    from app.authz.error_codes import ERRORS

    assert (error.code, error.message, error.status) == (
        code,
        message,
        ERRORS[code].status,
    )
