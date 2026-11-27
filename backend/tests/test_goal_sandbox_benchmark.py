import copy
import hashlib
from pathlib import Path

import pytest

from scripts import benchmark_goal_sandbox as benchmark


@pytest.mark.parametrize("retained_first", [False, True])
async def test_files_are_restored_and_the_same_document_is_returned(retained_first: bool) -> None:
    size = 262_144
    report = await benchmark.measure("filesystem", size, 0, retained_first=retained_first)
    per_call = report["arms"]["per_call"]
    retained = report["arms"]["retained"]
    expected = hashlib.sha256(benchmark.document(size).replace(b"|draft|", b"|final|")).hexdigest()

    for arm in (per_call, retained):
        assert arm["intermediate_sha256"] == arm["result_sha256"] == expected
        assert arm["downloaded_bytes"] == size + 64
        assert 0 < arm["preparation_seconds"] <= arm["total_seconds"]
    assert per_call["uploaded_bytes"] - retained["uploaded_bytes"] == 2 * size
    assert report["cost"] is None and report["passes_time_and_cost"] is None
    assert len(per_call["sandbox_ids"]) == 2
    assert len(retained["sandbox_ids"]) == 1


@pytest.fixture
def billing_case() -> tuple[dict, dict]:
    def arm(ids: list[str], total: float) -> dict:
        return {
            "sandbox_ids": ids,
            "total_seconds": total,
            "intermediate_sha256": "a" * 64,
            "result_sha256": "a" * 64,
        }

    report = {
        "run_id": "paired",
        "provider": "opensandbox",
        "schema_version": 1,
        "profile": {"image": "sha256:" + "0" * 64},
        "arms": {"per_call": arm(["step1", "step2"], 4), "retained": arm(["shared"], 3)},
    }
    invoice = {
        "run_id": "paired",
        "currency": "USD",
        "evidence_ref": "sample-invoice",
        "totals_by_sandbox": {"step1": "0.10", "step2": "0.20", "shared": "0.30"},
    }
    return report, invoice


@pytest.mark.parametrize(
    ("total", "cost", "decision"),
    [
        (3, "0.30", True),
        (4, "0.29", True),
        (4, "0.30", False),
        (5, "0.29", False),
        (3, "0.31", False),
    ],
)
def test_time_and_reconciled_cost_gate(
    billing_case: tuple[dict, dict], total: float, cost: str, decision: bool
) -> None:
    report, invoice = billing_case
    report["arms"]["retained"]["total_seconds"] = total
    invoice["totals_by_sandbox"]["shared"] = cost
    assessed = benchmark.assess(report, invoice)
    assert assessed["passes_time_and_cost"] is decision
    assert assessed["cost"]["totals"]["per_call"] == "0.30"


def test_incomplete_or_unrelated_evidence_does_not_yield_a_decision(
    billing_case: tuple[dict, dict],
) -> None:
    report, invoice = billing_case
    missing = copy.deepcopy(invoice)
    del missing["totals_by_sandbox"]["shared"]
    for invalid in (missing, {**invoice, "run_id": "another"}, {**invoice, "evidence_ref": ""}):
        with pytest.raises(ValueError, match="Billing"):
            benchmark.assess(report, invalid)
    for value in ("-1", "infinity", "NaN", "invalid"):
        invoice["totals_by_sandbox"]["shared"] = value
        with pytest.raises(ValueError, match="Billing totals|Invalid amount"):
            benchmark.assess(report, invoice)
    with pytest.raises(ValueError, match="Billing must match"):
        benchmark.assess({**report, "provider": "filesystem"}, invoice)


async def test_operator_supplied_package_requires_network_denied_provider(tmp_path: Path) -> None:
    (tmp_path / "stage.py").write_bytes(benchmark.SCRIPT)
    with pytest.raises(ValueError, match="OpenSandbox provider"):
        await benchmark.measure("filesystem", 512, 0, skill_dir=tmp_path)


def test_measured_package_digest_is_the_published_tree_digest(tmp_path: Path) -> None:
    from app.skills.package_hash import compute_package_tree_hash

    package = tmp_path / "skill"
    script = package / "scripts" / "edit.py"
    script.parent.mkdir(parents=True)
    script.write_bytes(benchmark.SCRIPT)
    (package / "SKILL.md").write_text("Reviewed test skill.")
    (package / ".DS_Store").write_text("ignored")
    files, digest = benchmark.packaged_workload(tmp_path / "frozen", package, "scripts/edit.py")
    assert files == {"SKILL.md": b"Reviewed test skill.", "scripts/edit.py": benchmark.SCRIPT}
    assert digest == compute_package_tree_hash(package)
    with pytest.raises(ValueError, match="entrypoint"):
        benchmark.packaged_workload(tmp_path / "another", package, "../script.py")
