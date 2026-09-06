from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ResourceContextNotFoundError(LookupError):
    """Uniform missing/foreign/revoked resource result."""

    def __str__(self) -> str:
        return "resource context item not found"


@dataclass(frozen=True, slots=True)
class ResourceContextLimitError(ValueError):
    actual_bytes: int
    maximum_bytes: int

    def __str__(self) -> str:
        return (
            f"resolved resource context is {self.actual_bytes} bytes; "
            f"maximum is {self.maximum_bytes} bytes"
        )


__all__ = ["ResourceContextLimitError", "ResourceContextNotFoundError"]
