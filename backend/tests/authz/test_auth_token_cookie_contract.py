"""JWT claims never carry organization authority (plan 8.1, 16.6 ratchet).

Authority (org, company, roles, super-user) is read fresh from the database per
request. Adding a claim, or reading a non-identity claim outside ``auth/jwt.py``,
must be a reviewed change to these constants.
"""

from __future__ import annotations

import ast
from pathlib import Path
from uuid import uuid4

import jwt as pyjwt
import pytest
from fastapi import Response

from app.auth import jwt as app_jwt
from app.auth.cookies import (
    ACTIVE_ORG_COOKIE,
    clear_auth_cookies,
    set_active_org_cookie,
    set_auth_cookies,
)
from app.config import settings

_IDENTITY = {"sub", "type", "iat", "exp", "jti"}
EXPECTED_CLAIMS = {
    "access": _IDENTITY | {"is_super_user"},
    "refresh": _IDENTITY,
    "csrf": _IDENTITY,
}
# The only use of a decoded payload allowed outside auth/jwt.py; raw PyJWT is banned there.
READABLE_CLAIMS = {"sub"}
APP_ROOT = Path(__file__).resolve().parents[2] / "app"


def _claims(token: str) -> set[str]:
    return set(pyjwt.decode(token, options={"verify_signature": False}))


def test_token_claim_sets_are_pinned() -> None:
    # Given: one token of every kind for the same user.
    user_id = uuid4()
    tokens = {
        "access": app_jwt.create_access_token(user_id, is_super_user=True),
        "refresh": app_jwt.create_refresh_token(user_id)[0],
        "csrf": app_jwt.create_csrf_token(user_id),
    }
    # Then: no org/tenant/role claim is baked in and the decoded model matches.
    assert {kind: _claims(token) for kind, token in tokens.items()} == EXPECTED_CLAIMS
    assert set(app_jwt.TokenPayload.model_fields) == EXPECTED_CLAIMS["access"]


def _bound_names(target: ast.expr) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.Starred):
        return _bound_names(target.value)
    if isinstance(target, ast.Tuple | ast.List):
        return set().union(*(_bound_names(element) for element in target.elts))
    return set()


def _decoder_aliases(tree: ast.Module) -> set[str]:
    aliases = {"decode_token"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            aliases |= {a.asname or a.name for a in node.names if a.name == "decode_token"}
    return aliases


def _is_decode(node: ast.AST, aliases: set[str]) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Name) and func.id in aliases) or (
        isinstance(func, ast.Attribute) and func.attr == "decode_token"
    )


def _scope_offences(scope: ast.AST, aliases: set[str]) -> list[str]:
    parents = {
        child: parent for parent in ast.walk(scope) for child in ast.iter_child_nodes(parent)
    }
    decoded: set[str] = set()
    offences: list[str] = []
    for node in ast.walk(scope):
        value, targets = None, []
        if isinstance(node, ast.Assign):
            value, targets = node.value, node.targets
        elif isinstance(node, ast.AnnAssign | ast.NamedExpr) and node.value is not None:
            value, targets = node.value, [node.target]
        if value is not None and any(_is_decode(sub, aliases) for sub in ast.walk(value)):
            decoded |= set().union(*(_bound_names(target) for target in targets))
    for node in ast.walk(scope):
        parent = parents.get(node)
        is_sub = isinstance(parent, ast.Attribute) and parent.attr in READABLE_CLAIMS
        if _is_decode(node, aliases) and not is_sub:
            if isinstance(parent, ast.Attribute | ast.Call):
                offences.append(f"decode_token(...) used via {type(parent).__name__}")
        elif (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id in decoded
            and not is_sub
        ):
            offences.append(ast.unparse(parent) if parent is not None else node.id)
    return offences


