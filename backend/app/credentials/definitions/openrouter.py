"""OpenRouter API key.

OpenRouter is an LLM gateway that fronts dozens of provider APIs behind a
single OpenAI-compatible surface. The credential carries an API key plus an
optional base URL override (defaulted to the public host).
"""

from __future__ import annotations

from app.credentials.authenticate import GenericAuth
from app.credentials.domain import CredentialDefinition, TestRequestSpec
from app.credentials.field import FieldDef, FieldKind

definition = CredentialDefinition(
    key="openrouter",
    display_name="OpenRouter",
    icon_id="openrouter",
    documentation_url="https://openrouter.ai/docs/api-reference",
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
            name="base_url",
            display_name="Base URL",
            kind=FieldKind.STRING,
            required=False,
            default="https://openrouter.ai/api/v1",
            placeholder="https://openrouter.ai/api/v1",
            description=(
                "Override the OpenRouter endpoint. Leave blank for the public host."
            ),
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
            "url": "https://openrouter.ai/api/v1/auth/key",
        },
        rules=[{"type": "responseCode", "value": 200}],
    ),
)
