"""M5 Slice E Stage 3 — Credential env injection (Phase 1 출시 게이트).

Spec §8.2~§8.4 + deletion-analysis §1.(b). Targets the resolution surface
젠슨 shipped in Stage 3:

* ``app.marketplace.credential_requirements.resolve_credential_bindings``
* ``app.marketplace.credential_requirements.build_runtime_env``
* ``app.marketplace.skill_runtime.resolve_runtime_credentials``

Test contract:

1. **Fail-fast**: a required ``user``-scope binding with no
   ``SkillCredentialBinding`` row AND no ``agent_skills.config`` override
   raises ``marketplace_credential_required`` 409 before the LLM run.
2. **Optional skip**: ``required=False`` requirements without a binding
   pass through silently (no missing entry surfaced).
3. **Mapped env only**: ``build_runtime_env`` emits exactly the env-var
   keys declared in ``env_map`` — other user credentials never leak.
4. **Override priority** (Spec §8.4): ``agent_skills.config.credential_bindings.{key}``
   > ``SkillCredentialBinding.scope='skill'`` > missing.
5. **Ownership drift** (silent missing): a credential row whose
   ``user_id`` no longer matches the caller is treated as missing
   regardless of the binding row that referenced it.
6. **Decrypt isolation**: decrypted values never round-trip into API
   responses or DB writes — the `data_encrypted` blob is the only
   persisted form. (Paired with redaction in stage 4.)
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.marketplace.credential_requirements import (
    build_runtime_env,
    resolve_credential_bindings,
)
from app.models.credential import Credential
from app.models.marketplace import SkillCredentialBinding
from app.models.skill import Skill
from app.models.user import User
from tests.conftest import TEST_USER_ID, TestSession

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSession() as session:
        yield session


@pytest.fixture
async def seeded_user(db_session: AsyncSession) -> uuid.UUID:
    """Ensure the test user exists (Credential.user_id FK)."""

    existing = (
        await db_session.execute(select(User).where(User.id == TEST_USER_ID))
    ).scalar_one_or_none()
    if existing is None:
        db_session.add(
            User(id=TEST_USER_ID, email="test@test.com", name="Test User")
        )
        await db_session.commit()
    return TEST_USER_ID


def _make_skill(
    *,
    user_id: uuid.UUID,
    name: str = "srt-booking",
    requirements: list[dict] | None = None,
) -> Skill:
    return Skill(
        id=uuid.uuid4(),
        user_id=user_id,
        name=name,
        slug=name,
        description=None,
        kind="package",
        storage_path="/tmp/skill",
        content_hash="0" * 64,
        size_bytes=0,
        credential_requirements=requirements,
    )


def _srt_requirement_dict(*, required: bool = True) -> dict:
    """SRT user-credential requirement matching Spec §6."""

    return {
        "key": "srt_account",
        "definition_key": "srt_account",
        "required": required,
        "label": "SRT login",
        "fields": ["username", "password"],
        "injection": "env",
        "scope": "user",
        "env_map": {
            "username": "KSKILL_SRT_ID",
            "password": "KSKILL_SRT_PASSWORD",
        },
    }


def _coupang_optional_requirement_dict() -> dict:
    return {
        "key": "coupang_partners",
        "definition_key": "coupang_partners",
        "required": False,
        "label": "Coupang Partners",
        "fields": ["access_key", "secret_key"],
        "injection": "env",
        "scope": "user",
        "env_map": {
            "access_key": "COUPANG_ACCESS_KEY",
            "secret_key": "COUPANG_SECRET_KEY",
        },
    }


def _persist_credential(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    definition_key: str,
    data: dict[str, str],
    name: str = "test cred",
) -> Credential:
    """Encrypt + add a credential row. Caller is responsible for commit."""

    blob, key_id, field_keys = credential_service.encrypt_data(data)
    cred = Credential(
        id=uuid.uuid4(),
        user_id=user_id,
        definition_key=definition_key,
        name=name,
        data_encrypted=blob,
        key_id=key_id,
        field_keys=field_keys,
        is_system=False,
    )
    db.add(cred)
    return cred


# ===========================================================================
# Fail-fast on missing required credential
# ===========================================================================


class TestMissingRequiredCredential:
    @pytest.mark.asyncio
    async def test_missing_required_credential_fails_fast(
        self, db_session: AsyncSession, seeded_user: uuid.UUID
    ) -> None:
        """``required=True`` requirement with no binding → resolve emits
        missing key; ``build_runtime_env`` raises 409."""

        skill = _make_skill(
            user_id=seeded_user,
            requirements=[_srt_requirement_dict(required=True)],
        )
        db_session.add(skill)
        await db_session.commit()

        resolved, missing = await resolve_credential_bindings(
            db_session, skill=skill, user_id=seeded_user
        )
        assert resolved == {}
        assert missing == ["srt_account"]

        # build_runtime_env converts the same input into the documented
        # 409 — chat_service uses this before any LLM tokens are spent.
        from app.exceptions import ConflictError
        from app.marketplace.credential_requirements import _LightUser

        with pytest.raises(ConflictError) as exc_info:
            await build_runtime_env(
                db_session,
                skill=skill,
                user=_LightUser(seeded_user),  # type: ignore[arg-type]
            )
        assert exc_info.value.code == "MARKETPLACE_CREDENTIAL_REQUIRED"
        assert exc_info.value.status == 409
        assert "srt_account" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_optional_credential_can_be_absent(
        self, db_session: AsyncSession, seeded_user: uuid.UUID
    ) -> None:
        """``required=False`` requirements with no binding pass through —
        progress.txt L48 (coupang_partners is optional)."""

        skill = _make_skill(
            user_id=seeded_user,
            requirements=[_coupang_optional_requirement_dict()],
        )
        db_session.add(skill)
        await db_session.commit()

        resolved, missing = await resolve_credential_bindings(
            db_session, skill=skill, user_id=seeded_user
        )
        # Optional requirement with no binding → not in resolved, not in
        # missing. The skill executes; the env-var simply isn't set.
        assert resolved == {}
        assert missing == []

        from app.marketplace.credential_requirements import _LightUser

        env = await build_runtime_env(
            db_session,
            skill=skill,
            user=_LightUser(seeded_user),  # type: ignore[arg-type]
        )
        assert env == {}, "optional credential leaked into runtime env"


# ===========================================================================
# Env scope — mapped vars only, no host env bleed
# ===========================================================================


class TestEnvInjectionScope:
    @pytest.mark.asyncio
    async def test_only_mapped_env_var_injected(
        self, db_session: AsyncSession, seeded_user: uuid.UUID
    ) -> None:
        """User has TWO bindings (srt + ktx). Skill requires only srt.

        ``build_runtime_env`` must emit ONLY the srt env vars. The user's
        ktx credential — although owned + active — does not appear."""

        srt_cred = _persist_credential(
            db_session,
            user_id=seeded_user,
            definition_key="srt_account",
            data={"username": "srt-id-A", "password": "srt-pw-A"},
        )
        ktx_cred = _persist_credential(
            db_session,
            user_id=seeded_user,
            definition_key="ktx_account",
            data={"username": "ktx-id-A", "password": "ktx-pw-A"},
        )
        skill = _make_skill(
            user_id=seeded_user,
            requirements=[_srt_requirement_dict(required=True)],
        )
        db_session.add(skill)
        await db_session.flush()
        # Bind both — only srt should be consulted (the requirement list
        # has no ktx_account entry).
        db_session.add_all(
            [
                SkillCredentialBinding(
                    id=uuid.uuid4(),
                    skill_id=skill.id,
                    user_id=seeded_user,
                    requirement_key="srt_account",
                    credential_id=srt_cred.id,
                    scope="skill",
                ),
                SkillCredentialBinding(
                    id=uuid.uuid4(),
                    skill_id=skill.id,
                    user_id=seeded_user,
                    requirement_key="ktx_account",
                    credential_id=ktx_cred.id,
                    scope="skill",
                ),
            ]
        )
        await db_session.commit()

        from app.marketplace.credential_requirements import _LightUser

        env = await build_runtime_env(
            db_session,
            skill=skill,
            user=_LightUser(seeded_user),  # type: ignore[arg-type]
        )
        assert env == {
            "KSKILL_SRT_ID": "srt-id-A",
            "KSKILL_SRT_PASSWORD": "srt-pw-A",
        }, f"unexpected env: {env}"
        # Defense in depth — KTX values are absent.
        assert all(not k.startswith("KSKILL_KTX") for k in env)

    @pytest.mark.asyncio
    async def test_subprocess_env_does_not_include_unrelated_env(
        self,
        db_session: AsyncSession,
        seeded_user: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Parent process env (set here via ``monkeypatch.setenv``) must
        NOT show up in ``build_runtime_env`` output.

        The executor builds the subprocess env from the explicit dict
        produced here plus a fixed base (PATH/PYTHONPATH/HOME/SKILL_OUTPUT_DIR/OUTPUTS_DIR)
        — see ``executor.py:185-191``. Nothing from os.environ leaks."""

        monkeypatch.setenv("SECRET_PASTE", "host-leak-value")
        skill = _make_skill(user_id=seeded_user, requirements=[])
        db_session.add(skill)
        await db_session.commit()

        from app.marketplace.credential_requirements import _LightUser

        env = await build_runtime_env(
            db_session,
            skill=skill,
            user=_LightUser(seeded_user),  # type: ignore[arg-type]
        )
        assert "SECRET_PASTE" not in env, (
            "host env var leaked through build_runtime_env"
        )
        assert env == {}


