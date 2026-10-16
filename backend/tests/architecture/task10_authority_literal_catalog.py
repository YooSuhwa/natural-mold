"""Independent literal oracle for Task 10 authority tests."""

from typing import Final, NamedTuple


class RoleLiteral(NamedTuple):
    name: str
    content_type: str = "metadata"
    manifest_kind: str | None = None


type InputLiteral = tuple[str | None, str, str]
type CommandLiteral = tuple[str, str, InputLiteral, tuple[InputLiteral, ...], str]
type EnvelopeLiteral = tuple[str, tuple[tuple[str, str | None, str], ...]]


def rs(*names: str) -> tuple[RoleLiteral, ...]:
    return tuple(RoleLiteral(name) for name in names)


def ms(kind: str, *names: str) -> tuple[RoleLiteral, ...]:
    return tuple(RoleLiteral(name, "metadata", kind) for name in names)


def src(*names: str) -> tuple[RoleLiteral, ...]:
    return tuple(RoleLiteral(name, "source") for name in names)


BASELINE = rs("exact_path_release", "legacy_release", "split_amendment", "parser_amendment")
BASELINE += rs("native_amendment", "stage_amendment", "r2_preflight", "metis")
BASELINE += rs("metis_disposition", "r7_plan", "r7_draft", "starting_git_snapshot")
BASELINE += ms("test_node_inventory", "preedit_test_node_inventory")
BASELINE += ms("source_snapshot_manifest", "preedit_source_snapshot_manifest")
POSTCOMMIT = rs("candidate_manifest") + ms("source_snapshot_manifest", "source_manifest")
POSTCOMMIT += ms("artifact_index", "implementation_evidence") + rs("operational_cleanup")
POSTCOMMIT += ms("path_manifest", "content_edit_path_manifest", "commit_path_manifest")
POSTCOMMIT += src("ast_helper_snapshot") + rs("postcommit_git_snapshot")
POSTCOMMIT += ms("test_node_inventory", "postedit_test_node_inventory")
POSTCOMMIT += ms("moved_node_mapping", "moved_node_mapping")
POSTCOMMIT += ms("verification_result", "node_preservation_result")
POSTCOMMIT += ms("argv_manifest", "i1_freeze_argv", "i1_seal_argv")
MATERIAL = rs("review_input", "review_binding", "freeze_execution", "freeze_cleanup")
MATERIAL += ms("argv_manifest", "seal_argv")
REVIEW_INPUT = rs("review_binding_seal", "seal_execution", "seal_cleanup")
REVIEW_INPUT += rs("review_request_text", "reviewer_request")
I1_RAW = rs("review_raw", "review_launch_receipt", "reviewer_provenance")
I1_RAW += ms("argv_manifest", "i1_parser_argv")
I1_REVIEWED = rs("review_acceptance", "review_verify_execution", "review_cleanup")
I1_REVIEWED += tuple(
    RoleLiteral(f"i1_f{number}_{suffix}", "metadata", kind)
    for number in range(1, 5)
    for suffix, kind in (("gate_context", "gate_context"), ("argv", "argv_manifest"))
)
I1_GATES = tuple(
    RoleLiteral(f"i1_f{number}_{suffix}", "metadata", kind)
    for number in range(1, 5)
    for suffix, kind in (("execution", None), ("result", "verification_result"), ("cleanup", None))
)
I1_ACCEPTED = rs("final_acceptance", "accepted_candidate_manifest")
I1_ACCEPTED += ms("source_snapshot_manifest", "accepted_source_manifest")
I1_ACCEPTED += ms("path_manifest", "accepted_commit_path_manifest")
I1_ACCEPTED += ms("test_node_inventory", "accepted_test_node_inventory")
SOURCE_INPUT = rs("baseline", "candidate") + ms("structural_contract", "structural_contract")
SOURCE_INPUT += ms("argv_manifest", "compare_argv") + rs("pre_git_snapshot")
SOURCE_ACCEPTED = rs("source_acceptance", "source_compare_execution", "source_cleanup")
SOURCE_ACCEPTED += rs("post_git_snapshot") + ms("test_node_inventory", "post_test_node_inventory")
I23 = rs("plan", "final_candidate")
I23 += ms("artifact_index", "accepted_seal_inventory", "evidence_manifest")
I23 += src("runner_entrypoint", "runner_dispatcher", "runner_runtime")
I23 += src("receipt_plugin", "pytest_config", "integration_conftest")
I23 += rs("collection_hook_proof", "static_registry", "fixture_catalog")
I23 += rs("native_source_contract", "native_intent")
I23 += ms("native_command_manifest", "native_preparation_command") + rs("i23_cleanup")
I23 += ms("argv_manifest", "freeze_argv", "seal_argv")
I24_RAW = I1_RAW[:-1] + ms("argv_manifest", "parser_argv")
I24_ACCEPTED = rs("review_acceptance", "review_verify_execution", "i24_cleanup")
I25_PREPARED = rs("native_intent") + ms("native_command_manifest", "native_preparation_command")
I25_PREPARED += rs("native_preparation_execution", "native_collection_receipt")
I25_PREPARED += rs("preparation_stdout", "preparation_stderr")
I25_PREPARED += rs("native_preparation_evidence", "preparation_cleanup")
I25_PREPARED += ms("native_command_manifest", "canonical_all_command")
I25_INPUTS = rs("native_all_execution", "native_execution_evidence", "outer_log")
I25_INPUTS += rs("i25_cleanup", "source_before", "source_after")
I25_INPUTS += rs("source_union", "whole_union")
I25_INPUTS += ms("argv_manifest", "native_verifier_argv")
I25_ACCEPTED = rs("native_acceptance", "native_verify_execution", "native_verifier_cleanup")
I25_ACCEPTED += ms("argv_manifest", "final_compliance_argv")

