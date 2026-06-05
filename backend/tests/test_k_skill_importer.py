"""ADR-017 Slice F — k-skill importer smoke tests.

Cover the Spec §5 invariants Sat called out in the M7 brief:

* discovery exclusion of meta dirs
* frontmatter ``name == dir name`` validation
* canonical content hash determinism
* idempotent re-run (no new version when bytes are unchanged)
* single skill failure doesn't abort the whole sync
* dry-run produces the same report shape as a real run without side effects
* secret_scan failure skips only the offending skill

The tests construct a synthetic ``data/upstreams/k-skill`` tree rather
than fetching the real repo so they're fast + offline-safe.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.marketplace.k_skill_importer import (
    ImportAction,
    compute_content_hash,
    discover_skills,
    import_skill,
    infer_execution_profile,
    run_sync,
    validate_skill,
)
from app.models.marketplace import MarketplaceItem, MarketplaceVersion

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_skill(
    root: Path,
    name: str,
    *,
    frontmatter_name: str | None = None,
    body: str = "body\n",
    requirements_txt: str | None = "requests\n",
    extras: dict[str, str] | None = None,
) -> Path:
    """Drop a synthetic skill under ``root/<name>/`` and return its path.

    ``frontmatter_name=None`` reuses ``name`` (happy path). Pass a
    different value to exercise the mismatch rejection rule.
    """

    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    fname = frontmatter_name if frontmatter_name is not None else name
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {fname}\ndescription: synthetic\nversion: 0.1.0\n---\n\n{body}",
    )
    if requirements_txt is not None:
        (skill_dir / "requirements.txt").write_text(requirements_txt)
    for rel, content in (extras or {}).items():
        (skill_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (skill_dir / rel).write_text(content)
    return skill_dir


# ---------------------------------------------------------------------------
# Pure helpers (no DB)
# ---------------------------------------------------------------------------


def test_discover_excludes_meta_dirs(tmp_path: Path) -> None:
    _write_skill(tmp_path, "srt-booking")
    # Excluded: docs/, .github/, node_modules/, scripts/
    docs = tmp_path / "docs" / "sample"
    docs.mkdir(parents=True)
    (docs / "SKILL.md").write_text("---\nname: sample\n---\n\n")
    gh = tmp_path / ".github" / "workflows" / "ci-fixture"
    gh.mkdir(parents=True)
    (gh / "SKILL.md").write_text("---\nname: ci-fixture\n---\n\n")

    discovered = discover_skills(tmp_path)
    assert [p.name for p in discovered] == ["srt-booking"]


def test_validate_skill_rejects_name_mismatch(tmp_path: Path) -> None:
    bad = _write_skill(tmp_path, "wrong-dir", frontmatter_name="something-else")
    ok, _, reason = validate_skill(bad)
    assert ok is False
    assert reason is not None and "name" in reason


def test_validate_skill_rejects_missing_description(tmp_path: Path) -> None:
    bad = tmp_path / "missing-description"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("---\nname: missing-description\nversion: 0.1.0\n---\n\nbody\n")
    ok, _, reason = validate_skill(bad)
    assert ok is False
    assert reason is not None and "description" in reason


def test_validate_skill_accepts_match(tmp_path: Path) -> None:
    good = _write_skill(tmp_path, "korean-spell-check")
    ok, fm, reason = validate_skill(good)
    assert ok is True and reason is None
    assert fm is not None and fm["name"] == "korean-spell-check"


def test_content_hash_is_deterministic(tmp_path: Path) -> None:
    skill_a = _write_skill(tmp_path / "a", "k1")
    skill_b = _write_skill(tmp_path / "b", "k1")
    # Same canonical layout → same hash (path-relative hashing).
    assert compute_content_hash(skill_a) == compute_content_hash(skill_b)


def test_content_hash_changes_when_body_changes(tmp_path: Path) -> None:
    skill = _write_skill(tmp_path, "k2")
    before = compute_content_hash(skill)
    (skill / "SKILL.md").write_text(
        "---\nname: k2\ndescription: synthetic\nversion: 0.2.0\n---\n\nupdated body\n"
    )
    after = compute_content_hash(skill)
    assert before != after


def test_execution_profile_detects_python(tmp_path: Path) -> None:
    skill = _write_skill(tmp_path, "py-skill")  # writes requirements.txt
    profile = infer_execution_profile(skill)
    assert profile["support_level"] == "ready_python"
    assert profile["requires_python"] is True


def test_execution_profile_falls_back_to_ready_python(tmp_path: Path) -> None:
    """Pure SKILL.md (no runners) skills are instruction-only — LLM follows
    SKILL.md via read_file. From the marketplace card POV this is
    ``ready_python`` (no setup needed). ``manual_only`` is reserved for
    skills that require external app/browser session, which the curated
    requirement map can opt into explicitly.
    """

    skill = _write_skill(tmp_path, "manual-skill", requirements_txt=None)
    profile = infer_execution_profile(skill)
    assert profile["support_level"] == "ready_python"
    assert profile["runners"] == []
    assert profile["requires_python"] is False


# ---------------------------------------------------------------------------
# Full sync — DB integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_sync_creates_then_idempotent(db: AsyncSession, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    storage = tmp_path / "storage"
    _write_skill(repo, "korean-spell-check")
    _write_skill(repo, "srt-booking")

    report1 = await run_sync(
        repo_dir=repo,
        builtin_storage_dir=storage,
        ref="main",
        commit_sha="deadbeef",
        db=db,
        dry_run=False,
    )
    await db.commit()
    assert report1.created == 2
    assert report1.unchanged == 0
    assert report1.failed == 0

    # Second run on same bytes — should be entirely unchanged.
    report2 = await run_sync(
        repo_dir=repo,
        builtin_storage_dir=storage,
        ref="main",
        commit_sha="deadbeef",
        db=db,
        dry_run=False,
    )
    await db.commit()
    assert report2.unchanged == 2
    assert report2.created == 0
    assert report2.updated == 0

    # Two MarketplaceItems exist tagged as k-skill.
    rows = (
        (await db.execute(select(MarketplaceItem).where(MarketplaceItem.source_kind == "k-skill")))
        .scalars()
        .all()
    )
    assert {r.source_external_id for r in rows} == {
        "korean-spell-check",
        "srt-booking",
    }
    # Each item has exactly 1 version.
    for item in rows:
        versions = (
            (
                await db.execute(
                    select(MarketplaceVersion).where(MarketplaceVersion.item_id == item.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(versions) == 1


@pytest.mark.asyncio
async def test_run_sync_new_version_when_content_changes(db: AsyncSession, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    storage = tmp_path / "storage"
    skill = _write_skill(repo, "korean-spell-check")

    r1 = await run_sync(
        repo_dir=repo,
        builtin_storage_dir=storage,
        ref="main",
        commit_sha="aaa",
        db=db,
        dry_run=False,
    )
    await db.commit()
    assert r1.created == 1

    # Mutate the skill content — should produce a new version.
    (skill / "SKILL.md").write_text(
        "---\nname: korean-spell-check\ndescription: synthetic\nversion: 0.2.0\n---\n\nupdated\n"
    )
    r2 = await run_sync(
        repo_dir=repo,
        builtin_storage_dir=storage,
        ref="main",
        commit_sha="bbb",
        db=db,
        dry_run=False,
    )
    await db.commit()
    assert r2.updated == 1

    item = (
        await db.execute(
            select(MarketplaceItem).where(
                MarketplaceItem.source_external_id == "korean-spell-check"
            )
        )
    ).scalar_one()
    versions = (
        (await db.execute(select(MarketplaceVersion).where(MarketplaceVersion.item_id == item.id)))
        .scalars()
        .all()
    )
    assert len(versions) == 2
    # version_number is monotonic per item.
    assert sorted(v.version_number for v in versions) == [1, 2]


@pytest.mark.asyncio
async def test_secret_scan_failure_isolates_to_one_skill(db: AsyncSession, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    storage = tmp_path / "storage"
    _write_skill(repo, "korean-spell-check")
    # Inject a .env file → secret_scan rejects this skill only.
    leaky = _write_skill(repo, "srt-booking")
    (leaky / ".env").write_text("SECRET=abc\n")

    report = await run_sync(
        repo_dir=repo,
        builtin_storage_dir=storage,
        ref="main",
        commit_sha="xyz",
        db=db,
        dry_run=False,
    )
    await db.commit()

    by_action = {r.name: r.action for r in report.results}
    assert by_action["korean-spell-check"] == ImportAction.CREATED
    assert by_action["srt-booking"] == ImportAction.FAILED_SECRET_SCAN

    rows = (
        (await db.execute(select(MarketplaceItem).where(MarketplaceItem.source_kind == "k-skill")))
        .scalars()
        .all()
    )
    # Only the clean skill produced a row.
    assert {r.source_external_id for r in rows} == {"korean-spell-check"}


@pytest.mark.asyncio
async def test_dry_run_does_not_write(db: AsyncSession, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    storage = tmp_path / "storage"
    _write_skill(repo, "korean-spell-check")

    report = await run_sync(
        repo_dir=repo,
        builtin_storage_dir=storage,
        ref="main",
        commit_sha="xyz",
        db=db,
        dry_run=True,
    )
    await db.rollback()
    assert report.dry_run is True
    # Report still attributes the action as "created" so the operator
    # sees the would-be effect.
    assert report.created == 1

    # No row landed in the DB.
    rows = (
        (await db.execute(select(MarketplaceItem).where(MarketplaceItem.source_kind == "k-skill")))
        .scalars()
        .all()
    )
    assert rows == []


@pytest.mark.asyncio
async def test_only_filter_restricts_run(db: AsyncSession, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    storage = tmp_path / "storage"
    _write_skill(repo, "korean-spell-check")
    _write_skill(repo, "srt-booking")

    report = await run_sync(
        repo_dir=repo,
        builtin_storage_dir=storage,
        ref="main",
        commit_sha="z",
        db=db,
        dry_run=False,
        only=["korean-spell-check"],
    )
    await db.commit()
    assert len(report.results) == 1
    assert report.results[0].name == "korean-spell-check"


@pytest.mark.asyncio
async def test_import_skill_validation_failure_is_isolated(
    db: AsyncSession, tmp_path: Path
) -> None:
    """A frontmatter mismatch returns ``FAILED_VALIDATION`` without
    raising. Bezos pin — single skill failure must not abort the sync."""

    repo = tmp_path / "repo"
    storage = tmp_path / "storage"
    bad = _write_skill(repo, "wrong-dir", frontmatter_name="other")

    result = await import_skill(
        bad,
        commit_sha="abc",
        ref="main",
        builtin_storage_dir=storage,
        db=db,
        dry_run=False,
    )
    assert result.action == ImportAction.FAILED_VALIDATION
    assert result.detail
