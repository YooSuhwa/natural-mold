"""Credential domain — definition catalog, encryption, OAuth2, external secrets.

Public surface:
- ``registry``: the singleton :class:`CredentialRegistry` populated at import time
  by ``app.credentials.definitions``.
- ``CredentialDefinition``, ``FieldDef``, ``GenericAuth``, ``TestRequestSpec``:
  domain dataclasses used by definition modules.
- ``apply_authentication`` / ``CredentialAuth``: helpers to apply a credential to
  an outbound HTTP request.
- ``resolve`` / ``resolve_deep``: limited interpolation engine for the
  ``={{ $credentials.<field> }}`` expression form (no JS evaluation).
"""

from app.credentials.authenticate import (  # noqa: I001
    CredentialAuth,
    GenericAuth,
    apply_authentication,
)
from app.credentials.domain import CredentialDefinition, TestRequestSpec
from app.credentials.field import FieldDef, FieldKind
from app.credentials.interpolation import resolve, resolve_deep
from app.credentials.registry import CredentialRegistry, registry

# Side-effect import — definitions register themselves into ``registry`` at
# import time, so this MUST happen after ``registry`` is bound above.
from app.credentials import definitions as definitions  # noqa: F401, E402, I001

__all__ = [
    "CredentialAuth",
    "CredentialDefinition",
    "CredentialRegistry",
    "FieldDef",
    "FieldKind",
    "GenericAuth",
    "TestRequestSpec",
    "apply_authentication",
    "registry",
    "resolve",
    "resolve_deep",
]
