"""Inspect synthetic chat side-effect counts during a local multi-worker cutover."""

from __future__ import annotations

import json
import os
import sys
from typing import LiteralString
from uuid import UUID

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import DictRow, dict_row


def snapshot(conversation_id: UUID) -> dict[str, object]:
    url = os.environ["MOLDY_AGUI_OBSERVER_URL"]
    info = conninfo_to_dict(url)
    if not str(info.get("host", "")).startswith("/") or not str(info.get("dbname", "")).startswith(
        "moldy_e2e_scripted_"
    ):
        raise ValueError("The observation requires the private acceptance cluster")
    statements: dict[str, LiteralString] = {
        "runs": "SELECT id, parent_run_id, status FROM conversation_runs WHERE conversation_id=%s ORDER BY id",
        "inputs": "SELECT id, run_id, status FROM conversation_run_inputs WHERE conversation_id=%s ORDER BY id",
        "message_events": "SELECT id, assistant_msg_id, status FROM message_events WHERE conversation_id=%s ORDER BY id",
        "skill_uses": "SELECT id, skill_id, execution_count FROM skill_usage_events WHERE conversation_id=%s ORDER BY id",
        "artifacts": "SELECT id, assistant_msg_id, status FROM conversation_artifacts WHERE conversation_id=%s ORDER BY id",
        "steers": "SELECT id, run_id, status FROM conversation_run_steers WHERE conversation_id=%s ORDER BY id",
        "receipts": (
            "SELECT t.run_id, t.tool_call_id, t.status FROM conversation_run_tool_receipts AS t "
            "JOIN conversation_runs AS r ON r.id=t.run_id WHERE r.conversation_id=%s ORDER BY 1,2"
        ),
    }
    with psycopg.Connection[DictRow].connect(url, row_factory=dict_row, connect_timeout=5) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        rows = {
            key: conn.execute(statement, (conversation_id,)).fetchall()
            for key, statement in statements.items()
        }
        count = conn.execute(
            "SELECT count(*) AS n FROM message_event_chunks WHERE conversation_id=%s",
            (conversation_id,),
        ).fetchone()
    if count is None:
        raise RuntimeError("Missing aggregate event count")
    return {
        "conversation_id": str(conversation_id),
        "counts": {
            **{key: len(value) for key, value in rows.items()},
            "event_chunks": count["n"],
        },
        **rows,
    }


if __name__ == "__main__":
    print(json.dumps(snapshot(UUID(sys.argv[1])), default=str))
