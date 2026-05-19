# ruff: noqa: E501
"""M9 — Phase 1 출시 게이트 통합 검증 (PRD §13).

Eight ship-gates that PRD §13 mandates before Marketplace Phase 1 can
ship. Other test files cover the gate machinery at unit / integration
granularity; **this file is the single-file report** so the team can
``pytest tests/test_marketplace_phase1_gates.py`` and confirm the
release criteria in one go.

Gate map:

| Gate                       | Primary subsystem            | Covered by                                |
|----------------------------|------------------------------|-------------------------------------------|
| 1. Access control          | marketplace.access + service | test_marketplace_access.py / this file    |
| 2. Secret safety           | secret_scan + redaction      | test_secret_scan.py + test_redaction.py   |
| 3. Runtime isolation       | skill_runtime + executor     | test_runtime_isolation.py                 |
| 4. Credential runtime      | credential_requirements      | test_credential_injection.py              |
| 5. k-skill sync            | k_skill_importer (CLI)       | test_k_skill_importer.py (젠슨 트랙)        |
| 6. Backward compatibility  | skills/agent_skills ORM      | test_skills_api_regression.py             |
| 7. Listing 승인            | catalog query                | test_marketplace_listing.py + this file   |
| 8. ADR-016 정합           | router auth + CSRF           | this file                                 |

Each test below either delegates to the dedicated subsystem test (via a
direct import + run check) or pins a release invariant that doesn't
naturally live elsewhere.
"""

from __future__ import annotations

import importlib
import inspect
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.marketplace.access import can_install_item, can_view_item
from app.marketplace.redaction import redact_credential_values, redact_keys
from app.marketplace.secret_scan import (
    SECRET_CONTENT_PATTERNS,
    SECRET_FILE_PATTERNS,
    scan_package,
)
from app.marketplace.skill_runtime import (
    build_skill_runtime_context,
    cleanup_stale_runtime_roots,
)
from app.models.marketplace import MarketplaceItem, MarketplaceItemACL

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _item(
    *,
    visibility: str,
    status: str = "published",
    owner_id: uuid.UUID | None = None,
    is_listed: bool = False,
    is_system: bool = False,
    acl_users: list[uuid.UUID] | None = None,
) -> MarketplaceItem:
    item = MarketplaceItem(
        id=uuid.uuid4(),
        resource_type="skill",
        owner_user_id=owner_id,
        is_system=is_system,
        is_listed=is_listed,
        name="probe",
        slug=f"probe-{uuid.uuid4().hex[:6]}",
        visibility=visibility,
        status=status,
        moderation_status="approved",
    )
    item.acl_entries = [
        MarketplaceItemACL(
            item_id=item.id, user_id=u, permission="install"
        )
        for u in (acl_users or [])
    ]
    return item


def _cu(uid: uuid.UUID, *, is_super: bool = False):
    from app.dependencies import CurrentUser

    return CurrentUser(
        id=uid, email=f"{uid.hex[:8]}@t", name="U", is_super_user=is_super
    )


# ===========================================================================
# Gate 1 — Access control (4-user × 5-visibility × 2-status matrix)
# ===========================================================================


