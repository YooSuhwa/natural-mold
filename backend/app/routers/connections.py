from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db
from app.schemas.connection import (
    ConnectionCreate,
    ConnectionResponse,
    ConnectionType,
    ConnectionUpdate,
)
from app.services import connection_service

router = APIRouter(prefix="/api/connections", tags=["connections"])


@router.get("", response_model=list[ConnectionResponse])
async def list_connections(
    type: ConnectionType | None = Query(default=None),
    provider_name: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    conns = await connection_service.list_connections(
        db, user.id, type=type, provider_name=provider_name
    )
    return [ConnectionResponse.model_validate(c) for c in conns]


@router.get("/{connection_id}", response_model=ConnectionResponse)
async def get_connection(
    connection_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    conn = await connection_service.get_connection(db, connection_id, user.id)
    if conn is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return ConnectionResponse.model_validate(conn)


@router.post("", response_model=ConnectionResponse, status_code=201)
async def create_connection(
    payload: ConnectionCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    conn = await connection_service.create_connection(db, user.id, payload)
    return ConnectionResponse.model_validate(conn)


@router.patch("/{connection_id}", response_model=ConnectionResponse)
async def update_connection(
    connection_id: uuid.UUID,
    payload: ConnectionUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    conn = await connection_service.update_connection(
        db, connection_id, user.id, payload
    )
    if conn is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return ConnectionResponse.model_validate(conn)


@router.delete("/{connection_id}", status_code=204)
async def delete_connection(
    connection_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    deleted = await connection_service.delete_connection(
        db, connection_id, user.id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Connection not found")
    return Response(status_code=204)
