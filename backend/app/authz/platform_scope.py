"""An explicit platform resource scope; organization data never defaults to it."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PlatformScope:
    """Used only by authenticated platform management or named platform bootstrap."""
