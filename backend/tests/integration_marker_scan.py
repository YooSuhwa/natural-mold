"""Find integration test modules by their pytest marker rather than by filename."""

from __future__ import annotations

import ast
from pathlib import Path


def _is_integration_mark(node: ast.AST) -> bool:
    """True for ``pytest.mark.integration`` (optionally called) anywhere inside ``node``."""
    return any(
        isinstance(sub, ast.Attribute)
        and sub.attr == "integration"
        and isinstance(sub.value, ast.Attribute)
        and sub.value.attr == "mark"
        for sub in ast.walk(node)
    )


def _module_mark(tree: ast.Module) -> bool:
    for node in tree.body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        value = node.value if isinstance(node, ast.Assign | ast.AnnAssign) else None
        named = any(
            isinstance(target, ast.Name) and target.id == "pytestmark" for target in targets
        )
        if named and value is not None and _is_integration_mark(value):
            return True
    return False


def _decorator_mark(tree: ast.Module) -> bool:
    return any(
        _is_integration_mark(decorator)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        for decorator in node.decorator_list
    )


def is_integration_module(relative_path: str, source: str) -> bool:
    """Module-level ``pytestmark`` (scalar or list), a marked test/class, or tests/integration/."""
    name = Path(relative_path).name
    if not name.startswith("test_") or not name.endswith(".py"):
        return False
    if relative_path.startswith("tests/integration/"):
        return True
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    return _module_mark(tree) or _decorator_mark(tree)


def integration_modules(backend_root: Path) -> set[str]:
    found: set[str] = set()
    for path in (backend_root / "tests").rglob("test_*.py"):
        relative = path.relative_to(backend_root).as_posix()
        if is_integration_module(relative, path.read_text()):
            found.add(relative)
    return found
