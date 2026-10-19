"""Immutable declarative contract for Task 10 Root authority consumers."""

from __future__ import annotations

from types import MappingProxyType
from typing import Literal, NamedTuple


class RoleSpec(NamedTuple):
    name: str
    content_type: Literal["source", "metadata"] = "metadata"
    manifest_kind: str | None = None


class StageSpec(NamedTuple):
    status: str
    roles: tuple[RoleSpec, ...]


class InputSpec(NamedTuple):
    argument: str | None
    stage: str
    role: str


class CommandSpec(NamedTuple):
    argv: InputSpec
    inputs: tuple[InputSpec, ...]
    output_stage: str


def m(name: str, kind: str | None = None) -> RoleSpec:
    return RoleSpec(name, "metadata", kind)


def roles(*groups: tuple[RoleSpec, ...]) -> tuple[RoleSpec, ...]:
    return tuple(item for group in groups for item in group)


BASELINE = roles(
    (m("exact_path_release"), m("legacy_release"), m("split_amendment")),
    (m("parser_amendment"), m("native_amendment"), m("stage_amendment")),
    (m("r2_preflight"), m("metis"), m("metis_disposition")),
    (m("r7_plan"), m("r7_draft"), m("starting_git_snapshot")),
    (m("preedit_test_node_inventory", "test_node_inventory"),),
    (m("preedit_source_snapshot_manifest", "source_snapshot_manifest"),),
)
POSTCOMMIT = roles(
    (m("candidate_manifest"), m("source_manifest", "source_snapshot_manifest")),
    (m("implementation_evidence", "artifact_index"), m("operational_cleanup")),
    (m("content_edit_path_manifest", "path_manifest"),),
    (m("commit_path_manifest", "path_manifest"), RoleSpec("ast_helper_snapshot", "source")),
    (m("postcommit_git_snapshot"),),
    (m("postedit_test_node_inventory", "test_node_inventory"),),
    (m("moved_node_mapping", "moved_node_mapping"),),
    (m("node_preservation_result", "verification_result"),),
    (m("i1_freeze_argv", "argv_manifest"), m("i1_seal_argv", "argv_manifest")),
)
MATERIAL = roles(
    (m("review_input"), m("review_binding"), m("freeze_execution")),
    (m("freeze_cleanup"), m("seal_argv", "argv_manifest")),
)
REVIEW_INPUT = roles(
    (m("review_binding_seal"), m("seal_execution"), m("seal_cleanup")),
    (m("review_request_text"), m("reviewer_request")),
)
I1_RAW = roles(
    (m("review_raw"), m("review_launch_receipt"), m("reviewer_provenance")),
    (m("i1_parser_argv", "argv_manifest"),),
)
I1_REVIEWED = roles(
    (m("review_acceptance"), m("review_verify_execution"), m("review_cleanup")),
    *(
        roles(
            (m(f"i1_f{number}_gate_context", "gate_context"),),
            (m(f"i1_f{number}_argv", "argv_manifest"),),
        )
        for number in range(1, 5)
    ),
)
I1_GATES = tuple(
    m(f"i1_f{number}_{suffix}", kind)
    for number in range(1, 5)
    for suffix, kind in (
        ("execution", None),
        ("result", "verification_result"),
        ("cleanup", None),
    )
)
I1_ACCEPTED = roles(
    (m("final_acceptance"), m("accepted_candidate_manifest")),
    (m("accepted_source_manifest", "source_snapshot_manifest"),),
    (m("accepted_commit_path_manifest", "path_manifest"),),
    (m("accepted_test_node_inventory", "test_node_inventory"),),
)
SOURCE_INPUT = roles(
    (m("baseline"), m("candidate"), m("structural_contract", "structural_contract")),
    (m("compare_argv", "argv_manifest"), m("pre_git_snapshot")),
)
SOURCE_ACCEPTED = roles(
    (m("source_acceptance"), m("source_compare_execution"), m("source_cleanup")),
    (m("post_git_snapshot"), m("post_test_node_inventory", "test_node_inventory")),
)
I23 = roles(
    (m("plan"), m("final_candidate"), m("accepted_seal_inventory", "artifact_index")),
    (m("evidence_manifest", "artifact_index"), RoleSpec("runner_entrypoint", "source")),
    tuple(
        RoleSpec(name, "source")
        for name in ("runner_dispatcher", "runner_runtime", "receipt_plugin")
    ),
    (
        RoleSpec("pytest_config", "source"),
        RoleSpec("integration_conftest", "source"),
        m("collection_hook_proof"),
    ),
    (m("static_registry"), m("fixture_catalog"), m("native_source_contract")),
    (m("native_intent"), m("native_preparation_command", "native_command_manifest")),
    (m("i23_cleanup"), m("freeze_argv", "argv_manifest")),
    (m("seal_argv", "argv_manifest"),),
)
I24_RAW = roles(
    (m("review_raw"), m("review_launch_receipt"), m("reviewer_provenance")),
    (m("parser_argv", "argv_manifest"),),
)
I24_ACCEPTED = (m("review_acceptance"), m("review_verify_execution"), m("i24_cleanup"))
I25_PREPARED = roles(
    (m("native_intent"), m("native_preparation_command", "native_command_manifest")),
    (m("native_preparation_execution"), m("native_collection_receipt")),
    (m("preparation_stdout"), m("preparation_stderr")),
    (m("native_preparation_evidence"), m("preparation_cleanup")),
    (m("canonical_all_command", "native_command_manifest"),),
)
I25_INPUTS = roles(
    (m("native_all_execution"), m("native_execution_evidence"), m("outer_log")),
    (m("i25_cleanup"), m("source_before"), m("source_after")),
    (m("source_union"), m("whole_union"), m("native_verifier_argv", "argv_manifest")),
)
I25_ACCEPTED = roles(
    (m("native_acceptance"), m("native_verify_execution")),
    (m("native_verifier_cleanup"), m("final_compliance_argv", "argv_manifest")),
)

