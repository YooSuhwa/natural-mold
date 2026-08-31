"""Build a deterministic import graph for the guarded backend packages."""

from __future__ import annotations

import ast
from collections.abc import Sequence
from pathlib import Path
from typing import Final, NamedTuple

if __package__:
    from . import runtime_dependency_sources as dependency_sources
else:
    import runtime_dependency_sources as dependency_sources

MAX_SOURCE_BYTES: Final = dependency_sources.MAX_SOURCE_BYTES
DependencyGraphError = dependency_sources.DependencyGraphError
ModuleSource = dependency_sources.ModuleSource
load_selected_sources = dependency_sources.load_selected_sources
load_tracked_sources = dependency_sources.load_tracked_sources


class ImportEdge(NamedTuple):
    importer: str
    imported: str


class DynamicImportSite(NamedTuple):
    module: str
    function: str
    callee: str


class DependencyGraph(NamedTuple):
    sources: tuple[ModuleSource, ...]
    edges: tuple[ImportEdge, ...]
    unresolved_dynamic_imports: tuple[DynamicImportSite, ...]


class _DynamicAliases(NamedTuple):
    importlib_modules: frozenset[str]
    builtins_modules: frozenset[str]
    import_module_functions: frozenset[str]
    import_functions: frozenset[str]


