"""Naver Open API — Search."""

from __future__ import annotations

from app.credentials.authenticate import GenericAuth
from app.credentials.domain import CredentialDefinition, TestRequestSpec
from app.credentials.field import FieldDef, FieldKind

definition = CredentialDefinition(
    key="naver_search",
    display_name="Naver Search",
    icon_id="naver",
    documentation_url="https://developers.naver.com/docs/serviceapi/search/blog/blog.md",
    category="search",
    properties=[
        FieldDef(
            name="client_id",
            display_name="Client ID",
            kind=FieldKind.STRING,
            required=True,
        ),
        FieldDef(
            name="client_secret",
            display_name="Client Secret",
            kind=FieldKind.PASSWORD,
            required=True,
            type_options={"password": True},
        ),
    ],
    authenticate=GenericAuth(
        properties={
            "headers": {
                "X-Naver-Client-Id": "={{ $credentials.client_id }}",
                "X-Naver-Client-Secret": "={{ $credentials.client_secret }}",
            }
        }
    ),
    test=TestRequestSpec(
        request={
            "method": "GET",
            "url": "https://openapi.naver.com/v1/search/blog.json",
            "params": {"query": "test", "display": 1},
        },
        rules=[{"type": "responseCode", "value": 200}],
    ),
)
