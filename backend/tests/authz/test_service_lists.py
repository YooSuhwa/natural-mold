"""Plan 8.2 list helpers: batch checks, visibility filtering, my_permissions."""

from uuid import uuid4

import pytest

from app.authz.service import ShadowSample, Verdict
from app.exceptions import AppError
from tests.authz.authz_service_testkit import (
    AGENT,
    RecordingClient,
    grants,
    service,
    user,
)


# 8.2 helpers built on batch checks.
async def test_batch_check_deduplicates_and_maps_results():
    other = f"agent:{uuid4()}"
    client = RecordingClient(grants("can_run"))
    result = await service(client).batch_check(
        user(), [("can_run", AGENT), ("can_run", other), ("can_run", AGENT)]
    )
    assert result == {("can_run", AGENT): True, ("can_run", other): False}
    assert len(client.queries) == 2


async def test_filter_visible_and_my_permissions_keep_input_order():
    other = f"agent:{uuid4()}"
    client = RecordingClient(grants("can_run", "can_share") | grants("can_run", obj=other))
    svc = service(client)
    assert await svc.filter_visible(user(), "can_run", [other, "agent:x", AGENT], legacy=[]) == [
        other,
        AGENT,
    ]
    perms = ["can_share", "can_edit", "can_run"]
    assert await svc.my_permissions(user(), AGENT, perms, legacy=[]) == ["can_share", "can_run"]


async def test_list_objects_returns_set():
    client = RecordingClient(grants("can_run"))
    assert await service(client).list_objects(user(), "can_run", "agent") == {AGENT}


LIST_OBJS = [AGENT, "agent:x"]


@pytest.mark.parametrize(
    ("mode", "allowed", "expected", "consulted", "samples_expected"),
    [
        ("legacy", grants("can_run", obj="agent:x"), [AGENT], False, 0),
        ("shadow", grants("can_run", obj="agent:x"), [AGENT], False, 2),
        ("enforce", grants("can_run", obj="agent:x"), ["agent:x"], True, 0),
    ],
)
async def test_filter_visible_is_mode_aware(mode, allowed, expected, consulted, samples_expected):
    samples: list[ShadowSample] = []
    client = RecordingClient(allowed)
    svc = service(client, mode, hook=samples.append)
    result = await svc.filter_visible(user(), "can_run", LIST_OBJS, legacy=[AGENT])
    assert result == expected
    assert bool(client.queries) is consulted
    assert len(samples) == samples_expected
    if samples:
        assert [(s.obj, s.legacy) for s in samples] == [
            (AGENT, Verdict.ALLOW),
            ("agent:x", Verdict.NOT_FOUND),
        ]


@pytest.mark.parametrize(
    ("mode", "expected", "consulted", "samples_expected"),
    [
        ("legacy", ["can_edit"], False, 0),
        ("shadow", ["can_edit"], False, 2),
        ("enforce", ["can_run"], True, 0),
    ],
)
async def test_my_permissions_is_mode_aware(mode, expected, consulted, samples_expected):
    samples: list[ShadowSample] = []
    client = RecordingClient(grants("can_run"))
    svc = service(client, mode, hook=samples.append)
    result = await svc.my_permissions(user(), AGENT, ["can_run", "can_edit"], legacy=["can_edit"])
    assert result == expected
    assert bool(client.queries) is consulted
    assert len(samples) == samples_expected
    if samples:
        assert [(s.permission, s.legacy) for s in samples] == [
            ("can_run", Verdict.FORBIDDEN),
            ("can_edit", Verdict.ALLOW),
        ]


@pytest.mark.parametrize("operation", ["filter_visible", "my_permissions"])
async def test_list_helpers_outage_in_enforce_is_authz_unavailable(operation):
    svc = service(RecordingClient(unavailable=True))
    calls = {
        "filter_visible": lambda: svc.filter_visible(user(), "can_run", [AGENT], legacy=[AGENT]),
        "my_permissions": lambda: svc.my_permissions(
            user(), AGENT, ["can_run"], legacy=["can_run"]
        ),
    }
    with pytest.raises(AppError) as caught:
        await calls[operation]()
    assert (caught.value.status, caught.value.code) == (503, "authz_unavailable")


@pytest.mark.parametrize("mode", ["legacy", "shadow", "enforce"])
async def test_filter_visible_empty_page_never_consults(mode):
    client = RecordingClient()
    assert await service(client, mode).filter_visible(user(), "can_run", [], legacy=[]) == []
    assert client.queries == []