# ===========================================================================
# Override priority — agent_skills.config > SkillCredentialBinding
# ===========================================================================


class TestOverridePrecedence:
    @pytest.mark.asyncio
    async def test_agent_skill_config_override_wins(
        self, db_session: AsyncSession, seeded_user: uuid.UUID
    ) -> None:
        """Two srt credentials A and B exist. SkillCredentialBinding
        points at A; ``agent_skills.config.credential_bindings.srt_account``
        points at B. Override (B) must win — Spec §8.4 priority."""

        cred_a = _persist_credential(
            db_session,
            user_id=seeded_user,
            definition_key="srt_account",
            data={"username": "default-A", "password": "default-A-pw"},
            name="A",
        )
        cred_b = _persist_credential(
            db_session,
            user_id=seeded_user,
            definition_key="srt_account",
            data={"username": "override-B", "password": "override-B-pw"},
            name="B",
        )
        skill = _make_skill(
            user_id=seeded_user,
            requirements=[_srt_requirement_dict(required=True)],
        )
        db_session.add(skill)
        await db_session.flush()
        db_session.add(
            SkillCredentialBinding(
                id=uuid.uuid4(),
                skill_id=skill.id,
                user_id=seeded_user,
                requirement_key="srt_account",
                credential_id=cred_a.id,
                scope="skill",
            )
        )
        await db_session.commit()

        resolved, missing = await resolve_credential_bindings(
            db_session,
            skill=skill,
            user_id=seeded_user,
            agent_skill_config={
                "credential_bindings": {"srt_account": str(cred_b.id)}
            },
        )
        assert missing == []
        entry = resolved["srt_account"]
        assert entry.credential_id == cred_b.id, "override binding lost"
        assert entry.decrypted == {
            "username": "override-B",
            "password": "override-B-pw",
        }

    @pytest.mark.asyncio
    async def test_skill_binding_fallback_when_no_override(
        self, db_session: AsyncSession, seeded_user: uuid.UUID
    ) -> None:
        """No override → SkillCredentialBinding wins. Catches the inverse
        bug where override is treated as mandatory."""

        cred_default = _persist_credential(
            db_session,
            user_id=seeded_user,
            definition_key="srt_account",
            data={"username": "default-only", "password": "pw"},
        )
        skill = _make_skill(
            user_id=seeded_user,
            requirements=[_srt_requirement_dict(required=True)],
        )
        db_session.add(skill)
        await db_session.flush()
        db_session.add(
            SkillCredentialBinding(
                id=uuid.uuid4(),
                skill_id=skill.id,
                user_id=seeded_user,
                requirement_key="srt_account",
                credential_id=cred_default.id,
                scope="skill",
            )
        )
        await db_session.commit()

        resolved, missing = await resolve_credential_bindings(
            db_session,
            skill=skill,
            user_id=seeded_user,
            agent_skill_config=None,
        )
        assert missing == []
        assert resolved["srt_account"].credential_id == cred_default.id

        # Empty override dict ≠ "no override" — make sure an empty dict
        # also falls through to the SkillCredentialBinding.
        resolved2, missing2 = await resolve_credential_bindings(
            db_session,
            skill=skill,
            user_id=seeded_user,
            agent_skill_config={"credential_bindings": {}},
        )
        assert missing2 == []
        assert resolved2["srt_account"].credential_id == cred_default.id


