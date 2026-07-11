"""Multi-user isolation matrix — User B must not observe User A's data.

ADR-016 §6 — every owner-scoped resource MUST 404 (not 403) when accessed
across users so the response shape doesn't leak existence (enumeration
oracle).

Each scenario uses two real cookie sessions against the unmodified app
(``raw_client``) to exercise the full JWT + service-layer ownership
filter path.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.config import settings
from app.models.model import Model
from tests.conftest import TestSession

# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------


async def _seed_default_model() -> str:
    """Insert a default Model row directly (POST /api/models is super-only)."""

    async with TestSession() as db:
        existing = (await db.execute(select(Model))).scalar_one_or_none()
        if existing:
            return str(existing.id)
        m = Model(
            provider="openai",
            model_name="gpt-4o",
            display_name="GPT-4o",
            is_default=True,
        )
        db.add(m)
        await db.commit()
        await db.refresh(m)
        return str(m.id)


class _Session:
    def __init__(self, csrf: str, cookies: dict[str, str]):
        self.csrf = csrf
        self.cookies = cookies

    def headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf}


async def _register(client: AsyncClient, *, email: str, super_first: bool = False) -> _Session:
    """Register a fresh user. ``super_first=True`` lets the first user be admin."""

    settings.allow_first_user_as_admin = super_first
    resp = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "correct horse", "name": email[:5]},
    )
    assert resp.status_code == 201, resp.text
    sess = _Session(
        csrf=resp.json()["csrf_token"],
        cookies={
            settings.cookie_name_access: resp.cookies[settings.cookie_name_access],
            settings.cookie_name_csrf: resp.cookies[settings.cookie_name_csrf],
        },
    )
    client.cookies.clear()
    return sess


def _apply(client: AsyncClient, sess: _Session) -> None:
    """Replace the client's cookies with this session's. Idempotent."""

    client.cookies.clear()
    for k, v in sess.cookies.items():
        client.cookies.set(k, v)


# ---------------------------------------------------------------------------
# 1. Cross-user agent access
# ---------------------------------------------------------------------------


async def _make_agent(
    client: AsyncClient,
    sess: _Session,
    model_id: str,
    *,
    identity_mode: str = "per_user",
) -> str:
    _apply(client, sess)
    resp = await client.post(
        "/api/agents",
        json={
            "name": "Owned Agent",
            "system_prompt": "hi",
            "model_id": model_id,
            "identity_mode": identity_mode,
        },
        headers=sess.headers(),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_user_b_cannot_get_user_a_agent(raw_client: AsyncClient):
    model_id = await _seed_default_model()
    a = await _register(raw_client, email="a@test.com")
    b = await _register(raw_client, email="b@test.com")
    agent_id = await _make_agent(raw_client, a, model_id)

    _apply(raw_client, b)
    resp = await raw_client.get(f"/api/agents/{agent_id}")
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_user_b_cannot_modify_user_a_agent(raw_client: AsyncClient):
    model_id = await _seed_default_model()
    a = await _register(raw_client, email="a@test.com")
    b = await _register(raw_client, email="b@test.com")
    agent_id = await _make_agent(raw_client, a, model_id)

    _apply(raw_client, b)
    upd = await raw_client.put(
        f"/api/agents/{agent_id}",
        json={"name": "Hijack"},
        headers=b.headers(),
    )
    assert upd.status_code == 404

    delete = await raw_client.delete(f"/api/agents/{agent_id}", headers=b.headers())
    assert delete.status_code == 404


@pytest.mark.asyncio
async def test_agent_list_is_per_user(raw_client: AsyncClient):
    model_id = await _seed_default_model()
    a = await _register(raw_client, email="a@test.com")
    b = await _register(raw_client, email="b@test.com")
    await _make_agent(raw_client, a, model_id)

    _apply(raw_client, b)
    resp = await raw_client.get("/api/agents")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# 2. Cross-user trigger access
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_b_cannot_modify_user_a_trigger(raw_client: AsyncClient):
    model_id = await _seed_default_model()
    a = await _register(raw_client, email="a@test.com")
    b = await _register(raw_client, email="b@test.com")
    agent_id = await _make_agent(raw_client, a, model_id, identity_mode="fixed")

    # User A creates an interval trigger via the agent-scoped path.
    _apply(raw_client, a)
    create = await raw_client.post(
        f"/api/agents/{agent_id}/triggers",
        json={
            "trigger_type": "interval",
            "schedule_config": {"interval_minutes": 60},
            "input_message": "hello",
        },
        headers=a.headers(),
    )
    assert create.status_code == 201, create.text
    trigger_id = create.json()["id"]

    # User B tries to update/delete via A's agent path. Service-level
    # ``user_id`` filter on the trigger row → 404.
    _apply(raw_client, b)
    upd = await raw_client.put(
        f"/api/agents/{agent_id}/triggers/{trigger_id}",
        json={"status": "paused"},
        headers=b.headers(),
    )
    assert upd.status_code == 404
    delete = await raw_client.delete(
        f"/api/agents/{agent_id}/triggers/{trigger_id}", headers=b.headers()
    )
    assert delete.status_code == 404

    # Global schedule center routes must apply the same trigger ownership filter.
    resp = await raw_client.get("/api/triggers", headers=b.headers())
    assert resp.status_code == 200
    assert resp.json() == []

    upd = await raw_client.patch(
        f"/api/triggers/{trigger_id}",
        json={"status": "paused"},
        headers=b.headers(),
    )
    assert upd.status_code == 404

    runs = await raw_client.get(f"/api/triggers/{trigger_id}/runs", headers=b.headers())
    assert runs.status_code == 404

    delete = await raw_client.delete(f"/api/triggers/{trigger_id}", headers=b.headers())
    assert delete.status_code == 404


# ---------------------------------------------------------------------------
# 3. System credentials — super_user gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regular_user_cannot_list_system_credentials(raw_client: AsyncClient):
    a = await _register(raw_client, email="a@test.com")
    _apply(raw_client, a)
    resp = await raw_client.get("/api/system-credentials")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_regular_user_cannot_create_system_credentials(raw_client: AsyncClient):
    a = await _register(raw_client, email="a@test.com")
    _apply(raw_client, a)
    resp = await raw_client.post(
        "/api/system-credentials",
        json={
            "definition_key": "anthropic",
            "name": "rogue system",
            "data": {"api_key": "k"},
        },
        headers=a.headers(),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_super_user_can_list_system_credentials(raw_client: AsyncClient):
    """First user with ``allow_first_user_as_admin=True`` is super_user."""

    super_user = await _register(raw_client, email="boss@test.com", super_first=True)
    _apply(raw_client, super_user)
    resp = await raw_client.get("/api/system-credentials")
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# 4. Models / templates — super_user mutation gates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regular_user_cannot_create_model(raw_client: AsyncClient):
    a = await _register(raw_client, email="a@test.com")
    _apply(raw_client, a)
    resp = await raw_client.post(
        "/api/models",
        json={
            "provider": "openai",
            "model_name": "gpt-4o",
            "display_name": "rogue",
        },
        headers=a.headers(),
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 5. Cross-user usage / spend visibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_b_cannot_read_user_a_agent_usage(raw_client: AsyncClient):
    model_id = await _seed_default_model()
    a = await _register(raw_client, email="a@test.com")
    b = await _register(raw_client, email="b@test.com")
    agent_id = await _make_agent(raw_client, a, model_id)

    _apply(raw_client, b)
    resp = await raw_client.get(f"/api/agents/{agent_id}/usage")
    # Either 404 (preferred) or zero-aggregate empty body. Spec mandates
    # that we never expose A's totals to B.
    if resp.status_code == 200:
        body = resp.json()
        # Must be empty / zeroed, not A's actual numbers.
        assert body.get("total_tokens", 0) == 0
        assert body.get("total_cost", 0) == 0
    else:
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 6. Cross-user conversation access
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_b_cannot_modify_user_a_conversation(raw_client: AsyncClient):
    """Synthesize a conversation row owned by A; B's PATCH/DELETE → 404."""

    from app.models.conversation import Conversation

    model_id = await _seed_default_model()
    a = await _register(raw_client, email="a@test.com")
    b = await _register(raw_client, email="b@test.com")
    agent_id = await _make_agent(raw_client, a, model_id)

    async with TestSession() as db:
        conv = Conversation(id=uuid.uuid4(), agent_id=uuid.UUID(agent_id), title="t")
        db.add(conv)
        await db.commit()
        conv_id = str(conv.id)

    _apply(raw_client, b)
    upd = await raw_client.patch(
        f"/api/conversations/{conv_id}",
        json={"title": "hijack"},
        headers=b.headers(),
    )
    assert upd.status_code == 404
    delete = await raw_client.delete(f"/api/conversations/{conv_id}", headers=b.headers())
    assert delete.status_code == 404


