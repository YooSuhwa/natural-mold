"""OpenAI API key."""

from __future__ import annotations

from app.credentials.authenticate import GenericAuth
from app.credentials.domain import CredentialDefinition, TestRequestSpec
from app.credentials.field import FieldDef, FieldKind

definition = CredentialDefinition(
    key="openai",
    display_name="OpenAI",
    icon_id="openai",
    documentation_url="https://platform.openai.com/docs/api-reference/authentication",
    category="llm",
    properties=[
        FieldDef(
            name="api_key",
            display_name="API Key",
            kind=FieldKind.PASSWORD,
            required=True,
            type_options={"password": True},
        ),
        FieldDef(
            name="organization",
            display_name="Organization (optional)",
            kind=FieldKind.STRING,
            required=False,
        ),
    ],
    authenticate=GenericAuth(
        properties={
            "headers": {
                "Authorization": "=Bearer {{ $credentials.api_key }}",
            }
        }
    ),
    test=TestRequestSpec(
        request={
            "method": "GET",
            "url": "https://api.openai.com/v1/models",
        },
        rules=[{"type": "responseCode", "value": 200}],
    ),
)
