"""F-10: credential ciphertext is opened only at the boundary (plan 11.3 table).

Static check (``f10_decrypt_scan``): no module outside the storage layer and
``credentials/boundary.py`` reaches a decryption primitive by any import form, alias,
attribute or ``getattr``; each ``open_values`` purpose is pinned to its modules.
"""

import uuid
from pathlib import Path

import pytest

from app.credentials.boundary import ResolvedCredential, SecretValues
from tests.authz.f10_decrypt_scan import decrypt_calls_outside_boundary, scan_module

APP = Path(__file__).resolve().parents[2] / "app"


def test_no_decryption_outside_the_boundary() -> None:
    assert decrypt_calls_outside_boundary(APP) == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("await credential_service.decrypt_with_external(c)", "decrypt_with_external"),
        ("f = cs.open_payload", "open_payload"),
        ("from app.credentials.service import decrypt_with_external as d", "import"),
        ("from app.credentials.service import decrypt_data", "import decrypt_data"),
        ("from app.security.cipher import decrypt", "import decrypt"),
        ("from app.security.cipher import decrypt as x", "import decrypt"),
        ("from app.security import cipher as c\nc.decrypt(b, k)", "cipher.decrypt"),
        ("import app.security.cipher as c\nc.decrypt(b, k)", "cipher.decrypt"),
        ("from app.security import cipher\ncipher.decrypt(b, k)", "cipher.decrypt"),
        ("app.security.cipher.decrypt(b, k)", "cipher.decrypt"),
        ("getattr(svc, 'decrypt_with_external')(c)", "getattr decrypt_with_external"),
        ("from app.security import cipher as c\ngetattr(c, 'decrypt')", "getattr decrypt"),
        ("from app.credentials.boundary import open_values as ov", "open_values alias"),
        ("await open_values(c, purpose)", "open_values purpose not literal"),
        ("await open_values(c, 'system')", "open_values purpose system"),
    ],
)
def test_the_scan_catches_each_route_around_the_boundary(source: str, expected: str) -> None:
    found = scan_module("services/leak.py", source)
    assert any(expected in item for item in found), found


def test_allowed_purposes_and_unrelated_decrypts_pass() -> None:
    assert scan_module("routers/models.py", "await open_values(c, 'probe')") == []
    assert scan_module("services/x.py", "AESGCM(k).decrypt(n, c, a)") == []


def test_resolved_values_never_print() -> None:
    values = SecretValues({"token": "sk-secret"})
    resolved = ResolvedCredential(
        credential_id=uuid.uuid4(),
        mode="per_user",
        resource_type="tool",
        resource_id=uuid.uuid4(),
        resource_owner_id=None,
        credential_subject_id=None,
        allowed_hosts=(),
        values=values,
    )
    assert "sk-secret" not in repr(resolved) + str(values) + repr(values)
    assert values.reveal() == {"token": "sk-secret"}
