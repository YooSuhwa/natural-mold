"""Only ``app/mcp/sdk.py`` imports the MCP SDK (plan 8.3, D12).

The SDK (``mcp``), FastMCP and the beta ``langchain.mcp`` namespace change between
releases; keeping them behind one module keeps an upgrade to one file.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
SDK_MODULES = ("mcp", "fastmcp", "langchain.mcp", "langchain_mcp_adapters")
ALLOWED = {"mcp/sdk.py"}


def _sdk_imports(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        found += [n for n in names if any(n == m or n.startswith(f"{m}.") for m in SDK_MODULES)]
    return found


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from mcp import types\n", ["mcp"]),
        ("import fastmcp.client\n", ["fastmcp.client"]),
        ("from langchain.mcp import MCPAdapter\n", ["langchain.mcp"]),
        ("from app.mcp import sdk\nimport mcpx\nfrom langchain.agents import x\n", []),
    ],
)
def test_the_scanner_finds_sdk_imports(source: str, expected: list[str]) -> None:
    assert _sdk_imports(source) == expected


def test_only_the_sdk_module_imports_the_sdk() -> None:
    offenders = {
        path.relative_to(APP).as_posix(): found
        for path in sorted(APP.rglob("*.py"))
        if path.relative_to(APP).as_posix() not in ALLOWED
        and (found := _sdk_imports(path.read_text()))
    }
    assert offenders == {}
