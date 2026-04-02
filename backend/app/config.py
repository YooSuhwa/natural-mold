from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://moldy:moldy@localhost:5432/moldy"
    database_url_sync: str = "postgresql://moldy:moldy@localhost:5432/moldy"

    # LLM API Keys
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    google_api_key: str = ""

    # Naver Open API
    naver_client_id: str = ""
    naver_client_secret: str = ""

    # Google Custom Search
    google_cse_id: str = ""

    # Google Chat Webhook
    google_chat_webhook_url: str = ""

    # Mock user (PoC: no auth)
    mock_user_id: str = "00000000-0000-0000-0000-000000000001"
    mock_user_email: str = "demo@moldy.dev"
    mock_user_name: str = "Demo User"

    # Encryption key for API keys in DB (Fernet)
    encryption_key: str = ""

    # MCP
    mcp_connection_timeout: int = 10
    tool_call_timeout: int = 30


settings = Settings()
