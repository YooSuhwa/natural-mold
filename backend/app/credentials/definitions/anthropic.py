"""Anthropic API key."""

from __future__ import annotations

from app.credentials.authenticate import GenericAuth
from app.credentials.domain import CredentialDefinition, TestRequestSpec
from app.credentials.field import FieldDef, FieldKind

definition = CredentialDefinition(
    key="anthropic",
    display_name="Anthropic",
    icon_id="anthropic",
    documentation_url="https://docs.anthropic.com/en/api/getting-started",
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
            "headers": {
                "x-api-key": "={{ $credentials.api_key }}",
                "anthropic-version": "2023-06-01",
            }
        }
    ),
    test=TestRequestSpec(
        # ``/v1/models`` is a cheap auth probe — no token consumption, no
        # per-model access gating. The earlier ``/v1/messages`` recipe with
        # a hardcoded ``claude-3-5-haiku-latest`` would 404 / 403 for keys
        # whose org doesn't have the Haiku family enabled, even when the
        # key itself is valid.
        request={
            "method": "GET",
            "url": "https://api.anthropic.com/v1/models",
        },
        rules=[{"type": "responseCode", "value": [200]}],
    ),
)