B, S, C = "I1_AUTHORITY_BASELINE", "I1_AUTHORITY_SCOPE_AMENDED", "I1_AUTHORITY_CAPACITY_AMENDED"
P, M = "I1_POSTCOMMIT_CANDIDATE", "I1_REVIEW_MATERIAL_FROZEN"
RI, RR = "I1_EXACT_COMMIT_REVIEW_INPUT", "I1_EXACT_COMMIT_REVIEW_RAW_RECEIVED"
R = "I23_FREEZE_READY"
PM = "PARENT_REVIEW_MATERIAL_FROZEN"
I24I = "I24_REVIEW_INPUT"
I24R = "I24_REVIEW_RAW_RECEIVED"
SI, SA = "SOURCE_STAGE_INPUT", "SOURCE_STAGE_ACCEPTED"
I24A = "I24_REVIEW_ACCEPTED"
NP = "I25_NATIVE_PREPARED"
NI = "I25_NATIVE_INPUTS"
NA = "I25_NATIVE_ACCEPTED"
STAGES: Final = (
    (B, "baseline", BASELINE),
    (S, "scope_amended", BASELINE),
    (C, "capacity_amended", BASELINE),
    (P, "provisional", POSTCOMMIT),
    (M, "material_frozen", MATERIAL),
    (RI, "input_registered", REVIEW_INPUT),
    (RR, "raw_received", I1_RAW),
    ("I1_EXACT_COMMIT_REVIEW_ACCEPTED", "review_accepted", I1_REVIEWED),
    ("I1_FINAL_GATES_ACCEPTED", "gates_accepted", I1_GATES),
    ("I1_ACCEPTED", "accepted", I1_ACCEPTED),
    (SI, "input_registered", SOURCE_INPUT),
    (SA, "accepted", SOURCE_ACCEPTED),
    (R, "input_registered", I23),
    (PM, "material_frozen", MATERIAL),
    (I24I, "input_registered", REVIEW_INPUT),
    (I24R, "raw_received", I24_RAW),
    (I24A, "accepted", I24_ACCEPTED),
    (NP, "prepared", I25_PREPARED),
    (NI, "input_registered", I25_INPUTS),
    (NA, "accepted", I25_ACCEPTED),
)


def command(
    identity: tuple[str, str], route: tuple[str, str, str], rows: tuple[InputLiteral, ...] = ()
) -> CommandLiteral:
    return *identity, (None, route[0], route[1]), rows, route[2]


def args(stage: str, *rows: tuple[str | None, str]) -> tuple[InputLiteral, ...]:
    return tuple((argument, stage, role) for argument, role in rows)


def freeze(context: str, argv: str, output: str, plan: str, *roles: str) -> CommandLiteral:
    options = "--candidate-manifest", "--evidence-manifest", "--native-manifest"
    rows = args(plan, ("--plan", "r7_plan" if context == P else "plan"))
    rows += args(context, *zip(options, roles, strict=True))
    cleanup = "operational_cleanup" if context == P else "i23_cleanup"
    rows += args(context, ("--cleanup-manifest-index", cleanup))
    return command((context, "freeze-review"), (context, argv, output), rows)


def seal(context: str, output: str) -> CommandLiteral:
    rows = args(context, ("--input", "review_input"), ("--binding", "review_binding"))
    rows += args(context, ("--freeze-argv-file", "seal_argv"))
    return command((context, "seal-review-binding"), (context, "seal_argv", output), rows)


def review(
    raw: str, parser: str, output: str, material: str, sealed: str, name: str
) -> CommandLiteral:
    rows = args(material, ("--binding", "review_binding"))
    rows += args(sealed, ("--binding-seal", "review_binding_seal"))
    rows += args(raw, ("--report", "review_raw"))
    return command((raw, name), (raw, parser, output), rows)


