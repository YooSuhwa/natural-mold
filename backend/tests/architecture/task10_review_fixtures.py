"""Independent registered review fixtures for Task 10 consumers."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, overload

from tests.architecture.task10_authority_fixtures import (
    CONTROL,
    JsonValue,
    PrivateAuthority,
    fixture_artifact,
    fixture_digest,
    fixture_head,
    legacy_artifact,
    write_argv_roles,
    write_candidate_document,
    write_fixture_artifact,
)
from tests.architecture.task10_authority_scenarios import (
    advance_authority,
    append_source_cycle,
    registered_capacity_chain,
    registered_i1_accepted_chain,
)

I1_CHECKS = ("GENESIS_CHAIN", "PREEDIT_SCOPE_RELEASE", "LOCATOR_ROLLBACK")
I1_CHECKS += ("ATOMIC_PUBLICATION", "ROLE_SCHEMA", "ACTUAL_ROOT_GIT")
I1_CHECKS += ("STRUCTURAL_AUTHORITY", "REVIEW_GRAMMAR", "NATIVE_FINAL_AUTHORITY")
I1_CHECKS += ("TEST_NODE_PRESERVATION", "FIXTURE_INDEPENDENCE", "CAP_PATH_SCOPE")
I24_CHECKS = ("R1_R8", "FIVE_ENTRIES", "ARCHIVE_PRECEDENCE", "B08_COMPATIBILITY")
I24_CHECKS += ("CHILD_LOCK_RELEASE", "GOOGLE_LEASE_ONLY", "GOOGLE_EXACT_CLIENT")
I24_CHECKS += ("GOOGLE_DEFAULT_PRESERVED", "GOOGLE_CLEANUP_ONCE", "GOOGLE_NO_AIOHTTP")
I24_CHECKS += ("STOCK_TASK_TOOL", "ZERO_TOOL_INTERCEPTOR", "FROZEN_RESPONSE_VARIANT")
I24_CHECKS += ("POST_MARKER_UNREACHABLE", "TOOL_RUNTIME_INVOCATION_ID")
I24_CHECKS += ("NO_LEAF_LOCK_ACROSS_CHILD_AWAIT", "OWNERSHIP_ACYCLIC")
I24_CHECKS += ("FACADE_PUBLIC_BEHAVIOR", "LIFECYCLE_WIRING", "PROVIDER_SDK_RETRIES")
I24_CHECKS += ("CURSOR_INVALIDATION_REUSE", "ALL_CONSUMER_MIGRATION")
type Label = Literal["I1", "I24"]
type Stop = Literal["raw", "prerequisite", "material"]
type ActiveStop = Literal["prerequisite", "material"]


class ReviewFixtureError(ValueError): ...


@dataclass(frozen=True, slots=True)
class RegisteredReviewFixture:
    authority: PrivateAuthority
    candidate: Mapping[str, JsonValue]
    request: Mapping[str, JsonValue]
    provenance: Mapping[str, JsonValue]
    request_text: str
    binding: Path
    binding_seal: Path
    report: Path


type Fixture = RegisteredReviewFixture
type Authority = PrivateAuthority


@dataclass(frozen=True, slots=True)
class ReviewRoute:
    checks: tuple[str, ...]
    roles: tuple[str, ...]
    seal_origin: str
    material_stage: str
    input_stage: str
    parser_role: str
    raw_stage: str


I1_ROLES = ("r7_plan", "candidate_manifest", "implementation_evidence")
I1_ROLES += ("node_preservation_result", "operational_cleanup")
I24_ROLES: Final = ("plan", "final_candidate", "evidence_manifest", "native_intent", "i23_cleanup")
I1_ROUTE: Final = ReviewRoute(
    *(I1_CHECKS, I1_ROLES, "i1_seal_argv"),
    *("I1_REVIEW_MATERIAL_FROZEN", "I1_EXACT_COMMIT_REVIEW_INPUT"),
    *("i1_parser_argv", "I1_EXACT_COMMIT_REVIEW_RAW_RECEIVED"),
)
I24_ROUTE: Final = ReviewRoute(
    *(I24_CHECKS, I24_ROLES, "seal_argv"),
    *("PARENT_REVIEW_MATERIAL_FROZEN", "I24_REVIEW_INPUT"),
    *("parser_argv", "I24_REVIEW_RAW_RECEIVED"),
)
ROUTES: Final = {"I1": I1_ROUTE, "I24": I24_ROUTE}


@dataclass(frozen=True, slots=True)
class FixtureWriter:
    root: Path
    prefix: str

    def json(self, name: str, value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        return write_fixture_artifact(self.root, f"{self.prefix}/{name}.json", value)

    def text(self, name: str, value: str) -> tuple[dict[str, JsonValue], Path]:
        path = self.root / f"{self.prefix}/{name}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        return fixture_artifact(self.root, path), path

    def legacy(self, artifact: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        return legacy_artifact(self.root, artifact)


def _report(label: Label, plan: str, candidate: str, binding: str) -> str:
    checks = ROUTES[label].checks
    headers = (
        (
            f"PLAN_SHA256: {plan}",
            f"CANDIDATE_MANIFEST_SHA256: {candidate}",
            f"BINDING_SHA256: {binding}",
        )
        if label == "I1"
        else ("VERDICT: APPROVE", f"BINDING_SHA256: {binding}", "OPEN_CRITICAL_HIGH: 0")
    )
    body = "\n".join((*headers, *(f"CHECK: {item}: PASS" for item in checks), "FINDINGS: none"))
    return body + "\n"


def _start(root: Path, label: Label) -> PrivateAuthority:
    if label == "I1":
        capacity = registered_capacity_chain(root)
        candidate = write_candidate_document(root, "i1", capacity)
        argv = write_argv_roles(root, "i1", "i1_freeze_argv", "i1_seal_argv")
        return advance_authority(
            capacity,
            "I1_POSTCOMMIT_CANDIDATE",
            candidate=candidate,
            role_artifacts={"candidate_manifest": candidate} | argv,
        )
    accepted = registered_i1_accepted_chain(root)
    _, accepted = append_source_cycle(accepted, label="review")
    candidate = write_candidate_document(root, "i24", accepted)
    argv = write_argv_roles(root, "i24", "freeze_argv", "seal_argv")
    intent = write_fixture_artifact(
        root,
        f"{CONTROL}/fixture-inputs/i24/native-intent.json",
        {
            "schema_version": 1,
            "kind": "task10-native-intent",
            "candidate": fixture_head(accepted),
            "status": "canonical_all_registry_static_uncollected",
        },
    )
    return advance_authority(
        accepted,
        "I23_FREEZE_READY",
        role_artifacts={
            "plan": fixture_artifact(root, accepted.role_paths["r7_plan"]),
            "final_candidate": candidate,
            "native_intent": intent,
            **argv,
        },
    )


@overload
def _registered(root: Path, label: Label, stop: Literal["raw"] = "raw") -> Fixture: ...


@overload
def _registered(root: Path, label: Label, stop: ActiveStop) -> Authority: ...


def _registered(root: Path, label: Label, stop: Stop = "raw") -> Fixture | Authority:
    previous, route = _start(root, label), ROUTES[label]
    if stop == "prerequisite":
        return previous
    writer = FixtureWriter(root, f"{CONTROL}/fixture-inputs/{label.lower()}")
    head = fixture_head(previous)
    role_names = ("plan", "candidate", "evidence", "native_source", "cleanup")
    roles = {
        key: writer.legacy(fixture_artifact(root, previous.role_paths[name]))
        for key, name in zip(role_names, route.roles, strict=True)
    }
    review = {"kind": "task10-review-input", "candidate": head}
    review |= {"roles": roles, "required_checks": list(route.checks)}
    review_artifact = writer.json("review-input", review)
    binding = {"schema_version": 1, "kind": "task10-review-binding", "candidate": head}
    binding |= {"roles": roles, "review_input": writer.legacy(review_artifact)}
    binding_artifact = writer.json("binding", binding)
    seal_argv = fixture_artifact(root, previous.role_paths[route.seal_origin])
    material_roles = {"review_input": review_artifact, "review_binding": binding_artifact}
    material_roles["seal_argv"] = seal_argv
    material = advance_authority(previous, route.material_stage, role_artifacts=material_roles)
    if stop == "material":
        return material
    plan_digest = fixture_digest(fixture_artifact(root, previous.role_paths[route.roles[0]]))
    candidate_digest = fixture_digest(fixture_artifact(root, previous.role_paths[route.roles[1]]))
    binding_digest = fixture_digest(binding_artifact)
    text = _report(label, plan_digest, candidate_digest, binding_digest)
    text_artifact, _ = writer.text("request", text)
    seal_document = {"schema_version": 1, "kind": "task10-review-binding-seal"}
    seal_document |= {"candidate": head, "binding": writer.legacy(binding_artifact)}
    seal_document |= {"review_input": writer.legacy(review_artifact)}
    seal_document |= {"freeze_argv": writer.legacy(seal_argv)}
    seal_artifact = writer.json("binding-seal", seal_document)
    request = {"schema_version": 1, "kind": "task10-reviewer-request"}
    request |= {"tool": "agents.spawn_agent", "task_name": f"task10-{label.lower()}-review"}
    request |= {
        "agent_type": "lazycodex-code-reviewer",
        "role": "independent_implementation_reviewer",
    }
    request |= {"fork_turns": "none", "model_id": "gpt-5.6-terra", "model_override": None}
    request |= {"message_sha256": hashlib.sha256(text.encode()).hexdigest()}
    request |= {"request_text": writer.legacy(text_artifact)}
    request |= {"plan": writer.legacy(fixture_artifact(root, previous.role_paths[route.roles[0]]))}
    request |= {"review_input": writer.legacy(review_artifact)}
    request |= {
        "binding": writer.legacy(binding_artifact),
        "binding_seal": writer.legacy(seal_artifact),
    }
    request |= {"binding_sha256": binding_digest, "candidate": head}
    request |= {"launch_owner": "root_coordinator", "status": "registered_before_launch"}
    request_artifact = writer.json("request", request)
    input_roles = {"review_binding_seal": seal_artifact, "review_request_text": text_artifact}
    input_roles["reviewer_request"] = request_artifact
    input_authority = advance_authority(material, route.input_stage, role_artifacts=input_roles)
    report_artifact, report_path = writer.text("report", text)
    launch = writer.json(
        "launch",
        {"agent_id": f"agent-{label.lower()}", "session_id": f"session-{label.lower()}"},
    )
    provenance = {"schema_version": 1, "kind": "task10-reviewer-provenance"}
    provenance |= {"request": writer.legacy(request_artifact), "tool": "agents.spawn_agent"}
    provenance |= {"task_name": request["task_name"], "agent_id": f"agent-{label.lower()}"}
    provenance |= {"session_id": f"session-{label.lower()}", "model_id": "gpt-5.6-terra"}
    provenance |= {"launch_receipt": writer.legacy(launch), "binding_sha256": binding_digest}
    provenance |= {"candidate": head, "raw_report": writer.legacy(report_artifact)}
    provenance |= {"raw_sha256": report_artifact["sha256"]}
    provenance |= {"started_at_utc": "2026-10-15T00:00:00Z"}
    provenance |= {"completed_at_utc": "2026-10-15T00:00:01Z", "status": "completed"}
    provenance_artifact = writer.json("provenance", provenance)
    parser_argv = writer.json(
        "parser-argv",
        {"manifest_kind": "argv_manifest", "cwd": "backend", "argv": ["verify-review"]},
    )
    raw_roles = {"review_raw": report_artifact, "review_launch_receipt": launch}
    raw_roles |= {"reviewer_provenance": provenance_artifact, route.parser_role: parser_argv}
    raw = advance_authority(input_authority, route.raw_stage, role_artifacts=raw_roles)
    candidate = json.loads(raw.record.read_text()).get("candidate")
    if not isinstance(candidate, dict):
        raise ReviewFixtureError("fixture_candidate_missing")
    binding_path = root / f"{writer.prefix}/binding.json"
    seal_path = root / f"{writer.prefix}/binding-seal.json"
    return RegisteredReviewFixture(
        raw,
        *(candidate, request, provenance, text),
        binding_path,
        seal_path,
        report_path,
    )


def registered_i1_review_fixture(root: Path) -> RegisteredReviewFixture:
    return _registered(root, "I1")


def registered_i24_review_fixture(root: Path) -> RegisteredReviewFixture:
    return _registered(root, "I24")


def registered_review_authority(root: Path, label: Label, stop: ActiveStop) -> Authority:
    return _registered(root, label, stop)


def _mutations(
    value: Mapping[str, JsonValue], *rows: tuple[str, JsonValue]
) -> tuple[Mapping[str, JsonValue], ...]:
    return tuple(dict(value) | {key: replacement} for key, replacement in rows)


def request_mutations(fixture: RegisteredReviewFixture) -> tuple[Mapping[str, JsonValue], ...]:
    return _mutations(
        fixture.request, ("schema_version", True), ("candidate", None), ("message_sha256", "")
    )


def provenance_mutations(fixture: RegisteredReviewFixture) -> tuple[Mapping[str, JsonValue], ...]:
    return _mutations(
        fixture.provenance,
        ("schema_version", 1.0),
        ("candidate", None),
        ("completed_at_utc", "2026-10-14T23:59:59Z"),
    )
