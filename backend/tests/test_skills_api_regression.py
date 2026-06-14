"""Slice A regression tests — m41 / m42 schema additions must not break
existing Skill / AgentSkillLink contracts.

베조스 (M2-S1 후속). 검증 대상은 deletion-analysis.md §3.1~§3.2의 매핑:

* m41 backfill 정책 (OI-5): text → 'user'/'created_by_me', package → 'import'/'imported_by_me'
* ``to_runtime_dict`` 키 셋 보존 (deepagents 호환)
* SkillResponse legacy 필드 보존 (frontend 회귀 가드)
* AgentSkillLink 생성 경로 무변경 (config nullable JSON 추가가 깨지 않음)
* 신규 row의 server_default가 instance attribute로도 일관되게 회수됨

These tests do NOT exercise the alembic upgrade path (conftest uses
``Base.metadata.create_all`` against in-memory aiosqlite). The migration
file ``m41_skills_marketplace_columns.py`` ships the backfill SQL; here
we ensure the SAME SQL applied to the test schema produces the documented
result, so a divergence in semantics surfaces immediately.
"""

from __future__ import annotations

import io
import uuid
import zipfile

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.skill import AgentSkillLink, Skill
from app.models.skill_evaluation import SkillEvaluationRun, SkillEvaluationSet
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill_row(
    *,
    user_id: uuid.UUID = TEST_USER_ID,
    name: str,
    kind: str,
    # Explicitly leave these None to simulate a "pre-m41" snapshot — the
    # m41 backfill is the system under test.
    origin_kind: str = "created_by_me",
    source_kind: str | None = None,
) -> Skill:
    return Skill(
        id=uuid.uuid4(),
        user_id=user_id,
        name=name,
        slug=name.lower().replace(" ", "-"),
        description=None,
        kind=kind,
        storage_path=None,
        content_hash=None,
        size_bytes=0,
        version=None,
        package_metadata=None,
        used_by_count=0,
        is_system=False,
        source_kind=source_kind,
        origin_kind=origin_kind,
        is_dirty=False,
    )


def _zip_minimal_skill(name: str = "demo") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "SKILL.md",
            f'---\nname: {name}\ndescription: "demo"\nversion: "1.0.0"\n---\n\nBody\n',
        )
    return buf.getvalue()


def _skill_md(name: str, description: str = "demo") -> str:
    return f'---\nname: {name}\ndescription: "{description}"\nversion: "1.0.0"\n---\n\nBody\n'


# ===========================================================================
# m41 — column defaults + backfill semantics
# ===========================================================================


class TestM41ColumnDefaults:
    @pytest.mark.asyncio
    async def test_new_skill_defaults_origin_kind_created_by_me(self, db: AsyncSession) -> None:
        """Plain ``Skill()`` without origin_kind must default to 'created_by_me'.

        Failing this means a new text skill (created from /skills POST)
        would show the wrong origin badge on the dashboard.
        """

        row = Skill(
            id=uuid.uuid4(),
            user_id=TEST_USER_ID,
            name="fresh",
            slug="fresh",
            description=None,
            kind="text",
            storage_path=None,
            content_hash=None,
            size_bytes=0,
            version=None,
            package_metadata=None,
            used_by_count=0,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)

        assert row.origin_kind == "created_by_me"
        assert row.is_dirty is False
        assert row.is_system is False
        assert row.source_marketplace_item_id is None

    @pytest.mark.asyncio
    async def test_m41_backfill_marks_package_skills_imported_by_me(self, db: AsyncSession) -> None:
        """Apply the m41 backfill SQL verbatim — package rows must flip.

        Mirror of ``m41_skills_marketplace_columns.py:179-184`` UPDATE.
        Bezos OI-5 / progress.txt L42: 빠뜨리면 모든 기존 package skill이
        잘못 'created_by_me'로 표시된다.
        """

        # Seed pre-m41 snapshots — origin_kind already at 'created_by_me'
        # (column default), source_kind NULL like a freshly migrated row.
        text_skill = _make_skill_row(name="text-one", kind="text")
        pkg_skill = _make_skill_row(name="pkg-one", kind="package")
        pkg_skill2 = _make_skill_row(name="pkg-two", kind="package")
        db.add_all([text_skill, pkg_skill, pkg_skill2])
        await db.flush()

        # Apply backfill SQL — same statements ship in m41.
        await db.execute(
            text(
                "UPDATE skills SET source_kind = 'user' WHERE source_kind IS NULL AND kind = 'text'"
            )
        )
        await db.execute(
            text(
                "UPDATE skills SET source_kind = 'import' "
                "WHERE source_kind IS NULL AND kind = 'package'"
            )
        )
        await db.execute(
            text("UPDATE skills SET origin_kind = 'imported_by_me' WHERE kind = 'package'")
        )
        await db.commit()

        for row in (text_skill, pkg_skill, pkg_skill2):
            await db.refresh(row)

        assert text_skill.source_kind == "user"
        assert text_skill.origin_kind == "created_by_me"
        assert pkg_skill.source_kind == "import"
        assert pkg_skill.origin_kind == "imported_by_me"
        assert pkg_skill2.origin_kind == "imported_by_me"


