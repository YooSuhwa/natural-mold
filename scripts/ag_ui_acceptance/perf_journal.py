"""Observe durable commit times for the six public E2E_SLOW_STREAM fixture deltas."""

from __future__ import annotations

from typing import TypedDict
from uuid import UUID

import psycopg
from psycopg.conninfo import conninfo_to_dict

# Public contract shared with app.agent_runtime.e2e_scripted_model.SLOW_STREAM_PARTS.
SLOW_STREAM_PARTS = (
    "E2E slow ",
    "stream ",
    "completed ",
    "after ",
    "detached ",
    "navigation.",
)


class JournalToken(TypedDict):
    token_index: int
    label: str
    ordinal: int
    seq_start: int
    seq_end: int
    commit_epoch_ms: float


def journal_timing(conversation_id: UUID, sync_dsn: str) -> list[JournalToken]:
    """Read the insert transaction time of each fixture token's append-only chunk."""
    info = conninfo_to_dict(sync_dsn)
    if not str(info.get("host", "")).startswith("/") or not str(info.get("dbname", "")).startswith(
        "moldy_e2e_scripted_agui_"
    ):
        raise ValueError("Journal timing requires the disposable PG16 observer socket")

    with psycopg.connect(sync_dsn, connect_timeout=5, options="-c statement_timeout=5000") as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        if conn.execute("SHOW track_commit_timestamp").fetchone() != ("on",):
            raise ValueError("Journal timing requires track_commit_timestamp=on from PG startup")
        journals = conn.execute(
            "SELECT id FROM message_events WHERE conversation_id = %s LIMIT 2",
            (conversation_id,),
        ).fetchall()
        if len(journals) != 1:
            raise ValueError("Journal timing requires one fixture turn per conversation")
        # Select only fixture indexes and timing metadata, never any stored payload.
        # A single chunk can contain several tokens committed in the same transaction.
        rows = conn.execute(
            """
            SELECT array_position(%s::text[], event #>> '{data,delta,text}'),
                   chunk.seq_start + idx - 1, chunk.seq_start, chunk.seq_end,
                   EXTRACT(epoch FROM pg_xact_commit_timestamp(chunk.xmin)) * 1000
            FROM message_event_chunks AS chunk
            CROSS JOIN LATERAL jsonb_array_elements(chunk.events::jsonb)
                 WITH ORDINALITY AS e(event, idx)
            WHERE chunk.conversation_id = %s AND chunk.message_event_id = %s
              AND event->>'method' = 'messages' AND event->'namespace' = '[]'::jsonb
              AND event #>> '{data,event}' = 'content-block-delta'
              AND event #>> '{data,delta,type}' = 'text-delta'
            ORDER BY chunk.seq_start, idx
            LIMIT %s
            """,
            (
                list(SLOW_STREAM_PARTS),
                conversation_id,
                journals[0][0],
                len(SLOW_STREAM_PARTS) + 1,
            ),
        ).fetchall()

    if [row[0] for row in rows] != list(range(1, len(SLOW_STREAM_PARTS) + 1)):
        raise ValueError("Stored root text deltas did not match all six ordered fixture tokens")
    tokens: list[JournalToken] = []
    for token_index, ordinal, seq_start, seq_end, committed in rows:
        if committed is None or not seq_start <= ordinal <= seq_end:
            raise ValueError("Incomplete append-only chunk timing metadata")
        tokens.append(
            JournalToken(
                token_index=token_index,
                label=SLOW_STREAM_PARTS[token_index - 1],
                ordinal=ordinal,
                seq_start=seq_start,
                seq_end=seq_end,
                commit_epoch_ms=float(committed),
            )
        )
    return tokens
