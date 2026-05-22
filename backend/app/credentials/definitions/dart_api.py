"""DART (전자공시) Open API key."""

from __future__ import annotations

from app.credentials.domain import CredentialDefinition
from app.credentials.field import FieldDef, FieldKind

definition = CredentialDefinition(
    key="dart_api",
    display_name="DART Open API",
    icon_id="document",
    documentation_url="https://opendart.fss.or.kr/",
    category="api",
    properties=[
        FieldDef(
            name="api_key",
            display_name="API Key",
            kind=FieldKind.PASSWORD,
            required=True,
            type_options={"password": True},
            description="OpenDART 인증키 (crtfc_key)",
        ),
    ],
)
