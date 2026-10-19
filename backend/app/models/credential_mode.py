"""Credential mode columns shared by ``tools`` and ``mcp_servers`` (plan D15, 6.1, 11.3).

``none``: no account. ``shared``: the resource's ``credential_id`` (its owner's account)
is used by everyone who may use the resource. ``per_user``: each caller connects their
own account through ``resource_credential_bindings``. ``none`` and ``shared`` follow the
credential on every ORM insert/update: attaching one to a ``none`` row makes it
``shared``, detaching it from a ``shared`` row makes it ``none``; ``per_user`` is only
changed by the mode API. m87 backfills existing rows the same way; the server default is
``none`` so a raw insert never yields ``shared`` without a credential.
"""

from typing import Final

from sqlalchemy import CheckConstraint, String, event
from sqlalchemy.orm import Mapped, mapped_column

CREDENTIAL_MODES: Final = ("none", "shared", "per_user")


def sync_mode(target: "CredentialModeColumns") -> None:
    credential_id = getattr(target, "credential_id", None)
    if target.credential_mode in (None, "none") and credential_id is not None:
        target.credential_mode = "shared"
    elif target.credential_mode in (None, "shared") and credential_id is None:
        target.credential_mode = "none"


def credential_mode_check(table: str) -> CheckConstraint:
    values = ",".join(repr(mode) for mode in CREDENTIAL_MODES)
    return CheckConstraint(f"credential_mode IN ({values})", name=f"ck_{table}_credential_mode")


class CredentialModeColumns:
    credential_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="none", server_default="none"
    )
    # The credential type a ``per_user`` connection must have (NULL: the definition's).
    credential_definition_key: Mapped[str | None] = mapped_column(String(80), nullable=True)


def _on_flush(_mapper: object, _connection: object, target: CredentialModeColumns) -> None:
    sync_mode(target)


event.listen(CredentialModeColumns, "before_insert", _on_flush, propagate=True)
event.listen(CredentialModeColumns, "before_update", _on_flush, propagate=True)

__all__ = ["CREDENTIAL_MODES", "CredentialModeColumns", "credential_mode_check", "sync_mode"]
