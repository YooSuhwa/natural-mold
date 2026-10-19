"""Authority and artifact facade for registered structural comparisons."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import task10_root_artifact_io as artifact_io
from scripts import task10_root_authority as root_authority
from scripts.task10_source_contract_rules import (
    GUARDED_FIELDS,
    JsonObject,
    JsonValue,
    load_contract,
)
from scripts.task10_structural_contract import (
    StageAuthorityError,
    parse_structural_contract,
    verify_structural_delta,
)

GUARDED = GUARDED_FIELDS


def _record(value: JsonValue, code: str) -> JsonObject:
    if not isinstance(value, dict):
        raise StageAuthorityError(code)
    return value


def _contract(
    chain: root_authority.AuthorityChain,
    artifact: artifact_io.AuthorityArtifact,
) -> tuple[JsonObject, str, str]:
    raw = artifact_io.read_regular(chain.root, artifact["path"])
    value = json.loads(raw)
    if not isinstance(value, dict) or raw != artifact_io.canonical_json_bytes(value):
        raise StageAuthorityError("structural_contract_schema")
    return parse_structural_contract(value)


def compare_stage(
    root: Path,
    authority_seal: Path,
    baseline_path: Path,
    candidate_path: Path,
) -> JsonObject:
    """Verify one registered structural delta through its active authority chain."""
    chain = root_authority.validate_authority(
        root,
        seal=authority_seal,
        require_stage="SOURCE_STAGE_INPUT",
    )
    root_authority.validate_live_git(chain)
    root_authority.resolve_occurrence(chain, "I1_AUTHORITY_CAPACITY_AMENDED")
    active = chain.records[-1]
    raw_candidate, raw_parent = active.get("candidate"), active.get("parent_seal")
    if not isinstance(raw_candidate, dict) or not isinstance(raw_parent, dict):
        raise StageAuthorityError("stage_occurrence_invalid")
    if (
        root_authority.resolve_occurrence(chain, "SOURCE_STAGE_INPUT", raw_candidate, raw_parent)
        != active
    ):
        raise StageAuthorityError("stage_occurrence_invalid")
    authority = root_authority.resolve_role(chain, "SOURCE_STAGE_INPUT", "structural_contract")
    contract, lane, contract_candidate = _contract(chain, authority)
    git_state = active.get("git_state")
    if not isinstance(git_state, dict):
        raise StageAuthorityError("stage_git_state_invalid")
    if contract_candidate != git_state.get("head"):
        raise StageAuthorityError("structural_candidate_head_mismatch")
    baseline_role = root_authority.adapt_registered_legacy(
        chain,
        _record(contract.get("baseline"), "stage_baseline_invalid"),
        "SOURCE_STAGE_INPUT",
        "baseline",
    )
    candidate_role = root_authority.adapt_registered_legacy(
        chain,
        _record(contract.get("candidate_contract"), "stage_candidate_invalid"),
        "SOURCE_STAGE_INPUT",
        "candidate",
    )
    if baseline_path.resolve() != (chain.root / baseline_role["path"]).resolve():
        raise StageAuthorityError("stage_baseline_path_mismatch")
    if candidate_path.resolve() != (chain.root / candidate_role["path"]).resolve():
        raise StageAuthorityError("stage_candidate_path_mismatch")
    baseline, candidate = load_contract(baseline_path), load_contract(candidate_path)
    if baseline.get("candidate") != candidate.get("candidate"):
        raise StageAuthorityError("stage_candidate_mismatch")
    if candidate.get("candidate") != contract_candidate:
        raise StageAuthorityError("structural_candidate_mismatch")
    if candidate.get("workspace_root") not in (None, str(chain.root.resolve())):
        raise StageAuthorityError("candidate_workspace_root_mismatch")
    verify_structural_delta(
        _record(baseline.get("sources"), "baseline_sources_required"),
        _record(candidate.get("sources"), "candidate_sources_required"),
        contract,
    )
    return {
        "status": "verified",
        "candidate": contract_candidate,
        "task": lane,
        "authority": {
            "path": str((chain.root / authority["path"]).resolve()),
            "sha256": authority["sha256"],
            "bytes": authority["bytes"],
        },
    }
