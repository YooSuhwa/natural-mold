"""User-level data access: lookup, create, login bookkeeping, deletion.

Kept separate from :mod:`app.services.auth_service` so the auth service
can compose user lookups + token issuance without circular imports.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.refresh_token import RefreshToken
from app.models.user import User

logger = logging.getLogger(__name__)

# 5 failures within the lockout window triggers a 15-minute account lock.
# Window/threshold are intentionally hard-coded — the values come from
# ADR-016 §5.1 and are tuned for human typing errors, not config knobs.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION = timedelta(minutes=15)


class ActiveConversationRunConflict(RuntimeError):
    """User cleanup cannot safely proceed while a conversation run is active."""


async def get_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    """PK lookup. Centralized so callers don't replicate the SELECT."""

    return (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()


async def get_by_email(db: AsyncSession, email: str) -> User | None:
    """Case-insensitive email lookup using the existing UNIQUE index.

    All writes funnel through ``create_user`` which lowercases — so the
    column invariant is "email is always stored lower". Normalize the
    query side to match and the b-tree index does the work without
    needing a functional/expression index.
    """

    return (
        await db.execute(select(User).where(User.email == email.strip().lower()))
    ).scalar_one_or_none()


async def email_exists(db: AsyncSession, email: str) -> bool:
    return await get_by_email(db, email) is not None


async def is_first_user(db: AsyncSession) -> bool:
    """``True`` iff ``users`` is empty.

    Used to decide first-signup ``is_super_user=true`` semantics. Counts
    rather than ``LIMIT 1`` so the intent is unambiguous in logs.
    """

    count = await db.scalar(select(func.count()).select_from(User))
    return (count or 0) == 0


async def create_user(
    db: AsyncSession,
    *,
    email: str,
    password_hash: str | None,
    name: str,
    display_name: str | None = None,
    is_super_user: bool = False,
) -> User:
    user = User(
        email=email.strip().lower(),
        name=name.strip(),
        display_name=display_name.strip() if display_name else None,
        hashed_password=password_hash,
        is_super_user=is_super_user,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


async def record_login_success(db: AsyncSession, user: User, *, ip: str | None) -> None:
    user.last_login_at = datetime.now(UTC)
    user.last_login_ip = ip
    user.failed_login_attempts = 0
    user.locked_until = None
    await db.flush()


async def record_login_failure(db: AsyncSession, user: User) -> None:
    user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
    if user.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
        user.locked_until = datetime.now(UTC) + LOCKOUT_DURATION
    await db.flush()


def is_locked(user: User) -> bool:
    """``True`` iff ``locked_until`` is set and still in the future."""

    if user.locked_until is None:
        return False
    locked_until = user.locked_until
    if locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=UTC)
    return locked_until > datetime.now(UTC)


# ---------------------------------------------------------------------------
# Deletion / cleanup (ADR-016 §6)
# ---------------------------------------------------------------------------
#
# Most of a user's owned rows go away via ``ON DELETE CASCADE`` on the FK
# (m36 added it for ``agents``, ``builder_sessions``, ``agent_triggers``,
# ``credentials``, ``refresh_tokens`` etc). What CASCADE *cannot* clean is
# the LangGraph PostgreSQL checkpoint store — it's a separate set of tables
# (``checkpoints``, ``checkpoint_writes``, ``checkpoint_blobs``) keyed by
# ``thread_id`` (= ``conversations.id``) with no FK back to ``users``. If
# we drop the User without deleting those threads first the checkpoint rows
# linger forever, leaking the user's transcript content even after delete.
#
# ``cleanup_user_resources`` walks the user's conversations, deletes the
# corresponding LangGraph threads and internal offloads, then revokes
# outstanding refresh tokens.
# ``delete_user`` is the higher-level call that runs cleanup and then
# removes the User row (CASCADE finishes the rest).


async def cleanup_user_resources(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Tear down external resources tied to ``user_id`` before User row delete.

    Currently:
    1. LangGraph checkpoints — every conversation owned (transitively via
       Agent.user_id) by this user.
    2. Internal compaction history and large-result spill files for those
       same trusted owner/conversation pairs.
    3. Active refresh tokens — revoked so any leaked browser session stops
       being able to mint new access tokens. (DB CASCADE removes the rows
       a moment later but this gives a tighter window.)
    4. Builder sessions — already covered by ``ON DELETE CASCADE`` since m36;
       no extra work here. Documented for future maintainers.

    Idempotent: safe to call repeatedly. A checkpoint failure is logged and
    does not block DB deletion. An internal-offload failure is retried across
    sibling conversations and then raised so transcript data cannot be orphaned
    by deleting its ownership rows.
    """

    locked_user_id = await db.scalar(select(User.id).where(User.id == user_id).with_for_update())
    if locked_user_id is None:
        return

    # 1. Conversations owned by the user (joined via Agent.user_id). The
    # deterministic row-lock order matches run creation's User -> Conversation
    # order and keeps the active-run check valid until the caller commits.
    conv_rows = (
        (
            await db.execute(
                select(Conversation.id)
                .join(Agent, Agent.id == Conversation.agent_id)
                .where(Agent.user_id == user_id)
                .order_by(Conversation.id)
                .with_for_update(of=Conversation)
            )
        )
        .scalars()
        .all()
    )

    if conv_rows:
        active_run_id = await db.scalar(
            select(ConversationRun.id)
            .where(
                ConversationRun.conversation_id.in_(conv_rows),
                ConversationRun.is_active.is_(True),
            )
            .limit(1)
        )
        if active_run_id is not None:
            raise ActiveConversationRunConflict("user cleanup blocked by active conversation run")

    offload_cleanup_failed = False
    if conv_rows:
        from app.agent_runtime import checkpointer as cp
        from app.agent_runtime.offload_storage import delete_conversation_offloads
        from app.agent_runtime.runtime_config import _DATA_DIR

        for conversation_id in conv_rows:
            thread_id = str(conversation_id)
            try:
                await cp.delete_thread(thread_id)
            except Exception:  # noqa: BLE001 — checkpoint cleanup must not block user delete
                logger.warning("cleanup_user_resources: checkpoint cleanup failed")
            try:
                delete_conversation_offloads(
                    _DATA_DIR,
                    owner_id=str(user_id),
                    conversation_id=thread_id,
                )
            except Exception:  # noqa: BLE001 — finish siblings, then fail closed
                logger.warning("cleanup_user_resources: offload cleanup failed")
                offload_cleanup_failed = True

    if offload_cleanup_failed:
        raise RuntimeError("user offload cleanup incomplete")

    # 2. Revoke active refresh tokens. The CASCADE on the FK will remove the
    # rows themselves when the User is deleted; we mark them revoked first so
    # ``auth_service`` replay-detection treats any stray token as compromised
    # if it shows up between cleanup and delete.
    now = datetime.now(UTC).replace(tzinfo=None)
    await db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )

    logger.info(
        "cleanup_user_resources: user %s — %d conversation thread(s), refresh tokens revoked",
        user_id,
        len(conv_rows),
    )


async def delete_user(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Hard-delete a user.

    Caller is responsible for calling ``commit`` after — we keep the
    transaction open so the audit/logging layer at the router level can
    decide whether to roll back on a failure mid-way.
    """

    user = await get_by_id(db, user_id)
    if user is None:
        return

    await cleanup_user_resources(db, user_id)
    await db.delete(user)
    await db.flush()
