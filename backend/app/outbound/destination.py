"""Where a resolved credential's values may be sent (D18, plan 11.9; F-11).

``CredentialDestination`` travels with a tool/MCP config. Every request that carries
the credential must target ``https`` (or a host in ``CREDENTIAL_HTTP_ALLOWED_HOSTS``)
and a host in ``allowed_hosts``. Empty ``allowed_hosts`` (generic HTTP keys, Q14) are
meant for the owner's own conversation run (``empty_ok``); in shared, delegated,
trigger and API runs ``CREDENTIAL_EMPTY_HOSTS_POLICY`` decides: ``warn`` (default,
rollout) sends and logs ``credential_empty_hosts``, ``enforce`` refuses. With empty
hosts the first host of an invocation pins the rest, so a redirect to another host
never carries the credential.

Tool runners send through the shared runtime client; ``bind_destination`` puts the
destination on a context variable that the ``outbound`` transport checks on every
request and redirect hop (``transport.GuardTransport``).
"""

from __future__ import annotations

import contextvars
import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import settings
from app.outbound.hosts import host_allowed, normalize_host, plaintext_allowed

logger = logging.getLogger(__name__)


class OutboundBlockedError(httpx.TransportError):
    """A request was refused before any byte left the process."""


@dataclass(frozen=True, slots=True)
class CredentialDestination:
    allowed_hosts: tuple[str, ...]
    empty_ok: bool

    def to_config(self) -> dict[str, Any]:
        return {"allowed_hosts": list(self.allowed_hosts), "empty_ok": self.empty_ok}

    @classmethod
    def from_config(cls, raw: object) -> CredentialDestination | None:
        if not isinstance(raw, Mapping):
            return None
        hosts = raw.get("allowed_hosts") or ()
        return cls(tuple(str(h) for h in hosts), bool(raw.get("empty_ok")))


def require_https(scheme: str, host: str) -> None:
    if scheme == "https" or (scheme == "http" and plaintext_allowed(host)):
        return
    raise OutboundBlockedError(
        f"credentialed requests must use https ({scheme}://{host} is not allowed; "
        "internal http hosts go in CREDENTIAL_HTTP_ALLOWED_HOSTS)"
    )


def check_destination(url: str | httpx.URL, destination: CredentialDestination) -> str:
    """Raise :class:`OutboundBlockedError` unless the credential may go to ``url``."""

    parsed = httpx.URL(str(url))
    host = normalize_host(parsed.host)
    require_https(parsed.scheme, host)
    if destination.allowed_hosts:
        if not host_allowed(host, destination.allowed_hosts):
            raise OutboundBlockedError(f"host '{host}' is not in the credential's allowed hosts")
    elif not destination.empty_ok:
        message = (
            "this credential has no allowed hosts; it is only sent in its owner's own "
            "conversation runs — add allowed hosts to the credential"
        )
        if settings.credential_empty_hosts_policy == "enforce":
            raise OutboundBlockedError(message)
        # ``warn`` (rollout default): allowed, logged for the owner to add hosts (Q14).
        logger.warning("credential_empty_hosts host=%s: %s", host, message)
    return host


@dataclass(slots=True)
class _Binding:
    destination: CredentialDestination
    first_host: str | None = field(default=None)

    def check(self, url: httpx.URL) -> None:
        host = check_destination(url, self.destination)
        if self.destination.allowed_hosts:
            return
        if self.first_host is None:
            self.first_host = host
        elif host != self.first_host:
            raise OutboundBlockedError(f"credentialed redirect to another host '{host}' blocked")


_current: contextvars.ContextVar[_Binding | None] = contextvars.ContextVar(
    "credential_destination", default=None
)


@contextmanager
def bind_destination(destination: CredentialDestination | None) -> Iterator[None]:
    """Check every request sent in this context against ``destination``."""

    if destination is None:
        yield
        return
    token = _current.set(_Binding(destination))
    try:
        yield
    finally:
        _current.reset(token)


def guarded_destination(
    credentials: object, destination: CredentialDestination | None
) -> CredentialDestination | None:
    """Fail closed: credential values without a destination are never sent."""

    if credentials is None:
        return None
    if destination is None:
        raise OutboundBlockedError("credential destination missing; refusing to send it")
    return destination


def check_bound_destination(url: httpx.URL) -> None:
    binding = _current.get()
    if binding is not None:
        binding.check(url)


def owner_own_run(identity: Any, agent_owner_id: object, subject_id: object) -> bool:
    """The agent owner's own conversation run (chat, or its subagents) on their account."""

    if identity is None:
        return False
    source = str(getattr(identity, "source", ""))
    caller = getattr(identity, "caller_user_id", None)
    return source in {"chat", "subagent"} and caller == agent_owner_id == subject_id


def destination_config(
    resolved: Any, identity: Any, agent_owner_id: object
) -> dict[str, Any] | None:
    """Config entry for a run's ``ResolvedCredential`` (``None`` when none is attached)."""

    if resolved is None:
        return None
    own = owner_own_run(identity, agent_owner_id, resolved.credential_subject_id)
    return CredentialDestination(tuple(resolved.allowed_hosts), own).to_config()


__all__ = [
    "CredentialDestination",
    "OutboundBlockedError",
    "bind_destination",
    "check_bound_destination",
    "check_destination",
    "destination_config",
    "guarded_destination",
    "owner_own_run",
    "require_https",
]
