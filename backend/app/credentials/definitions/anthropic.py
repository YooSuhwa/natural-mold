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
        request={
            "method": "POST",
            "url": "https://api.anthropic.com/v1/messages",
            "headers": {"content-type": "application/json"},
            "json": {
                "model": "claude-3-5-haiku-latest",
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}],
            },
        },
        # 200 = OK, 400 = bad request but auth ok (e.g. unknown model on org)
        rules=[{"type": "responseCode", "value": [200, 400]}],
    ),
)