@pytest.mark.asyncio
async def test_user_b_cannot_touch_user_a_conversation_surfaces(raw_client: AsyncClient):
    """BE-D1 gate matrix — every ``owned_conversation``-guarded route → 404 for B.

    Locks the routes converted in PR #292 (runs/active, runs/{id}, share,
    followup, read, switch-branch) so dropping the dependency from a route
    decorator/signature regresses loudly instead of opening an IDOR.
    """

    from app.models.conversation import Conversation

    model_id = await _seed_default_model()
    a = await _register(raw_client, email="conv-a@test.com")
    b = await _register(raw_client, email="conv-b@test.com")
    agent_id = await _make_agent(raw_client, a, model_id)

    async with TestSession() as db:
        conv = Conversation(id=uuid.uuid4(), agent_id=uuid.UUID(agent_id), title="t")
        db.add(conv)
        await db.commit()
        conv_id = str(conv.id)

    run_id = "00000000-0000-0000-0000-0000000000aa"

    _apply(raw_client, a)
    owner_resp = await raw_client.get(f"/api/conversations/{conv_id}/runs/active")
    assert owner_resp.status_code == 200  # sanity: gate passes for the owner

    _apply(raw_client, b)
    get_cases = [
        f"/api/conversations/{conv_id}/runs/active",
        f"/api/conversations/{conv_id}/runs/{run_id}",
        f"/api/conversations/{conv_id}/runs/{run_id}/stream",
        f"/api/conversations/{conv_id}/runs/{run_id}/ag-ui-stream",
        f"/api/conversations/{conv_id}/share",
        f"/api/conversations/{conv_id}/messages",
    ]
    for url in get_cases:
        resp = await raw_client.get(url)
        assert resp.status_code == 404, url
        assert resp.json()["error"]["code"] == "CONVERSATION_NOT_FOUND", url

    post_cases = [
        (f"/api/conversations/{conv_id}/followup-suggestion", None),
        (f"/api/conversations/{conv_id}/read", None),
        (
            f"/api/conversations/{conv_id}/messages/switch-branch",
            {"checkpoint_id": "cp-1"},
        ),
    ]
    for url, payload in post_cases:
        resp = await raw_client.post(url, json=payload, headers=b.headers())
        assert resp.status_code == 404, url
        assert resp.json()["error"]["code"] == "CONVERSATION_NOT_FOUND", url

    # 의도된 계약 (PR #292): 소유권 게이트가 body 검증보다 먼저 실행되므로,
    # 미소유 대화 + invalid body 는 422 가 아니라 404 다 — 미소유 리소스에
    # validation oracle 을 노출하지 않는다. (Depends 전환의 내재적 순서:
    # sub-dependency → body 검증. 구 인라인 체크 시절에는 422 가 먼저였다.)
    invalid_body = await raw_client.patch(
        f"/api/conversations/{conv_id}",
        json={"title": 12345},
        headers=b.headers(),
    )
    assert invalid_body.status_code == 404
    assert invalid_body.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"

    # artifacts DELETE — #282 전환분 중 유일한 mutation 게이트 (R2에서
    # decorator → 파라미터 위치로 정렬). 게이트 자체가 빠지면 여기서 잡는다.
    artifact_delete = await raw_client.delete(
        f"/api/conversations/{conv_id}/artifacts/{uuid.uuid4()}",
        headers=b.headers(),
    )
    assert artifact_delete.status_code == 404
    assert artifact_delete.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"