class _ImportVisitor(ast.NodeVisitor):
    def __init__(
        self,
        source: ModuleSource,
        modules: frozenset[str],
        dynamic_aliases: _DynamicAliases,
    ) -> None:
        self.source = source
        self.modules = modules
        self.dynamic_aliases = dynamic_aliases
        self.edges: set[ImportEdge] = set()
        self.unresolved: set[DynamicImportSite] = set()
        self._scopes: list[str] = []

    def _existing_module(self, target: str) -> str | None:
        candidate = target
        while candidate.startswith("app"):
            if candidate in self.modules:
                return candidate
            candidate, separator, _ = candidate.rpartition(".")
            if not separator:
                break
        return None

    def _add_target(self, target: str) -> bool:
        resolved = self._existing_module(target)
        if resolved is not None:
            self.edges.add(ImportEdge(self.source.module, resolved))
            return True
        return False

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            self._add_target(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        package = (
            self.source.module
            if self.source.path.endswith("/__init__.py")
            else self.source.module.rpartition(".")[0]
        )
        if node.level:
            parts = package.split(".") if package else []
            keep = len(parts) - node.level + 1
            if keep <= 0:
                raise DependencyGraphError(f"relative import escapes package: {self.source.path}")
            base = ".".join(parts[:keep])
            target = f"{base}.{node.module}" if node.module else base
        else:
            target = node.module or ""
        for alias in node.names:
            child = f"{target}.{alias.name}"
            self._add_target(child if child in self.modules else target)

    def _visit_scope(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        self._scopes.append(node.name)
        self.generic_visit(node)
        self._scopes.pop()

    visit_FunctionDef = _visit_scope  # noqa: N815
    visit_AsyncFunctionDef = _visit_scope  # noqa: N815
    visit_ClassDef = _visit_scope  # noqa: N815

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        callee = self._dynamic_callee(node.func)
        if callee is not None:
            literal = (
                node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
            )
            if isinstance(literal, str):
                resolved = True
                if callee == "importlib.import_module" and literal.startswith("."):
                    package = self._literal_package(node)
                    target = _resolve_relative_dynamic(literal, package)
                    resolved = target is not None and self._add_target(target)
                elif callee == "__import__" and (
                    literal.startswith(".") or self._has_nonzero_import_level(node)
                ):
                    resolved = False
                else:
                    self._add_target(literal)
                if not resolved:
                    self._record_unresolved(callee)
            else:
                self._record_unresolved(callee)
        self.generic_visit(node)

    def _record_unresolved(self, callee: str) -> None:
        function = ".".join(self._scopes) if self._scopes else "<module>"
        self.unresolved.add(DynamicImportSite(self.source.module, function, callee))

    @staticmethod
    def _literal_package(node: ast.Call) -> str | None:
        if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
            return node.args[1].value if isinstance(node.args[1].value, str) else None
        for keyword in node.keywords:
            if keyword.arg == "package" and isinstance(keyword.value, ast.Constant):
                return keyword.value.value if isinstance(keyword.value.value, str) else None
        return None

    @staticmethod
    def _has_nonzero_import_level(node: ast.Call) -> bool:
        level: ast.expr | None = node.args[4] if len(node.args) > 4 else None
        for keyword in node.keywords:
            if keyword.arg == "level":
                level = keyword.value
        return level is not None and not (isinstance(level, ast.Constant) and level.value == 0)

    def _dynamic_callee(self, node: ast.expr) -> str | None:
        match node:
            case ast.Name(id="__import__"):
                return "__import__"
            case ast.Name(id=name) if name in self.dynamic_aliases.import_module_functions:
                return "importlib.import_module"
            case ast.Name(id=name) if name in self.dynamic_aliases.import_functions:
                return "__import__"
            case ast.Attribute(value=ast.Name(id=name), attr="import_module") if (
                name in self.dynamic_aliases.importlib_modules
            ):
                return "importlib.import_module"
            case ast.Attribute(value=ast.Name(id=name), attr="__import__") if (
                name in self.dynamic_aliases.builtins_modules
            ):
                return "__import__"
            case _:
                return None


def _dynamic_aliases(tree: ast.AST) -> _DynamicAliases:
    importlib_modules: set[str] = set()
    builtins_modules: set[str] = set()
    import_module_functions: set[str] = set()
    import_functions: set[str] = set()
    assignments: list[tuple[str, ast.expr]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib":
                    importlib_modules.add(alias.asname or alias.name)
                elif alias.name == "builtins":
                    builtins_modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module in {"importlib", "builtins"}:
            for alias in node.names:
                if node.module == "importlib" and alias.name == "import_module":
                    import_module_functions.add(alias.asname or alias.name)
                elif node.module == "builtins" and alias.name == "__import__":
                    import_functions.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                assignments.append((target.id, node.value))
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            assignments.append((node.target.id, node.value))

    alias_sets = (
        importlib_modules,
        builtins_modules,
        import_module_functions,
        import_functions,
    )
    known_count = -1
    while known_count != sum(map(len, alias_sets)):
        known_count = sum(map(len, alias_sets))
        for target, value in assignments:
            match value:
                case ast.Name(id=name) if name in importlib_modules:
                    importlib_modules.add(target)
                case ast.Name(id=name) if name in builtins_modules:
                    builtins_modules.add(target)
                case ast.Name(id=name) if name in import_module_functions:
                    import_module_functions.add(target)
                case ast.Name(id=name) if name == "__import__" or name in import_functions:
                    import_functions.add(target)
                case ast.Attribute(value=ast.Name(id=name), attr="import_module") if (
                    name in importlib_modules
                ):
                    import_module_functions.add(target)
                case ast.Attribute(value=ast.Name(id=name), attr="__import__") if (
                    name in builtins_modules
                ):
                    import_functions.add(target)
                case _:
                    pass
    return _DynamicAliases(
        frozenset(importlib_modules),
        frozenset(builtins_modules),
        frozenset(import_module_functions),
        frozenset(import_functions),
    )


def _resolve_relative_dynamic(target: str, package: str | None) -> str | None:
    if package is None or not (package == "app" or package.startswith("app.")):
        return None
    dot_count = len(target) - len(target.lstrip("."))
    package_parts = package.split(".")
    if dot_count > len(package_parts):
        return None
    prefix = package_parts[: len(package_parts) - dot_count + 1]
    suffix = target[dot_count:]
    return ".".join((*prefix, suffix)) if suffix else ".".join(prefix)


def scan_sources(sources: tuple[ModuleSource, ...]) -> DependencyGraph:
    """Parse sources into a normalized graph, failing on malformed Python."""
    modules = frozenset(source.module for source in sources)
    if len(modules) != len(sources):
        raise DependencyGraphError("source inventory contains duplicate modules")
    if len({source.path for source in sources}) != len(sources):
        raise DependencyGraphError("source inventory contains duplicate paths")
    edges: set[ImportEdge] = set()
    unresolved: set[DynamicImportSite] = set()
    for source in sources:
        try:
            tree = ast.parse(source.source, filename=source.path)
        except (SyntaxError, ValueError) as error:
            raise DependencyGraphError(f"tracked source cannot be parsed: {source.path}") from error
        visitor = _ImportVisitor(source, modules, _dynamic_aliases(tree))
        visitor.visit(tree)
        edges.update(visitor.edges)
        unresolved.update(visitor.unresolved)
    return DependencyGraph(tuple(sorted(sources)), tuple(sorted(edges)), tuple(sorted(unresolved)))


def scan_repository(
    backend_root: Path, tracked_files: Sequence[str] | None = None
) -> DependencyGraph:
    """Load and scan the guarded source graph in one read-only operation."""
    root = backend_root.resolve()
    sources = (
        load_tracked_sources(root)
        if tracked_files is None
        else load_selected_sources(root, tracked_files)
    )
    return scan_sources(sources)