# ===========================================================================
# Value confinement
# ===========================================================================


class TestValueConfinement:
    @pytest.mark.asyncio
    async def test_decrypted_value_never_appears_in_credential_row(
        self, db_session: AsyncSession, seeded_user: uuid.UUID
    ) -> None:
        """The persisted ``credentials`` row stores only the encrypted
        blob. Decrypted plaintext exists only in transient resolution
        output — never in DB. Pairs with stage 4 redaction tests for the
        log/SSE/exception channels."""

        plaintext_marker = "SUPER_SECRET_MARKER_xyz_9999"
        cred = _persist_credential(
            db_session,
            user_id=seeded_user,
            definition_key="srt_account",
            data={"username": "id", "password": plaintext_marker},
        )
        await db_session.commit()

        # The persisted row stores only the ciphertext.
        row = await db_session.get(Credential, cred.id)
        assert row is not None
        assert plaintext_marker not in row.data_encrypted, (
            "plaintext credential value leaked into data_encrypted column"
        )
        # field_keys is a list of field NAMES, never the values.
        assert plaintext_marker not in (row.field_keys or [])
        assert plaintext_marker not in row.name


# ===========================================================================
# Ownership drift — silent missing
# ===========================================================================


class TestOwnershipMismatchSilentMissing:
    @pytest.mark.asyncio
    async def test_credential_ownership_mismatch_silent_missing(
        self, db_session: AsyncSession, seeded_user: uuid.UUID
    ) -> None:
        """A credential whose ``user_id`` no longer matches the caller is
        treated as if the binding didn't exist. The required key surfaces
        in ``missing`` so the runtime can fail fast — the OTHER user's
        secret is never decrypted."""

        # Seed a second user + their credential.
        other_user_id = uuid.uuid4()
        db_session.add(
            User(id=other_user_id, email="other@test.com", name="Other")
        )
        other_cred = _persist_credential(
            db_session,
            user_id=other_user_id,
            definition_key="srt_account",
            data={"username": "leak-me", "password": "leak-me-pw"},
        )
        # Skill belongs to seeded_user, but binding row (which we forge
        # to simulate corruption / re-assignment after the fact) points
        # at the other user's credential.
        skill = _make_skill(
            user_id=seeded_user,
            requirements=[_srt_requirement_dict(required=True)],
        )
        db_session.add(skill)
        await db_session.flush()
        db_session.add(
            SkillCredentialBinding(
                id=uuid.uuid4(),
                skill_id=skill.id,
                user_id=seeded_user,
                requirement_key="srt_account",
                credential_id=other_cred.id,
                scope="skill",
            )
        )
        await db_session.commit()

        resolved, missing = await resolve_credential_bindings(
            db_session, skill=skill, user_id=seeded_user
        )
        assert resolved == {}, (
            "ownership mismatch leaked decrypted credential into resolution"
        )
        assert missing == ["srt_account"]


# ===========================================================================
# Smoke (always runs) — Slice D contract preserved
# ===========================================================================


class TestCredentialPipelineSurface:
    def test_all_k_skill_definitions_registered(self) -> None:
        from app.credentials.registry import registry

        keys = {d.key for d in registry.all()}
        for required in (
            "srt_account",
            "ktx_account",
            "foresttrip_account",
            "kipris_plus_api",
            "dart_api",
            "odsay_api",
            "coupang_partners",
            "k_skill_proxy",
        ):
            assert required in keys, (
                f"k-skill credential definition {required!r} not registered "
                f"— Slice D (Spec §6) regression"
            )

    def test_skill_runtime_resolve_credentials_symbol_exists(self) -> None:
        """Stage 3 added ``resolve_runtime_credentials`` — pin the symbol
        so an accidental rename surfaces at collection time."""

        from app.marketplace import skill_runtime as sr

        assert hasattr(sr, "resolve_runtime_credentials")
        assert hasattr(sr, "ResolvedCredential")
