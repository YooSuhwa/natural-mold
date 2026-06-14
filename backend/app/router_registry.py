from __future__ import annotations

from fastapi import FastAPI

from app.config import settings


def include_app_routers(app: FastAPI) -> None:
    from app.routers import (
        agent_api,
        agent_blueprints,
        agent_runtime_api,
        agents,
        artifacts,
        assistant,
        audit,
        auth,
        builder,
        conversations,
        credentials,
        feedback,
        health,
        marketplace,
        mcp,
        memory,
        models,
        shares,
        skills,
        system_llm_settings,
        templates,
        tools,
        triggers,
        uploads,
        usage,
    )

    app.include_router(audit.router)
    app.include_router(auth.router)
    app.include_router(agent_blueprints.router)
    app.include_router(agent_api.router)
    app.include_router(agent_runtime_api.router)
    app.include_router(agents.router)
    app.include_router(agents.middleware_router)
    app.include_router(artifacts.router)
    app.include_router(builder.router)
    app.include_router(assistant.router)
    app.include_router(conversations.router)
    app.include_router(credentials.router)
    app.include_router(health.router)
    app.include_router(marketplace.router)
    app.include_router(memory.router)
    app.include_router(mcp.router)
    app.include_router(mcp.catalog_router)
    app.include_router(models.router)
    app.include_router(shares.router)
    app.include_router(templates.router)
    app.include_router(skills.router)
    app.include_router(system_llm_settings.router)
    app.include_router(tools.router)
    app.include_router(triggers.router)
    app.include_router(uploads.router)
    app.include_router(feedback.router)
    app.include_router(usage.router)

    if settings.e2e_test_helpers_enabled:
        from app.routers import e2e_chat_run_helpers

        app.include_router(e2e_chat_run_helpers.router)
