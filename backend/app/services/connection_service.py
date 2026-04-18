from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.connection import Connection
from app.schemas.connection import (
    ConnectionCreate,
    ConnectionExtraConfig,
    ConnectionUpdate,
)
from app.services import credential_service


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _commit_or_409(db: AsyncSession) -> None:
    """partial unique index(`uq_connections_one_default_per_scope`) 위반을
    409로 변환. 동시 요청이 같은 scope에 default=true로 쓰려고 할 때 DB가
    잡아주는 최종 안전망. create/update/delete 세 경로에서 동일 패턴으로 호출."""
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="concurrent modification on the default connection in "
            "this scope — please retry",
        ) from exc


async def list_connections(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    type: str | None = None,
    provider_name: str | None = None,
) -> list[Connection]:
    stmt = select(Connection).where(Connection.user_id == user_id)
    if type is not None:
        stmt = stmt.where(Connection.type == type)
    if provider_name is not None:
        stmt = stmt.where(Connection.provider_name == provider_name)
    stmt = stmt.order_by(Connection.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().unique().all())


async def get_connection(
    db: AsyncSession, conn_id: uuid.UUID, user_id: uuid.UUID
) -> Connection | None:
    result = await db.execute(
        select(Connection).where(
            Connection.id == conn_id,
            Connection.user_id == user_id,
        )
    )
    return result.scalars().unique().one_or_none()


async def get_default_connection(
    db: AsyncSession,
    user_id: uuid.UUID,
    type_: str,
    provider_name: str,
) -> Connection | None:
    """Fetch the user's default connection for (type, provider_name) scope.

    partial unique index `uq_connections_one_default_per_scope`가 유일성을
    보장하므로 결과는 0 또는 1건. credential은 eager load 되어 호출자가
    세션 밖에서도 decrypt 가능하다. user_id 필터는 cross-tenant leak 방지.
    """
    result = await db.execute(
        select(Connection)
        .where(
            Connection.user_id == user_id,
            Connection.type == type_,
            Connection.provider_name == provider_name,
            Connection.is_default.is_(True),
        )
        .options(selectinload(Connection.credential))
        .limit(1)
    )
    return result.scalars().unique().one_or_none()


async def get_default_connections_for_providers(
    db: AsyncSession,
    user_id: uuid.UUID,
    type_: str,
    provider_names: set[str],
) -> dict[str, Connection]:
    """Bulk-load default connections for several providers in a single query.

    PREBUILT tool 해석 경로의 N+1 방지. 한 에이전트가 naver/google_search/
    google_workspace 도구를 동시에 쓰면 provider당 쿼리 대신 IN 1회로 처리.
    provider_name IN 집합이 비면 즉시 빈 dict 반환. user_id 필터 필수.
    """
    if not provider_names:
        return {}
    result = await db.execute(
        select(Connection)
        .where(
            Connection.user_id == user_id,
            Connection.type == type_,
            Connection.provider_name.in_(provider_names),
            Connection.is_default.is_(True),
        )
        .options(selectinload(Connection.credential))
    )
    return {conn.provider_name: conn for conn in result.scalars().unique().all()}


async def _count_in_scope(
    db: AsyncSession,
    user_id: uuid.UUID,
    type_: str,
    provider_name: str,
) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(Connection)
        .where(
            Connection.user_id == user_id,
            Connection.type == type_,
            Connection.provider_name == provider_name,
        )
    )
    return int(result.scalar() or 0)


