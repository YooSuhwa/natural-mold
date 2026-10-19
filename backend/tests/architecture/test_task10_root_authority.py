import ast
import json
from pathlib import Path

import pytest

from scripts import task10_review_binding as binding_api
from scripts import task10_root_artifact_io as io
from scripts import task10_root_authority as auth
from scripts import task10_root_authority_contract as spec
from scripts import task10_root_authority_validation as check
from tests.architecture import task10_authority_fixtures as fx
from tests.architecture import task10_authority_literal_catalog as lit
from tests.architecture import task10_authority_scenarios as case
from tests.architecture import task10_review_fixtures as review

ROOT = Path(__file__).resolve().parents[3]


def test_private_registered_chain_and_fixture_independent(tmp_path: Path) -> None:
    fixture = fx.register_private_baseline(tmp_path / "private")
    chain = auth.validate_authority(fixture.root, fixture.seal, "I1_AUTHORITY_BASELINE")
    publisher = chain.records[-1]["publisher"]
    assert isinstance(publisher, dict) and publisher["actor_role"] == "fixture_registrar"
    assert publisher["output_scope"] == "fixture_workspace/private_fixture_only"
    tree = ast.parse((ROOT / "backend/tests/architecture/task10_authority_fixtures.py").read_text())
    assert all(
        "task10_root" not in ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    )


def test_preedit_inventory_and_registered_legacy_adapter(tmp_path: Path) -> None:
    actual = auth.validate_authority(ROOT)
    inventory = auth.resolve_role(actual, "I1_AUTHORITY_BASELINE", "preedit_test_node_inventory")
    assert inventory["sha256"] == "5458d43f286d528f51368d4c1c1e30ca6d88c211b3748196cf890c4897c93ed9"
    fixture = fx.register_private_baseline(tmp_path / "private")
    chain = auth.validate_authority(fixture.root)
    registered = auth.resolve_role(chain, "I1_AUTHORITY_BASELINE", "r7_plan")
    path = fixture.root / registered["path"]
    legacy = {"path": str(path), "sha256": registered["sha256"], "bytes": registered["bytes"]}
    adapted = auth.adapt_registered_legacy(chain, legacy, "I1_AUTHORITY_BASELINE", "r7_plan")
    assert adapted == registered
    with pytest.raises(io.ArtifactIOError, match="legacy_tuple_not_registered"):
        io.adapt_legacy_tuple(fixture.root, legacy, "source", registered)


def test_active_scope_and_historical_baseline_are_distinct() -> None:
    chain = auth.validate_authority(ROOT, require_stage="I1_AUTHORITY_CAPACITY_AMENDED")
    assert auth.resolve_active_scope(chain)["sequence"] == 4
    assert spec.COMMAND_SPECS["freeze-review", spec.POST].inputs[0].stage == spec.SCOPE
    auth.resolve_role(chain, "I1_AUTHORITY_SCOPE_AMENDED", "exact_path_release")
    capacity = auth.resolve_role(chain, "I1_AUTHORITY_CAPACITY_AMENDED", "exact_path_release")
    release = json.loads((ROOT / capacity["path"]).read_text())
    assert release["approved_plan"]["sha256"] == lit.APPROVED_R13_SHA256
    assert auth.resolve_role(chain, spec.SCOPE, "r7_plan")["sha256"] == lit.RUNTIME_R7_SHA256


def test_late_baseline_retry_rejected(tmp_path: Path) -> None:
    with pytest.raises(auth.AuthorityError, match="authority_stage_order"):
        auth.validate_authority(case.late_baseline_chain(tmp_path / "private").root)


def test_source_stage_two_cycles_select_exact_candidate_and_parent(tmp_path: Path) -> None:
    accepted = case.registered_i1_accepted_chain(tmp_path / "private")
    _, first = case.append_source_cycle(accepted, label="first")
    second_input, second = case.append_source_cycle(first, label="second")
    chain = auth.validate_authority(second.root)
    record = json.loads(second_input.record.read_text())
    selected = auth.resolve_occurrence(
        chain, "SOURCE_STAGE_INPUT", record["candidate"], record["parent_seal"]
    )
    assert selected["sequence"] == 14
    with pytest.raises(auth.AuthorityError, match="authority_occurrence_not_registered"):
        auth.resolve_occurrence(chain, "SOURCE_STAGE_INPUT", record["candidate"], chain.records[0])


