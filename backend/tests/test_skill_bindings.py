"""ADR-017 Slice D — skill credential binding API + validate_binding rules.

Covers:

* ``GET /api/skills/{id}/credential-requirements`` — empty + populated
* ``GET /api/skills/{id}/credential-bindings`` — empty
* ``PUT /api/skills/{id}/credential-bindings/{key}`` — happy path + 4
   rejection rules (cross-user credential, definition_key mismatch,
   system credential, unknown requirement key)
* ``DELETE`` — idempotent (missing key returns 204, not 404)

Test layout follows existing patterns (``tests/test_skills.py``,
``tests/test_credential_resolver.py``): SQLite in-memory + ``client``
fixture from ``conftest`` (auth + CSRF overridden) + direct DB writes
for fixture setup.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.credential import Credential
from app.models.skill import Skill
from app.models.user import User
from tests.conftest import TEST_USER_ID

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


_SRT_REQUIREMENT = {
    "key": "srt_login",
    "definition_key": "srt_account",
    "required": True,
    "label": "SRT 계정",
    "description": "예매에 사용할 SRT 회원 정보",
    "fields": ["username", "password"],
    "injection": "env",
    "scope": "user",
    "env_map": {"SRT_USERNAME": "username", "SRT_PASSWORD": "password"},
}


async def _make_test_user(db: AsyncSession) -> uuid.UUID:
    """Ensure the override user row exists (FK target for skills/creds)."""

    user = await db.get(User, TEST_USER_ID)
    if user is not None:
        return user.id
    user = User(
        id=TEST_USER_ID,
        email="test@test.com",
        name="Test",
        hashed_password="h",
        is_active=True,
        is_super_user=True,
    )
    db.add(user)
    await db.flush()
    return user.id


async def _make_user(
    db: AsyncSession, *, email: str = "other@test.com"
) -> uuid.UUID:
    user = User(
        id=uuid.uuid4(),
        email=email,
        name="O",
        hashed_password="h",
        is_active=True,
        is_super_user=False,
    )
    db.add(user)
    await db.flush()
    return user.id


async def _make_skill(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    requirements: list[dict] | None = None,
    slug: str | None = None,
) -> Skill:
    skill = Skill(
        id=uuid.uuid4(),
        user_id=user_id,
        name="srt-booker",
        slug=slug or f"srt-booker-{uuid.uuid4().hex[:8]}",
        kind="package",
        storage_path=None,
        content_hash=None,
        size_bytes=0,
        credential_requirements=requirements,
    )
    db.add(skill)
    await db.flush()
    return skill


async def _make_credential(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    definition_key: str,
    is_system: bool = False,
    name: str = "cred",
) -> Credential:
    cred = Credential(
        id=uuid.uuid4(),
        user_id=user_id,
        definition_key=definition_key,
        name=name,
        data_encrypted="opaque",
        key_id="kv1",
        field_keys=[],
        is_shared=False,
        is_system=is_system,
        status="active",
    )
    db.add(cred)
    await db.flush()
    return cred


# ---------------------------------------------------------------------------
# GET credential-requirements
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requirements_empty_when_skill_has_none(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _make_test_user(db)
    skill = await _make_skill(db, user_id=TEST_USER_ID, requirements=None)
    await db.commit()

    r = await client.get(f"/api/skills/{skill.id}/credential-requirements")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_requirements_lists_skill_entries(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _make_test_user(db)
    skill = await _make_skill(
        db, user_id=TEST_USER_ID, requirements=[_SRT_REQUIREMENT]
    )
    await db.commit()

    r = await client.get(f"/api/skills/{skill.id}/credential-requirements")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["key"] == "srt_login"
    assert body[0]["definition_key"] == "srt_account"
    assert body[0]["required"] is True
    # ``env_map`` is NOT exposed on the OUT schema — it's a publish-time
    # detail. Confirm the public projection stays minimal.
    assert "env_map" not in body[0]


# ---------------------------------------------------------------------------
# GET credential-bindings — initially empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bindings_empty_for_new_skill(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _make_test_user(db)
    skill = await _make_skill(
        db, user_id=TEST_USER_ID, requirements=[_SRT_REQUIREMENT]
    )
    await db.commit()

    r = await client.get(f"/api/skills/{skill.id}/credential-bindings")
    assert r.status_code == 200
    assert r.json() == []


# ---------------------------------------------------------------------------
# PUT — happy path + idempotent update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_binding_create_and_update_idempotent(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _make_test_user(db)
    skill = await _make_skill(
        db, user_id=TEST_USER_ID, requirements=[_SRT_REQUIREMENT]
    )
    cred1 = await _make_credential(
        db, user_id=TEST_USER_ID, definition_key="srt_account"
    )
    cred2 = await _make_credential(
        db,
        user_id=TEST_USER_ID,
        definition_key="srt_account",
        name="cred2",
    )
    await db.commit()

    # First write — create.
    r1 = await client.put(
        f"/api/skills/{skill.id}/credential-bindings/srt_login",
        json={"credential_id": str(cred1.id)},
    )
    assert r1.status_code == 200
    body1 = r1.json()
    assert body1["requirement_key"] == "srt_login"
    assert body1["credential_id"] == str(cred1.id)

    # Second write — same key, different credential → update.
    r2 = await client.put(
        f"/api/skills/{skill.id}/credential-bindings/srt_login",
        json={"credential_id": str(cred2.id)},
    )
    assert r2.status_code == 200
    assert r2.json()["id"] == body1["id"]
    assert r2.json()["credential_id"] == str(cred2.id)

    # List shows exactly one binding.
    rl = await client.get(f"/api/skills/{skill.id}/credential-bindings")
    assert rl.status_code == 200
    rows = rl.json()
    assert len(rows) == 1
    assert rows[0]["credential_id"] == str(cred2.id)


# ---------------------------------------------------------------------------
# PUT — rejection rules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_binding_rejects_other_user_credential(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _make_test_user(db)
    other = await _make_user(db, email="other@test.com")
    skill = await _make_skill(
        db, user_id=TEST_USER_ID, requirements=[_SRT_REQUIREMENT]
    )
    other_cred = await _make_credential(
        db, user_id=other, definition_key="srt_account"
    )
    await db.commit()

    r = await client.put(
        f"/api/skills/{skill.id}/credential-bindings/srt_login",
        json={"credential_id": str(other_cred.id)},
    )
    # Cross-user credentials surface as ``credential_not_found`` (404)
    # so we don't leak existence (rules/security.md).
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "CREDENTIAL_NOT_FOUND"


@pytest.mark.asyncio
async def test_binding_rejects_definition_key_mismatch(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _make_test_user(db)
    skill = await _make_skill(
        db, user_id=TEST_USER_ID, requirements=[_SRT_REQUIREMENT]
    )
    wrong = await _make_credential(
        db, user_id=TEST_USER_ID, definition_key="anthropic"
    )
    await db.commit()

    r = await client.put(
        f"/api/skills/{skill.id}/credential-bindings/srt_login",
        json={"credential_id": str(wrong.id)},
    )
    # ValidationError → 422 (see app/exceptions.py).
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "MARKETPLACE_CREDENTIAL_MISMATCH"


@pytest.mark.asyncio
async def test_binding_rejects_system_credential(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _make_test_user(db)
    skill = await _make_skill(
        db, user_id=TEST_USER_ID, requirements=[_SRT_REQUIREMENT]
    )
    sys_cred = await _make_credential(
        db,
        user_id=None,
        definition_key="srt_account",
        is_system=True,
        name="sys-srt",
    )
    await db.commit()

    r = await client.put(
        f"/api/skills/{skill.id}/credential-bindings/srt_login",
        json={"credential_id": str(sys_cred.id)},
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "MARKETPLACE_CREDENTIAL_MISMATCH"


@pytest.mark.asyncio
async def test_binding_unknown_requirement_key_returns_400(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _make_test_user(db)
    skill = await _make_skill(
        db, user_id=TEST_USER_ID, requirements=[_SRT_REQUIREMENT]
    )
    cred = await _make_credential(
        db, user_id=TEST_USER_ID, definition_key="srt_account"
    )
    await db.commit()

    r = await client.put(
        f"/api/skills/{skill.id}/credential-bindings/unknown_key",
        json={"credential_id": str(cred.id)},
    )
    # ValidationError → 422 (per app/exceptions.py); description hints at
    # the unknown key for ops.
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "MARKETPLACE_CREDENTIAL_MISMATCH"


# ---------------------------------------------------------------------------
# DELETE — idempotent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_binding_idempotent(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _make_test_user(db)
    skill = await _make_skill(
        db, user_id=TEST_USER_ID, requirements=[_SRT_REQUIREMENT]
    )
    cred = await _make_credential(
        db, user_id=TEST_USER_ID, definition_key="srt_account"
    )
    await db.commit()

    # Create binding.
    r1 = await client.put(
        f"/api/skills/{skill.id}/credential-bindings/srt_login",
        json={"credential_id": str(cred.id)},
    )
    assert r1.status_code == 200

    # First delete — wipes the row.
    r2 = await client.delete(
        f"/api/skills/{skill.id}/credential-bindings/srt_login"
    )
    assert r2.status_code == 204

    # Second delete — already gone, still 204 (idempotent — see
    # rules/security.md: do not leak existence via DELETE).
    r3 = await client.delete(
        f"/api/skills/{skill.id}/credential-bindings/srt_login"
    )
    assert r3.status_code == 204
