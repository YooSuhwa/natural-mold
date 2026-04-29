"""Google Generative AI (Gemini) API key."""

from __future__ import annotations

from app.credentials.authenticate import GenericAuth
from app.credentials.domain import CredentialDefinition, TestRequestSpec
from app.credentials.field import FieldDef, FieldKind

definition = CredentialDefinition(
    key="google_genai",
    display_name="Google Generative AI",
    icon_id="google",
    documentation_url="https://ai.google.dev/api",
    category="llm",
    properties=[
        FieldDef(
            name="api_key",
            display_name="API Key",
            kind=FieldKind.PASSWORD,
            required=True,
            type_options={"password": True},
        ),
    ],
    authenticate=GenericAuth(
        properties={
            "params": {"key": "={{ $credentials.api_key }}"}
        }
    ),
    test=TestRequestSpec(
        request={
            "method": "GET",
            "url": "https://generativelanguage.googleapis.com/v1beta/models",
        },
        rules=[{"type": "responseCode", "value": 200}],
    ),
)
