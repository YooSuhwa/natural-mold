"""I-08 review fixes: every remaining skill/attachment reader and mutator refuses another
company's path, new revision/version/builder files go to the company folder, prefix case
variants fail closed, and Langfuse masking keeps scope metadata when bodies are off."""

import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.marketplace.install.snapshot import _copy_snapshot
from app.marketplace.publish_service import _version_snapshot_dir
from app.marketplace.skill_runtime import _build_descriptor_from_skill_dict
from app.models.marketplace import MarketplaceVersion
from app.models.skill import Skill
from app.observability.langfuse_metadata import _metadata_for_run
from app.observability.langfuse_redaction import redact_payload
from app.services.skill_builder_package_storage import _skill_dir
from app.services.skill_revision_storage import _revision_path
from app.skills import service as skill_service
from app.skills.creation import TextSkillCreation
from app.skills.package_metadata import refresh_package_metadata
from app.storage.paths import (
    ForeignTenantPathError,
    check_company_path,
    run_data_path_or_none,
)
from tests.authz.test_i08_rate_keys_and_traces import _cfg
from tests.authz.unit_scope_fixture import TEST_TENANT_ID, create_test_text_skill, unit_context
from tests.conftest import TEST_USER_ID

OTHER = uuid.uuid5(uuid.NAMESPACE_URL, "moldy/i08-readers-other-company")
SKILL_MD = "---\nname: notes\ndescription: Notes skill\n---\n\nBody\n"


def test_case_variants_of_the_company_folder_fail_closed(tmp_path: Path) -> None:
    for top in ("Tenants", "TENANTS"):
        with pytest.raises(ForeignTenantPathError):
            check_company_path(tmp_path / top / str(TEST_TENANT_ID) / "f", tmp_path, TEST_TENANT_ID)


def test_run_reads_accept_own_and_platform_paths_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    own = f"tenants/{TEST_TENANT_ID}/skills/a"
    assert run_data_path_or_none(own, TEST_TENANT_ID) == (tmp_path / own).resolve()
    assert run_data_path_or_none("skills/system", TEST_TENANT_ID) is not None
    assert run_data_path_or_none(f"tenants/{OTHER}/skills/a", TEST_TENANT_ID) is None
    raw = {"id": str(uuid.uuid4()), "slug": "s", "storage_path": f"tenants/{OTHER}/skills/a"}
    assert _build_descriptor_from_skill_dict(raw, tmp_path / "rt", TEST_TENANT_ID) is None


async def test_install_refuses_another_company_snapshot(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    version = MarketplaceVersion(storage_path=f"tenants/{OTHER}/skills/_marketplace_versions/v")
    with pytest.raises(Exception, match="another company"):
        await _copy_snapshot(version, tmp_path / "target", TEST_TENANT_ID)


def test_new_company_files_are_prefixed() -> None:
    version_id, skill_id = uuid.uuid4(), uuid.uuid4()
    assert _version_snapshot_dir(TEST_TENANT_ID, version_id) == (
        f"tenants/{TEST_TENANT_ID}/skills/_marketplace_versions/{version_id}"
    )
    assert _version_snapshot_dir(None, version_id) == f"skills/_marketplace_versions/{version_id}"
    key, _path = _revision_path(Skill(id=skill_id, tenant_id=TEST_TENANT_ID), 3)
    assert key == f"tenants/{TEST_TENANT_ID}/skill-revisions/{skill_id}/r3/skill.zip"
    assert _skill_dir(skill_id, TEST_TENANT_ID, "package", None) == (
        f"tenants/{TEST_TENANT_ID}/skills/{skill_id}"
    )


async def test_skill_mutations_fail_closed_on_another_company_path(
    db: AsyncSession, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    context = unit_context(TEST_USER_ID)
    creation = TextSkillCreation(name="Notes", slug=None, description="d", content=SKILL_MD)
    skill = await create_test_text_skill(db, creation, context)
    skill.storage_path = f"tenants/{OTHER}/skills/{skill.id}/SKILL.md"
    await db.flush()
    with pytest.raises(ForeignTenantPathError):
        await skill_service.update_text_content(db, skill=skill, content=SKILL_MD, context=context)
    with pytest.raises(ForeignTenantPathError):
        await skill_service.delete_skill(db, skill, context)
    assert await db.get(Skill, skill.id) is not None  # the row was not deleted
    skill.kind = "package"
    with pytest.raises(ForeignTenantPathError):
        refresh_package_metadata(skill)


def test_mask_keeps_scope_metadata_when_bodies_are_off(monkeypatch) -> None:
    """The SDK applies ``mask`` to metadata too; company/org/run ids must survive."""
    monkeypatch.setattr(settings, "deployment_mode", "multi_tenant")
    monkeypatch.setattr(settings, "langfuse_capture_input_output", True)
    metadata = _metadata_for_run(_cfg(), run_id="run-1", source="chat")
    masked = redact_payload({**metadata, "input": "secret body", "note": "free text"})
    assert masked["moldy_tenant_id"] == str(TEST_TENANT_ID)
    assert masked["moldy_run_id"] == "run-1"
    assert f"tenant:{TEST_TENANT_ID}" in masked["langfuse_tags"]
    assert masked["input"] == "[redacted]"
    assert masked["note"] == "[redacted]"
