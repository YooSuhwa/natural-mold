"""I-07 purge and export cover the I-08 company folders ``tenants/<tenant_id>/`` under
every storage root (uploads, artifacts, data: skills, skill revisions, company
marketplace snapshots); another company's folder and anything a symlink points at stay."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import settings
from app.models.organization import Tenant
from app.services.platform.tenant_files import company_files, export_entries
from app.services.platform.tenant_purge_job import run_tenant_purge_job
from tests.authz.i07_company_seed import SeededCompany, seed_company
from tests.conftest import TestSession

NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)
TREES = {
    "uploads": ("a.txt",),
    "data": (
        "skills/s1/SKILL.md",
        "skill-revisions/s1/r1/skill.zip",
        "skills/_marketplace_versions/v/SKILL.md",
    ),
    "artifacts": ("conversations/c/a/v1/out.md",),
}


def _roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    roots = {name: tmp_path / f"root-{name}" for name in TREES}
    monkeypatch.setattr(settings, "upload_dir", str(roots["uploads"]))
    monkeypatch.setattr(settings, "data_root", str(roots["data"]))
    monkeypatch.setattr(settings, "artifact_storage_dir", str(roots["artifacts"]))
    monkeypatch.setattr(settings, "artifact_storage_backend", "local")
    return roots


def _seed_tree(roots: dict[str, Path], company: SeededCompany) -> list[Path]:
    files = []
    for name, relatives in TREES.items():
        for relative in relatives:
            path = roots[name] / "tenants" / str(company.tenant_id) / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{company.slug}:{relative}")
            files.append(path)
    return files


async def _companies(tmp_path: Path) -> tuple[SeededCompany, SeededCompany]:
    async with TestSession() as db:
        doomed = await seed_company(db, tmp_path / "seed", "doomed")
        other = await seed_company(db, tmp_path / "seed", "other")
        tenant = await db.get(Tenant, doomed.tenant_id)
        assert tenant is not None
        tenant.status, tenant.deletion_due_at = "deletion_scheduled", NOW - timedelta(hours=1)
        await db.commit()
    return doomed, other


async def test_purge_removes_the_company_folders_and_keeps_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = _roots(tmp_path, monkeypatch)
    doomed, other = await _companies(tmp_path)
    doomed_files, other_files = _seed_tree(roots, doomed), _seed_tree(roots, other)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("not a company folder")

    [result] = await run_tenant_purge_job(maker=TestSession, now=NOW)

    assert result.tenant_id == doomed.tenant_id
    for root in roots.values():
        assert not (root / "tenants" / str(doomed.tenant_id)).exists()
    assert not any(path.exists() for path in doomed_files)
    assert all(path.exists() for path in other_files)
    assert (outside / "keep.txt").exists()


async def test_a_symlinked_company_folder_is_never_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = _roots(tmp_path, monkeypatch)
    doomed, _other = await _companies(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("victim")
    link = roots["uploads"] / "tenants" / str(doomed.tenant_id)
    link.parent.mkdir(parents=True)
    link.symlink_to(outside, target_is_directory=True)

    await run_tenant_purge_job(maker=TestSession, now=NOW)

    assert (outside / "keep.txt").read_text() == "victim"


async def test_export_carries_the_company_folders_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = _roots(tmp_path, monkeypatch)
    doomed, other = await _companies(tmp_path)
    _seed_tree(roots, doomed)
    _seed_tree(roots, other)
    async with TestSession() as db:
        files = await company_files(db, doomed.tenant_id)
    names = [name for name, _path in export_entries(files)]
    assert "files/company/uploads/a.txt" in names
    assert "files/company/data/skill-revisions/s1/r1/skill.zip" in names
    assert "files/company/artifacts/conversations/c/a/v1/out.md" in names
    assert len(names) == len(set(names))
    assert not any(str(other.tenant_id) in str(path) for _name, path in export_entries(files))
