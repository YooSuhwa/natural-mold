"""Generic HTTP Bearer-token credential."""

from __future__ import annotations

from app.credentials.authenticate import GenericAuth
from app.credentials.domain import CredentialDefinition
from app.credentials.field import FieldDef, FieldKind

definition = CredentialDefinition(
    key="http_bearer",
    display_name="HTTP Bearer Token",
    icon_id="key",
    category="http",
    properties=[
        FieldDef(
            name="token",
            display_name="Token",
            kind=FieldKind.PASSWORD,
            required=True,
            type_options={"password": True},
        ),
    ],
    authenticate=GenericAuth(
        properties={
            "headers": {
                "Authorization": "=Bearer {{ $credentials.token }}",
            }
        }
    ),
)
