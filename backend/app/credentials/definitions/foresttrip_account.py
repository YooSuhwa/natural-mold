"""Forest-trip (산림청 휴양림 예약) account — login credentials."""

from __future__ import annotations

from app.credentials.domain import CredentialDefinition
from app.credentials.field import FieldDef, FieldKind

definition = CredentialDefinition(
    key="foresttrip_account",
    display_name="Forest Trip Account",
    icon_id="tree",
    category="account",
    properties=[
        FieldDef(
            name="username",
            display_name="Username",
            kind=FieldKind.STRING,
            required=True,
            description="숲나들e 회원 아이디",
        ),
        FieldDef(
            name="password",
            display_name="Password",
            kind=FieldKind.PASSWORD,
            required=True,
            type_options={"password": True},
        ),
    ],
)