def test_preedit_inventory_and_moved_node_mapping_stage(tmp_path: Path) -> None:
    fixture = case.registered_capacity_chain(tmp_path / "private")
    updated = case.advance_authority(fixture, "I1_POSTCOMMIT_CANDIDATE")
    chain = auth.validate_authority(updated.root, updated.seal, "I1_POSTCOMMIT_CANDIDATE")
    moved = auth.resolve_role(chain, "I1_POSTCOMMIT_CANDIDATE", "moved_node_mapping")
    assert moved == json.loads(updated.record.read_text())["roles"][9]["artifact"]


def test_capacity_lineage_literal_catalog_and_shared_fixtures(tmp_path: Path) -> None:
    capacity = case.registered_capacity_chain(tmp_path / "capacity")
    chain = auth.validate_authority(capacity.root, capacity.seal, "I1_AUTHORITY_CAPACITY_AMENDED")
    assert auth.resolve_active_scope(chain)["sequence"] == 4
    i1 = review.registered_i1_review_fixture(tmp_path / "i1")
    i24 = review.registered_i24_review_fixture(tmp_path / "i24")
    i1_stage = auth.validate_authority(i1.authority.root).records[-1]["stage"]
    assert i1_stage == "I1_EXACT_COMMIT_REVIEW_RAW_RECEIVED"
    i24_stage = auth.validate_authority(i24.authority.root).records[-1]["stage"]
    assert i24_stage == "I24_REVIEW_RAW_RECEIVED"
    assert i1.request_text.count("PLAN_SHA256:") == 1
    assert i24.request_text.count("OPEN_CRITICAL_HIGH: 0") == 1
    assert json.loads(i1.authority.role_paths["reviewer_request"].read_text()) == i1.request
    assert json.loads(i1.authority.role_paths["reviewer_provenance"].read_text()) == i1.provenance
    review_authority = review.registered_review_authority
    for fixture, label, origin_stage, origin_role, material_stage in (
        (i1, "I1", "I1_POSTCOMMIT_CANDIDATE", "i1_seal_argv", "I1_REVIEW_MATERIAL_FROZEN"),
        (i24, "I24", "I23_FREEZE_READY", "seal_argv", "PARENT_REVIEW_MATERIAL_FROZEN"),
    ):
        assert label in ("I1", "I24")
        prerequisite = review_authority(tmp_path / f"{label}-pre", label, "prerequisite")
        material = review_authority(tmp_path / f"{label}-material", label, "material")
        assert auth.validate_authority(prerequisite.root).records[-1]["stage"] == origin_stage
        assert auth.validate_authority(material.root).records[-1]["stage"] == material_stage
        fixture_chain = auth.validate_authority(fixture.authority.root)
        material_argv = auth.resolve_role(fixture_chain, material_stage, "seal_argv")
        assert material_argv == auth.resolve_role(fixture_chain, origin_stage, origin_role)
        seal_argv = json.loads(fixture.binding_seal.read_text())["freeze_argv"]
        adapted = auth.adapt_registered_legacy(
            fixture_chain, seal_argv, material_stage, "seal_argv"
        )
        assert adapted == material_argv
        for authority, name in ((prerequisite, "freeze-review"), (material, "seal-review-binding")):
            command_chain = auth.validate_authority(authority.root)
            stage = command_chain.records[-1]["stage"]
            assert isinstance(stage, str)
            command = spec.COMMAND_SPECS[name, stage]
            inputs = tuple(authority.role_paths[item.role] for item in command.inputs)
            request = binding_api.ReviewCommand(inputs, ())
            expected = tuple(auth.resolve_role(command_chain, *i[1:]) for i in command.inputs)
            assert binding_api._command(command_chain, name, request) == expected
    i24_chain = auth.validate_authority(i24.authority.root)
    final_candidate = auth.resolve_role(i24_chain, "I23_FREEZE_READY", "final_candidate")
    candidate_document = json.loads((i24.authority.root / final_candidate["path"]).read_text())
    assert candidate_document["candidate"] == i24.request["candidate"]
    assert candidate_document["workspace_root"] == str(i24.authority.root.resolve())
    structural_case = case.build_structural_case(tmp_path / "structural")
    structural = json.loads(structural_case.contract.read_text())
    assert set(structural) == set(case.STRUCTURAL_KEYS)
    assert len(lit.PUBLIC_COMMANDS) == 9 and len(lit.ENVELOPES) == 13
    stage_items = spec.STAGE_SPECS.items()
    actual_stages = {name: (stage.status, tuple(stage.roles)) for name, stage in stage_items}
    expected_stages = {name: (status, tuple(roles)) for name, status, roles in lit.STAGES}
    assert actual_stages == expected_stages
    actual_commands = tuple(
        (context, name, *item) for (name, context), item in spec.COMMAND_SPECS.items()
    )
    assert actual_commands == lit.COMMANDS


