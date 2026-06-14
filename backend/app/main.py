from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()  # .env → OS 환경 변수 (LangSmith 등 외부 SDK용)

import os
import ssl
import uuid
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

# Root logger 설정 — uvicorn 자체 logger 외 ``app.*`` logger 의 INFO/WARNING/
# ERROR 가 stdout 으로 도달하도록 한다. 미설정 시 ``logger.info()`` 호출이
# silent 하게 사라져 진단이 불가능 (e.g. credential resolution 분기 로그,
# stream_agent_response 의 partial flush 실패 로그).
#
# 운영 환경의 structured logger / log shipper 설정을 덮어쓰지 않도록 root
# handler 가 *비어있을 때만* basicConfig 적용. uvicorn ``--log-config`` /
# JSON formatter / OpenTelemetry handler 가 사전 등록된 경우 우회한다.
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.exception_handlers import register_exception_handlers

logger = logging.getLogger(__name__)

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.database import async_session
from app.hooks import register_default_hooks
from app.models.agent_trigger import AgentTrigger
from app.models.model import Model
from app.models.template import Template
from app.rate_limit import limiter
from app.scheduler import (
    add_trigger_job,
    cleanup_skill_runtime_roots,
    get_scheduler,
    register_broker_eviction_job,
    register_catalog_update_job,
    register_conversation_run_stale_sweep_job,
    register_credential_rotation_job,
    register_health_check_job,
    register_mcp_health_job,
    register_refresh_token_gc_job,
    register_skill_runtime_cleanup_job,
    release_scheduler_leader,
    sweep_stale_conversation_runs,
    try_acquire_scheduler_leader,
)
from app.security.production_check import enforce_production_safety
from app.seed.bootstrap_from_env import bootstrap_system_credentials
from app.seed.default_marketplace_skills import seed_default_marketplace_skills
from app.seed.default_models import DEFAULT_MODELS
from app.seed.default_templates import DEFAULT_TEMPLATES
from app.seed.e2e_llm import seed_e2e_llm
from app.seed.e2e_scripted_model import seed_e2e_scripted_model
from app.seed.e2e_user import seed_e2e_user
from app.services.spend_writer import spend_queue


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Refuse boot on insecure prod config; emit dev hints otherwise.
    # Covers JWT secret, cookie Secure flag, first-user-admin toggle,
    # CORS origins, encryption keys — all the things that are safe
    # locally but catastrophic if shipped with their defaults.
    enforce_production_safety(settings)

    # Startup: seed default data.
    async with async_session() as db:
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

        try:
            await seed_e2e_user(db)
            await db.commit()
        except Exception:  # noqa: BLE001 — local e2e seed is non-fatal
            await db.rollback()
            logger.exception("seed_e2e_user failed — continuing startup.")

        try:
            await seed_e2e_scripted_model(db)
            await db.commit()
        except Exception:  # noqa: BLE001 — local e2e model seed is non-fatal
            await db.rollback()
            logger.exception("seed_e2e_scripted_model failed — continuing startup.")

        try:
            await seed_e2e_llm(db)
            await db.commit()
        except Exception:  # noqa: BLE001 — local e2e LLM seed is non-fatal
            await db.rollback()
            logger.exception("seed_e2e_llm failed — continuing startup.")

        try:
            await seed_default_marketplace_skills(db)
            await db.commit()
        except Exception:  # noqa: BLE001 — default marketplace seed is non-fatal
            await db.rollback()
            logger.exception("seed_default_marketplace_skills failed — continuing startup.")

        # Operator-managed system credentials seeded from env (Cipher V2). Stored
        # as ``is_system=True, user_id=NULL`` so they survive every user's
        # lifecycle and surface only to super_user-gated endpoints.
        if getattr(settings, "encryption_keys", ""):
            try:
                await bootstrap_system_credentials(db)
                await db.commit()
            except Exception:  # noqa: BLE001 — lifespan boundary
                await db.rollback()
                logger.exception("bootstrap_system_credentials failed — continuing startup.")

        # ADR-013 — sync builder/assistant `_ENV_FALLBACK` from credentials
        # so user-registered LLM keys (POST /api/credentials) are visible to
        # the builder helper without a server restart. ``.env`` keys keep
        # priority; this only fills empty slots.
        from app.agent_runtime.model_factory import (
            sync_env_fallback_from_credentials,
        )

        try:
            await sync_env_fallback_from_credentials(db)
        except Exception:  # noqa: BLE001 — lifespan boundary
            logger.exception("sync_env_fallback_from_credentials failed — continuing startup.")

    # Checkpointer 초기화 — psycopg v3 호환 URL 사용.
    from app.agent_runtime.checkpointer import init_checkpointer

    await init_checkpointer(
        settings.database_url_sync,
        min_size=settings.checkpointer_pool_min_size,
        max_size=settings.checkpointer_pool_max_size,
    )

    # Hook framework — register built-in hooks before any runtime call.
    register_default_hooks()

    await sweep_stale_conversation_runs()

    # Spend writer — drain queue in the background so spend rows accumulate
    # without blocking agent runs. Must start before any hook is invoked.
    await spend_queue.start()

    # Start scheduler and reload active triggers. In multi-process deploys,
    # only the process holding the Postgres advisory lock registers jobs.
    scheduler = get_scheduler()
    scheduler_is_leader = await try_acquire_scheduler_leader()
    if scheduler_is_leader:
        scheduler.start()

        # Recurring credential key rotation (re-encrypts rows under stale keys).
        register_credential_rotation_job()
        # Recurring health check for active models / MCP servers.
        register_health_check_job()
        # Recurring multi-source model catalog rebuild.
        register_catalog_update_job()
        # Lightweight per-server MCP health polling (refreshes health_status only).
        register_mcp_health_job()
        # W3-out M4 — EventBroker GC (60s interval, TTL 300s).
        register_broker_eviction_job()
        register_conversation_run_stale_sweep_job()
        # ADR-016 §4.2 — refresh-token whitelist GC (nightly).
        register_refresh_token_gc_job()
        # ADR-017 Slice E — per-thread skill runtime root cleanup
        # (10m interval, 1h retention). Also run once at startup to clear
        # anything left over from a previous server crash.
        cleanup_skill_runtime_roots()
        register_skill_runtime_cleanup_job()

        async with async_session() as db:
            result = await db.execute(select(AgentTrigger).where(AgentTrigger.status == "active"))
            for trigger in result.scalars():
                trigger.next_run_at = add_trigger_job(
                    trigger.id,
                    trigger.trigger_type,
                    {**trigger.schedule_config, "timezone": trigger.timezone},
                )
            await db.commit()

    yield
    # Shutdown — order matters (rules/async-lifespan.md):
    # 1. in-flight consumer (SSE listener) 에 sentinel 송신
    # 2. asyncio.sleep(0) 으로 task switch 보장 → subscribe finally 실행
    # 3. scheduler / background task 종료
    # 4. persistent layer (DB / checkpointer) flush
    import asyncio

    from app.agent_runtime.event_broker import registry as broker_registry
    from app.services.conversation_run_worker import get_run_task_registry

    await get_run_task_registry().shutdown(timeout_seconds=10.0)

    # 1. SSE listener 들에 sentinel 먼저. 이 순서가 뒤집히면 scheduler GC가
    # 먼저 죽은 채로 listener 가 ``queue.get()`` 에 영원히 블록될 수 있다.
    closed = broker_registry.close_all()
    if closed:
        logger.info("Shutdown: closed %d live EventBroker(s)", closed)
    # 2. subscribe task 의 ``finally: listeners.discard(queue)`` 가 실제로
    # 실행될 event loop 기회 보장. 한 번의 yield 면 충분하다.
    await asyncio.sleep(0)

    # 3. background scheduler 종료.
    if scheduler.running:
        scheduler.shutdown(wait=False)
    await release_scheduler_leader()

    # 4. Drain the spend queue so in-flight aggregates make it to the DB
    # before the process exits. ``stop`` swallows its own errors.
    await spend_queue.stop()

    from app.agent_runtime.tool_factory import close_tool_http_client

    await close_tool_http_client()

    from app.agent_runtime.checkpointer import shutdown_checkpointer

    await shutdown_checkpointer()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Moldy",
        description="AI Agent Builder API",
        version="0.1.0",
        lifespan=lifespan,
    )

    # slowapi attaches per-request state via app.state.limiter. The handler
    # converts ``RateLimitExceeded`` into a 429 JSONResponse with a
    # ``Retry-After`` header.
    app.state.limiter = limiter
    # slowapi's handler signature is narrower than FastAPI's ExceptionHandler
    # protocol (RateLimitExceeded vs Exception). Cast satisfies pyright.
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Authorization",
            "X-Api-Key",
            "X-CSRF-Token",
        ],
        # W3-out — cross-origin (3000 → 8001) 에서 frontend 가 SSE stream 의
        # ``X-Run-Id`` (resume 식별자) / ``X-Resume-Mode`` (관찰성) 를 읽으려면
        # CORS expose_headers 에 명시해야 한다. 누락 시 browser fetch.headers.get
        # 은 항상 null 을 반환해 auto-resume 가 silent no-op.
        expose_headers=["X-Run-Id", "X-Resume-Mode", "X-Request-Id", "X-Conversation-Id"],
    )

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response

    from app.router_registry import include_app_routers

    include_app_routers(app)
    register_exception_handlers(app)

    @app.get("/api/health")
    async def health_check():
        return {"status": "ok"}

    return app


app = create_app()
