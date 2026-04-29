"""Google Custom Search API."""

from __future__ import annotations

from app.credentials.authenticate import GenericAuth
from app.credentials.domain import CredentialDefinition, TestRequestSpec
from app.credentials.field import FieldDef, FieldKind

definition = CredentialDefinition(
    key="google_search",
    display_name="Google Custom Search",
    icon_id="google",
    documentation_url="https://developers.google.com/custom-search/v1/overview",
    category="search",
    properties=[
        FieldDef(
            name="api_key",
            display_name="API Key",
            kind=FieldKind.PASSWORD,
            required=True,
            type_options={"password": True},
        ),
        FieldDef(
            name="cse_id",
            display_name="Custom Search Engine ID",
            kind=FieldKind.STRING,
            required=True,
        ),
    ],
    authenticate=GenericAuth(
        properties={
            "params": {
                "key": "={{ $credentials.api_key }}",
                "cx": "={{ $credentials.cse_id }}",
            }
        }
    ),
    test=TestRequestSpec(
        request={
            "method": "GET",
            "url": "https://www.googleapis.com/customsearch/v1",
            "params": {"q": "test"},
        },
        rules=[{"type": "responseCode", "value": 200}],
    ),
)
