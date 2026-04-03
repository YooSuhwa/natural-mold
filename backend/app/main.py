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

    # certifi 기본 CA + 사내 CA를 합친 임시 번들 생성
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
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.exceptions import AppError

logger = logging.getLogger(__name__)

from app.config import settings
from app.database import async_session
from app.models.agent_trigger import AgentTrigger
from app.models.model import Model
from app.models.template import Template
from app.models.tool import Tool
from app.models.user import User
from app.scheduler import add_trigger_job, get_scheduler
from app.seed.default_models import DEFAULT_MODELS
from app.seed.default_templates import DEFAULT_TEMPLATES
from app.seed.default_tools import DEFAULT_TOOLS


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup: seed default data
    async with async_session() as db:
        # Ensure mock user exists
        result = await db.execute(select(User).where(User.id == uuid.UUID(settings.mock_user_id)))
        if not result.scalar_one_or_none():
            db.add(
                User(
                    id=uuid.UUID(settings.mock_user_id),
                    email=settings.mock_user_email,
                    name=settings.mock_user_name,
                )
            )

        # Seed default models
        result = await db.execute(select(Model).limit(1))
        if not result.scalar_one_or_none():
            for model_data in DEFAULT_MODELS:
                db.add(Model(**model_data))

        # Seed default templates (upsert by name)
        existing_tmpl = await db.execute(select(Template.name))
        existing_tmpl_names = {r[0] for r in existing_tmpl.all()}
        for tmpl_data in DEFAULT_TEMPLATES:
            if tmpl_data["name"] not in existing_tmpl_names:
                db.add(Template(**tmpl_data))

        # Seed system tools — upsert by name + sync type field
        existing_tools_result = await db.execute(select(Tool).where(Tool.is_system.is_(True)))
        existing_tools_map = {t.name: t for t in existing_tools_result.scalars().all()}

        for tool_data in DEFAULT_TOOLS:
            existing = existing_tools_map.get(tool_data["name"])
            if not existing:
                db.add(Tool(**tool_data))
            elif existing.type != tool_data["type"]:
                existing.type = tool_data["type"]

        await db.commit()

    # Start scheduler and reload active triggers
    scheduler = get_scheduler()
    scheduler.start()

    async with async_session() as db:
        result = await db.execute(select(AgentTrigger).where(AgentTrigger.status == "active"))
        for trigger in result.scalars():
            add_trigger_job(trigger.id, trigger.trigger_type, trigger.schedule_config)

    yield
    # Shutdown
    scheduler.shutdown(wait=False)


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
        agent_creation,
        agents,
        conversations,
        models,
        templates,
        tools,
        triggers,
        usage,
    )

    app.include_router(agents.router)
    app.include_router(agent_creation.router)
    app.include_router(conversations.router)
    app.include_router(models.router)
    app.include_router(templates.router)
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
                    "details": exc.errors(),
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
