"""One table for run error codes of prepared-lease failures (parent Task 10, I24 M2).

Chat, Agent API and trigger entries all map a failure to the durable
``ConversationRun.error_code`` through this module, so the same denial never
surfaces as ``stream_error`` in one entry and ``runtime_error`` in another:

* a mid-run :class:`~app.agent_runtime.lease_dispatch_permit.DispatchDeniedError`
  (or any exception chained from one) whose ``code`` is in
  :data:`DENIAL_RUN_ERROR_CODES` maps to that table's run code;
* a pre-marker ``PreparedStartError`` (code :data:`PREPARED_START_FAILED`) maps
  to its ``__cause__``'s machine code, when that code is a plain identifier.

Linked envelopes inherit the mapped code (:func:`inherited_envelope_code`).
Only machine codes are returned, never exception text or physical paths.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

PREPARED_START_FAILED = "prepared_run_start_failed"
DENIAL_RUN_ERROR_CODES: Mapping[str, str] = {
    "tenant_suspended": "tenant_suspended",
    "forbidden": "forbidden",
    "organization_archived": "organization_archived",
    "prepared_run_lease_missing": "prepared_run_lease_missing",
}
_INHERITED = frozenset(DENIAL_RUN_ERROR_CODES.values())
_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}(:[A-Za-z0-9_]{1,64})?")
_MAX_CHAIN = 8


def _code(error: BaseException) -> str | None:
    code = getattr(error, "code", None)
    return code if isinstance(code, str) else None


def _chain(error: BaseException | None) -> list[BaseException]:
    seen: list[BaseException] = []
    while error is not None and len(seen) < _MAX_CHAIN and error not in seen:
        seen.append(error)
        error = error.__cause__ or error.__context__
    return seen


def denial_error_code(error: BaseException | None) -> str | None:
    """The run code for a dispatch denial anywhere in ``error``'s chain."""

    for item in _chain(error):
        code = _code(item)
        if code is not None and code in DENIAL_RUN_ERROR_CODES:
            return DENIAL_RUN_ERROR_CODES[code]
    return None


def prestart_error_code(error: BaseException | None) -> str | None:
    """The cause's machine code of a pre-marker ``PreparedStartError``."""

    if error is None or _code(error) != PREPARED_START_FAILED or error.__cause__ is None:
        return None
    code = _code(error.__cause__)
    return code if code is not None and _CODE.fullmatch(code) else None


def run_error_code(error: BaseException | None, default: str) -> str:
    """Durable ``ConversationRun.error_code`` for a failed run."""

    return denial_error_code(error) or prestart_error_code(error) or default


def inherited_envelope_code(run_error: str | None) -> str | None:
    """A linked API/trigger envelope inherits only the shared denial codes."""

    return run_error if run_error in _INHERITED else None


__all__ = [
    "DENIAL_RUN_ERROR_CODES",
    "PREPARED_START_FAILED",
    "denial_error_code",
    "inherited_envelope_code",
    "prestart_error_code",
    "run_error_code",
]
