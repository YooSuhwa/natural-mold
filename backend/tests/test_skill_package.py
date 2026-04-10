"""Tests for package skill system — upload, prompt injection, script execution, tools."""

from __future__ import annotations

import io
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.model import Model
from app.models.skill import AgentSkillLink, Skill
from app.services import skill_service
from app.services.chat_service import (
    build_effective_prompt,
    build_tools_config,
)
from tests.conftest import TEST_USER_ID

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill_zip(
    files: dict[str, str | bytes],
    prefix: str = "",
) -> bytes:
    """Create an in-memory .skill ZIP with given files."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            path = f"{prefix}/{name}" if prefix else name
            if isinstance(content, str):
                zf.writestr(path, content)
            else:
                zf.writestr(path, content)
    return buf.getvalue()


def _make_skill_md(name: str = "test-skill", description: str = "A test skill") -> str:
    return f"""---
name: {name}
description: "{description}"
---

# {name}

This is the skill body.

Use ${{SKILL_DIR}}/scripts/run.py to do things.
"""


async def _seed_model(db: AsyncSession) -> Model:
    model = Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-4o",
        display_name="GPT-4o",
    )
    db.add(model)
    await db.commit()
    await db.refresh(model)
    return model


async def _seed_agent_with_skill(
    db: AsyncSession,
    skill_type: str = "text",
    skill_content: str = "text content",
    storage_path: str | None = None,
) -> tuple[Agent, Skill]:
    model = await _seed_model(db)
    agent = Agent(
        user_id=TEST_USER_ID,
        name="Test Agent",
        system_prompt="You are a helpful assistant.",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()

    skill = Skill(
        user_id=TEST_USER_ID,
        name="Test Skill",
        content=skill_content,
        type=skill_type,
        storage_path=storage_path,
    )
    db.add(skill)
    await db.flush()

    link = AgentSkillLink(agent_id=agent.id, skill_id=skill.id)
    db.add(link)
    await db.commit()

    # Eager-load relationships
    await db.refresh(agent, ["skill_links", "tool_links", "model"])
    for sl in agent.skill_links:
        await db.refresh(sl, ["skill"])

    return agent, skill


# ===========================================================================
# 1. Upload service (skill_service.upload_skill_package)
# ===========================================================================


class TestUploadSkillPackage:
    """skill_service.upload_skill_package() 테스트."""

    @pytest.mark.asyncio
    async def test_upload_basic(self, db: AsyncSession, tmp_path: Path):
        """기본 ZIP 업로드 — SKILL.md 파싱, DB 저장, 파일 추출."""
        zip_data = _make_skill_zip(
            {
                "SKILL.md": _make_skill_md("my-skill", "My description"),
                "scripts/run.py": "print('hello')",
                "references/data.md": "# Reference",
            }
        )

        with patch.object(
            skill_service.settings,
            "skill_storage_dir",
            str(tmp_path),
        ):
            skill = await skill_service.upload_skill_package(db, zip_data, TEST_USER_ID)

        assert skill.name == "my-skill"
        assert skill.description == "My description"
        assert "This is the skill body." in skill.content
        assert skill.type == "package"
        assert skill.storage_path is not None

        # Files extracted
        dest = Path(skill.storage_path)
        assert (dest / "SKILL.md").is_file()
        assert (dest / "scripts" / "run.py").is_file()
        assert (dest / "references" / "data.md").is_file()

    @pytest.mark.asyncio
    async def test_upload_with_prefix(self, db: AsyncSession, tmp_path: Path):
        """1단계 하위 디렉토리에 SKILL.md가 있는 경우."""
        zip_data = _make_skill_zip(
            {
                "SKILL.md": _make_skill_md("nested-skill"),
                "scripts/main.py": "pass",
            },
            prefix="my-skill",
        )

        with patch.object(
            skill_service.settings,
            "skill_storage_dir",
            str(tmp_path),
        ):
            skill = await skill_service.upload_skill_package(db, zip_data, TEST_USER_ID)

        assert skill.name == "nested-skill"
        dest = Path(skill.storage_path)
        assert (dest / "SKILL.md").is_file()
        assert (dest / "scripts" / "main.py").is_file()

    @pytest.mark.asyncio
    async def test_upload_no_skill_md(self, db: AsyncSession, tmp_path: Path):
        """SKILL.md가 없으면 ValueError."""
        zip_data = _make_skill_zip({"readme.txt": "hello"})

        with (
            patch.object(skill_service.settings, "skill_storage_dir", str(tmp_path)),
            pytest.raises(ValueError, match="SKILL.md not found"),
        ):
            await skill_service.upload_skill_package(db, zip_data, TEST_USER_ID)

    @pytest.mark.asyncio
    async def test_upload_invalid_zip(self, db: AsyncSession, tmp_path: Path):
        """유효하지 않은 ZIP 파일이면 ValueError."""
        with (
            patch.object(skill_service.settings, "skill_storage_dir", str(tmp_path)),
            pytest.raises(ValueError, match="Invalid ZIP"),
        ):
            await skill_service.upload_skill_package(db, b"not a zip file", TEST_USER_ID)

    @pytest.mark.asyncio
    async def test_upload_too_large(self, db: AsyncSession, tmp_path: Path):
        """패키지 크기 제한 초과 시 ValueError."""
        zip_data = _make_skill_zip({"SKILL.md": _make_skill_md()})

        with (
            patch.object(skill_service.settings, "skill_storage_dir", str(tmp_path)),
            patch.object(skill_service.settings, "skill_max_package_bytes", 10),
            pytest.raises(ValueError, match="too large"),
        ):
            await skill_service.upload_skill_package(db, zip_data, TEST_USER_ID)

    @pytest.mark.asyncio
    async def test_upload_zip_slip_rejected(self, db: AsyncSession, tmp_path: Path):
        """경로 탈출(../) 시도 시 ValueError."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("SKILL.md", _make_skill_md())
            zf.writestr("../../../etc/passwd", "evil")

        with (
            patch.object(skill_service.settings, "skill_storage_dir", str(tmp_path)),
            pytest.raises(ValueError, match="[Pp]ath traversal"),
        ):
            await skill_service.upload_skill_package(db, buf.getvalue(), TEST_USER_ID)

    @pytest.mark.asyncio
    async def test_upload_frontmatter_parsing(self, db: AsyncSession, tmp_path: Path):
        """frontmatter에서 name, description 추출."""
        md = """---
name: custom-name
description: "커스텀 설명"
---

Body here.
"""
        zip_data = _make_skill_zip({"SKILL.md": md})

        with patch.object(skill_service.settings, "skill_storage_dir", str(tmp_path)):
            skill = await skill_service.upload_skill_package(db, zip_data, TEST_USER_ID)

        assert skill.name == "custom-name"
        assert skill.description == "커스텀 설명"
        assert "Body here." in skill.content
        # frontmatter는 content에 포함되지 않아야 함
        assert "---" not in skill.content


