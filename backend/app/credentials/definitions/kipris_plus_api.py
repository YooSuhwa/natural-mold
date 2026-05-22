"""KIPRIS Plus API key — Korean patent search."""

from __future__ import annotations

from app.credentials.domain import CredentialDefinition
from app.credentials.field import FieldDef, FieldKind

definition = CredentialDefinition(
    key="kipris_plus_api",
    display_name="KIPRIS Plus API",
    icon_id="search",
    documentation_url="https://plus.kipris.or.kr/",
    category="api",
    properties=[
        FieldDef(
            name="api_key",
            display_name="API Key",
            kind=FieldKind.PASSWORD,
            required=True,
            type_options={"password": True},
            description="KIPRIS Plus 서비스 키 (ServiceKey)",
        ),
    ],
)
