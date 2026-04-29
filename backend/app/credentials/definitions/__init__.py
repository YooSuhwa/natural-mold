"""Built-in credential definitions — registered into the global registry on import."""

from app.credentials.definitions.anthropic import definition as anthropic
from app.credentials.definitions.azure_openai import definition as azure_openai
from app.credentials.definitions.google_genai import definition as google_genai
from app.credentials.definitions.google_search import definition as google_search
from app.credentials.definitions.google_workspace_oauth2 import (
    definition as google_workspace_oauth2,
)
from app.credentials.definitions.http_api_key import definition as http_api_key
from app.credentials.definitions.http_basic import definition as http_basic
from app.credentials.definitions.http_bearer import definition as http_bearer
from app.credentials.definitions.mcp_oauth2 import definition as mcp_oauth2
from app.credentials.definitions.naver_search import definition as naver_search
from app.credentials.definitions.openai import definition as openai
from app.credentials.registry import registry

for _definition in (
    naver_search,
    google_search,
    google_workspace_oauth2,
    openai,
    anthropic,
    google_genai,
    azure_openai,
    http_bearer,
    http_api_key,
    http_basic,
    mcp_oauth2,
):
    registry.register(_definition)


__all__ = [
    "anthropic",
    "azure_openai",
    "google_genai",
    "google_search",
    "google_workspace_oauth2",
    "http_api_key",
    "http_basic",
    "http_bearer",
    "mcp_oauth2",
    "naver_search",
    "openai",
]
