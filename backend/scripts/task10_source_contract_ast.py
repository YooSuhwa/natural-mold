"""Pure source capture primitives for the Task 10 company-admin contract."""

from __future__ import annotations

import ast
import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path

if __package__:
    from scripts.task10_source_contract_rules import SOURCE_PATHS
else:
    from task10_source_contract_rules import SOURCE_PATHS

SAME_TX = {
    "test_existing_conversation_snapshot_survives_agent_policy_edit": (
        "f5efe12191a23952512ddf963f0655f0e44f8c7cc0d2cf2487c37e8efc60ac45"
    ),
    "test_create_run_rejects_second_active_run": (
        "28864ee8d16de52b325964aa00edf369aa587b07ae5d0bfa0cdc1ee5edde5a73"
    ),
    "test_active_runs_for_conversations_batches_by_ids": (
        "389a498f2977bdb7037ed780a45033ab5f37f05da820c27c7cea1a910012c623"
    ),
}

type JsonValue = str | int | bool | None | Sequence["JsonValue"] | Mapping[str, "JsonValue"]
type JsonObject = dict[str, JsonValue]


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _name(node: ast.AST) -> str:
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        target = node.targets[0] if isinstance(node, ast.Assign) else node.target
        if isinstance(target, ast.Name):
            return target.id
    return "-"


def _dump(node: ast.AST) -> str:
    return ast.dump(node, annotate_fields=True, include_attributes=False)


def _decorator_name(node: ast.AST) -> str:
    current = node.func if isinstance(node, ast.Call) else node
    parts: list[str] = []
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _imports(tree: ast.Module) -> list[str]:
    rows: list[str] = []
    for node in ast.walk(tree):
        module = "." * node.level + (node.module or "") if isinstance(node, ast.ImportFrom) else ""
        for alias in node.names if isinstance(node, (ast.Import, ast.ImportFrom)) else ():
            imported = f"{module}:{alias.name}" if module else alias.name
            rows.append(f"{imported} as {alias.asname}" if alias.asname else imported)
    return sorted(rows)


def _exports(tree: ast.Module) -> list[str]:
    exports: list[str] = []
    for node in tree.body:
        if _name(node) == "__all__" and isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if isinstance(value, (ast.List, ast.Tuple)):
                exports.extend(
                    item.value
                    for item in value.elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                )
        name = _name(node)
        if name != "-" and not name.startswith("_"):
            exports.append(name)
    return sorted(set(exports))


_GUARDED = ("hydrate", "prepare", "resolve", "get_checkpointer", "build_subagent")
_ROUTES = {"get", "post", "put", "patch", "delete", "api_route", "websocket"}
_GOOGLE_NODES = {
    "validate_environment",
    "_build_request_config",
    "_agenerate",
    "_astream",
    "HttpRetryOptions",
    "HttpOptions",
}


def _resolve(node: ast.AST, aliases: Mapping[str, str]) -> str:
    name = _decorator_name(node)
    if not name:
        return ""
    root, *tail = name.split(".")
    return ".".join((aliases.get(root, root), *tail))


class ContractCaptureError(ValueError):
    pass


