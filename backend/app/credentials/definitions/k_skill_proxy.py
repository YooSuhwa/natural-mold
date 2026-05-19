"""k-skill proxy — system_dependency credential.

Used by k-skill imported skills that route through an internal proxy
(hosted LLM gateway, scrape relay, etc.). ``base_url`` is required so
the skill knows where to send traffic; ``api_key`` is optional because
some proxies authenticate by IP/mTLS instead.

The marketplace ``credential_requirements[].scope`` for this definition
defaults to ``'system_dependency'`` — published k-skill versions use it
to declare "the proxy is required to run but the *user* doesn't have to
bind a personal credential". The runtime resolves the binding from a
system credential if one exists.
"""

from __future__ import annotations

from app.credentials.domain import CredentialDefinition
from app.credentials.field import FieldDef, FieldKind

definition = CredentialDefinition(
    key="k_skill_proxy",
    display_name="k-skill Proxy",
    icon_id="cloud",
    category="system",
    properties=[
        FieldDef(
            name="base_url",
            display_name="Proxy Base URL",
            kind=FieldKind.STRING,
            required=True,
            description="예: https://k-skill-proxy.example.com",
            placeholder="https://...",
        ),
        FieldDef(
            name="api_key",
            display_name="API Key",
            kind=FieldKind.PASSWORD,
            required=False,
            type_options={"password": True},
            description="프록시가 키 인증을 요구할 때만 입력",
        ),
    ],
)
