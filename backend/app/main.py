from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.database import async_session, Base, engine
from app.models.model import Model
from app.models.template import Template
from app.models.user import User
from app.config import settings
from app.seed.default_models import DEFAULT_MODELS
from app.seed.default_templates import DEFAULT_TEMPLATES

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

        # Seed default templates
        result = await db.execute(select(Template).limit(1))
        if not result.scalar_one_or_none():
            for tmpl_data in DEFAULT_TEMPLATES:
                db.add(Template(**tmpl_data))

        await db.commit()

    yield
    # Shutdown


def create_app() -> FastAPI:
    app = FastAPI(
        title="Moldy",
        description="AI Agent Builder API",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from app.routers import agents, models, templates, tools

    app.include_router(agents.router)
    app.include_router(models.router)
    app.include_router(templates.router)
    app.include_router(tools.router)

    @app.get("/api/health")
    async def health_check():
        return {"status": "ok"}

    return app


app = create_app()