class _SemanticVisitor(ast.NodeVisitor):
    def __init__(self, tree: ast.Module) -> None:
        self.aliases: dict[str, str] = {}
        self.unknown: set[str] = set()
        self.owners: list[str] = []
        self.locks: list[str] = []
        self.hydration: set[str] = set()
        self.routes: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = (
                    "." * node.level + (node.module or "")
                    if isinstance(node, ast.ImportFrom)
                    else ""
                )
                for item in node.names:
                    self.aliases[item.asname or item.name] = (
                        f"{module}.{item.name}" if module else item.name
                    )
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    self._alias(target, value)

    def _alias(self, target: ast.AST, value: ast.AST | None) -> None:
        if not isinstance(target, ast.Name) or value is None:
            return
        if resolved := _resolve(value, self.aliases):
            self.aliases[target.id] = resolved
        elif any(token in _dump(value).lower() for token in _GUARDED):
            self.unknown.add(target.id)

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            name = _decorator_name(decorator)
            if name.rsplit(".", 1)[-1] in _ROUTES:
                argument = (
                    decorator.args[0]
                    if isinstance(decorator, ast.Call) and decorator.args
                    else None
                )
                path = (
                    argument.value
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
                    else ""
                )
                self.routes.append(f"{name}|{path}|{node.name}")
        self.owners.append(node.name)
        for statement in node.body:
            self.visit(statement)
        self.owners.pop()

    def visit(self, node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._function(node)
            return
        super().visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        surface, resolved = _decorator_name(node), _resolve(node.func, self.aliases)
        tail = surface.rsplit(".", 1)[-1]
        if tail == "with_for_update" or tail.startswith(("lock_", "acquire_")):
            owner = ".".join(self.owners) if self.owners else "<module>"
            self.locks.append(f"{node.lineno:06d}|{owner}|{surface}|{_dump(node)}")
        if resolved and any(token in resolved.lower() for token in _GUARDED):
            self.hydration.add(resolved)
        if isinstance(node.func, ast.Name) and node.func.id in self.unknown:
            raise ContractCaptureError("dynamic_guarded_alias")
        self.generic_visit(node)


def _statement_rows(tree: ast.Module) -> list[JsonValue]:
    rows: list[JsonValue] = []

    def visit(body: list[ast.stmt], prefix: str) -> None:
        for index, node in enumerate(body):
            identity = f"{prefix}/{index}:{type(node).__name__}:{_name(node)}"
            rows.append({"identity": identity, "sha256": sha256_bytes(_dump(node).encode())})
            for field, value in ast.iter_fields(node):
                if (
                    isinstance(value, list)
                    and value
                    and all(isinstance(item, ast.stmt) for item in value)
                ):
                    visit(value, f"{identity}.{field}")

    visit(tree.body, "module")
    return rows


def capture_python(path: Path) -> JsonObject:
    payload = path.read_bytes()
    text = payload.decode("utf-8")
    tree = ast.parse(text, filename=str(path))
    nodes = {
        f"{index}:{type(node).__name__}:{_name(node)}": sha256_bytes(_dump(node).encode())
        for index, node in enumerate(tree.body)
    }
    semantic = _SemanticVisitor(tree)
    semantic.visit(tree)
    lock_rows = semantic.locks
    return {
        "sha256": sha256_bytes(payload),
        "bytes": len(payload),
        "lines": len(text.splitlines()),
        "pure_loc": sum(
            bool(line.strip()) and not line.lstrip().startswith("#") for line in text.splitlines()
        ),
        "module_ast_sha256": sha256_bytes(_dump(tree).encode()),
        "nodes": nodes,
        "statements": _statement_rows(tree),
        "imports": _imports(tree),
        "exports": _exports(tree),
        "routes": semantic.routes,
        "locks": lock_rows,
        "lock_edges": [
            f"{left}->{right}" for left, right in zip(lock_rows, lock_rows[1:], strict=False)
        ],
        "with_for_update": [
            row for row in lock_rows if row.split("|", 3)[2].rsplit(".", 1)[-1] == "with_for_update"
        ],
        "hydration_aliases": sorted(semantic.hydration),
    }


def capture_sources(root: Path) -> JsonObject:
    return {relative: capture_python(root / relative) for relative in SOURCE_PATHS}


def capture_same_tx(root: Path) -> dict[str, str]:
    path = root / "backend/tests/test_conversation_run_service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    captured = {}
    for name in SAME_TX:
        matches = [node for node in ast.walk(tree) if getattr(node, "name", None) == name]
        if len(matches) != 1:
            raise ContractCaptureError(f"same_tx_node_not_unique:{name}")
        captured[name] = sha256_bytes(ast.dump(matches[0]).encode())
    if captured != SAME_TX:
        raise ContractCaptureError("same_tx_contract_changed")
    return captured


def capture_google(root: Path, paths: Sequence[str]) -> JsonObject:
    rows: dict[str, JsonValue] = {}
    for relative in paths:
        path = root / relative
        if not path.is_file():
            raise ContractCaptureError(f"google_source_missing:{relative}")
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        anchors = {
            _name(node): sha256_bytes(_dump(node).encode())
            for node in ast.walk(tree)
            if _name(node) in _GOOGLE_NODES
        }
        rows[relative] = {
            "sha256": sha256_bytes(text.encode()),
            "request_anchor": "request" in text,
            "refresh_anchor": "refresh" in text,
            "transport_anchor": "Request" in text or "AuthorizedSession" in text,
            "nodes": anchors,
        }
    return rows
