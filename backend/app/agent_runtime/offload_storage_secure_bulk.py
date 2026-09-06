"""Fail-closed bulk operations for one-way hashed offload storage."""

from __future__ import annotations

from typing import Final

from deepagents.backends.protocol import GlobResult, GrepResult, LsResult

BULK_ENUMERATION_DENIED: Final = "internal offload directories cannot be enumerated"
BULK_SEARCH_DENIED: Final = "internal offload directories cannot be searched"


class DenyHashedBulkOperationsMixin:
    """Deny operations that cannot project one-way physical tokens to logical paths."""

    domain: str
    _moldy_dirfd_safe = True

    def ls(self, path: str) -> LsResult:
        return LsResult(error=BULK_ENUMERATION_DENIED, entries=None)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return GlobResult(error=BULK_ENUMERATION_DENIED, matches=None)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
        context_lines: int = 0,
    ) -> GrepResult:
        return GrepResult(error=BULK_SEARCH_DENIED, matches=None)
