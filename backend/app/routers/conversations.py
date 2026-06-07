from __future__ import annotations

from fastapi import APIRouter

from app.routers import (
    conversation_branches,
    conversation_crud,
    conversation_files,
    conversation_messages,
    conversation_traces,
)

router = APIRouter(tags=["conversations"])

router.include_router(conversation_crud.router)
router.include_router(conversation_traces.router)
router.include_router(conversation_messages.router)
router.include_router(conversation_branches.router)
router.include_router(conversation_files.router)
