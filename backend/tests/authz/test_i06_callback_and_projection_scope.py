"""I-06: the OAuth callback and MCP App projection reads bind their company."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials.creation import CredentialCreation
from app.credentials.oauth_callback_scope import live_callback_state
from app.database import SCOPE_INFO_KEY
from app.mcp.apps_projection_store import OwnerCache, load_verified_artifact
from app.models.credential_oauth_state import CredentialOAuthState
from tests.authz.agent_execution_runtime_fixture import seed_runtime_run
from tests.authz.unit_scope_fixture import TEST_TENANT_ID, create_test_credential, unit_context
from tests.conftest import TEST_USER_ID, TestSession

COMPANY = {"app.tenant_id": str(TEST_TENANT_ID), "app.user_id": str(TEST_USER_ID)}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _seed_state(raw: str) -> None:
    await seed_runtime_run()
    async with TestSession() as db:
        cred = await create_test_credential(
            db,
            CredentialCreation(
                definition_key="mcp_oauth2",
                name="OAuth",
                data={"access_token_url": "https://id.example/token", "client_id": "cid"},
            ),
            unit_context(TEST_USER_ID),
        )
        db.add(
            CredentialOAuthState(
                state_hash=hashlib.sha256(raw.encode()).hexdigest(),
                credential_id=cred.id,
                user_id=TEST_USER_ID,
                redirect_uri="http://test/api/oauth2-credential/callback",
                origin="credential",
                expires_at=_now() + timedelta(minutes=10),
            )
        )
        await db.commit()


async def test_valid_callback_state_binds_its_credential_company() -> None:
    await _seed_state("state-i06")
    async with TestSession() as db:
        pending = await live_callback_state(db, hashlib.sha256(b"state-i06").hexdigest(), _now())
        assert pending is not None
        assert db.info[SCOPE_INFO_KEY] == COMPANY


async def test_unknown_or_expired_callback_state_leaves_the_session_unbound() -> None:
    await _seed_state("state-expired")
    async with TestSession() as db:
        digest = hashlib.sha256(b"state-expired").hexdigest()
        assert await live_callback_state(db, digest, _now() + timedelta(hours=1)) is None
        assert await live_callback_state(db, "0" * 64, _now()) is None
        assert db.info.get(SCOPE_INFO_KEY) is None


class _Recorder:
    def __init__(self) -> None:
        self.scopes: list[object] = []

    def __call__(self) -> AsyncSession:
        session = TestSession()
        self.scopes.append(session.info)
        return session


async def test_projection_binding_is_read_in_the_conversation_company() -> None:
    _run, _agent, conversation = await seed_runtime_run()
    recorder = _Recorder()
    loaded = await load_verified_artifact(
        conversation_id=str(conversation.id),
        run_id=str(uuid.uuid4()),
        tool_call_id="call-1",
        binding_id=str(uuid.uuid4()),
        session_factory=recorder,
    )
    assert loaded is None
    scopes = [info.get(SCOPE_INFO_KEY) for info in recorder.scopes]  # type: ignore[attr-defined]
    assert scopes == [{"app.scope": "platform"}, COMPANY]


async def test_projection_for_an_unknown_conversation_never_opens_a_company_session() -> None:
    recorder = _Recorder()
    loaded = await load_verified_artifact(
        conversation_id=str(uuid.uuid4()),
        run_id=str(uuid.uuid4()),
        tool_call_id="call-1",
        binding_id=str(uuid.uuid4()),
        session_factory=recorder,
    )
    assert loaded is None
    assert len(recorder.scopes) == 1


async def test_projection_looks_up_the_conversation_company_once_per_pass() -> None:
    _run, _agent, conversation = await seed_runtime_run()
    recorder = _Recorder()
    owner_cache: OwnerCache = {}
    for tool_call_id in ("call-1", "call-2"):
        await load_verified_artifact(
            conversation_id=str(conversation.id),
            run_id=str(uuid.uuid4()),
            tool_call_id=tool_call_id,
            binding_id=str(uuid.uuid4()),
            session_factory=recorder,
            owner_cache=owner_cache,
        )
    scopes = [info.get(SCOPE_INFO_KEY) for info in recorder.scopes]  # type: ignore[attr-defined]
    assert scopes == [{"app.scope": "platform"}, COMPANY, COMPANY]
