"""Built-in credential definitions — registered into the global registry on import."""

from app.credentials.definitions.anthropic import definition as anthropic
from app.credentials.definitions.azure_openai import definition as azure_openai
from app.credentials.definitions.coupang_partners import definition as coupang_partners
from app.credentials.definitions.dart_api import definition as dart_api
from app.credentials.definitions.foresttrip_account import (
    definition as foresttrip_account,
)
from app.credentials.definitions.google_genai import definition as google_genai
from app.credentials.definitions.google_search import definition as google_search
from app.credentials.definitions.google_workspace_oauth2 import (
    definition as google_workspace_oauth2,
)
from app.credentials.definitions.http_api_key import definition as http_api_key
from app.credentials.definitions.http_basic import definition as http_basic
from app.credentials.definitions.http_bearer import definition as http_bearer
from app.credentials.definitions.k_skill_proxy import definition as k_skill_proxy
from app.credentials.definitions.kipris_plus_api import definition as kipris_plus_api
from app.credentials.definitions.ktx_account import definition as ktx_account
from app.credentials.definitions.mcp_oauth2 import definition as mcp_oauth2
from app.credentials.definitions.mcp_secret import definition as mcp_secret
from app.credentials.definitions.naver_search import definition as naver_search
from app.credentials.definitions.odsay_api import definition as odsay_api
from app.credentials.definitions.openai import definition as openai
from app.credentials.definitions.openai_compatible import (
    definition as openai_compatible,
)
from app.credentials.definitions.openrouter import definition as openrouter
from app.credentials.definitions.srt_account import definition as srt_account
from app.credentials.registry import registry

for _definition in (
    naver_search,
    google_search,
    google_workspace_oauth2,
    openai,
    anthropic,
    google_genai,
    azure_openai,
    openrouter,
    openai_compatible,
    http_bearer,
    http_api_key,
    http_basic,
    mcp_secret,
    mcp_oauth2,
    # ADR-017 Slice D — 8 new definitions for marketplace skills.
    srt_account,
    ktx_account,
    foresttrip_account,
    kipris_plus_api,
    dart_api,
    odsay_api,
    coupang_partners,
    k_skill_proxy,
):
    registry.register(_definition)


__all__ = [
    "anthropic",
    "azure_openai",
    "coupang_partners",
    "dart_api",
    "foresttrip_account",
    "google_genai",
    "google_search",
    "google_workspace_oauth2",
    "http_api_key",
    "http_basic",
    "http_bearer",
    "k_skill_proxy",
    "kipris_plus_api",
    "ktx_account",
    "mcp_secret",
    "mcp_oauth2",
    "naver_search",
    "odsay_api",
    "openai",
    "openai_compatible",
    "openrouter",
    "srt_account",
]
