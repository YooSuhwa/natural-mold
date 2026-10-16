from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.authz.execution_lock_cursor import ExecutionProofError
from app.exceptions import AppError

logger = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        if exc.code in {
            "forbidden",
            "csrf_mismatch",
            "not_authenticated",
            "tenant_suspended",
            "tenant_unavailable",
            "permission_required",
            "capability_required",
        }:
            from app.services import audit_service

            current_user = getattr(request.state, "current_user", None)
            org_context = getattr(request.state, "org_context", None)
            event = {
                "actor_type": "user" if current_user else "public",
                "actor_user_id": getattr(current_user, "id", None),
                "actor_email_snapshot": getattr(current_user, "email", None),
                "owner_user_id": getattr(current_user, "id", None),
                "owner_email_snapshot": getattr(current_user, "email", None),
                "action": (
                    "auth.csrf_denied" if exc.code == "csrf_mismatch" else "auth.access_denied"
                ),
                "target_type": "http_request",
                "target_id": request.url.path,
                "outcome": "denied",
                "reason_code": exc.code,
                "reason_message": exc.message,
                "request": request,
                "metadata": {"method": request.method, "path": request.url.path},
            }
            if org_context is None:  # denied before a company was resolved
                await audit_service.record_platform_event_best_effort(**event)
            else:
                await audit_service.record_event_best_effort(tenant=org_context, **event)
        error: dict[str, object] = {"code": exc.code, "message": exc.message}
        if exc.details is not None:
            error["details"] = jsonable_encoder(exc.details)
        return JSONResponse(status_code=exc.status, content={"error": error})

    @app.exception_handler(ExecutionProofError)
    async def execution_proof_error_handler(
        request: Request, exc: ExecutionProofError
    ) -> JSONResponse:
        # Canonical execution-order violations are conflicts, never 500s.
        logger.warning("execution proof denied: %s %s", exc.code, request.url.path)
        return JSONResponse(
            status_code=409,
            content={"error": {"code": exc.code, "message": "실행 권한 확인에 실패했습니다"}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        details = exc.errors()
        for error in details:
            error.pop("input", None)
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "입력값 검증에 실패했습니다",
                    "details": jsonable_encoder(details),
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = f"HTTP_{exc.status_code}"
        message = "HTTP 오류가 발생했습니다"
        details = None

        if isinstance(exc.detail, str):
            message = exc.detail
        elif isinstance(exc.detail, dict):
            detail_code = exc.detail.get("code")
            detail_message = exc.detail.get("message") or exc.detail.get("detail")
            if isinstance(detail_code, str):
                code = detail_code
            message = detail_message if isinstance(detail_message, str) else str(exc.detail)
            details = jsonable_encoder(exc.detail)
        elif exc.detail is not None:
            message = str(exc.detail)
            details = jsonable_encoder(exc.detail)

        error: dict[str, object] = {"code": code, "message": message}
        if details is not None:
            error["details"] = details
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": error},
            headers=exc.headers,
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