@pytest.mark.parametrize("mode", ["wrong_parent", "second_capacity", "late_capacity", "nonnull"])
def test_invalid_capacity_occurrence_rejected(tmp_path: Path, mode: str) -> None:
    with pytest.raises(auth.AuthorityError, match="authority_stage_order|candidate_invalid"):
        auth.validate_authority(case.invalid_capacity_chain(tmp_path / mode, mode).root)


def test_shared_helpers_have_no_production_oracle_imports() -> None:
    base = ROOT / "backend/tests/architecture"
    names = ("task10_authority_literal_catalog.py", "task10_authority_scenarios.py")
    names += ("task10_review_fixtures.py",)
    for tree in (ast.parse((base / name).read_text()) for name in names):
        imports = (
            ast.unparse(node)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        )
        assert all("scripts.task10_" not in statement for statement in imports)


@pytest.mark.parametrize("target", ["record", "seal", "locator"])
def test_forged_record_seal_locator_and_role_drift_reject(tmp_path: Path, target: str) -> None:
    fixture = fx.register_private_baseline(tmp_path / "private")
    path = {"record": fixture.record, "seal": fixture.seal, "locator": fixture.locator}[target]
    fx.mutate_json(path, "status" if target != "locator" else "generation", "forged")
    with pytest.raises((auth.AuthorityError, io.ArtifactIOError)):
        auth.validate_authority(fixture.root)


def test_locator_rollback_gap_and_unlisted_role_reject(tmp_path: Path) -> None:
    fixture = fx.register_private_baseline(tmp_path / "private")
    chain = auth.validate_authority(fixture.root)
    with pytest.raises(auth.AuthorityError, match="root_role_not_registered"):
        auth.resolve_role(chain, "I1_AUTHORITY_BASELINE", "unlisted")
    fx.mutate_json(fixture.locator, "generation", 2)
    with pytest.raises((auth.AuthorityError, io.ArtifactIOError)):
        auth.validate_authority(fixture.root)


def test_git_porcelain_and_malformed_fields() -> None:
    raw = b" M tracked\0R  index-dest\0index-origin\0 R work-dest\0work-origin\0"
    raw += b" C copy-dest\0copy-origin\0?? bad-\xff.txt\0"
    state = auth.parse_git_status(raw, "5" * 40)
    assert state["entries"] == [
        {"xy": " M", "paths": ["tracked"]},
        {"xy": "R ", "paths": ["index-dest", "index-origin"]},
        {"xy": " R", "paths": ["work-dest", "work-origin"]},
        {"xy": " C", "paths": ["copy-dest", "copy-origin"]},
        {"xy": "??", "paths": ["bad-\udcff.txt"]},
    ]
    malformed: tuple[io.JsonValue, ...] = (
        {"head": "a" * 40, "entries": [{"xy": True, "paths": ["a"]}], "canonical_paths": ["a"]},
        {"head": "a" * 40, "entries": [{"xy": "R ", "paths": ["a"]}], "canonical_paths": ["a"]},
        {"head": "a" * 40, "entries": [{"xy": "zz", "paths": ["a"]}], "canonical_paths": ["a"]},
    )
    for value in malformed:
        with pytest.raises(auth.AuthorityError, match="git_state_schema"):
            check.validate_git_state(value)
    for raw_value in (b"\xff  name\0", b"M  \0", b"zz name\0"):
        with pytest.raises(auth.AuthorityError, match="git_status_invalid"):
            auth.parse_git_status(raw_value, "5" * 40)
    for version in (True, 1.0):
        sample = {"schema_version": version, "kind": "sample"}
        with pytest.raises(auth.AuthorityError, match="sample_schema"):
            check.exact(sample, {"schema_version", "kind"}, "sample")
        with pytest.raises(auth.AuthorityError, match="record_seal_mismatch"):
            check.validate_sequences(1, version, 1)


