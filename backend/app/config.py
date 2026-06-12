from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Runtime mode. ``production`` triggers strict boot-time validation
    # of auth/cookie/CORS/secret settings (see app.security.production_check).
    # Default ``dev`` only warns so the local workflow stays frictionless.
    app_env: Literal["dev", "production"] = "dev"

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

    # Tavily Search (hosted backend key for Deep Research)
    tavily_api_key: str = ""

    # Google Chat Webhook
    google_chat_webhook_url: str = ""

    # Google Workspace OAuth2 (Gmail, Calendar)
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_refresh_token: str = ""

    # Encryption key for API keys in DB (Fernet — legacy, removed in M5)
    encryption_key: str = ""

    # Cipher V2 — comma-separated 64-char hex keys. First is active.
    # Boot fails if empty (see app/security/key_provider.py).
    encryption_keys: str = ""

    # External Secrets (Vault)
    external_secrets_enabled: bool = False
    vault_url: str = ""
    vault_token: str = ""
    vault_kv_mount: str = "secret"

    # Credential rotation cron (APScheduler crontab format; default: weekly Sun 03:00)
    credential_rotation_cron: str = "0 3 * * 0"

    # Daily health check sweep (APScheduler crontab format; default: 04:00 UTC)
    health_check_cron: str = "0 4 * * *"
    health_check_concurrency: int = 4
    # Daily refresh-token GC (APScheduler crontab; default: 05:00 UTC).
    # Deletes ``refresh_tokens`` rows whose ``expires_at`` is older than the
    # retention window — keeps replay history available for that window so
    # a barely-expired hash still classifies correctly. ADR-016 §4.2.
    refresh_token_gc_cron: str = "0 5 * * *"
    refresh_token_gc_retention_days: int = 1
    # Model catalog refresh (APScheduler crontab format; default: every 6 hours)
    catalog_update_cron: str = "0 */6 * * *"
    # Retention window for ``health_check_history`` rows. The cleanup job is a
    # follow-up; the value is wired now so deployments can configure ahead.
    health_check_history_retention_days: int = 90

    # MCP
    mcp_connection_timeout: int = 10
    tool_call_timeout: int = 30
    # Interval (minutes) for the lightweight per-server health polling job
    # registered by ``scheduler.register_mcp_health_job``.
    mcp_health_check_interval_minutes: int = 5

    # ADR-018 — single root for all on-disk data. ``skills.storage_path`` and
    # ``marketplace_versions.storage_path`` store paths *relative to this root*
    # so they remain portable across worktrees and deploy targets. All other
    # ``*_dir`` settings default to subdirectories under this root.
    data_root: str = "./data"

    # Skills (package)
    skill_storage_dir: str = "./data/skills"
    skill_max_package_bytes: int = 52428800
    # Skills (JavaScript package runner). Skill scripts run without a shell;
    # this only points the subprocess runner at a Node binary and shared
    # dependency directory for built-in document-generation skills.
    skill_node_binary: str = "node"
    skill_node_modules_dir: str = "./skill-node/node_modules"

    # ADR-017 Slice F — k-skill upstream import (super_user CLI only).
    # ``k_skill_sync_dir`` is the local git working tree the importer
    # ``git clone`` / ``git fetch`` mirrors into; ``k_skill_builtin_storage_dir``
    # is where successfully imported package directories land (used as
    # ``MarketplaceVersion.storage_path`` for ``is_system=True`` items).
    # Both paths are local-only — no remote write back to upstream.
    k_skill_upstream_url: str = "https://github.com/NomaDamas/k-skill.git"
    k_skill_upstream_ref: str = "main"
    k_skill_sync_dir: str = "./data/upstreams/k-skill"
    k_skill_builtin_storage_dir: str = "./data/marketplace/k-skill"

    # Conversation outputs
    conversation_output_dir: str = "./data/conversations"

    # Chat message uploads (P1-7 attachments). Files served via
    # /api/uploads/{id}; max bytes default 20 MiB.
    upload_dir: str = "./data/uploads"
    upload_max_bytes: int = 20 * 1024 * 1024

    # Agent/runtime generated artifacts. The first integrated release uses
    # local disk; S3/MinIO can be added behind the storage backend without
    # changing API URLs.
    artifact_storage_backend: Literal["local", "s3"] = "local"
    artifact_storage_dir: str = "./data/artifacts"
    artifact_max_bytes: int = 100 * 1024 * 1024
    artifact_max_files_per_run: int = 100
    artifact_preview_max_text_bytes: int = 1 * 1024 * 1024
    artifact_s3_endpoint_url: str = ""
    artifact_s3_bucket: str = ""
    artifact_s3_access_key_id: str = ""
    artifact_s3_secret_access_key: str = ""

    # Builder / Assistant sub-agent model defaults (서비스 내부용)
    builder_model_provider: str = "anthropic"
    builder_model_name: str = "claude-sonnet-4-6"
    builder_fallback_provider: str = "openai"
    builder_fallback_name: str = "gpt-5.4"
    assistant_model_provider: str = "anthropic"
    assistant_model_name: str = "claude-sonnet-4-6"

    # 에이전트 생성 시 기본 모델 (사용자 에이전트용, DB Model.display_name 또는 provider:model_name)
    # 비어있으면 DB의 is_default 모델 → 첫 번째 모델 순서로 fallback
    default_agent_model: str = ""

    # Agent image generation (OpenRouter + Gemini Flash Image)
    openrouter_api_key: str = ""
    image_gen_base_url: str = "https://openrouter.ai/api/v1"
    image_gen_model: str = "google/gemini-3.1-flash-image-preview"
    agent_image_dir: str = "./data/agents"
    user_avatar_dir: str = "./data/users"

    # Rate limiting (slowapi). Public endpoints (/api/shares/{token}*) are
    # auth-free + walk LangGraph state, so cap per-IP throughput. Disable in
    # tests to avoid coupling timing to assertions.
    rate_limit_enabled: bool = True
    share_public_rate_limit: str = "60/minute"

    # Snapshot cache for public share GETs. Keyed by
    # ``(token, conversation.active_branch_checkpoint_id)`` — bumping the
    # active branch (new turn / fork) yields a new cache key automatically.
    share_snapshot_cache_ttl_s: float = 60.0
    share_snapshot_cache_max: int = 1024

    # Langfuse trace debugger POC. When unset, tracing auto-enables if the
    # public key, secret key, and base URL are configured. Set false to force
    # the message_events fallback even when credentials exist.
    langfuse_enabled: bool | None = None
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = ""
    langfuse_project: str = "moldy"
    langfuse_capture_input_output: bool = True
    langfuse_redaction_enabled: bool = True
    langfuse_sample_rate: float = 1.0

    # ----- ADR-016: multi-user auth -----
    # JWT signing secret. MUST be set in production (>= 32 bytes, random).
    # If empty in dev, ``app.auth.jwt`` generates an ephemeral key at import
    # time + emits a warning — tokens won't survive a process restart.
    jwt_secret: str = ""
    # HMAC signing secret for external Agent API key hashes. Production should
    # set this independently; dev falls back to JWT_SECRET for local ergonomics.
    api_key_hash_secret: str | None = None
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30
    # Grace window after a refresh rotation during which a re-presented
    # (already-rotated) token is treated as a tab-race rather than a
    # replay attack — but only when the replacement is still active AND
    # the originating user-agent matches. Outside this window, or with
    # a UA mismatch, replay detection fires (mass-revoke). See
    # auth_service.rotate_refresh.
    refresh_rotation_grace_seconds: int = 10
    csrf_token_expire_minutes: int = 60
    cookie_name_access: str = "moldy_at"
    cookie_name_refresh: str = "moldy_rt"
    cookie_name_csrf: str = "moldy_csrf"
    # In dev (HTTP) cookies must NOT be Secure or browsers reject them.
    # Production deployments MUST set ``cookie_secure=true``.
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_domain: str | None = None
    # First user signing up is granted ``is_super_user=true`` — convenient
    # for bootstrapping. Toggle off (``false``) immediately after the
    # operator account exists in production. See ADR-016 §8.4.
    allow_first_user_as_admin: bool = True

    # Local Playwright E2E bootstrap account. Disabled by default in code so
    # tests and production are not affected unless env explicitly opts in.
    e2e_seed_user_enabled: bool = False
    e2e_user_email: str = "playwright-e2e@moldy.dev"
    e2e_user_password: str = "correct horse battery staple 42"
    e2e_user_name: str = "E2E User"
    e2e_scripted_model_enabled: bool = False
    e2e_test_helpers_enabled: bool = False
    # E2E real-LLM provisioning (OpenAI-compatible, e.g. LiteLLM). When all three
    # are set, ``seed.e2e_llm`` creates a system credential + Model + System LLM
    # ``text_primary``/``text_fallback`` so the Builder/Assistant/subagent/chat
    # flows run against a real endpoint. Empty = keyless (scripted model only).
    e2e_llm_base_url: str = ""
    e2e_llm_api_key: str = ""
    e2e_llm_model: str = ""
    chat_run_stale_after_seconds: int = 300
    chat_run_stale_sweep_interval_seconds: int = 60

    # CORS allowed origins. Comma-separated origins, e.g.
    # "https://moldy.dev,https://staging.moldy.dev". Default permits the
    # local dev frontend (Next 16 on :3000). ``allow_credentials=true`` is
    # required for HttpOnly cookie auth so wildcard ("*") is rejected.
    cors_allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]


settings = Settings()