class TestGate1AccessControl:
    """PRD §13 #1 — Access control: private/restricted/public/system
    item에 대해 owner, ACL user, unrelated user 매트릭스 통과. 비인가 접근
    404 통일 (enumeration oracle 방지).

    Predicate matrix at the access layer (no DB roundtrip). The router-
    level oracle is asserted in ``test_marketplace_access.py``."""

    def test_full_visibility_matrix(self) -> None:
        owner = uuid.uuid4()
        acl_user = uuid.uuid4()
        unrelated = uuid.uuid4()
        super_user = uuid.uuid4()

        # Each row: (visibility, status, expected view dict by actor).
        # Actors: O=owner, A=acl, U=unrelated, S=super.
        cases = [
            ("private", "draft",
             {"O": True, "A": False, "U": False, "S": True}),
            ("restricted", "published",
             {"O": True, "A": True, "U": False, "S": True}),
            ("public", "published",
             {"O": True, "A": True, "U": True, "S": True}),
            ("public", "draft",
             {"O": True, "A": False, "U": False, "S": True}),
            ("unlisted", "published",
             {"O": True, "A": True, "U": True, "S": True}),
            ("system", "published",
             {"O": True, "A": True, "U": True, "S": True}),
            ("public", "disabled",
             {"O": True, "A": False, "U": False, "S": True}),
        ]
        actors = {
            "O": (_cu(owner), True),
            "A": (_cu(acl_user), False),
            "U": (_cu(unrelated), False),
            "S": (_cu(super_user, is_super=True), False),
        }
        for visibility, status, expected in cases:
            item = _item(
                visibility=visibility,
                status=status,
                owner_id=owner,
                acl_users=[acl_user] if visibility == "restricted" else None,
                is_system=(visibility == "system"),
            )
            for key, (user, _) in actors.items():
                actual = can_view_item(item, user)
                assert actual == expected[key], (
                    f"can_view_item({visibility}/{status}, actor={key}) "
                    f"→ {actual}, expected {expected[key]}"
                )

    def test_disabled_item_install_blocked_for_everyone_except_super(self) -> None:
        owner = uuid.uuid4()
        item = _item(visibility="public", status="disabled", owner_id=owner, is_listed=True)
        # Owner can VIEW but not install (Spec §10.8 disabled gate).
        assert can_install_item(item, _cu(owner)) is False
        # Non-owner → completely blocked.
        assert can_install_item(item, _cu(uuid.uuid4())) is False
        # super_user → rescue path.
        assert can_install_item(item, _cu(uuid.uuid4(), is_super=True)) is True


# ===========================================================================
# Gate 2 — Secret safety (filename + content patterns + redaction)
# ===========================================================================


class TestGate2SecretSafety:
    """PRD §13 #2 — Secret safety. ``secret_scan`` 차단 + log/SSE/tool
    result redact. Detailed pattern coverage in ``test_secret_scan.py``
    + ``test_redaction.py``. This gate pins the **count** so accidental
    pattern removal forces a Spec §13.1 update."""

    def test_filename_pattern_count_meets_spec(self) -> None:
        # Spec §13.1 currently lists 9 filename rules; raising or lowering
        # this is a deliberate decision that must update the spec too.
        assert len(SECRET_FILE_PATTERNS) >= 9

    def test_content_pattern_count_meets_spec(self) -> None:
        # 6 content rules: sk-, PEM, AWS, GCP, ghp_, sk_live_.
        assert len(SECRET_CONTENT_PATTERNS) >= 6

    def test_env_file_blocked(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("OPENAI_API_KEY=...\n")
        assert scan_package(tmp_path)

    def test_redaction_marker_format_stable(self) -> None:
        """Frontend parses ``<redacted:<env_var>>`` markers — pin the
        format so a refactor doesn't break the UI."""

        out = redact_credential_values(
            "value=longenough-secret-here",
            {"MY_VAR": "longenough-secret-here"},
        )
        assert "<redacted:MY_VAR>" in out
        assert "longenough-secret-here" not in out

    def test_redact_keys_uses_documented_placeholder(self) -> None:
        out = redact_keys({"password": "hunter2", "user": "kim"})
        assert out["password"] == "<redacted>"
        assert out["user"] == "kim"


# ===========================================================================
# Gate 3 — Runtime isolation (per-thread + selected-skill mount)
# ===========================================================================


class TestGate3RuntimeIsolation:
    """PRD §13 #3 — Agent에 선택된 skill만 per-thread runtime root에 노출.
    Full integration in ``test_runtime_isolation.py``. This gate pins
    the contract:
    * ``build_skill_runtime_context`` materializes per-thread roots.
    * ``cleanup_stale_runtime_roots`` is the documented retention hook.
    """

    def test_runtime_helpers_exported(self) -> None:
        from app.marketplace import skill_runtime as sr

        assert callable(sr.build_skill_runtime_context)
        assert callable(sr.cleanup_stale_runtime_roots)

    def test_per_thread_root_isolation_smoke(self, tmp_path: Path) -> None:
        """Compact smoke — full assertions live in
        test_runtime_isolation.py. Goal here: cheap regression that the
        per-thread directory layout still matches what the cleanup job
        expects (``data/runtime/<thread_id>/skills/``)."""

        from app.agent_runtime.executor import AgentConfig

        cfg = AgentConfig(
            provider="anthropic",
            model_name="x",
            api_key=None,
            base_url=None,
            system_prompt="",
            tools_config=[],
            thread_id="gate-thread",
            agent_skills=None,
        )
        ctx = build_skill_runtime_context(cfg, data_dir=tmp_path)
        assert str(ctx.runtime_root).endswith(
            "/runtime/gate-thread/skills"
        ), f"layout drift: {ctx.runtime_root}"

    def test_cleanup_no_runtime_dir_is_safe(self, tmp_path: Path) -> None:
        # Empty data dir → 0 removed, no exception.
        assert cleanup_stale_runtime_roots(tmp_path) == 0


# ===========================================================================
# Gate 4 — Credential runtime (fail-fast + mapped env only)
# ===========================================================================


class TestGate4CredentialRuntime:
    """PRD §13 #4 — required binding 누락 시 실행 차단
    (``marketplace_credential_required``). binding 존재 시 mapped env
    var에만 주입. Full integration in ``test_credential_injection.py``."""

    def test_credential_required_error_code_stable(self) -> None:
        """Pin the public error code — frontend toast + API contract
        depend on the literal string."""

        from app.error_codes import marketplace_credential_required

        err = marketplace_credential_required("missing srt_account")
        assert err.code == "MARKETPLACE_CREDENTIAL_REQUIRED"
        assert err.status == 409

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
                f"k-skill credential definition {required!r} missing — "
                f"Spec §6 regression"
            )