async def _promote_default_if_orphaned(
    db: AsyncSession,
    user_id: uuid.UUID,
    type_: str,
    provider_name: str,
) -> None:
    """scope에 row가 있는데 default가 0개면 가장 최근 row를 default로 승격.
    POST가 첫 row를 자동 default로 만드는 것과 대칭. ADR-008 §5 invariant.

    SQL-native 구현: 전체 row를 메모리로 가져오지 않고 (a) default 존재 체크,
    (b) 없으면 최신 후보 id 조회, (c) UPDATE — 총 2~3회 왕복에 scope 당 O(1) row 전송.
    """
    # (a) 이미 default가 있으면 no-op
    existing_default = await db.execute(
        select(Connection.id)
        .where(
            Connection.user_id == user_id,
            Connection.type == type_,
            Connection.provider_name == provider_name,
            Connection.is_default.is_(True),
        )
        .limit(1)
    )
    if existing_default.scalar() is not None:
        return

    # (b) 가장 최근 row id 조회 (없으면 빈 scope)
    candidate = await db.execute(
        select(Connection.id)
        .where(
            Connection.user_id == user_id,
            Connection.type == type_,
            Connection.provider_name == provider_name,
        )
        .order_by(Connection.created_at.desc())
        .limit(1)
    )
    promote_id = candidate.scalar()
    if promote_id is None:
        return

    # (c) 승격. ORM identity map이 이미 load한 row가 있다면 같은 트랜잭션의
    # UPDATE로 반영되므로 expire 불필요.
    await db.execute(
        update(Connection)
        .where(Connection.id == promote_id)
        .values(is_default=True, updated_at=_now())
    )


async def _clear_default_in_scope(
    db: AsyncSession,
    user_id: uuid.UUID,
    type_: str,
    provider_name: str,
    *,
    exclude_id: uuid.UUID | None = None,
) -> None:
    stmt = (
        update(Connection)
        .where(
            Connection.user_id == user_id,
            Connection.type == type_,
            Connection.provider_name == provider_name,
            Connection.is_default.is_(True),
        )
        .values(is_default=False, updated_at=_now())
    )
    if exclude_id is not None:
        stmt = stmt.where(Connection.id != exclude_id)
    await db.execute(stmt)


async def create_connection(
    db: AsyncSession, user_id: uuid.UUID, payload: ConnectionCreate
) -> Connection:
    # credential 소유권 검증 — 다른 유저의 credential을 참조 못 하도록.
    # credential_service.get_credential이 미존재/타 유저 모두 404 반환.
    if payload.credential_id is not None:
        await credential_service.get_credential(
            db, payload.credential_id, user_id
        )

    existing = await _count_in_scope(
        db, user_id, payload.type, payload.provider_name
    )

    is_default = payload.is_default
    if existing == 0:
        # First connection in scope — force default on.
        is_default = True
    elif payload.is_default:
        await _clear_default_in_scope(
            db, user_id, payload.type, payload.provider_name
        )

    conn = Connection(
        user_id=user_id,
        type=payload.type,
        provider_name=payload.provider_name,
        display_name=payload.display_name,
        credential_id=payload.credential_id,
        extra_config=(
            payload.extra_config.model_dump(exclude_none=True)
            if payload.extra_config is not None
            else None
        ),
        is_default=is_default,
        status=payload.status,
    )
    db.add(conn)
    await _commit_or_409(db)
    await db.refresh(conn)
    return conn


