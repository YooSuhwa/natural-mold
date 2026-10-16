"""Immutable inventory and structural comparison rules for Task 10."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

type JsonValue = str | int | bool | None | Sequence["JsonValue"] | Mapping[str, "JsonValue"]
type JsonObject = dict[str, JsonValue]

SOURCE_PATHS = (
    "backend/app/authz/agent_execution_scope.py",
    "backend/app/authz/conversation_run_scope.py",
    "backend/app/agent_runtime/runtime_config.py",
    "backend/app/agent_runtime/runtime_component_builder.py",
    "backend/app/agent_runtime/deep_agent_factory.py",
    "backend/app/agent_runtime/model_factory.py",
    "backend/app/agent_runtime/offload_storage.py",
    "backend/app/agent_runtime/langgraph_agent_stream_runner.py",
    "backend/app/agent_runtime/agent_stream_runner.py",
    "backend/app/agent_runtime/trigger_executor.py",
    "backend/app/agent_runtime/subagents.py",
    "backend/app/agent_runtime/checkpointer.py",
    "backend/app/services/agent_service.py",
    "backend/app/services/builder_service.py",
    "backend/app/services/skill_builder_hidden_agent.py",
    "backend/app/services/agent_execution_owner.py",
    "backend/app/services/chat/conversations.py",
    "backend/app/services/conversation_side_chat_service.py",
    "backend/app/services/agent_execution_runtime.py",
    "backend/app/services/conversation_run_service.py",
    "backend/app/services/conversation_runtime_policy.py",
    "backend/app/services/conversation_run_worker.py",
    "backend/app/services/conversation_stream_service.py",
    "backend/app/services/conversation_run_queue_service.py",
    "backend/app/services/conversation_run_queue_worker.py",
    "backend/app/agent_api/runtime_service.py",
    "backend/app/services/trigger_service.py",
    "backend/app/routers/e2e_chat_run_helpers.py",
    "backend/app/services/e2e_mcp_apps_fixture_service.py",
    "backend/tests/test_conversation_run_service.py",
    "backend/app/routers/conversation_messages.py",
    "backend/app/routers/conversation_branches.py",
    "backend/app/routers/conversation_agent_protocol_commands.py",
    "backend/app/router_registry.py",
    "backend/app/services/agent_deletion.py",
    "backend/app/services/agent_lifecycle.py",
    "backend/app/services/agent_lifecycle_projection.py",
    "backend/app/services/agent_purge_preparation.py",
    "backend/app/services/agent_purge_relational.py",
)
B08_HASHES = {
    "backend/app/services/agent_lifecycle.py": (
        "f0e3c4cf509bdd19b193d24e356840c45aacb7db055d5059ce756a08ac6a09b3"
    ),
    "backend/app/services/agent_lifecycle_projection.py": (
        "3992bc02c85cdebf26c2ad57c93b5279fd87ec6b55ae352faf6a8fc5ee06a84b"
    ),
    "backend/app/services/agent_purge_preparation.py": (
        "4c8e8c96d51780a8aa278c12c5bd1e4816289d1bb677cab9841970932727a170"
    ),
    "backend/app/services/agent_purge_relational.py": (
        "0def5bb6057015af18b7542d0a4e3bbf6051399ad2bf92681d406d25c62c58bb"
    ),
}
GOOGLE_PATHS = (
    "backend/.venv/lib/python3.12/site-packages/langchain_google_genai/chat_models.py",
    "backend/.venv/lib/python3.12/site-packages/google/genai/types.py",
)
GUARDED_FIELDS = (
    "imports",
    "exports",
    "routes",
    "locks",
    "lock_edges",
    "with_for_update",
    "hydration_aliases",
)


class RuleError(ValueError): ...


def registered_release(root: Path) -> Path:
    return root / ".omo/evidence/company-administration/task-10-i1-implementation-release-r1.json"


def capture_document(
    root: Path,
    head: str,
    dirty: list[str],
    sources: JsonObject,
    google: JsonObject,
    same_tx: Mapping[str, str],
    provenance: JsonObject,
) -> JsonObject:
    for relative, digest in B08_HASHES.items():
        row = sources.get(relative)
        if not isinstance(row, dict) or row.get("sha256") != digest:
            raise RuleError(f"b08_mismatch:{relative}")
    exact_locks = sum(
        len(locks)
        for row in sources.values()
        if isinstance(row, dict) and isinstance(locks := row.get("with_for_update"), list)
    )
    document: JsonObject = {
        "schema_version": 1,
        "status": "captured",
        "workspace_root": str(root.resolve()),
        "candidate": head,
        "dirty_paths": sorted(dirty),
        "b08": B08_HASHES,
        "google": google,
        "same_tx_nodes": same_tx,
        "exact_with_for_update_count": exact_locks,
        "sources": sources,
    }
    document.update(provenance)
    return document


def verify_candidate_sources(candidate_path: Path) -> None:
    candidate = load_contract(candidate_path)
    raw_root, sources = candidate.get("workspace_root"), candidate.get("sources")
    if not isinstance(raw_root, str) or not isinstance(sources, dict) or not sources:
        raise RuleError("candidate_source_proof_missing")
    root = Path(raw_root)
    for relative, value in sources.items():
        if not isinstance(relative, str) or not isinstance(value, dict):
            raise RuleError("candidate_source_proof_invalid")
        digest = value.get("sha256")
        path = root / relative
        if not isinstance(digest, str) or not path.is_file():
            raise RuleError("candidate_source_proof_invalid")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuleError(f"candidate_changed_after_review:{relative}")


def emit_owned_capture(
    fresh: JsonObject,
    candidate: Path,
    named: Sequence[Path | None],
) -> JsonObject:
    if not all(path and path.is_file() for path in named):
        raise RuleError("registered_role_outputs_required")
    candidate.write_text(json.dumps(fresh, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    paths = [candidate, *(path for path in named if path is not None)]
    roles: list[JsonValue] = [
        {
            "path": str(path.resolve()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
        for path in paths
    ]
    return {"status": "verified", "candidate": fresh["candidate"], "roles": roles}


def capture_summary(fresh: JsonObject) -> JsonObject:
    sources = fresh.get("sources")
    if not isinstance(sources, dict):
        raise RuleError("candidate_sources_required")
    return {
        "status": "verified",
        "candidate": fresh["candidate"],
        "dirty_paths": fresh["dirty_paths"],
        "source_count": len(sources),
    }


def load_contract(path: Path) -> JsonObject:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuleError("json_object_required")
    return value


def _record(value: JsonValue, code: str) -> JsonObject:
    if not isinstance(value, dict):
        raise RuleError(code)
    return value


def flatten_source_field(sources: JsonObject, field: str) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for relative, source in sources.items():
        item = _record(source, "stage_source_invalid")
        if field == "nodes":
            for identity, digest in _record(item.get(field), "stage_nodes_required").items():
                result[f"{relative}::{identity}"] = digest
            continue
        rows = item.get(field)
        if not isinstance(rows, list):
            raise RuleError("stage_statements_required")
        for row in rows:
            statement = _record(row, "stage_statement_invalid")
            identity = statement.get("identity")
            if not isinstance(identity, str) or set(statement) != {"identity", "sha256"}:
                raise RuleError("stage_statement_invalid")
            result[f"{relative}::{identity}"] = statement.get("sha256")
    return result


def compare(baseline_path: Path, candidate_path: Path) -> JsonObject:
    return compare_documents(load_contract(baseline_path), load_contract(candidate_path))


def compare_documents(baseline: JsonObject, candidate: JsonObject) -> JsonObject:
    if candidate.get("candidate") != baseline.get("candidate"):
        raise RuleError("contract_candidate_mismatch")
    before = _record(baseline.get("sources"), "baseline_sources_required")
    after = _record(candidate.get("sources"), "candidate_sources_required")
    if set(before) != set(after):
        raise RuleError("source_file_set_changed")
    for relative in before:
        old = _record(before[relative], "source_record_invalid")
        new = _record(after[relative], "source_record_invalid")
        for field in GUARDED_FIELDS:
            if old.get(field) != new.get(field):
                raise RuleError(f"{field}_contract_changed:{relative}")
        old_nodes = _record(old.get("nodes"), "nodes_required")
        new_nodes = _record(new.get("nodes"), "nodes_required")
        if old_nodes != new_nodes:
            raise RuleError(f"node_contract_changed:{relative}")
        old_rows, new_rows = old.get("statements"), new.get("statements")
        if (
            not isinstance(old_rows, list)
            or not isinstance(new_rows, list)
            or len(old_rows) != len(new_rows)
        ):
            raise RuleError(f"statement_contract_changed:{relative}")
        old_items = [_record(row, "statement_invalid") for row in old_rows]
        new_items = [_record(row, "statement_invalid") for row in new_rows]
        if [item.get("identity") for item in old_items] != [
            item.get("identity") for item in new_items
        ]:
            raise RuleError(f"statement_order_changed:{relative}")
        if old_items != new_items:
            raise RuleError(f"statement_contract_changed:{relative}")
        if old.get("sha256") != new.get("sha256") and (
            old.get("module_ast_sha256") == new.get("module_ast_sha256") or old_nodes == new_nodes
        ):
            raise RuleError(f"full_file_contract_changed:{relative}")
    return {"status": "verified", "candidate": candidate.get("candidate")}
