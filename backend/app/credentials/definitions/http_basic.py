"""HTTP Basic authentication."""

from __future__ import annotations

from app.credentials.authenticate import GenericAuth
from app.credentials.domain import CredentialDefinition
from app.credentials.field import FieldDef, FieldKind

definition = CredentialDefinition(
    key="http_basic",
    display_name="HTTP Basic Auth",
    icon_id="key",
    category="http",
    properties=[
        FieldDef(
            name="username",
            display_name="Username",
            kind=FieldKind.STRING,
            required=True,
        ),
        FieldDef(
            name="password",
            display_name="Password",
            kind=FieldKind.PASSWORD,
            required=True,
            type_options={"password": True},
        ),
    ],
    authenticate=GenericAuth(
        properties={
            "basic": {
                "username": "={{ $credentials.username }}",
                "password": "={{ $credentials.password }}",
            }
        }
    ),
)