def test_capture_git_state_reports_dirty_drift(tmp_path: Path) -> None:
    fixture = fx.register_private_baseline(tmp_path / "private")
    assert auth.capture_git_state(fixture.root)["canonical_paths"] == []
    (fixture.root / "tracked.txt").write_text("changed\n")
    paths = auth.capture_git_state(fixture.root)["canonical_paths"]
    assert isinstance(paths, list) and "tracked.txt" in paths


@pytest.mark.parametrize(
    "relative",
    [
        "out/result.json",
        ".omo/evidence/company-administration/task10-root-authority/forbidden.json",
    ],
)
def test_public_writer_existing_and_protected_output(tmp_path: Path, relative: str) -> None:
    root = fx.fixture_output_root(tmp_path)
    if relative.startswith(".omo"):
        with pytest.raises(io.ArtifactIOError, match="protected_output"):
            io.write_public_output(root, relative, b"new")
        return
    final = root / relative
    final.write_text("old")
    with pytest.raises(io.ArtifactIOError, match="output_exists"):
        io.write_public_output(root, relative, b"new")
    assert final.read_text() == "old"


def test_public_writer_symlink_parent_and_hardlink_final(tmp_path: Path) -> None:
    root, outside = fx.fixture_roots(tmp_path, "root", "outside")
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(NotADirectoryError, match="Not a directory"):
        io.write_public_output(root, "link/result", b"new")
    protected = root / "protected"
    protected.write_text("old")
    (root / "hard").hardlink_to(protected)
    with pytest.raises(io.ArtifactIOError, match="output_exists"):
        io.write_public_output(root, "hard", b"new")
    assert protected.read_text() == "old"


def test_public_writer_prelink_and_postlink_interruption(tmp_path: Path) -> None:
    root = fx.fixture_output_root(tmp_path)

    def pre(phase: str) -> None:
        if phase == "temp_fsynced":
            raise OSError("stop")

    with pytest.raises(OSError, match="stop"):
        io.write_public_output(root, "out/pre", b"value", phase_hook=pre)
    assert list((root / "out").iterdir()) == []

    def post(phase: str) -> None:
        if phase == "linked":
            raise OSError("stop")

    with pytest.raises(io.ArtifactCommitted) as committed:
        io.write_public_output(root, "out/post", b"value", phase_hook=post)
    assert committed.value.artifact == io.artifact_tuple(root, "out/post", "metadata")
    assert [path.name for path in (root / "out").iterdir()] == ["post"]


def test_public_writer_parent_toctou_swap_fails(tmp_path: Path) -> None:
    root = fx.fixture_output_root(tmp_path)
    parent = root / "out"

    def swap(phase: str) -> None:
        if phase == "temp_fsynced":
            parent.rename(root / "moved")
            parent.mkdir()

    with pytest.raises(io.ArtifactIOError, match="output_parent_changed"):
        io.write_public_output(root, "out/result", b"value", phase_hook=swap)
    assert not (root / "out/result").exists() and list((root / "moved").iterdir()) == []


def test_exact_task10_source_caps() -> None:
    for relative, cap in fx.SOURCE_CAPS.items():
        lines = (ROOT / "backend" / relative).read_text().splitlines()
        pure = sum(bool(line.strip()) and not line.lstrip().startswith("#") for line in lines)
        assert pure <= cap, (relative, pure, cap)