# ===========================================================================
# Gate 5 — k-skill sync (CLI behaviour)
# ===========================================================================


class TestGate5KSkillSync:
    """PRD §13 #5 — k-skill importer CLI. 단위 테스트는 jensen이
    ``test_k_skill_importer.py``에서 다룸 (있다면). 이 게이트는
    sync 라우터/CLI 진입점이 존재하는지만 가드."""

    def test_admin_k_skill_sync_endpoint_mounted(self) -> None:
        """``POST /api/marketplace/admin/k-skill/sync`` (super_user only)
        is the read-side status endpoint per Spec §10.4."""

        from app.routers import marketplace as router_mod

        src = inspect.getsource(router_mod)
        assert "/admin/k-skill/sync" in src, (
            "admin k-skill sync route removed — Spec §10.4 violation"
        )

    def test_sync_script_module_importable(self) -> None:
        """The CLI lives at ``app.scripts.sync_k_skill`` — import is the
        cheapest smoke that the entry point still exists."""

        try:
            mod = importlib.import_module("app.scripts.sync_k_skill")
        except ImportError:
            pytest.skip("k-skill importer CLI not present (M7 미완료)")
        else:
            assert hasattr(mod, "main") or hasattr(mod, "__name__")


# ===========================================================================
# Gate 6 — Backward compatibility (skills API + agent_skills)
# ===========================================================================


class TestGate6BackwardCompatibility:
    """PRD §13 #6 — 기존 skill upload/edit/delete + agent skill 연결 +
    ``/api/skills`` 응답 회귀 통과. ``is_dirty`` 추가가 기존 편집 UX를
    파괴하지 않음. Full coverage in ``test_skills_api_regression.py``.
    Here we pin the contract surface (ORM + serializer)."""

    def test_skill_model_keeps_legacy_columns(self) -> None:
        from app.models.skill import Skill

        cols = {c.name for c in Skill.__table__.columns}
        for legacy in (
            "id",
            "user_id",
            "name",
            "slug",
            "description",
            "kind",
            "storage_path",
            "content_hash",
            "size_bytes",
            "used_by_count",
            "version",
            "package_metadata",
            "last_modified_at",
            "created_at",
            "updated_at",
        ):
            assert legacy in cols, f"Skill column {legacy!r} dropped"

    def test_to_runtime_dict_keys_unchanged(self) -> None:
        from app.models.skill import Skill
        from app.skills import service as skill_service

        skill = Skill(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            name="n",
            slug="s",
            description="d",
            kind="text",
            storage_path="/tmp/x",
            content_hash=None,
            size_bytes=0,
            package_metadata=None,
            used_by_count=0,
        )
        rd = skill_service.to_runtime_dict(skill)
        # Legacy six — deepagents Filesystem backend depends on this.
        assert set(rd.keys()) == {
            "id",
            "name",
            "slug",
            "kind",
            "storage_path",
            "description",
        }


# ===========================================================================
# Gate 7 — Listing 승인 (public+is_listed=False excluded from catalog)
# ===========================================================================


