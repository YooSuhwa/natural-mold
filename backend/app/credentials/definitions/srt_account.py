"""SRT (Super Rapid Train) account — login credentials.

Used by skills that automate booking/lookup on the SRT website. The
upstream skill packages drive Playwright/Selenium with these values, so
the server only stores + injects them as env vars.
"""

from __future__ import annotations

from app.credentials.domain import CredentialDefinition
from app.credentials.field import FieldDef, FieldKind

definition = CredentialDefinition(
    key="srt_account",
    display_name="SRT Account",
    icon_id="train",
    category="account",
    properties=[
        FieldDef(
            name="username",
            display_name="Username",
            kind=FieldKind.STRING,
            required=True,
            description="SRT 회원번호 또는 이메일",
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