# ===========================================================================
# to_runtime_dict — deepagents contract preserved
# ===========================================================================


class TestRuntimeDictContract:
    @pytest.mark.asyncio
    async def test_to_runtime_dict_keys_unchanged(self, db: AsyncSession) -> None:
        """The deepagents Filesystem backend keys ``slug``/``storage_path``
        when materializing skill descriptors. m41 added 12 columns, but the
        serializer surface MUST stay the legacy six.
        """

        row = _make_skill_row(name="rd", kind="text")
        row.storage_path = "/tmp/rd/SKILL.md"
        row.description = "skill description"
        db.add(row)
        await db.flush()

        rd = skill_service.to_runtime_dict(row)
        # Legacy contract — adding a key here breaks the FilesystemBackend
        # mount in executor.py:544. Adding new metadata should go to a
        # *separate* descriptor function, not this one.
        assert set(rd.keys()) == {
            "id",
            "name",
            "slug",
            "kind",
            "storage_path",
            "description",
        }
        assert rd["storage_path"] == "/tmp/rd/SKILL.md"
        assert rd["description"] == "skill description"


# ===========================================================================
# Routers — legacy upload + GET shape preservation
# ===========================================================================


class TestLegacyRoutersStillWork:
    @pytest.mark.asyncio
    async def test_legacy_text_skill_create_still_works(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/skills",
            json={
                "name": "Legacy Skill",
                "description": "no marketplace metadata",
                "content": _skill_md("legacy-skill", "no marketplace metadata"),
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        # Legacy SkillResponse contract — these must remain.
        for key in (
            "id",
            "name",
            "slug",
            "description",
            "kind",
            "version",
            "storage_path",
            "content_hash",
            "size_bytes",
            "used_by_count",
            "package_metadata",
            "last_modified_at",
            "created_at",
            "updated_at",
        ):
            assert key in body, f"missing legacy field: {key}"
        assert body["kind"] == "text"

    @pytest.mark.asyncio
    async def test_legacy_upload_package_still_works(self, client: AsyncClient) -> None:
        """Package upload via multipart must succeed after m41 columns added.

        Slice C will add secret_scan to this path — but Slice A must not
        already break the flow.
        """

        resp = await client.post(
            "/api/skills/upload",
            files={
                "file": (
                    "demo.skill",
                    _zip_minimal_skill("upload-demo"),
                    "application/zip",
                ),
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["kind"] == "package"
        assert body["content_hash"]

    @pytest.mark.asyncio
    async def test_legacy_list_response_includes_legacy_fields(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        # Seed one skill directly so the GET shape is deterministic.
        row = _make_skill_row(name="list-shape", kind="text")
        db.add(row)
        await db.commit()

        resp = await client.get("/api/skills")
        assert resp.status_code == 200
        rows = resp.json()
        assert any(r["slug"] == "list-shape" for r in rows)
        # Every row keeps the legacy contract.
        for r in rows:
            for key in (
                "id",
                "name",
                "slug",
                "kind",
                "storage_path",
                "content_hash",
                "size_bytes",
                "used_by_count",
            ):
                assert key in r, f"GET /api/skills row missing {key!r}"

    @pytest.mark.asyncio
    async def test_skill_response_includes_execution_profile(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        row = _make_skill_row(name="profile-probe", kind="package")
        row.execution_profile = {"tool_dependencies": ["tavily_search"]}
        db.add(row)
        await db.commit()

        resp = await client.get(f"/api/skills/{row.id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["execution_profile"] == {"tool_dependencies": ["tavily_search"]}

    @pytest.mark.asyncio
    async def test_skill_response_includes_quality_summaries(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        row = _make_skill_row(name="quality-probe", kind="text")
        row.content_hash = "a" * 64
        evaluation_set_id = uuid.uuid4()
        evaluation_set = SkillEvaluationSet(
            id=evaluation_set_id,
            user_id=TEST_USER_ID,
            skill_id=row.id,
            name="Smoke",
            evals=[{"input": "a"}],
        )
        run = SkillEvaluationRun(
            user_id=TEST_USER_ID,
            skill_id=row.id,
            evaluation_set_id=evaluation_set_id,
            status="completed",
            skill_content_hash=row.content_hash,
            summary={"pass_rate": 0.9},
        )
        db.add_all([row, evaluation_set, run])
        await db.commit()

        resp = await client.get(f"/api/skills/{row.id}")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["latest_evaluation_summary"]["status"] == "passed"
        assert body["latest_evaluation_summary"]["pass_rate"] == 0.9
        assert body["health"]["state"] == "ready"

    @pytest.mark.asyncio
    async def test_skill_list_marks_missing_evaluation(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        row = _make_skill_row(name="quality-list-probe", kind="text")
        db.add(row)
        await db.commit()

        resp = await client.get("/api/skills")

        assert resp.status_code == 200, resp.text
        probe = next(r for r in resp.json() if r["slug"] == "quality-list-probe")
        assert probe["latest_evaluation_summary"]["status"] == "missing"
        assert probe["health"]["state"] == "needs_evaluation"


# ===========================================================================
# Slice A embed contract — origin_summary / publication_summary / installation
# ===========================================================================


class TestResponseEmbedsAfterSliceA:
    """``GET /api/skills/{id}`` and the list endpoint must populate the three
    new summary embeds (Spec §10.8 D8). They may be ``None`` when the user
    has no marketplace lineage yet, but the keys themselves must exist so
    the frontend can render badges unconditionally."""

    @pytest.mark.asyncio
    async def test_text_skill_create_returns_origin_publication_installation_keys(
        self, client: AsyncClient
    ) -> None:
        resp = await client.post(
            "/api/skills",
            json={"name": "Embed Probe", "content": _skill_md("embed-probe")},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        # Embed keys must always be present — value can be None.
        for embed_key in (
            "origin_summary",
            "publication_summary",
            "installation",
        ):
            assert embed_key in body, f"SkillResponse missing {embed_key} embed — Slice A 회귀"

    @pytest.mark.asyncio
    async def test_get_skill_includes_origin_summary_for_text_skill(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        row = _make_skill_row(name="origin-probe", kind="text")
        db.add(row)
        await db.commit()

        resp = await client.get(f"/api/skills/{row.id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Text skill, no marketplace lineage → origin_kind=created_by_me.
        origin = body.get("origin_summary")
        assert origin is not None, "origin_summary expected for detail response"
        assert origin["kind"] == "created_by_me"
        # Publication state must be not_published until owner publishes.
        publication = body.get("publication_summary")
        assert publication is not None
        assert publication["state"] == "not_published"
        # Installation embed: key present but value may be None when the
        # skill has no source_marketplace_item_id (router behaviour —
        # only populated for marketplace-installed skills).
        assert "installation" in body


# ===========================================================================
# build_skills_prompt — LLM prompt block회귀
# ===========================================================================


class TestSkillsPromptBlock:
    def test_skills_prompt_block_unchanged_for_two_skills(self) -> None:
        """The system prompt block injected for ``/skills/`` mount must stay
        deterministic — LLM behaviour is sensitive to whitespace and the
        exact 'Available Skills' header. Slice A added 12 ORM columns and
        Slice E will change the mount root, but neither should perturb
        this rendering."""

        from app.skills.prompt import build_skills_prompt

        skills = [
            {
                "id": "1",
                "name": "Spell Check",
                "slug": "spell-check",
                "kind": "text",
                "storage_path": "/skills/spell-check/SKILL.md",
                "description": "Korean spell checker",
            },
            {
                "id": "2",
                "name": "SRT Booking",
                "slug": "srt-booking",
                "kind": "package",
                "storage_path": "/skills/srt-booking",
                "description": "",  # empty → "(no description)"
            },
        ]
        block = build_skills_prompt(skills)
        assert block.startswith("\n## Available Skills")
        assert "The agent has access to the following skills mounted under /skills/:" in block
        assert "- **Spell Check**: Korean spell checker" in block
        assert "Read `/skills/spell-check/SKILL.md`" in block
        # Empty description fallback contract — Slice E must not break this.
        assert "- **SRT Booking**: (no description)" in block
        assert "Read `/skills/srt-booking/SKILL.md`" in block

    def test_skills_prompt_block_empty_when_no_skills(self) -> None:
        from app.skills.prompt import build_skills_prompt

        assert build_skills_prompt([]) == ""
        assert build_skills_prompt([None, None]) == ""  # type: ignore[list-item]


# ===========================================================================
# Upload origin_kind — service-layer default per kind (deletion-analysis OI-5)
# ===========================================================================


class TestUploadOriginKindForNewUploads:
    """OI-5: text → created_by_me, package → imported_by_me (Spec §15.2).

    The m41 backfill SQL covers EXISTING rows. New uploads after migration
    rely on the column default ``'created_by_me'`` and the service layer
    overriding for the package path.

    Verifies actual behaviour — fails fast if the service forgets to set
    origin_kind for package uploads (which would leave new package skills
    silently mis-labeled as 'created_by_me' on the dashboard).
    """

    @pytest.mark.asyncio
    async def test_text_skill_create_sets_origin_kind_created_by_me(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        resp = await client.post(
            "/api/skills",
            json={"name": "T-Origin", "content": _skill_md("t-origin")},
        )
        assert resp.status_code == 201, resp.text
        skill_id = uuid.UUID(resp.json()["id"])

        skill = await db.get(Skill, skill_id)
        assert skill is not None
        assert skill.origin_kind == "created_by_me"
        assert skill.is_dirty is False
        assert skill.is_system is False
        # source_kind is allowed to be NULL on fresh text-skill creation
        # (the m41 backfill only sets it for *existing* rows at migration
        # time — Spec §15.2 is silent on the post-migration semantics).

    @pytest.mark.asyncio
    async def test_legacy_upload_package_sets_origin_kind_imported_by_me(
        self, client: AsyncClient, db: AsyncSession
    ) -> None:
        """Bezos OI-5 / Spec §15.2 — ``.skill`` package uploads must be
        marked ``origin_kind='imported_by_me'`` so the dashboard
        distinguishes external packages from in-app authored skills.

        Bug history: ``create_package_skill`` originally fell through to
        the column default ``'created_by_me'`` (caught by an earlier
        strict xfail). 젠슨 service-layer fix landed 2026-05-19; this is
        the promoted assertion.
        """

        resp = await client.post(
            "/api/skills/upload",
            files={
                "file": (
                    "origin.skill",
                    _zip_minimal_skill("origin-probe"),
                    "application/zip",
                ),
            },
        )
        assert resp.status_code == 201, resp.text
        skill_id = uuid.UUID(resp.json()["id"])
        skill = await db.get(Skill, skill_id)
        assert skill is not None
        assert skill.kind == "package"
        assert skill.origin_kind == "imported_by_me", (
            f"Spec §15.2 regression — package upload origin_kind = "
            f"{skill.origin_kind!r}, expected 'imported_by_me'"
        )


# ===========================================================================
# m42 — AgentSkillLink.config nullable JSON
# ===========================================================================


class TestAgentSkillLinkAfterM42:
    @pytest.mark.asyncio
    async def test_link_creation_without_config_unchanged(self, db: AsyncSession) -> None:
        """write_tools.py:398 still calls ``AgentSkillLink(skill_id=s.id)``
        with no ``config`` kwarg. The link row must persist with config=None.
        """

        # Seed minimum agent + skill rows. We bypass model_id FK since
        # aiosqlite doesn't enforce FK in this test harness by default.
        agent = Agent(
            id=uuid.uuid4(),
            user_id=TEST_USER_ID,
            name="test-agent",
            description=None,
            system_prompt="",
            model_id=uuid.uuid4(),
        )
        skill = _make_skill_row(name="link-skill", kind="text")
        db.add_all([agent, skill])
        await db.flush()

        link = AgentSkillLink(agent_id=agent.id, skill_id=skill.id)
        db.add(link)
        await db.flush()
        await db.refresh(link)

        assert link.config is None
        assert link.skill_id == skill.id
        assert link.agent_id == agent.id

    @pytest.mark.asyncio
    async def test_link_accepts_credential_bindings_override(self, db: AsyncSession) -> None:
        """Slice D wires ``config['credential_bindings']`` — verify the JSON
        column round-trips so Slice E can consume it deterministically.
        """

        agent = Agent(
            id=uuid.uuid4(),
            user_id=TEST_USER_ID,
            name="ov-agent",
            description=None,
            system_prompt="",
            model_id=uuid.uuid4(),
        )
        skill = _make_skill_row(name="ov-skill", kind="text")
        db.add_all([agent, skill])
        await db.flush()

        binding_cred_id = uuid.uuid4()
        link = AgentSkillLink(
            agent_id=agent.id,
            skill_id=skill.id,
            config={"credential_bindings": {"srt": str(binding_cred_id)}},
        )
        db.add(link)
        await db.commit()

        reloaded = (
            await db.execute(select(AgentSkillLink).where(AgentSkillLink.skill_id == skill.id))
        ).scalar_one()
        assert reloaded.config == {"credential_bindings": {"srt": str(binding_cred_id)}}
