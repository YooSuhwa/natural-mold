from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()  # .env → OS 환경 변수 (LangSmith 등 외부 SDK용)

import os
import ssl
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

# 사내 프록시 SSL 인증서 — certifi CA + HC_SSL.pem 결합 번들 생성
_hc_cert = os.path.expanduser("~/.ssl/HC_SSL.pem")
if os.path.exists(_hc_cert):
    import tempfile

    import certifi

    _combined = tempfile.NamedTemporaryFile(suffix=".pem", delete=False)
    with open(certifi.where(), "rb") as f:
        _combined.write(f.read())
    with open(_hc_cert, "rb") as f:
        _combined.write(b"\n")
        _combined.write(f.read())
    _combined.close()

    os.environ["SSL_CERT_FILE"] = _combined.name
    os.environ["REQUESTS_CA_BUNDLE"] = _combined.name
    ssl_ctx = ssl.create_default_context(cafile=_combined.name)
    ssl._create_default_https_context = lambda: ssl_ctx

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.exceptions import AppError

logger = logging.getLogger(__name__)

from app.config import settings
from app.database import async_session
from app.hooks import register_default_hooks
from app.models.agent_trigger import AgentTrigger
from app.models.model import Model
from app.models.template import Template
from app.models.user import User
from app.scheduler import (
    add_trigger_job,
    get_scheduler,
    register_catalog_update_job,
    register_credential_rotation_job,
    register_health_check_job,
    register_mcp_health_job,
)
from app.seed.bootstrap_from_env import bootstrap_credentials_from_env
from app.seed.default_models import DEFAULT_MODELS
from app.seed.default_templates import DEFAULT_TEMPLATES
from app.services.spend_writer import spend_queue


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup: warn if Cipher V2 keys are unconfigured.
    if not getattr(settings, "encryption_keys", ""):
        logger.warning(
            "ENCRYPTION_KEYS is not set. Credential creation will be rejected. "
            "Configure one or more 64-char hex keys in .env "
            "(see ENCRYPTION_KEYS in .env.example)."
        )

    # Startup: seed default data.
    async with async_session() as db:
        # Ensure mock user exists.
        mock_user_id = uuid.UUID(settings.mock_user_id)
        result = await db.execute(select(User).where(User.id == mock_user_id))
        if not result.scalar_one_or_none():
            db.add(
                User(
                    id=mock_user_id,
                    email=settings.mock_user_email,
                    name=settings.mock_user_name,
                )
            )

        # Seed default models (insert if table empty).
        result = await db.execute(select(Model).limit(1))
        if not result.scalar_one_or_none():
            for model_data in DEFAULT_MODELS:
                # Strip legacy fields the new ``Model`` schema rejects.
                clean = {
                    k: v
                    for k, v in model_data.items()
                    if k not in {"provider_id", "api_key_encrypted"}
                }
                db.add(Model(**clean))

        # Seed default templates (upsert by name).
        existing_tmpl = await db.execute(select(Template.name))
        existing_tmpl_names = {r[0] for r in existing_tmpl.all()}
        for tmpl_data in DEFAULT_TEMPLATES:
            if tmpl_data["name"] not in existing_tmpl_names:
                db.add(Template(**tmpl_data))

        await db.commit()

        # Greenfield env-derived credentials (Cipher V2 encrypted).
        if getattr(settings, "encryption_keys", ""):
            try:
                await bootstrap_credentials_from_env(db, mock_user_id)
                await db.commit()
            except Exception:  # noqa: BLE001 — lifespan boundary
                await db.rollback()
                logger.exception(
                    "bootstrap_credentials_from_env failed — continuing startup."
                )

    # Checkpointer 초기화 — psycopg v3 호환 URL 사용.
    from app.agent_runtime.checkpointer import init_checkpointer

    await init_checkpointer(settings.database_url_sync)

    # Hook framework — register built-in hooks before any runtime call.
    register_default_hooks()

    # Spend writer — drain queue in the background so spend rows accumulate
    # without blocking agent runs. Must start before any hook is invoked.
    await spend_queue.start()

    # Start scheduler and reload active triggers.
    scheduler = get_scheduler()
    scheduler.start()

    # Recurring credential key rotation (re-encrypts rows under stale keys).
    register_credential_rotation_job()
    # Recurring health check for active models / MCP servers.
    register_health_check_job()
    # Recurring multi-source model catalog rebuild (LiteLLM/OpenRouter/llm-prices/pydantic).
    register_catalog_update_job()
    # Lightweight per-server MCP health polling (refreshes health_status only).
    register_mcp_health_job()

    async with async_session() as db:
        result = await db.execute(select(AgentTrigger).where(AgentTrigger.status == "active"))
        for trigger in result.scalars():
            add_trigger_job(trigger.id, trigger.trigger_type, trigger.schedule_config)

    yield
    # Shutdown
    scheduler.shutdown(wait=False)

    # Drain the spend queue so in-flight aggregates make it to the DB before
    # the process exits. ``stop`` swallows its own errors.
    await spend_queue.stop()

    from app.agent_runtime.checkpointer import shutdown_checkpointer

    await shutdown_checkpointer()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Moldy",
        description="AI Agent Builder API",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from app.routers import (
        agents,
        assistant,
        builder,
        conversations,
        credentials,
        health,
        mcp,
        models,
        skills,
        templates,
        tools,
        triggers,
        usage,
    )

    app.include_router(agents.router)
    app.include_router(agents.middleware_router)
    app.include_router(builder.router)
    app.include_router(assistant.router)
    app.include_router(conversations.router)
    app.include_router(credentials.router)
    app.include_router(health.router)
    app.include_router(mcp.router)
    app.include_router(mcp.catalog_router)  # /api/mcp-server-types
    app.include_router(models.router)
    app.include_router(templates.router)
    app.include_router(skills.router)
    app.include_router(tools.router)
    app.include_router(triggers.router)
    app.include_router(usage.router)

    # ---- Exception handlers ----

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "입력값 검증에 실패했습니다",
                    "details": jsonable_encoder(exc.errors()),
                }
            },
        )

    @app.exception_handler(Exception)
    async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "서버 오류가 발생했습니다",
                }
            },
        )

    @app.get("/api/health")
    async def health_check():
        return {"status": "ok"}

    return app


app = create_app()
