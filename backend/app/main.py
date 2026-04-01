from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup
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

    # Routers will be added here as they are implemented
    # from app.routers import agents, tools, models, templates, conversations, usage
    # app.include_router(agents.router)

    @app.get("/api/health")
    async def health_check():
        return {"status": "ok"}

    return app


app = create_app()
