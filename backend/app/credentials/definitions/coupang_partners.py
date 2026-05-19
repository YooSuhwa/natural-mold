"""Coupang Partners API — affiliate-link generation.

Optional credential: skills can be installed without binding this. The
runtime treats a missing binding as "user skipped affiliate enrichment"
rather than blocking install (Spec §0.1 / progress.txt gotcha).
"""

from __future__ import annotations

from app.credentials.domain import CredentialDefinition
from app.credentials.field import FieldDef, FieldKind

definition = CredentialDefinition(
    key="coupang_partners",
    display_name="Coupang Partners",
    icon_id="shopping",
    documentation_url="https://partners.coupang.com/",
    category="api",
    properties=[
        FieldDef(
            name="access_key",
            display_name="Access Key",
            kind=FieldKind.STRING,
            required=True,
            description="Coupang Partners Access Key",
        ),
        FieldDef(
            name="secret_key",
            display_name="Secret Key",
            kind=FieldKind.PASSWORD,
            required=True,
            type_options={"password": True},
        ),
    ],
)
