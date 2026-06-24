"""Backfill heuristic secret-redaction over stored trace rows (ADR-021 §9 Q5).

WHAT THIS DOES
==============
`redact_protocol_data` (``app/agent_runtime/protocol_redaction.py``) masks secrets
once, at persistence time (``protocol_persistence.py``), into the per-turn event
stream stored in ``message_events.events`` and ``message_event_chunks.events``
(JSON). Those columns hold the full LangGraph/SSE event sequence for one assistant
turn, including tool inputs/outputs where ``{{$credentials.x}}`` interpolation
(ADR-009) injects API keys, ``Authorization``/``Cookie`` headers and DSNs.

Rows written *before* ADR-021's improvements were masked by older, buggier
heuristics (the ADR documents three consecutive regressions: substring key
matching, ReDoS, acronym-key leaks) and *before* value-based masking existed, so
plaintext secrets can still sit in those past rows. The operator debug-trace view
(``trace_debug_service``) reads exactly these rows, so a residual leak there is
reachable by any super_user.

This script re-runs the *current* `redact_protocol_data` over each stored event's
``data`` and rewrites rows whose redacted output differs.

HARD LIMITATION — HEURISTICS ONLY (read this)
=============================================
**Value-based masking cannot be applied retroactively.** ADR-021's primary defence
is exact-substring replacement of the *real injected credential plaintext*, but
that value set only ever lived in a run-scoped ContextVar at run time. It is gone
now (and the credential may since have been rotated). So this backfill runs with
``secret_values=()`` — i.e. the (now-improved) **heuristics only**: sensitive key
names (``api_key`` / ``authorization`` / ...), anchored value patterns
(``Bearer <token>``, ``eyJ...`` JWTs, ``sk-...`` keys, ``moldy_at=...`` cookies),
and bounded ``key=value`` assignment leaks.

Consequences:

* A secret that the heuristics still cannot recognise (an opaque vendor token with
  no recognisable key/prefix, an encoded/base64 variant) will **not** be caught.
  Backfill closes the gap the improved heuristics now cover; it does not give
  value-exact guarantees.
* Because heuristics *guess*, re-running them can also **over-redact** harmless
  text (e.g. ``session=session`` inside a Python traceback is masked to
  ``session=<redacted>``). The dry-run report exists so you eyeball this before
  applying. The trade-off is deliberate: at the operator debug surface, masking a
  benign traceback token is preferable to leaking a credential.

SAFETY MODEL
============
* **Dry-run by default.** Nothing is written unless you pass ``--apply``.
* Dry-run reports per-table counts and a sample of before/after diffs, with the
  secrets themselves elided so raw plaintext is never printed to stdout.
* Writes happen in **batches inside transactions** (``--batch-size``); a failure
  rolls back the current batch, not the whole run.
* **Idempotent** — re-running over already-clean rows changes nothing (the
  redaction output equals the stored value, so the row is skipped).
* **BACK UP THE DATABASE before ``--apply``.** This mutates stored trace data in
  place. Recommended:
  ``pg_dump "$DATABASE_URL_SYNC" -t message_events -t message_event_chunks > backup.sql``

BACKFILL vs RETENTION (ADR-021 §9 Q5 — recommendation)
======================================================
Two ways to address residual plaintext in past trace rows:

1. **Backfill (this script).** Re-mask existing rows in place.
   + Preserves trace history for debugging; one-time, auditable, idempotent.
   - Heuristics-only (see limitation above) — it cannot promise the value-exact
     coverage the live path now has, and it can over-redact. It is a *mitigation*,
     not a guarantee, for the historical window.

2. **Retention / expiry policy.** Periodically delete (or truncate ``events`` on)
   ``message_events`` / ``message_event_chunks`` older than N days — e.g. an
   APScheduler job alongside the existing refresh-token GC, or partition-drop.
   + A short retention window structurally bounds *any* leak (known or unknown
     pattern, encoded variants included) to N days — it does not depend on the
     redactor recognising the secret. It also caps unbounded trace growth.
   - Loses old debugging traces; needs a product decision on the window.

**Recommendation:** do **both**, retention-first. (1) Run this backfill **once** now
as immediate remediation for the patterns the improved heuristics catch. (2) Then
adopt a bounded **retention policy** as the durable control, because it is the only
mechanism that bounds the secrets heuristics *miss*. Backfill mitigates today's
known residue; retention bounds tomorrow's unknowns. Retention should be the
primary long-term control; backfill is the catch-up pass.

USAGE
=====
Read-only audit (safe, the default)::

    uv run python scripts/backfill_message_event_redaction.py --dry-run

Apply (mutates data — back up first)::

    uv run python scripts/backfill_message_event_redaction.py --apply

Flags: ``--dry-run`` (default) / ``--apply``, ``--batch-size N`` (write batch /
commit granularity), ``--limit N`` (cap rows scanned *per table*, for spot checks),
``--samples N`` (max masked before/after diffs to print).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.protocol_redaction import redact_protocol_data
from app.database import async_session
from app.models.message_event import MessageEvent, MessageEventChunk

logger = logging.getLogger("backfill.message_event_redaction")

# Heuristics-only: never read the run-scoped ContextVar. Past runs' injected
# plaintext is gone, so we force value-based masking off and rely on the
# (now-improved) key/value heuristics. ``()`` (not ``None``) is what disables
# the ContextVar lookup in ``redact_protocol_data``.
_NO_VALUES: tuple[str, ...] = ()

# Mask opaque runs so raw secrets never reach stdout in dry-run diffs.
# Threshold matches ``_MIN_REDACT_LEN`` (5) in ``app/marketplace/redaction.py``:
# the redactor catches secrets as short as 5 chars, so a 12-char floor here would
# let a 5–11 char secret print in plaintext in the before/after sample.
_ELIDE_RE = re.compile(r"[A-Za-z0-9+/=_\-.]{5,}")


@dataclass
class TableStats:
    table: str
    rows_scanned: int = 0
    rows_changed: int = 0
    events_changed: int = 0
    rows_written: int = 0


@dataclass
class BackfillReport:
    apply: bool
    stats: list[TableStats] = field(default_factory=list)
    samples: list[str] = field(default_factory=list)

    @property
    def total_rows_changed(self) -> int:
        return sum(s.rows_changed for s in self.stats)

    @property
    def total_rows_written(self) -> int:
        return sum(s.rows_written for s in self.stats)


def _resolve_method(event: dict[str, Any]) -> str:
    """Pick the method string ``redact_protocol_data`` expects for an event.

    Stored events come in two shapes (confirmed against the dev DB):

    * protocol-format items carry an explicit ``"method"`` (e.g. ``values`` /
      ``updates`` / ``tools`` / ``custom`` / ``message_start``);
    * legacy/SSE-wire items only carry an ``"event"`` name (``message_start`` /
      ``tool_call_result`` / ...).

    ``method`` only steers the *memory* redaction branches (``tools`` / ``custom``);
    every other value just runs the key/value heuristics. We mirror what
    ``trace_debug_service`` does on read (``event.get("event")``), preferring the
    explicit ``method`` when present, and fall back to the ``"debug_traces"``
    sentinel the debug view itself uses.
    """

    method = event.get("method")
    if isinstance(method, str) and method:
        return method
    name = event.get("event")
    if isinstance(name, str) and name:
        return name
    return "debug_traces"


def redact_events(events: Any) -> tuple[list[dict[str, Any]] | Any, int]:
    """Return ``(maybe_new_events, changed_event_count)`` for one row's ``events``.

    Re-runs heuristics-only redaction over each event's ``data``. Returns the
    original object (and ``0``) when nothing changes, so callers can cheaply skip
    clean rows and stay idempotent.
    """

    if not isinstance(events, list):
        return events, 0

    changed = 0
    new_events: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict) or "data" not in event:
            new_events.append(event)
            continue
        before = event["data"]
        after = redact_protocol_data(_resolve_method(event), before, secret_values=_NO_VALUES)
        if after != before:
            changed += 1
            new_events.append({**event, "data": after})
        else:
            new_events.append(event)

    if changed == 0:
        return events, 0
    return new_events, changed


def _elide(text: str) -> str:
    # Elide the WHOLE run — no leading-char preview. Keeping a prefix leaked up
    # to the first 4 chars of a 5-char secret (and one prefix per special-char
    # segment) into the dry-run sample, violating the "raw plaintext never
    # reaches stdout" contract (ADR-021 review).
    return _ELIDE_RE.sub(lambda m: f"<elided:{len(m.group(0))}>", text)


def _diff_windows(before: Any, after: Any, *, context: int = 36) -> list[tuple[str, str]]:
    """Masked before/after windows around each change. Never prints raw secrets."""

    import difflib

    b = json.dumps(before, ensure_ascii=False)
    a = json.dumps(after, ensure_ascii=False)
    out: list[tuple[str, str]] = []
    matcher = difflib.SequenceMatcher(None, b, a, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        win_b = _elide(b[max(0, i1 - context) : i2 + context])
        win_a = _elide(a[max(0, j1 - context) : j2 + context])
        out.append((win_b, win_a))
    return out


async def _process_table(
    session: AsyncSession,
    *,
    model: type[MessageEvent] | type[MessageEventChunk],
    apply: bool,
    batch_size: int,
    limit: int | None,
    samples: list[str],
    max_samples: int,
) -> TableStats:
    table = model.__tablename__
    stats = TableStats(table=table)

    stmt = select(model.id, model.events).order_by(model.created_at)
    if limit is not None:
        stmt = stmt.limit(limit)

    result = await session.execute(stmt)
    rows = result.all()

    pending: list[tuple[Any, list[dict[str, Any]]]] = []
    for row_id, events in rows:
        stats.rows_scanned += 1
        new_events, changed = redact_events(events)
        if changed == 0:
            continue
        stats.rows_changed += 1
        stats.events_changed += changed

        if len(samples) < max_samples:
            windows = _diff_windows(_first_changed_data(events), _first_changed_data(new_events))
            for win_b, win_a in windows:
                if len(samples) >= max_samples:
                    break
                samples.append(
                    f"[{table}] row={row_id}\n      before: …{win_b}…\n      after : …{win_a}…"
                )

        if apply:
            pending.append((row_id, new_events))
            if len(pending) >= batch_size:
                stats.rows_written += await _flush(session, model, pending)
                pending.clear()

    if apply and pending:
        stats.rows_written += await _flush(session, model, pending)
        pending.clear()

    return stats


def _first_changed_data(events: Any) -> Any:
    """Pick the first event ``data`` for diffing (small, representative)."""

    if not isinstance(events, list):
        return events
    for event in events:
        if isinstance(event, dict) and "data" in event:
            return event["data"]
    return events


async def _flush(
    session: AsyncSession,
    model: type[MessageEvent] | type[MessageEventChunk],
    pending: Sequence[tuple[Any, list[dict[str, Any]]]],
) -> int:
    """Write one batch transactionally; roll back the batch on error.

    The preceding row scan issues a ``SELECT`` which autobegins a transaction, so a
    plain ``session.begin()`` here would raise "a transaction is already begun". We
    use a nested SAVEPOINT when a transaction is already active (the script and the
    injected-session test path both hit this) and fall back to a fresh transaction
    otherwise — either way the batch commits or rolls back as a unit.
    """

    written = 0
    begin = session.begin_nested if session.in_transaction() else session.begin
    try:
        async with begin():
            for row_id, new_events in pending:
                await session.execute(
                    update(model).where(model.id == row_id).values(events=new_events)
                )
                written += 1
        await session.commit()
    except Exception:
        logger.exception(
            "batch write failed for %s (%d rows in batch rolled back)",
            model.__tablename__,
            len(pending),
        )
        raise
    logger.info("committed %d rows to %s", written, model.__tablename__)
    return written


async def run_backfill(
    *,
    apply: bool = False,
    batch_size: int = 100,
    limit: int | None = None,
    max_samples: int = 8,
    session: AsyncSession | None = None,
) -> BackfillReport:
    """Re-apply heuristic redaction over both trace tables.

    With ``apply=False`` (default) this is read-only. ``session`` is injectable for
    tests; otherwise a session is opened from the app engine.
    """

    report = BackfillReport(apply=apply)

    async def _run(active: AsyncSession) -> None:
        for model in (MessageEvent, MessageEventChunk):
            stats = await _process_table(
                active,
                model=model,
                apply=apply,
                batch_size=batch_size,
                limit=limit,
                samples=report.samples,
                max_samples=max_samples,
            )
            report.stats.append(stats)
            logger.info(
                "%s: scanned=%d rows_changed=%d events_changed=%d rows_written=%d",
                stats.table,
                stats.rows_scanned,
                stats.rows_changed,
                stats.events_changed,
                stats.rows_written,
            )

    if session is not None:
        await _run(session)
    else:
        async with async_session() as owned:
            await _run(owned)

    return report


def _print_report(report: BackfillReport) -> None:
    mode = "APPLY (rows written)" if report.apply else "DRY-RUN (read-only, no writes)"
    print(f"\n=== message-event redaction backfill — {mode} ===")
    for stats in report.stats:
        print(
            f"  {stats.table}: scanned={stats.rows_scanned} "
            f"rows_needing_change={stats.rows_changed} "
            f"events_changed={stats.events_changed} "
            f"rows_written={stats.rows_written}"
        )
    print(
        f"  TOTAL: rows_needing_change={report.total_rows_changed} "
        f"rows_written={report.total_rows_written}"
    )
    if report.samples:
        print("\n  sample before/after diffs (secrets elided):")
        for sample in report.samples:
            print(f"    - {sample}")
    else:
        print("\n  no rows require re-redaction (already clean / no residual matches).")
    if not report.apply and report.total_rows_changed:
        print("\n  Re-run with --apply to write these changes. BACK UP THE DB FIRST.")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Re-apply heuristic secret redaction (ADR-021) over stored message_events / "
            "message_event_chunks. DRY-RUN by default. HEURISTICS ONLY — value-based "
            "masking cannot be applied retroactively. BACK UP THE DB before --apply: "
            'pg_dump "$DATABASE_URL_SYNC" -t message_events -t message_event_chunks > backup.sql'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and report only (default). No writes.",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Write changes. Mutates stored trace data in place — back up the DB first.",
    )
    parser.add_argument(
        "--batch-size", type=int, default=100, help="Rows per write transaction (default 100)."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap rows scanned PER TABLE (spot checks). Default: all rows.",
    )
    parser.add_argument(
        "--samples", type=int, default=8, help="Max masked before/after diffs to print (default 8)."
    )
    return parser.parse_args(argv)


async def _main_async(args: argparse.Namespace) -> None:
    report = await run_backfill(
        apply=args.apply,
        batch_size=args.batch_size,
        limit=args.limit,
        max_samples=args.samples,
    )
    _print_report(report)


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = _parse_args(argv)
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
