"""Generic per-user secret for first-party MCP servers."""

from __future__ import annotations

from app.credentials.authenticate import GenericAuth
from app.credentials.domain import CredentialDefinition
from app.credentials.field import FieldDef, FieldKind

definition = CredentialDefinition(
    key="mcp_secret",
    display_name="MCP Secret",
    icon_id="mcp",
    category="mcp",
    properties=[
        FieldDef(
            name="secret",
            display_name="Secret",
            kind=FieldKind.PASSWORD,
            required=True,
            type_options={"password": True},
            description=(
                "Per-user secret forwarded to first-party MCP servers as X-Moldy-Credential."
            ),
        ),
    ],
    authenticate=GenericAuth(
        properties={
            "headers": {
                "X-Moldy-Credential": "={{ $credentials.secret }}",
            }
        }
    ),
)
