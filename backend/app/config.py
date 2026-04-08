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

    # Google Workspace OAuth2 (Gmail, Calendar)
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_refresh_token: str = ""

    # Mock user (PoC: no auth)
    mock_user_id: str = "00000000-0000-0000-0000-000000000001"
    mock_user_email: str = "demo@moldy.dev"
    mock_user_name: str = "Demo User"

    # Encryption key for API keys in DB (Fernet)
    encryption_key: str = ""

    # MCP
    mcp_connection_timeout: int = 10
    tool_call_timeout: int = 30

    # Skills (package)
    skill_storage_dir: str = "./data/skills"
    skill_max_package_bytes: int = 52428800

    # Conversation outputs
    conversation_output_dir: str = "./data/conversations"

    # Builder / Assistant sub-agent model defaults (서비스 내부용)
    builder_model_provider: str = "openai"
    builder_model_name: str = "gpt-5.4-mini"
    assistant_model_provider: str = "openai"
    assistant_model_name: str = "gpt-5.4-mini"

    # 에이전트 생성 시 기본 모델 (사용자 에이전트용, DB Model.display_name 또는 provider:model_name)
    # 비어있으면 DB의 is_default 모델 → 첫 번째 모델 순서로 fallback
    default_agent_model: str = ""

    # Agent image generation (OpenRouter + Gemini Flash Image)
    image_gen_api_key: str = ""
    image_gen_base_url: str = "https://openrouter.ai/api/v1"
    image_gen_model: str = "google/gemini-3.1-flash-image-preview"
    agent_image_dir: str = "./data/agents"


settings = Settings()
