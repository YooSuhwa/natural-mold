"""Deployment and organization opt-in gates for a measured workspace profile."""

import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import settings
from app.schemas.org import OrganizationSettings, OrganizationSettingsPatch
from app.services.goal_sandbox_profile import (
    goal_sandbox_provider_fingerprint,
    measured_skill_hashes,
    skill_tree_digest,
)
from app.skills.package_hash import compute_package_tree_hash


@pytest.fixture
def report(tmp_path, monkeypatch):
    image = "worker@sha256:" + "a" * 64
    monkeypatch.setattr(settings, "skill_sandbox_image", image)
    monkeypatch.setattr(settings, "goal_sandbox_evidence_path", tmp_path / "assessed.json")
    data_hash = "b" * 64
    base = {
        "total_seconds": 10,
        "sandbox_ids": ["a", "b"],
        "intermediate_sha256": data_hash,
        "result_sha256": data_hash,
    }
    return {
        "provider": "opensandbox",
        "schema_version": 1,
        "run_id": str(uuid4()),
        "profile": {
            "domain": settings.skill_sandbox_domain,
            "cpu": settings.skill_sandbox_cpu,
            "memory": settings.skill_sandbox_memory,
            "image": image,
            "network_domains": [],
        },
        "workload": {"input_sha256": data_hash, "input_bytes": 1234, "skill_tree_sha256": "c" * 64},
        "arms": {
            "per_call": base,
            "retained": {**base, "sandbox_ids": ["c"], "total_seconds": 8},
        },
        "cost": {
            "currency": "KRW",
            "evidence_ref": "invoice:comparison",
            "totals": {"per_call": "20", "retained": "20"},
        },
        "passes_time_and_cost": True,
    }


def accepted(report, digest="c" * 64):
    settings.goal_sandbox_evidence_path.write_text(json.dumps(report))
    return measured_skill_hashes(digest)


def test_operator_report_grants_only_exact_bytes_and_deployment(report, monkeypatch):
    assert accepted(report)
    assert not accepted(report, digest="d" * 64)
    before = goal_sandbox_provider_fingerprint()
    monkeypatch.setattr(settings, "skill_sandbox_domain", "different-deployment.test")
    assert not accepted(report)
    assert goal_sandbox_provider_fingerprint() != before


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("provider",), "filesystem"),
        (("cost",), None),
        (("cost", "evidence_ref"), " "),
        (("cost", "totals", "retained"), "21"),
        (("cost", "totals", "retained"), "NaN"),
        (("arms", "retained", "total_seconds"), 10),
        (("arms", "retained", "sandbox_ids"), ["a"]),
        (("arms", "retained", "result_sha256"), "e" * 64),
        (("profile", "network_domains"), ["allowed.test"]),
        (("passes_time_and_cost",), None),
    ],
)
def test_unmeasured_or_failed_gate_cannot_enable_retention(report, path, value):
    target = report
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert not accepted(report)


def test_refuse_mutable_images_and_files_under_data_root(report, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "skill_sandbox_image", "worker:latest")
    report["profile"]["image"] = "worker:latest"
    assert not accepted(report)
    image = "sha256:" + "e" * 64
    monkeypatch.setattr(settings, "skill_sandbox_image", image)
    report["profile"]["image"] = image
    assert accepted(report)
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    assert not accepted(report)


def test_org_setting_explicit_enable_and_reset():
    assert OrganizationSettings().goal_sandbox_enabled is False
    assert OrganizationSettingsPatch().update_values() == {}
    assert OrganizationSettingsPatch(goal_sandbox_enabled=True).update_values() == {
        "goal_sandbox_enabled": True
    }
    assert OrganizationSettingsPatch(goal_sandbox_enabled=None).update_values() == {
        "goal_sandbox_enabled": False
    }


def test_tree_digest_matches_package_hash_and_covers_all_uploaded_files(tmp_path: Path):
    files = [("script.py", b"main"), ("SKILL.md", "스킬".encode())]
    for rel, data in files:
        (tmp_path / rel).write_bytes(data)
    assert skill_tree_digest(files) == compute_package_tree_hash(tmp_path)
    assert skill_tree_digest([*files, ("extra", b"new")]) != compute_package_tree_hash(tmp_path)


def test_both_measured_packages_can_open_only_the_reviewed_pair(report):
    first, second = "c" * 64, "d" * 64
    report["workload"]["skill_tree_sha256s"] = [first, second]
    assert accepted(report, first) == accepted(report, second) == (first, second)
    assert accepted(report, "e" * 64) is None
    report["workload"]["skill_tree_sha256s"] = [second, first]
    assert accepted(report, second) is None
    report["workload"]["skill_tree_sha256s"] = [first, first]
    assert accepted(report, first) == (first,)