STAGE_SPECS = MappingProxyType(
    {
        "I1_AUTHORITY_BASELINE": StageSpec("baseline", BASELINE),
        "I1_AUTHORITY_SCOPE_AMENDED": StageSpec("scope_amended", BASELINE),
        "I1_AUTHORITY_CAPACITY_AMENDED": StageSpec("capacity_amended", BASELINE),
        "I1_POSTCOMMIT_CANDIDATE": StageSpec("provisional", POSTCOMMIT),
        "I1_REVIEW_MATERIAL_FROZEN": StageSpec("material_frozen", MATERIAL),
        "I1_EXACT_COMMIT_REVIEW_INPUT": StageSpec("input_registered", REVIEW_INPUT),
        "I1_EXACT_COMMIT_REVIEW_RAW_RECEIVED": StageSpec("raw_received", I1_RAW),
        "I1_EXACT_COMMIT_REVIEW_ACCEPTED": StageSpec("review_accepted", I1_REVIEWED),
        "I1_FINAL_GATES_ACCEPTED": StageSpec("gates_accepted", I1_GATES),
        "I1_ACCEPTED": StageSpec("accepted", I1_ACCEPTED),
        "SOURCE_STAGE_INPUT": StageSpec("input_registered", SOURCE_INPUT),
        "SOURCE_STAGE_ACCEPTED": StageSpec("accepted", SOURCE_ACCEPTED),
        "I23_FREEZE_READY": StageSpec("input_registered", I23),
        "PARENT_REVIEW_MATERIAL_FROZEN": StageSpec("material_frozen", MATERIAL),
        "I24_REVIEW_INPUT": StageSpec("input_registered", REVIEW_INPUT),
        "I24_REVIEW_RAW_RECEIVED": StageSpec("raw_received", I24_RAW),
        "I24_REVIEW_ACCEPTED": StageSpec("accepted", I24_ACCEPTED),
        "I25_NATIVE_PREPARED": StageSpec("prepared", I25_PREPARED),
        "I25_NATIVE_INPUTS": StageSpec("input_registered", I25_INPUTS),
        "I25_NATIVE_ACCEPTED": StageSpec("accepted", I25_ACCEPTED),
    }
)


def command(route: tuple[str, str, str], *inputs: InputSpec) -> CommandSpec:
    argv_stage, argv_role, output_stage = route
    return CommandSpec(InputSpec(None, argv_stage, argv_role), inputs, output_stage)


HISTORICAL_SCOPE = "I1_AUTHORITY_SCOPE_AMENDED"
SCOPE = "I1_AUTHORITY_CAPACITY_AMENDED"
POST = "I1_POSTCOMMIT_CANDIDATE"
I1_MATERIAL = "I1_REVIEW_MATERIAL_FROZEN"
I1_INPUT = "I1_EXACT_COMMIT_REVIEW_INPUT"
PARENT_READY = "I23_FREEZE_READY"
PARENT_MATERIAL = "PARENT_REVIEW_MATERIAL_FROZEN"
PARENT_INPUT = "I24_REVIEW_INPUT"
PARENT_RAW = "I24_REVIEW_RAW_RECEIVED"
PARENT_ACCEPTED = "I24_REVIEW_ACCEPTED"


