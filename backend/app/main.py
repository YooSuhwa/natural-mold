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
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError

logger = logging.getLogger(__name__)

from app.config import settings
from app.database import async_session
from app.models.agent_trigger import AgentTrigger
from app.models.llm_provider import LLMProvider
from app.models.model import Model
from app.models.template import Template
from app.models.tool import Tool
from app.models.user import User
from app.scheduler import add_trigger_job, get_scheduler
from app.seed.default_models import DEFAULT_MODELS
from app.seed.default_providers import DEFAULT_PROVIDERS
from app.seed.default_templates import DEFAULT_TEMPLATES
from app.seed.default_tools import DEFAULT_TOOLS


async def _enforce_m6_legacy_invariants(db: AsyncSession) -> None:
    """Block startup if m12-preflight invariants are violated on pre-m12 schema.

    information_schema는 PostgreSQL 전용. sqlite 테스트 환경에서는 조용히
    건너뛴다 (conftest가 최신 모델로 테이블을 만들기 때문에 legacy 컬럼이
    아예 없음).
    """
    import os

    from sqlalchemy import text as sa_text

    from app.services.legacy_invariants import collect_legacy_checks

    bypass = os.environ.get("ALLOW_DIRTY_AGENT_TOOLS_CONFIG") == "1"

    try:
        async def column_exists_async(table: str, column: str) -> bool:
            result = await db.execute(
                sa_text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = :table AND column_name = :column"
                ),
                {"table": table, "column": column},
            )
            return result.first() is not None

        cache: dict[tuple[str, str], bool] = {}
        for table, column in [
            ("agent_tools", "config"),
            ("tools", "credential_id"),
            ("tools", "auth_config"),
            ("tools", "mcp_server_id"),
        ]:
            cache[(table, column)] = await column_exists_async(table, column)

        checks = collect_legacy_checks(
            dialect=db.bind.dialect.name if db.bind else "postgresql",
            column_exists=lambda table, column: cache.get((table, column), False),
        )

        errors: list[str] = []
        for label, sql in checks:
            count = (await db.execute(sa_text(sql))).scalar() or 0
            if count:
                errors.append(f"  - {label}: {count} row(s)")
    except Exception:  # noqa: BLE001
        # information_schema 미지원 dialect (sqlite 등) — 프로덕션 외 경로.
        return

    if not errors:
        return

    if bypass:
        logger.warning(
            "M6 deploy-order bypass: legacy auth rows detected but "
            "ALLOW_DIRTY_AGENT_TOOLS_CONFIG=1 — continuing startup with "
            "silent behavior changes:\n%s\nEnsure migration runs "
            "immediately after.",
            "\n".join(errors),
        )
        return

    raise RuntimeError(
        "M6 deploy-order error — legacy auth rows detected on a schema "
        "that still predates m12. Serving traffic now would silently "
        "change or fail tool auth. Apply "
        "`alembic upgrade m12_drop_legacy_columns` (or backfill) before "
        "starting this build. Dirty rows:\n"
        + "\n".join(errors)
        + "\nEmergency bypass: ALLOW_DIRTY_AGENT_TOOLS_CONFIG=1."
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup: warn if encryption is unconfigured (credentials cannot be created)
    if not settings.encryption_key:
        logger.warning(
            "ENCRYPTION_KEY is not set. Credential creation will be rejected "
            "and any existing API keys in DB are stored as plaintext. "
            "Generate a key with `python -c \"from cryptography.fernet import "
            "Fernet; print(Fernet.generate_key().decode())\"` and set it in .env.",
        )

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

        # Seed default providers (upsert by provider_type)
        existing_providers = await db.execute(select(LLMProvider.provider_type))
        existing_provider_types = {r[0] for r in existing_providers.all()}
        for prov_data in DEFAULT_PROVIDERS:
            if prov_data["provider_type"] not in existing_provider_types:
                db.add(LLMProvider(**prov_data))

        # Seed default models
        result = await db.execute(select(Model).limit(1))
        if not result.scalar_one_or_none():
            # Map provider_type → provider_id for linking
            provider_map_result = await db.execute(
                select(LLMProvider.id, LLMProvider.provider_type)
            )
            ptype_to_id = {r[1]: r[0] for r in provider_map_result.all()}
            for model_data in DEFAULT_MODELS:
                pid = ptype_to_id.get(model_data.get("provider"))
                if pid:
                    model_data = {**model_data, "provider_id": pid}
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
            else:
                if existing.type != tool_data["type"]:
                    existing.type = tool_data["type"]
                if tool_data.get("tags") and existing.tags != tool_data["tags"]:
                    existing.tags = tool_data["tags"]

        await db.commit()

        # Seed mock user의 PREBUILT default connections (env → credential → connection).
        # Alembic m10에서 실행하던 시드를 lifespan으로 이동 — migration은 mock user가
        # 생성되기 전에 실행되므로 silent skip이 발생하고 Alembic이 revision을 applied로
        # 마킹해 재시도 경로가 사라졌다. 여기서 실행하면 매 기동마다 idempotent하게
        # 재시도하고, mock user 시드와의 순서가 보장된다.
        #
        # 실패 시에도 startup을 blocking하지 않는다 (env fallback 경로가 살아 있으므로
        # seed 실패 = UI에 connection이 없어 보이는 degrade일 뿐). 특정 provider race는
        # 함수 내부 SAVEPOINT로 이미 처리되므로 여기선 방어선(정말 예기치 못한 예외).
        from app.seed.prebuilt_connections import seed_mock_user_prebuilt_connections

        try:
            await seed_mock_user_prebuilt_connections(db)
            await db.commit()
        except Exception:  # noqa: BLE001 — lifespan 경계, startup 보호
            await db.rollback()
            # traceback 포함 로그 — seed 실패 원인을 잃지 않고 startup은 계속
            # 진행한다 (env fallback으로 runtime 정상 동작).
            logger.exception(
                "Prebuilt connection seed failed — continuing startup. "
                "Runtime env fallback remains active."
            )

        # M6 deploy-order guard: 새 runtime은 legacy auth 경로를 모두 제거했으므로
        # m12 preflight와 동일한 invariant를 startup에서도 강제한다. dirty row가
        # 남아있는 상태로 트래픽을 받으면 silent wrong-secret / fail-closed 발생.
        # 긴급 bypass: ALLOW_DIRTY_AGENT_TOOLS_CONFIG=1.
        await _enforce_m6_legacy_invariants(db)

    # Checkpointer 초기화 — psycopg v3 호환 URL 사용
    from app.agent_runtime.checkpointer import init_checkpointer

    await init_checkpointer(settings.database_url_sync)

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
        connections,
        conversations,
        credentials,
        models,
        providers,
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
    app.include_router(connections.router)
    app.include_router(conversations.router)
    app.include_router(credentials.router)
    app.include_router(providers.router)
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
        # Pydantic의 ctx.error(ValueError 객체)는 JSON 직렬화 불가 →
        # jsonable_encoder로 정리. field/model_validator가 ValueError를
        # raise하는 모든 422 경로에서 500이 나는 잠복 버그(베조스 S4 /
        # Codex adversarial Finding 1)를 해소.
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
