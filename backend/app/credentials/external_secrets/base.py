"""Abstract base for external secret providers (env / Vault / future backends)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any


class ProviderState(StrEnum):
    INITIAL = "initial"
    CONNECTED = "connected"
    ERROR = "error"
    DISCONNECTED = "disconnected"


class SecretsProvider(ABC):
    """Backend that maps stable secret names to runtime values.

    Implementations must be safe to call from async contexts. Long-running I/O
    should occur inside :meth:`connect` (eager) or :meth:`get_secret` (lazy);
    constructors must not perform network calls.
    """

    name: str = ""
    display_name: str = ""

    def __init__(self) -> None:
        self.state = ProviderState.INITIAL

    @abstractmethod
    def init(self, settings: Any) -> None:
        """Configure the provider from application settings."""

    @abstractmethod
    async def connect(self) -> None:
        """Open any pooled connections / verify credentials."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Release pooled connections."""

    @abstractmethod
    async def get_secret(self, name: str) -> str | None:
        """Return the secret value or ``None`` if not present."""

    @abstractmethod
    async def has_secret(self, name: str) -> bool:
        """Return True if ``name`` resolves to a value."""

    @abstractmethod
    async def list_secrets(self) -> list[str]:
        """List known secret names (best-effort; some backends are write-only)."""

    @abstractmethod
    async def test(self) -> tuple[bool, str | None]:
        """Health check. Returns ``(ok, error_message)``."""


__all__ = ["ProviderState", "SecretsProvider"]
