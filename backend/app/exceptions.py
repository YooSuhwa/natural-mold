"""Custom exception hierarchy for standardized error handling."""

from collections.abc import Mapping


class AppError(Exception):
    """Base application error."""

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        details: Mapping[str, object] | None = None,
    ):
        self.code = code
        self.message = message
        self.status = status
        self.details = details
        super().__init__(message)


class NotFoundError(AppError):
    """Resource not found (404)."""

    def __init__(self, code: str, message: str):
        super().__init__(code, message, status=404)


class ValidationError(AppError):
    """Validation error (422)."""

    def __init__(self, code: str, message: str):
        super().__init__(code, message, status=422)


class ConflictError(AppError):
    """Conflict error (409)."""

    def __init__(self, code: str, message: str):
        super().__init__(code, message, status=409)


class ForbiddenError(AppError):
    """Forbidden (403)."""

    def __init__(self, code: str, message: str):
        super().__init__(code, message, status=403)


class ExternalServiceError(AppError):
    """External service error (502)."""

    def __init__(self, code: str, message: str):
        super().__init__(code, message, status=502)
