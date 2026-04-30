"""Credential definition dataclass — the per-type schema + behavior bundle.

A :class:`CredentialDefinition` is registered once per credential family (Naver
Search, OpenAI, ...). It declares form fields, optional generic authentication,
an optional connectivity test request, and OAuth2 pre-authentication hooks.

Algorithm/structure attribution: see NOTICES.md.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.credentials.authenticate import GenericAuth
from app.credentials.field import FieldDef


@dataclass
class TestRequestSpec:
    """Connectivity test recipe.

    ``request`` is a request-options dict (``method``, ``url``, optionally
    ``headers``, ``params``, ``json``). All values support
    ``={{ $credentials.<field> }}`` interpolation.

    ``rules`` is a list of acceptance/rejection conditions evaluated against
    the response, e.g. ``[{"type": "responseCode", "value": 200}]`` or
    ``[{"type": "responseSuccessBody", "key": "ok", "value": True}]``.
    """

    request: dict[str, Any]
    rules: list[dict[str, Any]] = field(default_factory=list)


# Type alias for the optional preAuthentication hook used by OAuth2 definitions.
# Receives the decrypted credential payload and returns the patch (e.g. fresh
# ``access_token`` and ``expires_at``) to merge back in.
PreAuthCallable = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass
class CredentialDefinition:
    """Per-type credential schema and behavior."""

    key: str
    display_name: str
    icon_id: str | None = None
    documentation_url: str | None = None
    properties: list[FieldDef] = field(default_factory=list)
    authenticate: GenericAuth | None = None
    test: TestRequestSpec | None = None
    pre_authentication: PreAuthCallable | None = None
    extends: list[str] = field(default_factory=list)
    category: str = "general"

    def serialize(self) -> dict[str, Any]:
        """JSON-friendly representation for the API catalog endpoint."""

        return {
            "key": self.key,
            "display_name": self.display_name,
            "icon_id": self.icon_id,
            "documentation_url": self.documentation_url,
            "category": self.category,
            "extends": list(self.extends),
            "properties": [p.serialize() for p in self.properties],
            "has_test": self.test is not None,
            "has_oauth": self.pre_authentication is not None,
        }


__all__ = ["CredentialDefinition", "PreAuthCallable", "TestRequestSpec"]
