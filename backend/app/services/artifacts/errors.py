"""Artifact service errors (BE-S8 split).

Leaf module so every cluster (``recorder`` / ``library`` / ``content`` /
``summary``) and external callers (routers) can raise/except the same type
without import cycles.
"""

from __future__ import annotations


class ArtifactNotFoundError(LookupError):
    pass