COMMAND_SPECS = MappingProxyType(
    {
        ("freeze-review", POST): command(
            (POST, "i1_freeze_argv", I1_MATERIAL),
            InputSpec("--plan", SCOPE, "r7_plan"),
            InputSpec("--candidate-manifest", POST, "candidate_manifest"),
            InputSpec("--evidence-manifest", POST, "implementation_evidence"),
            InputSpec("--native-manifest", POST, "node_preservation_result"),
            InputSpec("--cleanup-manifest-index", POST, "operational_cleanup"),
        ),
        ("seal-review-binding", I1_MATERIAL): command(
            (I1_MATERIAL, "seal_argv", I1_INPUT),
            InputSpec("--input", I1_MATERIAL, "review_input"),
            InputSpec("--binding", I1_MATERIAL, "review_binding"),
            InputSpec("--freeze-argv-file", I1_MATERIAL, "seal_argv"),
        ),
        ("compare-stage", "SOURCE_STAGE_INPUT"): command(
            ("SOURCE_STAGE_INPUT", "compare_argv", "SOURCE_STAGE_ACCEPTED"),
            InputSpec("--baseline", "SOURCE_STAGE_INPUT", "baseline"),
            InputSpec("--candidate", "SOURCE_STAGE_INPUT", "candidate"),
            InputSpec(None, "SOURCE_STAGE_INPUT", "structural_contract"),
        ),
        ("verify-review-i1", "I1_EXACT_COMMIT_REVIEW_RAW_RECEIVED"): command(
            (
                "I1_EXACT_COMMIT_REVIEW_RAW_RECEIVED",
                "i1_parser_argv",
                "I1_EXACT_COMMIT_REVIEW_ACCEPTED",
            ),
            InputSpec("--binding", I1_MATERIAL, "review_binding"),
            InputSpec("--binding-seal", I1_INPUT, "review_binding_seal"),
            InputSpec("--report", "I1_EXACT_COMMIT_REVIEW_RAW_RECEIVED", "review_raw"),
        ),
        ("freeze-review", PARENT_READY): command(
            (PARENT_READY, "freeze_argv", PARENT_MATERIAL),
            InputSpec("--plan", PARENT_READY, "plan"),
            InputSpec("--candidate-manifest", PARENT_READY, "final_candidate"),
            InputSpec("--evidence-manifest", PARENT_READY, "evidence_manifest"),
            InputSpec("--native-manifest", PARENT_READY, "native_intent"),
            InputSpec("--cleanup-manifest-index", PARENT_READY, "i23_cleanup"),
        ),
        ("seal-review-binding", PARENT_MATERIAL): command(
            (PARENT_MATERIAL, "seal_argv", PARENT_INPUT),
            InputSpec("--input", PARENT_MATERIAL, "review_input"),
            InputSpec("--binding", PARENT_MATERIAL, "review_binding"),
            InputSpec("--freeze-argv-file", PARENT_MATERIAL, "seal_argv"),
        ),
        ("verify-review-i24", PARENT_RAW): command(
            (PARENT_RAW, "parser_argv", PARENT_ACCEPTED),
            InputSpec("--binding", PARENT_MATERIAL, "review_binding"),
            InputSpec("--binding-seal", PARENT_INPUT, "review_binding_seal"),
            InputSpec("--report", PARENT_RAW, "review_raw"),
        ),
        ("prepare-native-collect-only", PARENT_ACCEPTED): command(
            (PARENT_READY, "native_preparation_command", "I25_NATIVE_PREPARED"),
            InputSpec(None, PARENT_READY, "native_intent"),
        ),
        ("run-native-all", "I25_NATIVE_PREPARED"): command(
            ("I25_NATIVE_PREPARED", "canonical_all_command", "I25_NATIVE_INPUTS"),
            InputSpec(None, "I25_NATIVE_PREPARED", "native_preparation_evidence"),
        ),
        ("verify-native-evidence", "I25_NATIVE_INPUTS"): command(
            ("I25_NATIVE_INPUTS", "native_verifier_argv", "I25_NATIVE_ACCEPTED"),
            InputSpec("--binding", PARENT_MATERIAL, "review_binding"),
            InputSpec("--binding-seal", PARENT_INPUT, "review_binding_seal"),
            InputSpec("--candidate-manifest", PARENT_READY, "final_candidate"),
            InputSpec("--native-intent", PARENT_READY, "native_intent"),
            InputSpec("--native-preparation", "I25_NATIVE_PREPARED", "native_preparation_evidence"),
            InputSpec("--native-run-manifest", "I25_NATIVE_INPUTS", "native_execution_evidence"),
            InputSpec("--native-log", "I25_NATIVE_INPUTS", "outer_log"),
            InputSpec("--cleanup-manifest-index", "I25_NATIVE_INPUTS", "i25_cleanup"),
        ),
        ("verify-final-compliance", "I25_NATIVE_ACCEPTED"): command(
            ("I25_NATIVE_ACCEPTED", "final_compliance_argv", "EXTERNAL"),
            InputSpec("--plan", PARENT_READY, "plan"),
            InputSpec("--binding", PARENT_MATERIAL, "review_binding"),
            InputSpec("--binding-seal", PARENT_INPUT, "review_binding_seal"),
            InputSpec("--review", PARENT_RAW, "review_raw"),
            InputSpec("--review-acceptance", PARENT_ACCEPTED, "review_acceptance"),
            InputSpec("--candidate-manifest", PARENT_READY, "final_candidate"),
            InputSpec("--evidence-manifest", PARENT_READY, "evidence_manifest"),
            InputSpec("--native-source-manifest", PARENT_READY, "native_source_contract"),
            InputSpec("--native-preparation", "I25_NATIVE_PREPARED", "native_preparation_evidence"),
            InputSpec("--native-run-manifest", "I25_NATIVE_INPUTS", "native_execution_evidence"),
            InputSpec("--native-acceptance", "I25_NATIVE_ACCEPTED", "native_acceptance"),
            InputSpec("--cleanup-manifest-index", "I25_NATIVE_INPUTS", "i25_cleanup"),
        ),
    }
)
