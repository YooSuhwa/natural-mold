"""Unit contracts for the Todo09 static import-graph scanner."""

from __future__ import annotations

import pytest

from scripts.runtime_dependency_graph import (
    DependencyGraphError,
    ModuleSource,
    scan_sources,
)


def _source(module: str, source: str) -> ModuleSource:
    return ModuleSource(module, f"{module.replace('.', '/')}.py", source)


def _graph(*sources: ModuleSource):
    return scan_sources(tuple(sources))


def test_scanner_normalizes_static_import_forms_including_nested_and_type_only() -> None:
    graph = _graph(
        _source("app.models", ""),
        _source("app.models.user", "class User: pass\n"),
        _source("app.routers", ""),
        _source(
            "app.routers.uses",
            """
import app.models.user
from app.models import user
from app.models.user import User as Alias
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import app.models.user as type_user

def nested() -> None:
    from app.models import user as nested_user
""",
        ),
    )

    assert {(edge.importer, edge.imported) for edge in graph.edges} == {
        ("app.routers.uses", "app.models.user"),
    }


def test_scanner_normalizes_package_and_relative_imports() -> None:
    graph = _graph(
        _source("app.services", ""),
        _source("app.services.package", ""),
        _source("app.services.package.sibling", ""),
        _source("app.services.package.child", "from . import sibling\n"),
    )

    assert len(graph.edges) == 1
    # The package import resolves to the concrete sibling module.
    assert graph.edges[0].importer == "app.services.package.child"
    assert graph.edges[0].imported == "app.services.package.sibling"


def test_scanner_collects_literal_dynamic_imports_and_rejects_unresolved_ones() -> None:
    graph = _graph(
        _source("app.models", ""),
        _source("app.models.user", ""),
        _source("app.models.admin", ""),
        _source(
            "app.agent_runtime.dynamic",
            """
import importlib
import importlib as loader
from importlib import import_module as load_module
importlib.import_module("app.models.user")
loader.import_module("app.models.admin")
load_module("app.models.admin")

def choose(name: str) -> None:
    __import__(name)
""",
        ),
    )

    assert {(edge.importer, edge.imported) for edge in graph.edges} == {
        ("app.agent_runtime.dynamic", "app.models.user"),
        ("app.agent_runtime.dynamic", "app.models.admin"),
    }
    assert graph.unresolved_dynamic_imports[0].module == "app.agent_runtime.dynamic"
    assert graph.unresolved_dynamic_imports[0].function == "choose"
    assert graph.unresolved_dynamic_imports[0].callee == "__import__"


def test_scanner_follows_chained_importlib_assignment_aliases() -> None:
    graph = _graph(
        _source("app.services.worker", ""),
        _source(
            "app.agent_runtime.dynamic",
            "import importlib\nload = importlib.import_module\nchained = load\n"
            'chained("app.services.worker")\n',
        ),
    )

    assert {(edge.importer, edge.imported) for edge in graph.edges} == {
        ("app.agent_runtime.dynamic", "app.services.worker")
    }


def test_scanner_follows_builtins_import_assignment_aliases() -> None:
    graph = _graph(
        _source("app.services.worker", ""),
        _source(
            "app.agent_runtime.dynamic",
            "from builtins import __import__ as load\nchained = load\n"
            'chained("app.services.worker")\n',
        ),
    )

    assert {(edge.importer, edge.imported) for edge in graph.edges} == {
        ("app.agent_runtime.dynamic", "app.services.worker")
    }


def test_scanner_resolves_relative_importlib_and_flags_unknown_package() -> None:
    graph = _graph(
        _source("app.services.boundary", ""),
        _source(
            "app.agent_runtime.dynamic",
            "import importlib\n"
            'importlib.import_module(".boundary", package="app.services")\n'
            "package = 'app.services'\n"
            'importlib.import_module(".boundary", package=package)\n',
        ),
    )

    assert {(edge.importer, edge.imported) for edge in graph.edges} == {
        ("app.agent_runtime.dynamic", "app.services.boundary")
    }
    assert len(graph.unresolved_dynamic_imports) == 1
    assert graph.unresolved_dynamic_imports[0].callee == "importlib.import_module"


def test_scanner_keeps_same_named_class_methods_as_distinct_dynamic_sites() -> None:
    graph = _graph(
        _source(
            "app.agent_runtime.dynamic",
            "class A:\n    def load(self, name: str):\n        return __import__(name)\n\n"
            "class B:\n    def load(self, name: str):\n        return __import__(name)\n",
        )
    )

    assert {site.function for site in graph.unresolved_dynamic_imports} == {"A.load", "B.load"}


def test_scanner_rejects_relative_import_that_escapes_package() -> None:
    sources = (
        _source("app.routers", ""),
        _source("app.routers.handler", "from ....models import user\n"),
    )

    with pytest.raises(DependencyGraphError, match="relative import escapes package"):
        scan_sources(sources)
