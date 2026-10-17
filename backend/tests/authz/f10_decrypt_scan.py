"""AST scan for credential decryption outside the boundary (plan 11.3 table; F-10).

Flags, outside the storage layer and ``credentials/boundary.py``:

* any import of a primitive name (with or without ``as``), or of ``decrypt`` from
  ``app.security.cipher``;
* any attribute access ``x.<primitive>`` (call, alias or reference);
* ``<cipher>.decrypt`` where the name is bound to ``app.security.cipher`` by any import
  form or alias;
* ``getattr(x, "<primitive>")`` / ``getattr(cipher, "decrypt")`` with a literal name;
* ``open_values`` imported under another name, called with a non-literal purpose, or
  with a purpose the module is not allowed to use.
"""

import ast
from pathlib import Path

PRIMITIVES = frozenset({"decrypt_with_external", "open_payload", "decrypt_data", "decrypt_tenant"})
CIPHER = "app.security.cipher"
STORAGE = frozenset(
    {
        "credentials/boundary.py",
        "credentials/service.py",
        "credentials/tenant_encryption.py",
        # H-08: storage layer of the SSO connection secret (client secret / bind password).
        "auth/sso/secret_box.py",
    }
)
PURPOSES: dict[str, frozenset[str]] = {
    "run": frozenset({"credentials/run_resolution.py", "agent_runtime/credential_resolution.py"}),
    "redaction": frozenset(
        {"credentials/run_resolution.py", "services/conversation_history_secrets.py"}
    ),
    "refresh": frozenset({"mcp/auth.py", "mcp/oauth.py", "credentials/oauth_service.py"}),
    "probe": frozenset(
        {
            "services/model_test_run.py",  # F-13: model test probe after its decisions
            "routers/credentials.py",
            "services/health_check.py",
            "services/model_discovery.py",
        }
    ),
    "system": frozenset(
        {"routers/system_llm_settings.py", "services/system_credential_resolver.py"}
    ),
    "install": frozenset({"marketplace/credential_requirements.py"}),
}


def _cipher_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == CIPHER and alias.asname:
                    names.add(alias.asname)
        elif isinstance(node, ast.ImportFrom) and node.module == "app.security":
            names |= {a.asname or a.name for a in node.names if a.name == "cipher"}
    return names


def _attr_chain(node: ast.expr) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _is_cipher(node: ast.expr, names: set[str]) -> bool:
    chain = _attr_chain(node)
    return chain in names or chain == CIPHER


def _imports(node: ast.ImportFrom) -> list[str]:
    found = []
    for alias in node.names:
        if alias.name in PRIMITIVES or (node.module == CIPHER and alias.name == "decrypt"):
            found.append(f"import {alias.name}")
        if alias.name == "open_values" and alias.asname not in (None, "open_values"):
            found.append("open_values alias")
    return found


def _calls(node: ast.Call, rel: str, names: set[str]) -> list[str]:
    func, found = node.func, []
    if isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) >= 2:
        target, attr = node.args[0], node.args[1]
        literal = attr.value if isinstance(attr, ast.Constant) else None
        if isinstance(literal, str) and (
            literal in PRIMITIVES or (literal == "decrypt" and _is_cipher(target, names))
        ):
            found.append(f"getattr {literal}")
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name == "open_values":
        purpose = node.args[1] if len(node.args) >= 2 else None
        if not (isinstance(purpose, ast.Constant) and isinstance(purpose.value, str)):
            found.append("open_values purpose not literal")
        elif rel not in PURPOSES.get(purpose.value, frozenset()):
            found.append(f"open_values purpose {purpose.value}")
    return found


def scan_module(rel: str, source: str) -> list[str]:
    tree = ast.parse(source)
    names = _cipher_names(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            found += [(node.lineno, item) for item in _imports(node)]
        elif isinstance(node, ast.Attribute):
            if node.attr in PRIMITIVES:
                found.append((node.lineno, node.attr))
            elif node.attr == "decrypt" and _is_cipher(node.value, names):
                found.append((node.lineno, "cipher.decrypt"))
        elif isinstance(node, ast.Call):
            found += [(node.lineno, item) for item in _calls(node, rel, names)]
    return [f"{rel}:{line} {item}" for line, item in sorted(found)]


def decrypt_calls_outside_boundary(root: Path) -> list[str]:
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel in STORAGE or rel.startswith("security/"):
            continue
        found += scan_module(rel, path.read_text(encoding="utf-8"))
    return found


__all__ = ["decrypt_calls_outside_boundary", "scan_module"]