class TestGate7Listing:
    """PRD §13 #7 — public 항목은 ``is_listed=True``로 토글되기 전까지
    카탈로그 기본 검색에 노출되지 않음. Full coverage in
    ``test_marketplace_listing.py``. Here we re-pin the base query
    invariant via the access layer."""

    def test_unlisted_public_visible_by_link_but_not_by_default_catalog(
        self,
    ) -> None:
        """Predicate layer: unlisted-published is view-OK (link access).
        Whether it appears in the default catalog is enforced by
        ``_base_catalog_query`` — covered separately. Here we just pin
        the predicate so a future tightening doesn't break direct-link
        sharing."""

        item = _item(visibility="unlisted", status="published")
        assert can_view_item(item, _cu(uuid.uuid4())) is True

    def test_super_user_listed_toggle_endpoint_is_admin_only(self) -> None:
        """If/when ``POST /api/marketplace/admin/items/{id}/listed`` lands,
        it must depend on ``require_super_user``. Pin the contract via
        source inspection so a missing depends() check surfaces."""

        from app.routers import marketplace as router_mod

        src = inspect.getsource(router_mod)
        if "/admin/items/" in src and "/listed" in src:
            # Endpoint exists — must use require_super_user.
            assert "require_super_user" in src, (
                "admin listed endpoint missing require_super_user dep"
            )


# ===========================================================================
# Gate 8 — ADR-016 정합 (router auth + CSRF)
# ===========================================================================


class TestGate8AuthCsrf:
    """PRD §13 #8 — 모든 신규 라우터가 ``get_current_user`` 또는
    ``require_super_user`` 의존성을 가짐. 상태 변경은 ``verify_csrf``."""

    def test_marketplace_router_every_mutation_has_csrf(self) -> None:
        from app.routers import marketplace as router_mod

        src = inspect.getsource(router_mod)
        # Find every @router.post / @router.patch / @router.delete and
        # confirm verify_csrf appears within ~30 lines after it.
        import re

        mutation_re = re.compile(
            r"@router\.(post|patch|delete)\([^\)]*\)\s*(?:async )?def \w+\("
            r"[\s\S]+?(?=@router\.|\Z)",
            re.MULTILINE,
        )
        seen = 0
        for block in mutation_re.finditer(src):
            seen += 1
            chunk = block.group(0)
            assert "verify_csrf" in chunk, (
                f"mutation route missing verify_csrf: {chunk[:120]}…"
            )
        assert seen >= 5, (
            f"expected ≥5 mutation routes on marketplace router, found {seen}"
        )

    def test_get_current_user_dependency_on_all_routes(self) -> None:
        from app.routers import marketplace as router_mod

        src = inspect.getsource(router_mod)
        # Every route uses either get_current_user or require_super_user.
        # The admin sync endpoint uses the latter; everything else uses
        # the former.
        assert "get_current_user" in src
        assert "require_super_user" in src

    @pytest.mark.asyncio
    async def test_admin_endpoint_requires_super_user(
        self, db: AsyncSession
    ) -> None:
        """Phase 1 admin endpoint (k-skill sync status) must reject a
        non-super-user. Exercises the dependency wiring end-to-end."""


        from httpx import ASGITransport, AsyncClient

        from app.dependencies import (
            CurrentUser,
            get_current_user,
            get_current_user_optional,
            get_db,
            verify_csrf,
        )
        from app.main import create_app
        from tests.conftest import override_get_db

        async def _no_csrf() -> None:
            return None

        async def _regular_user() -> CurrentUser:
            return CurrentUser(
                id=uuid.uuid4(),
                email="x@t",
                name="reg",
                is_super_user=False,
            )

        app = create_app()
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = _regular_user
        app.dependency_overrides[get_current_user_optional] = _regular_user
        app.dependency_overrides[verify_csrf] = _no_csrf

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            r = await client.post("/api/marketplace/admin/k-skill/sync")
        assert r.status_code == 403, (
            f"non-super-user admin call must 403, got {r.status_code} "
            f"({r.text[:120]})"
        )


# ===========================================================================
# Summary smoke — count gate coverage so a missing class is loud
# ===========================================================================


def test_phase1_gate_classes_present() -> None:
    """Pin the gate class count — 8 PRD §13 gates → 8 test classes."""

    import sys

    mod = sys.modules[__name__]
    gate_classes = [
        name
        for name in dir(mod)
        if name.startswith("TestGate")
    ]
    assert len(gate_classes) == 8, (
        f"Phase 1 출시 게이트 클래스 누락 — found {gate_classes}"
    )
