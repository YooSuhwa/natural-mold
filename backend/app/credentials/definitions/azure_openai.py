"""Azure OpenAI Service."""

from __future__ import annotations

from app.credentials.authenticate import GenericAuth
from app.credentials.domain import CredentialDefinition
from app.credentials.field import FieldDef, FieldKind

definition = CredentialDefinition(
    key="azure_openai",
    display_name="Azure OpenAI",
    icon_id="azure",
    documentation_url="https://learn.microsoft.com/azure/ai-services/openai/reference",
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
            name="endpoint",
            display_name="Endpoint",
            kind=FieldKind.STRING,
            required=True,
            placeholder="https://<your-resource>.openai.azure.com/",
        ),
        FieldDef(
            name="api_version",
            display_name="API Version",
            kind=FieldKind.STRING,
            required=True,
            default="2024-02-15-preview",
        ),
        FieldDef(
            name="deployment",
            display_name="Deployment Name",
            kind=FieldKind.STRING,
            required=True,
        ),
    ],
    authenticate=GenericAuth(
        properties={
            "headers": {
                "api-key": "={{ $credentials.api_key }}",
            },
            "params": {
                "api-version": "={{ $credentials.api_version }}",
            },
        }
    ),
)
