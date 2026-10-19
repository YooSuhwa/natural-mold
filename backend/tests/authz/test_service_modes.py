"""Plan 8.5 / 7.3: legacy, shadow and enforce modes and condition context."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.authz.org_context import OrgContext
from app.authz.principal import Principal
from app.authz.service import AuthzNotConsulted, AuthzService, ShadowSample, Verdict
from app.exceptions import AppError
from tests.authz.authz_service_testkit import (
    AGENT,
    NO_IP,
    ORG,
    USER,
    RecordingClient,
    grants,
    require,
    service,
    user,
)


# 8.4 row 9 / 8.5: shadow mode answers with the legacy verdict and only exposes a hook.
@pytest.mark.parametrize(
    ("legacy", "expected"),
    [(Verdict.ALLOW, None), (Verdict.NOT_FOUND, 404), (Verdict.FORBIDDEN, 403)],
)
async def test_shadow_responds_with_legacy_and_submits_sample(legacy, expected):
    samples: list[ShadowSample] = []
    client = RecordingClient(grants("can_run"))  # OpenFGA would say 403
    svc = service(client, "shadow", hook=samples.append)
    if expected is None:
        await require(svc, legacy=legacy)
    else:
        with pytest.raises(AppError) as caught:
            await require(svc, legacy=legacy)
        assert caught.value.status == expected
    assert client.queries == []  # never on the request path
    assert samples == [
        ShadowSample(user(), "can_edit", AGENT, "can_run", ORG, legacy),
    ]


async def test_shadow_hook_failure_never_changes_response():
    def broken(sample: ShadowSample) -> None:
        raise RuntimeError("recorder down")

    await require(service(RecordingClient(), "shadow", hook=broken))


async def test_shadow_evaluation_reports_openfga_verdict():
    svc = service(RecordingClient(grants("can_run")), "shadow")
    verdict = await svc.evaluate(
        user(), "can_edit", AGENT, min_visible="can_run", resource_org_id=ORG
    )
    assert verdict is Verdict.FORBIDDEN


# 8.5: legacy mode never consults OpenFGA.
async def test_legacy_mode_applies_legacy_verdict_without_hook_or_client():
    samples: list[ShadowSample] = []
    client = RecordingClient(grants("can_run", "can_edit"))
    svc = service(client, "legacy", hook=samples.append)
    with pytest.raises(AppError) as caught:
        await require(svc, legacy=Verdict.FORBIDDEN)
    assert caught.value.code == "permission_required"
    assert (client.queries, samples) == ([], [])
    with pytest.raises(AuthzNotConsulted):
        await svc.check(user(), "can_run", AGENT)


async def test_enforce_types_limit_enforcement_to_listed_types():
    samples: list[ShadowSample] = []
    client = RecordingClient()
    svc = service(client, types=frozenset({"skill"}), hook=samples.append)
    await require(svc, legacy=Verdict.ALLOW)  # agent is not yet enforced
    assert client.queries == [] and len(samples) == 1
    skill = f"skill:{uuid4()}"
    with pytest.raises(AppError):
        await svc.require(
            user(),
            "can_edit",
            skill,
            min_visible="can_use",
            resource_org_id=ORG,
            legacy=Verdict.ALLOW,
        )


def test_from_settings_parses_mode_and_types():
    from app.config import settings as app_settings

    copied = app_settings.model_copy(
        update={"authz_mode": "enforce", "authz_enforce_types": " agent, skill ,"}
    )
    svc = AuthzService.from_settings(RecordingClient(), copied)
    assert (svc.mode, svc.enforce_types) == ("enforce", frozenset({"agent", "skill"}))


# 7.3: every query carries the condition context.
@pytest.mark.parametrize(
    ("principal", "subject", "context"),
    [
        (
            user(),
            f"user:{USER}",
            {"current_time": "2026-10-17T03:04:05Z", "run_source": "CHAT", "user_ip": NO_IP},
        ),
        (
            Principal(kind="agent", id=USER, org_id=ORG, run_source="TRIGGER", user_ip="10.0.0.7"),
            f"agent:{USER}",
            {
                "current_time": "2026-10-17T03:04:05Z",
                "run_source": "TRIGGER",
                "user_ip": "10.0.0.7",
            },
        ),
    ],
    ids=["user", "agent"],
)
async def test_condition_context_is_injected_into_every_query(principal, subject, context):
    client = RecordingClient()
    svc = service(client)
    await svc.check(principal, "can_run", AGENT)
    await svc.batch_check(principal, [("can_run", AGENT)])
    await svc.list_objects(principal, "can_run", "agent")
    assert len(client.queries) == 3
    assert all(q.user == subject and q.context == context for q in client.queries)


def test_condition_time_is_normalized_to_utc():
    offset = datetime(2026, 10, 17, 12, 4, 5, tzinfo=timezone(timedelta(hours=9)))
    svc = AuthzService(RecordingClient(), mode="enforce", clock=lambda: offset)
    assert svc.condition_context(user())["current_time"] == "2026-10-17T03:04:05Z"


@pytest.mark.parametrize("legacy", [Verdict.FORBIDDEN, Verdict.NOT_FOUND])
async def test_enforced_type_ignores_conflicting_legacy(legacy):
    await require(service(RecordingClient(grants("can_run", "can_edit"))), legacy=legacy)


@pytest.mark.parametrize(
    ("types", "consulted"),
    [(frozenset({"agent"}), False), (frozenset({"organization"}), True), (frozenset(), True)],
)
async def test_capabilities_enforced_only_with_organization_type(types, consulted):
    ctx = OrgContext(org_id=ORG, tenant_id=uuid4(), user_id=USER)
    client = RecordingClient()
    svc = service(client, types=types)
    if consulted:
        with pytest.raises(AppError):
            await svc.require_cap(ctx, "cap_create_agent", legacy=True)
    else:
        await svc.require_cap(ctx, "cap_create_agent", legacy=True)
    assert bool(client.queries) is consulted


def test_from_settings_rejects_unknown_enforce_types():
    from app.config import settings as app_settings

    copied = app_settings.model_copy(
        update={"authz_mode": "enforce", "authz_enforce_types": "agent,agnet"}
    )
    with pytest.raises(ValueError, match="agnet"):
        AuthzService.from_settings(RecordingClient(), copied)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.0.0.7", "10.0.0.7"),
        ("2001:db8::1", "2001:db8::1"),
        ("not-an-ip", NO_IP),
        ("10.0.0.7, 10.0.0.8", NO_IP),
        ("", NO_IP),
        (None, NO_IP),
    ],
)
def test_user_ip_is_validated_with_fallback(raw, expected):
    principal = Principal(kind="user", id=USER, org_id=ORG, user_ip=raw)
    assert service(RecordingClient()).condition_context(principal)["user_ip"] == expected