I1_FREEZE_ROLES = "candidate_manifest", "implementation_evidence", "node_preservation_result"
EVIDENCE = "--evidence-manifest", "evidence_manifest"
NATIVE_SOURCE = "--native-source-manifest", "native_source_contract"
REVIEW_ACCEPTANCE = "--review-acceptance", "review_acceptance"
COMPARE = args(SI, ("--baseline", "baseline"), ("--candidate", "candidate"))
COMPARE += args(SI, (None, "structural_contract"))
NATIVE = args(PM, ("--binding", "review_binding"))
NATIVE += args(I24I, ("--binding-seal", "review_binding_seal"))
NATIVE += args(R, ("--candidate-manifest", "final_candidate"), ("--native-intent", "native_intent"))
NATIVE += args(NP, ("--native-preparation", "native_preparation_evidence"))
NATIVE += args(NI, ("--native-run-manifest", "native_execution_evidence"))
NATIVE += args(NI, ("--native-log", "outer_log"), ("--cleanup-manifest-index", "i25_cleanup"))
FINAL = args(R, ("--plan", "plan")) + args(PM, ("--binding", "review_binding"))
FINAL += args(I24I, ("--binding-seal", "review_binding_seal"))
FINAL += args(I24R, ("--review", "review_raw")) + args(I24A, REVIEW_ACCEPTANCE)
FINAL += args(R, ("--candidate-manifest", "final_candidate"), EVIDENCE, NATIVE_SOURCE)
FINAL += args(NP, ("--native-preparation", "native_preparation_evidence"))
FINAL += args(NI, ("--native-run-manifest", "native_execution_evidence"))
FINAL += args(NA, ("--native-acceptance", "native_acceptance"))
FINAL += args(NI, ("--cleanup-manifest-index", "i25_cleanup"))
COMMANDS: Final = (
    freeze(P, "i1_freeze_argv", M, C, *I1_FREEZE_ROLES),
    seal(M, RI),
    command((SI, "compare-stage"), (SI, "compare_argv", SA), COMPARE),
    review(RR, "i1_parser_argv", "I1_EXACT_COMMIT_REVIEW_ACCEPTED", M, RI, "verify-review-i1"),
    freeze(R, "freeze_argv", PM, R, "final_candidate", "evidence_manifest", "native_intent"),
    seal(PM, I24I),
    review(I24R, "parser_argv", "I24_REVIEW_ACCEPTED", PM, I24I, "verify-review-i24"),
    command(
        (I24A, "prepare-native-collect-only"),
        (R, "native_preparation_command", NP),
        args(R, (None, "native_intent")),
    ),
    command(
        (NP, "run-native-all"),
        (NP, "canonical_all_command", NI),
        args(NP, (None, "native_preparation_evidence")),
    ),
    command((NI, "verify-native-evidence"), (NI, "native_verifier_argv", NA), NATIVE),
    command(
        (NA, "verify-final-compliance"),
        (NA, "final_compliance_argv", "EXTERNAL"),
        FINAL,
    ),
)
PUBLIC_COMMANDS = ("capture", "compare", "compare-statements", "verify")
PUBLIC_COMMANDS += ("freeze-review", "seal-review-binding", "verify-review")
PUBLIC_COMMANDS += ("verify-native-evidence", "verify-final-compliance")


def envelope(name: str, *outputs: tuple[str, str | None, str]) -> EnvelopeLiteral:
    return name, outputs


def gate(number: int, first: str, second: str) -> EnvelopeLiteral:
    return envelope(
        f"i1-f{number}",
        (f"i1_f{number}_result", None, "verification_result"),
        (first, None, "opaque_bytes"),
        (second, None, "opaque_bytes"),
    )


ENVELOPES: Final = (
    envelope(
        "freeze-review",
        ("review_input", "--input", "review_input"),
        ("review_binding", "--binding", "review_binding"),
    ),
    envelope("seal-review-binding", ("review_binding_seal", "--output", "review_binding_seal")),
    envelope("verify-review-i1", ("review_acceptance", "--output", "review_acceptance")),
    envelope("verify-review-i24", ("review_acceptance", "--output", "review_acceptance")),
    envelope("compare-stage", ("source_acceptance", "--output", "source_acceptance")),
    envelope(
        "prepare-native-collect-only",
        ("native_collection_receipt", None, "postgres_collection_receipt"),
        ("native_preparation_evidence", None, "native_preparation"),
    ),
    envelope(
        "run-native-all",
        ("native_execution_evidence", "--manifest", "canonical_native_run"),
        ("outer_log", None, "opaque_bytes"),
    ),
    envelope("verify-native-evidence", ("native_acceptance", "--output", "native_acceptance")),
    envelope("verify-final-compliance", ("final_compliance", "--output", "final_compliance")),
    gate(1, "f1_plan_compliance", "f1_plan_compliance_xml"),
    gate(2, "f2_source_cap_census", "f2_code_quality_log"),
    gate(3, "f3_real_cli_xml", "f3_real_cli_log"),
    gate(4, "f4_live_authority", "f4_live_status"),
)
RUNTIME_R7_SHA256: Final = "1405651ac3f5bac2cbb7b4ae71a34484d6e322f49535fe899e9e6d2396bb9eb6"
APPROVED_R13_SHA256: Final = "e12c9d3ff13685b8a9d85bb73f123bafa4a066b017869d225273f31523ea348f"
