"""Generic HTTP API key with a user-configurable header name.

The defining feature of this credential is the ``header_name`` field — many
services use a custom header (``X-API-Key``, ``Api-Key``, ``Apikey`` ...). The
value is interpolated into the *header name* itself, so a credential like
``{header_name: "X-Foo-Key", api_key: "abc"}`` produces ``X-Foo-Key: abc``.
"""

from __future__ import annotations

from typing import Any

from app.credentials.authenticate import GenericAuth, apply_authentication
from app.credentials.domain import CredentialDefinition
from app.credentials.field import FieldDef, FieldKind
from app.credentials.interpolation import resolve

# Default authenticate recipe — the header name is interpolated separately at
# runtime by ``apply_with_dynamic_header`` because GenericAuth maps fixed
# header names to interpolated values.

definition = CredentialDefinition(
    key="http_api_key",
    display_name="HTTP API Key",
    icon_id="key",
    category="http",
    properties=[
        FieldDef(
            name="header_name",
            display_name="Header Name",
            kind=FieldKind.STRING,
            required=True,
            default="X-API-Key",
            placeholder="X-API-Key",
        ),
        FieldDef(
            name="api_key",
            display_name="API Key",
            kind=FieldKind.PASSWORD,
            required=True,
            type_options={"password": True},
        ),
    ],
    # Placeholder static auth — replaced at apply-time by
    # :func:`apply_with_dynamic_header` because header names themselves are
    # user-configurable.
    authenticate=GenericAuth(properties={}),
)


def apply_with_dynamic_header(
    request_options: dict[str, Any],
    credentials: dict[str, Any],
) -> dict[str, Any]:
    """Convenience helper for callers that want to apply this definition.

    Builds a per-call :class:`GenericAuth` whose header key is the resolved
    ``header_name`` value, then delegates to :func:`apply_authentication`.
    """

    header_name = resolve("={{ $credentials.header_name }}", credentials)
    if not header_name:
        header_name = "X-API-Key"
    auth = GenericAuth(
        properties={
            "headers": {str(header_name): "={{ $credentials.api_key }}"}
        }
    )
    return apply_authentication(auth, request_options, credentials)