async def update_connection(
    db: AsyncSession,
    conn_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: ConnectionUpdate,
) -> Connection | None:
    conn = await get_connection(db, conn_id, user_id)
    if conn is None:
        return None

    # pre-update scope 캡처 — 옛/새 scope 양쪽에서 default invariant 유지용
    pre_type = conn.type
    pre_provider_name = conn.provider_name

    # exclude_unset으로 "미전송" vs "명시적 None 전송"을 구분.
    # credential_id/extra_config=None은 명시적 해제로 반영된다.
    fields = payload.model_dump(exclude_unset=True)

    # credential 소유권 검증 (None 해제는 검증 불필요)
    if "credential_id" in fields and fields["credential_id"] is not None:
        await credential_service.get_credential(
            db, fields["credential_id"], user_id
        )

    # 새 scope / 새 is_default를 먼저 계산 (아직 conn을 mutate하지 않음)
    new_provider_name = (
        fields["provider_name"]
        if ("provider_name" in fields and fields["provider_name"] is not None)
        else conn.provider_name
    )
    new_is_default = conn.is_default
    if "is_default" in fields:
        new_is_default = bool(fields["is_default"])

    # 새 scope에서 default로 승격된다면 경쟁 default를 먼저 해제한다.
    # conn.provider_name을 mutate하기 전에 해야 — 그래야 UPDATE를 issue하는
    # 과정에서 conn의 pending 변경이 autoflush되어 partial unique (is_default=true
    # 기준)을 임시로 두 번 위반하는 상황을 피할 수 있다.
    if new_is_default:
        await _clear_default_in_scope(
            db,
            conn.user_id,
            conn.type,
            new_provider_name,
            exclude_id=conn.id,
        )

    # 이제 in-memory mutation 적용
    if "provider_name" in fields and fields["provider_name"] is not None:
        conn.provider_name = fields["provider_name"]
    if "display_name" in fields and fields["display_name"] is not None:
        conn.display_name = fields["display_name"]
    if "credential_id" in fields:
        conn.credential_id = fields["credential_id"]
    if "extra_config" in fields:
        # payload.extra_config가 None이면 None, 아니면 typed model → dict
        conn.extra_config = (
            payload.extra_config.model_dump(exclude_none=True)
            if payload.extra_config is not None
            else None
        )
    if "status" in fields and fields["status"] is not None:
        conn.status = fields["status"]

    if "is_default" in fields:
        if fields["is_default"] is True:
            conn.is_default = True
        elif fields["is_default"] is False:
            conn.is_default = False

    # PATCH 후 상태를 ConnectionCreate 규칙으로 재검증 — POST가 거부할 조합을
    # PATCH로 우회하지 못하게 (prebuilt + non-enum provider_name, mcp +
    # extra_config 없음 등). 단 invariant에 영향을 주는 필드가 실제로 변경된
    # 경우에만 실행 — 그 외 필드(display_name 등)의 PATCH에선 m9-migrated
    # legacy plaintext env_vars를 가진 row도 grandfather 처리되어야 한다.
    # credential_id가 바뀌면 MCP invariant(cred + non-none auth → env_vars 필수)
    # 를 재검증해야 한다 (Codex 6차 adversarial P2).
    revalidate = (
        ("provider_name" in fields and fields["provider_name"] is not None)
        or "extra_config" in fields
        or "credential_id" in fields
    )
    if revalidate:
        try:
            _extra_cfg_arg = (
                ConnectionExtraConfig.model_validate(conn.extra_config)
                if isinstance(conn.extra_config, dict)
                else conn.extra_config
            )
            ConnectionCreate(
                type=conn.type,
                provider_name=conn.provider_name,
                display_name=conn.display_name,
                credential_id=conn.credential_id,
                extra_config=_extra_cfg_arg,
                is_default=conn.is_default,
                status=conn.status,
            )
        except ValidationError as exc:
            # errors()의 "input" 필드에 UUID 등 JSON 직렬화 불가한 값이
            # 포함될 수 있으므로 jsonable_encoder로 정리.
            raise HTTPException(
                status_code=422,
                detail=jsonable_encoder(
                    exc.errors(include_url=False, include_context=False)
                ),
            ) from exc

    # ADR-008 §5 invariant: scope에 row가 있으면 default도 있다. helper는
    # idempotent(default 있으면 no-op)이므로 옛/새 scope 양쪽에 호출해 대칭 유지.
    # - 옛 scope: default 행이 demote/rename-out/delete된 경우 대체 승격
    # - 새 scope: rename으로 들어온 row가 빈 scope에 떨어진 경우 자신을 승격
    scope_changed = (pre_type, pre_provider_name) != (
        conn.type,
        conn.provider_name,
    )
    if scope_changed:
        await _promote_default_if_orphaned(
            db, conn.user_id, pre_type, pre_provider_name
        )
    await _promote_default_if_orphaned(
        db, conn.user_id, conn.type, conn.provider_name
    )

    conn.updated_at = _now()
    await _commit_or_409(db)
    await db.refresh(conn)
    return conn


async def delete_connection(
    db: AsyncSession, conn_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    conn = await get_connection(db, conn_id, user_id)
    if conn is None:
        return False

    was_default = conn.is_default
    del_type = conn.type
    del_provider_name = conn.provider_name

    await db.delete(conn)
    await db.flush()  # 삭제를 세션에 반영해 후속 쿼리가 사라진 row를 보지 않도록

    if was_default:
        await _promote_default_if_orphaned(
            db, user_id, del_type, del_provider_name
        )

    await _commit_or_409(db)
    return True
