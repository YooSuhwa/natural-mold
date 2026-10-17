import logging
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from typing import Protocol, overload
from uuid import UUID

import anyio
from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session, SessionTransaction

from app.config import settings


class ShieldedAsyncSession(AsyncSession):
    async def __aexit__(self, type_: object, value: object, traceback: object) -> None:
        await close_session_shielded(self)


# Pool knobs apply only to postgresql URLs — sqlite's NullPool/StaticPool
# rejects them (dev/test envs may point DATABASE_URL at sqlite).
_pool_kwargs: dict[str, int] = (
    {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout,
        "pool_recycle": settings.db_pool_recycle,
    }
    if settings.database_url.startswith("postgresql")
    else {}
)
engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True, **_pool_kwargs)
async_session = async_sessionmaker(engine, class_=ShieldedAsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Scoped sessions (plan 8.8, B-13). Only these factories create sessions; the
# scope lives in ``session.info`` and ``after_begin`` re-applies it with
# transaction-local ``set_config(..., true)`` on every transaction, so commits
# inside one request never drop it. Settings are issued only on PostgreSQL with
# ``DB_RLS_ENABLED`` on, so the default install has zero runtime change.
#
# Deferred to I-06 (m92), recorded here so the gaps are not lost:
# * The request session is unscoped until ``get_org_context`` binds it, so the
#   token/user and membership lookups that run first must be RLS-exempt
#   (identity tables) or use an explicit platform/identity scope.
# * The LangGraph checkpointer uses its own psycopg pool outside Moldy RLS; the
#   ``conversation_checkpointer(thread_id, ctx)`` gate belongs to I-06.
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

SCOPE_INFO_KEY = "moldy_db_scope"
REASON_INFO_KEY = "moldy_db_reason"
_SETTINGS = ("app.tenant_id", "app.user_id", "app.scope")


class TenantScoped(Protocol):
    """``OrgContext``/``TenantContext``/``SessionTenant`` all satisfy this."""

    @property
    def tenant_id(self) -> UUID: ...

    @property
    def user_id(self) -> UUID | None: ...


class SessionScopeError(RuntimeError):
    """A session may be bound once, at its root transaction, to one scope."""


@dataclass(frozen=True, slots=True)
class SessionTenant:
    """Company scope for derived work that carries ids, not a full context."""

    tenant_id: UUID
    user_id: UUID | None = None


def _tenant_values(ctx: TenantScoped) -> dict[str, str]:
    values = {"app.tenant_id": str(ctx.tenant_id)}
    if ctx.user_id is not None:
        values["app.user_id"] = str(ctx.user_id)
    return values


def _scoped[S](session: S, values: dict[str, str] | None, reason: str | None = None) -> S:
    info = getattr(session, "info", None)  # test doubles may return context managers
    if isinstance(info, dict):
        if values is not None:
            info[SCOPE_INFO_KEY] = values
        if reason is not None:
            info[REASON_INFO_KEY] = reason
    return session


@overload
def tenant_session(ctx: TenantScoped | None) -> ShieldedAsyncSession: ...
@overload
def tenant_session[S](ctx: TenantScoped | None, *, maker: Callable[[], S]) -> S: ...
def tenant_session(
    ctx: TenantScoped | None, *, maker: Callable[[], object] | None = None
) -> object:
    """A company session; ``None`` is unbound (fails closed under RLS) until ``bind_tenant``."""
    session = (maker or async_session)()
    return _scoped(session, None if ctx is None else _tenant_values(ctx))


@overload
def platform_session(reason: str) -> ShieldedAsyncSession: ...
@overload
def platform_session[S](reason: str, *, maker: Callable[[], S]) -> S: ...
def platform_session(reason: str, *, maker: Callable[[], object] | None = None) -> object:
    """Trusted infrastructure scope; reasons are pinned by the B-13 ratchet test."""
    return _scoped((maker or async_session)(), {"app.scope": "platform"}, reason)


@overload
def tenant_sessions(ctx: TenantScoped) -> Callable[[], ShieldedAsyncSession]: ...
@overload
def tenant_sessions[S](ctx: TenantScoped, *, maker: Callable[[], S]) -> Callable[[], S]: ...
def tenant_sessions(
    ctx: TenantScoped, *, maker: Callable[[], object] | None = None
) -> Callable[[], object]:
    """Factory form for injection points; the default maker is read per call."""
    return lambda: _scoped((maker or async_session)(), _tenant_values(ctx))


@overload
def platform_sessions(reason: str) -> Callable[[], ShieldedAsyncSession]: ...
@overload
def platform_sessions[S](reason: str, *, maker: Callable[[], S]) -> Callable[[], S]: ...
def platform_sessions(
    reason: str, *, maker: Callable[[], object] | None = None
) -> Callable[[], object]:
    return lambda: _scoped((maker or async_session)(), {"app.scope": "platform"}, reason)


def _is_postgres_connection(connection: Connection) -> bool:
    return connection.dialect.name == "postgresql"


def _supports_transaction_settings(connection: Connection) -> bool:
    return settings.db_rls_enabled and _is_postgres_connection(connection)


_unbound_transactions = 0


def unbound_transaction_count() -> int:
    """Unbound sessions that began a transaction while RLS was enforced."""
    return _unbound_transactions


def _set_config(values: dict[str, str]) -> tuple[str, dict[str, str]]:
    names = [name for name in _SETTINGS if name in values]
    columns = ", ".join(f"set_config('{name}', :v{i}, true)" for i, name in enumerate(names))
    return f"SELECT {columns}", {f"v{i}": values[name] for i, name in enumerate(names)}


@event.listens_for(Session, "after_begin")
def _apply_scope(
    session: Session, _transaction: SessionTransaction, connection: Connection
) -> None:
    if not _supports_transaction_settings(connection):
        return
    values = session.info.get(SCOPE_INFO_KEY)
    if values:
        sql, params = _set_config(values)
        connection.execute(text(sql), params)
        return
    global _unbound_transactions
    _unbound_transactions += 1
    logger.debug("unbound database session began a transaction under RLS (fails closed)")


def session_tenant(session: object) -> UUID | None:
    """The company a scoped session is bound to, else ``None`` (E-08 audit retention)."""
    info = getattr(session, "info", None)  # test doubles may not carry a dict
    values = info.get(SCOPE_INFO_KEY) if isinstance(info, dict) else None
    raw = values.get("app.tenant_id") if isinstance(values, dict) else None
    return UUID(raw) if isinstance(raw, str) else None


async def bind_tenant(session: AsyncSession, ctx: TenantScoped) -> None:
    """Bind an unbound request session once its company is resolved.

    The open transaction (if any) is updated immediately; later transactions
    get it from ``after_begin``. A session never switches company or scope.
    """
    values = _tenant_values(ctx)
    current = session.info.get(SCOPE_INFO_KEY)
    if current is not None and current != values:
        raise SessionScopeError("database session is already bound to another scope")
    if session.in_nested_transaction():
        raise SessionScopeError(
            "bind the database session at its root, not in a nested transaction"
        )
    session.info[SCOPE_INFO_KEY] = values
    if session.in_transaction():
        connection = (await session.connection()).sync_connection
        if connection is not None and _supports_transaction_settings(connection):
            sql, params = _set_config(values)
            await session.execute(text(sql), params)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    session = tenant_session(None)
    try:
        yield session
    finally:
        await close_session_shielded(session)


async def close_session_shielded(session: AsyncSession) -> None:
    with anyio.CancelScope(shield=True):
        await session.close()


async def shutdown_database() -> None:
    """Dispose the application engine after all DB-backed owners stop."""
    await engine.dispose()


def is_postgres(db: AsyncSession) -> bool:
    """True when the session's engine is Postgres.

    Fallback to ``False`` (= SQLite test path) when ``db.bind`` is
    ``None`` so the helper is safe to call from any service.
    """

    return db.bind is not None and db.bind.dialect.name == "postgresql"