# ===========================================================================
# 2. Upload API (router)
# ===========================================================================


class TestUploadSkillRouter:
    """POST /api/skills/upload 엔드포인트 테스트."""

    @pytest.mark.asyncio
    async def test_upload_endpoint(self, client: AsyncClient, tmp_path: Path):
        zip_data = _make_skill_zip(
            {
                "SKILL.md": _make_skill_md("api-skill"),
            }
        )

        with patch.object(skill_service.settings, "skill_storage_dir", str(tmp_path)):
            resp = await client.post(
                "/api/skills/upload",
                files={"file": ("test.skill", zip_data, "application/zip")},
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "api-skill"
        assert data["type"] == "package"

    @pytest.mark.asyncio
    async def test_upload_endpoint_invalid_zip(self, client: AsyncClient):
        resp = await client.post(
            "/api/skills/upload",
            files={"file": ("bad.skill", b"not a zip", "application/zip")},
        )
        assert resp.status_code == 422


# ===========================================================================
# 3. Prompt injection (chat_service helpers)
# ===========================================================================


class TestPromptInjection:
    """build_effective_prompt, build_tools_config 테스트."""

    @pytest.mark.asyncio
    async def test_build_effective_prompt_returns_system_prompt(self, db: AsyncSession):
        """build_effective_prompt는 항상 agent.system_prompt를 그대로 반환."""
        model = await _seed_model(db)
        agent = Agent(
            user_id=TEST_USER_ID,
            name="No Skills",
            system_prompt="Base prompt.",
            model_id=model.id,
        )
        db.add(agent)
        await db.commit()
        await db.refresh(agent, ["skill_links", "tool_links"])

        result = build_effective_prompt(agent)
        assert result == "Base prompt."

    @pytest.mark.asyncio
    async def test_build_effective_prompt_ignores_skills(self, db: AsyncSession):
        """스킬이 연결되어 있어도 system_prompt만 반환 (스킬 주입은 SkillsMiddleware 담당)."""
        agent, _ = await _seed_agent_with_skill(db, skill_type="text", skill_content="Skill body")
        result = build_effective_prompt(agent)
        assert result == "You are a helpful assistant."
        assert "Skill body" not in result

    @pytest.mark.asyncio
    async def test_build_tools_config_no_skill_package(self, db: AsyncSession):
        """build_tools_config는 더 이상 skill_package 타입을 생성하지 않음."""
        agent, _ = await _seed_agent_with_skill(
            db,
            skill_type="package",
            skill_content="content",
            storage_path="/fake/skills/abc",
        )
        config = build_tools_config(agent)
        skill_configs = [c for c in config if c.get("type") == "skill_package"]
        assert len(skill_configs) == 0


# ===========================================================================
# 6. File serving API
# ===========================================================================


class TestFileServingRouter:
    """GET /api/skills/{id}/files/{path} 테스트."""

    @pytest.mark.asyncio
    async def test_serve_file(self, client: AsyncClient, db: AsyncSession, tmp_path: Path):
        skill = Skill(
            user_id=TEST_USER_ID,
            name="pkg",
            content="body",
            type="package",
            storage_path=str(tmp_path),
        )
        db.add(skill)
        await db.commit()
        await db.refresh(skill)

        test_file = tmp_path / "test.txt"
        test_file.write_text("file content")

        resp = await client.get(f"/api/skills/{skill.id}/files/test.txt")
        assert resp.status_code == 200
        assert resp.text == "file content"

    @pytest.mark.asyncio
    async def test_serve_file_not_found(
        self, client: AsyncClient, db: AsyncSession, tmp_path: Path
    ):
        skill = Skill(
            user_id=TEST_USER_ID,
            name="pkg",
            content="body",
            type="package",
            storage_path=str(tmp_path),
        )
        db.add(skill)
        await db.commit()
        await db.refresh(skill)

        resp = await client.get(f"/api/skills/{skill.id}/files/nope.txt")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_serve_file_path_traversal(
        self, client: AsyncClient, db: AsyncSession, tmp_path: Path
    ):
        skill = Skill(
            user_id=TEST_USER_ID,
            name="pkg",
            content="body",
            type="package",
            storage_path=str(tmp_path),
        )
        db.add(skill)
        await db.commit()
        await db.refresh(skill)

        resp = await client.get(f"/api/skills/{skill.id}/files/../../../etc/passwd")
        assert resp.status_code in (400, 404)
