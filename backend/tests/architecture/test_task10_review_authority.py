from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from scripts import task10_review_binding as binding_api
from scripts import task10_review_metadata as metadata_api
from scripts import task10_review_report as report_api
from scripts.task10_root_authority import resolve_role, validate_authority
from tests.architecture.task10_authority_fixtures import CONTROL
from tests.architecture.task10_review_fixtures import (
    RegisteredReviewFixture,
    provenance_mutations,
    registered_i1_review_fixture,
    registered_i24_review_fixture,
    registered_review_authority,
    request_mutations,
)


def _verify(fixture: RegisteredReviewFixture, output: str = "acceptance.json") -> Path:
    acceptance = fixture.authority.root / "out" / output
    acceptance.parent.mkdir(exist_ok=True)
    request = binding_api.ReviewCommand(
        (fixture.binding, fixture.binding_seal, fixture.report), (acceptance,)
    )
    binding_api.verify_review(fixture.authority.root, fixture.authority.seal, request)
    return acceptance


@pytest.mark.parametrize(
    "factory",
    [registered_i1_review_fixture, registered_i24_review_fixture],
    ids=["i1_review_chain", "i23_i24_chain"],
)
def test_i1_review_chain_binds_registered_roles_and_accepts(
    tmp_path: Path,
    factory: Callable[[Path], RegisteredReviewFixture],
) -> None:
    fixture = factory(tmp_path / "private")
    acceptance = _verify(fixture)
    value = json.loads(acceptance.read_text())
    assert value["status"] == "verified"
    assert value["candidate"] == fixture.provenance["candidate"]


def test_freeze_rejects_wrong_role_argv_and_stale_git(tmp_path: Path) -> None:
    authority = registered_review_authority(tmp_path / "private", "I1", "prerequisite")
    chain = validate_authority(authority.root, authority.seal)
    specs = (
        ("I1_AUTHORITY_CAPACITY_AMENDED", "r7_plan"),
        ("I1_POSTCOMMIT_CANDIDATE", "candidate_manifest"),
        ("I1_POSTCOMMIT_CANDIDATE", "implementation_evidence"),
        ("I1_POSTCOMMIT_CANDIDATE", "node_preservation_result"),
        ("I1_POSTCOMMIT_CANDIDATE", "operational_cleanup"),
    )
    inputs = tuple(authority.root / resolve_role(chain, *spec)["path"] for spec in specs)
    sibling = authority.root / CONTROL / "fixture-inputs/same-plan.json"
    sibling.parent.mkdir(parents=True, exist_ok=True)
    sibling.write_bytes(inputs[0].read_bytes())
    outputs = (authority.root / "out/review.json", authority.root / "out/binding.json")
    request = binding_api.ReviewCommand((sibling, *inputs[1:]), outputs)
    with pytest.raises(binding_api.BindingError, match="authority_argument_path_mismatch"):
        binding_api.freeze_review(authority.root, authority.seal, request)
    (authority.root / "tracked.txt").write_text("dirty\n")
    with pytest.raises(ValueError, match="live_git_state_drift"):
        binding_api.freeze_review(
            authority.root, authority.seal, binding_api.ReviewCommand(inputs, outputs)
        )


def test_registered_raw_fixture_rejects_malformed_grammar(tmp_path: Path) -> None:
    fixture = registered_i1_review_fixture(tmp_path / "private")
    fixture.report.write_text("FINDINGS: none\n")
    headers = fixture.request_text.splitlines()
    facts = report_api.ReviewHashFacts(
        headers[0].removeprefix("PLAN_SHA256: "),
        headers[1].removeprefix("CANDIDATE_MANIFEST_SHA256: "),
        headers[2].removeprefix("BINDING_SHA256: "),
    )
    with pytest.raises(report_api.ReviewReportError, match="review_report_grammar_mismatch"):
        report_api.parse_review_report(fixture.report, facts, report_api.I1_GRAMMAR)


@pytest.mark.parametrize("version", [True, 1.0], ids=["bool_version", "float_version"])
def test_reviewer_request_rejects_wrong_schema_version(
    tmp_path: Path, version: bool | float
) -> None:
    fixture = registered_i1_review_fixture(tmp_path / "private")
    request = dict(fixture.request) | {"schema_version": cast(metadata_api.JsonValue, version)}
    with pytest.raises(metadata_api.ReviewMetadataError, match="review_document_schema"):
        metadata_api.validate_reviewer_exchange(
            request,
            fixture.provenance,
            fixture.request,
            fixture.provenance,
            fixture.request_text,
            fixture.request_text,
            str(fixture.provenance["raw_sha256"]),
        )


@pytest.mark.parametrize("kind", ["request", "provenance"])
def test_registered_metadata_mutations_reject_wrong_message_and_rfc3339(
    tmp_path: Path, kind: str
) -> None:
    fixture = registered_i24_review_fixture(tmp_path / "private")
    values = request_mutations(fixture) if kind == "request" else provenance_mutations(fixture)
    for value in values:
        request = value if kind == "request" else fixture.request
        provenance = value if kind == "provenance" else fixture.provenance
        with pytest.raises(metadata_api.ReviewMetadataError):
            metadata_api.validate_reviewer_exchange(
                request,
                provenance,
                fixture.request,
                fixture.provenance,
                fixture.request_text,
                fixture.request_text,
                str(fixture.provenance["raw_sha256"]),
            )
