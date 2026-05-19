"""ODsay (대중교통 길찾기) API key."""

from __future__ import annotations

from app.credentials.domain import CredentialDefinition
from app.credentials.field import FieldDef, FieldKind

definition = CredentialDefinition(
    key="odsay_api",
    display_name="ODsay API",
    icon_id="route",
    documentation_url="https://lab.odsay.com/",
    category="api",
    properties=[
        FieldDef(
            name="api_key",
            display_name="API Key",
            kind=FieldKind.PASSWORD,
            required=True,
            type_options={"password": True},
            description="ODsay LAB 발급 apiKey",
        ),
    ],
)
