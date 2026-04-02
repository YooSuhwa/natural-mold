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
    import certifi
    import tempfile

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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.database import async_session
from app.models.model import Model
from app.models.template import Template
from app.models.tool import Tool
from app.models.agent_trigger import AgentTrigger
from app.models.user import User
from app.config import settings
from app.scheduler import get_scheduler, add_trigger_job
from app.seed.default_models import DEFAULT_MODELS
from app.seed.default_templates import DEFAULT_TEMPLATES
from app.seed.default_tools import DEFAULT_TOOLS

import uuid


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup: seed default data
    async with async_session() as db:
        # Ensure mock user exists
        result = await db.execute(
            select(User).where(User.id == uuid.UUID(settings.mock_user_id))
        )
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
        existing_tools_result = await db.execute(
            select(Tool).where(Tool.is_system.is_(True))
        )
        existing_tools_map = {t.name: t for t in existing_tools_result.scalars().all()}

        for tool_data in DEFAULT_TOOLS:
            existing = existing_tools_map.get(tool_data["name"])
            if not existing:
                db.add(Tool(**tool_data))
            else:
                if existing.type != tool_data["type"]:
                    existing.type = tool_data["type"]
                if tool_data.get("tags") and existing.tags != tool_data["tags"]:
                    existing.tags = tool_data["tags"]

        await db.commit()

    # Start scheduler and reload active triggers
    scheduler = get_scheduler()
    scheduler.start()

    async with async_session() as db:
        result = await db.execute(
            select(AgentTrigger).where(AgentTrigger.status == "active")
        )
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

    from app.routers import agent_creation, agents, conversations, fix_agent, models, templates, tools, triggers, usage

    app.include_router(agents.router)
    app.include_router(agent_creation.router)
    app.include_router(fix_agent.router)
    app.include_router(conversations.router)
    app.include_router(models.router)
    app.include_router(templates.router)
    app.include_router(tools.router)
    app.include_router(triggers.router)
    app.include_router(usage.router)

    @app.get("/api/health")
    async def health_check():
        return {"status": "ok"}

    return app


app = create_app()
