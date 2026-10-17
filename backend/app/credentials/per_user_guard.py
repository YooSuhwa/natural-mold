"""A ``per_user`` resource never runs on its own ``credential_id`` (D15, 11.3; F-04).

That credential is the owner's account; each caller must use their own connection
(``resource_credential_bindings``). Until F-05 resolves those connections at run time,
every execution path (chat/trigger/API tool config, MCP connection, tool test run, MCP
Apps calls) fails closed with ``CREDENTIAL_BINDING_REQUIRED`` instead of falling back to
the owner's account. Owner-side management probes (MCP discover/health) are not runs.
"""

from typing import Final

from app.exceptions import ValidationError

CREDENTIAL_BINDING_REQUIRED: Final = "CREDENTIAL_BINDING_REQUIRED"
MESSAGE: Final = "사용자별 연결 방식의 도구·MCP 서버는 아직 실행할 수 없습니다"


def is_per_user(row: object) -> bool:
    return getattr(row, "credential_mode", None) == "per_user"


def binding_required() -> ValidationError:
    return ValidationError(CREDENTIAL_BINDING_REQUIRED, MESSAGE)


def require_resource_credential(row: object) -> None:
    """Raise before a run reads ``row.credential_id`` (tool or MCP server)."""
    if is_per_user(row):
        raise binding_required()


__all__ = [
    "CREDENTIAL_BINDING_REQUIRED",
    "binding_required",
    "is_per_user",
    "require_resource_credential",
]
