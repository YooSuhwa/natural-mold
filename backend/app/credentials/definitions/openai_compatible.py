"""OpenAI-compatible API endpoint (self-hosted, gateway, vLLM, Ollama, ...).

Use this credential when the target service speaks the OpenAI HTTP surface but
is not on the official ``api.openai.com`` host. The user supplies the base URL
explicitly; the API key is optional because some local deployments accept
unauthenticated requests.
"""

from __future__ import annotations

from app.credentials.authenticate import GenericAuth
from app.credentials.domain import CredentialDefinition, TestRequestSpec
from app.credentials.field import FieldDef, FieldKind

definition = CredentialDefinition(
    key="openai_compatible",
    display_name="OpenAI Compatible Endpoint",
    icon_id="plug",
    documentation_url=(
        "https://platform.openai.com/docs/api-reference/models/list"
    ),
    category="llm",
    properties=[
        FieldDef(
            name="base_url",
            display_name="Base URL",
            kind=FieldKind.STRING,
            required=True,
            placeholder="https://your-host/v1",
            description=(
                "Full URL up to and including the API version segment "
                "(e.g. https://your-host/v1)."
            ),
        ),
        FieldDef(
            name="api_key",
            display_name="API Key (optional)",
            kind=FieldKind.PASSWORD,
            required=False,
            type_options={"password": True},
            description=(
                "Bearer token for the endpoint. Leave blank for unauthenticated "
                "deployments."
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
            "url": "={{ $credentials.base_url }}/models",
        },
        rules=[{"type": "responseCode", "value": [200, 401, 403]}],
    ),
)