def _claim_reads(source: str) -> list[str]:
    """Offences: any use of a decoded payload other than ``.sub``, and raw PyJWT use."""
    tree = ast.parse(source)
    aliases = _decoder_aliases(tree)
    offences: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name == "jwt" for a in node.names):
            offences.append("import jwt")
        elif isinstance(node, ast.ImportFrom) and node.module == "jwt":
            offences.append("from jwt import")
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            offences.extend(_scope_offences(node, aliases))
    module_level = ast.Module(
        body=[
            stmt
            for stmt in tree.body
            if not isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        ],
        type_ignores=[],
    )
    offences.extend(_scope_offences(module_level, aliases))
    return offences


@pytest.mark.parametrize(
    "source",
    [
        "def f(t):\n    p = decode_token(t)\n    return p.is_super_user\n",
        "def f(t):\n    p: TokenPayload = decode_token(t)\n    return p.exp\n",
        "def f(t):\n    if (p := decode_token(t)):\n        return p.jti\n",
        "def f(t):\n    p, other = decode_token(t), 1\n    return p.type\n",
        "from app.auth.jwt import decode_token as d\ndef f(t):\n    p = d(t)\n    return p.iat\n",
        "def f(t):\n    return decode_token(t).is_super_user\n",
        "def f(t):\n    return use(decode_token(t))\n",
        "def f(t):\n    p = decode_token(t)\n    return getattr(p, 'org_id')\n",
        "def f(t):\n    p = decode_token(t)\n    return p.model_dump()\n",
        "def f(t):\n    p = decode_token(t)\n    return vars(p)\n",
        "import jwt\n",
        "import jwt as pyjwt\npyjwt.decode(t)\n",
        "from jwt import decode\n",
    ],
)
def test_scanner_detects_authority_claim_reads(source: str) -> None:
    assert _claim_reads(source)


def test_scanner_allows_identity_reads_and_unrelated_payloads() -> None:
    source = (
        "def f(t):\n    p = decode_token(t)\n    return uuid.UUID(p.sub)\n"
        "def g(t):\n    return decode_token(t).sub\n"
        "def h(payload):\n    return payload.org_id\n"
    )
    assert _claim_reads(source) == []


def test_application_reads_only_identity_claims() -> None:
    offenders = {
        path.relative_to(APP_ROOT).as_posix(): reads
        for path in sorted(APP_ROOT.rglob("*.py"))
        if path.relative_to(APP_ROOT).as_posix() != "auth/jwt.py"
        and (reads := _claim_reads(path.read_text()))
    }
    assert offenders == {}


def _attributes(header: str) -> set[str]:
    _name_value, *attributes = (part.strip() for part in header.split(";"))
    return {attribute for attribute in attributes if not attribute.startswith("expires=")}


@pytest.mark.parametrize(
    ("secure", "samesite", "domain"),
    [(False, "lax", ""), (True, "strict", "moldy.example.com")],
)
def test_active_org_cookie_attributes_equal_refresh_cookie(
    monkeypatch: pytest.MonkeyPatch, secure: bool, samesite: str, domain: str
) -> None:
    monkeypatch.setattr(settings, "cookie_secure", secure)
    monkeypatch.setattr(settings, "cookie_samesite", samesite)
    monkeypatch.setattr(settings, "cookie_domain", domain)
    response = Response()
    set_auth_cookies(response, access_token="a", refresh_token="r", csrf_token="c")
    set_active_org_cookie(response, uuid4())
    headers = {header.split("=", 1)[0]: header for header in response.headers.getlist("set-cookie")}
    assert _attributes(headers[ACTIVE_ORG_COOKIE]) == _attributes(
        headers[settings.cookie_name_refresh]
    )


def test_logout_clears_every_auth_and_org_cookie() -> None:
    response = Response()
    clear_auth_cookies(response)
    cleared = {
        header.split("=", 1)[0]
        for header in response.headers.getlist("set-cookie")
        if "Max-Age=0" in header
    }
    assert cleared == {
        settings.cookie_name_access,
        settings.cookie_name_refresh,
        settings.cookie_name_csrf,
        ACTIVE_ORG_COOKIE,
    }
