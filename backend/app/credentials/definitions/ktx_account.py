"""KTX (Korail) account — login credentials.

Same shape as ``srt_account`` but distinct definition_key so skills can
declare independent requirements when both rail services are needed.
"""

from __future__ import annotations

from app.credentials.domain import CredentialDefinition
from app.credentials.field import FieldDef, FieldKind

definition = CredentialDefinition(
    key="ktx_account",
    display_name="KTX (Korail) Account",
    icon_id="train",
    category="account",
    properties=[
        FieldDef(
            name="username",
            display_name="Username",
            kind=FieldKind.STRING,
            required=True,
            description="Korail 회원번호 또는 이메일",
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
