"""Create and pause a real old Moldy chat with the pre-F04 backend and its locked runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from importlib import import_module
from importlib.metadata import version
from typing import Any
from uuid import UUID

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.agent_runtime.checkpointer import init_checkpointer, shutdown_checkpointer
from app.approvals.recording import record_interrupted_run
from app.authz.org_context import OrgContext
from app.database import engine, tenant_session
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.approval_request import ApprovalRequest
from app.services import conversation_run_service, trace_storage
from app.services.agent_execution_owner import OwnedExecutionRequest, accept_owned_execution
from app.services.conversation_stream_persistence import build_persist_callback


async def _start(args: argparse.Namespace, context: OrgContext) -> dict[str, Any]:
    async with tenant_session(context) as db:
        accepted = await accept_owned_execution(
            db,
            OwnedExecutionRequest(
                conversation_id=args.conversation_id,
                root_agent_id=args.root_id,
                context=context,
                source="chat",
                input_preview="Please review this draft.",
            ),
        )
        assert any(
            item.role == "root" and item.agent_id_snapshot == args.root_id
            for item in accepted.participants
        )
        assert any(
            item.role == "subagent"
            and item.path == (str(args.root_id), str(item.agent_id_snapshot))
            for item in accepted.participants
        )
        run = await conversation_run_service.get_run_for_user(
            db, conversation_id=args.conversation_id, run_id=accepted.run_id, context=context
        )
        assert run is not None and run.status == "queued"
        await conversation_run_service.transition_run(
            db, run, "running", worker_instance_id="old-runtime"
        )
        await db.commit()
        return {
            "runId": str(accepted.run_id),
            "participants": [
                {"id": str(item.agent_id_snapshot), "role": item.role, "path": list(item.path)}
                for item in accepted.participants
            ],
        }


async def _interrupt(
    args: argparse.Namespace, context: OrgContext, run_id: UUID, captured: dict[str, Any]
) -> dict[str, Any]:
    journal = captured["journal"]
    ident = captured["interruptId"]
    assert captured["deepagents"] == "0.7.32" and captured["langgraph"] == "1.2.19"
    assert journal and all(
        item["run_id"] == str(run_id) and item["thread_id"] == str(args.conversation_id)
        for item in journal
    )
    assert any(
        item["method"] == "input.requested" and item["data"].get("interrupt_id") == ident
        for item in journal
    )
    if args.journal == "retained":
        callback = build_persist_callback(args.conversation_id, str(run_id), tenant=context)
        await callback(journal)

    async with tenant_session(context) as db:
        run = await conversation_run_service.get_run_for_user(
            db,
            conversation_id=args.conversation_id,
            run_id=run_id,
            context=context,
            for_update=True,
        )
        assert run is not None and run.status == "running"
        await conversation_run_service.transition_run(db, run, "interrupted", interrupt_id=ident)
        if args.journal == "retained":
            await trace_storage.finalize_turn(
                db, assistant_msg_id=str(run_id), conversation_id=args.conversation_id
            )
        await db.commit()
    if args.approval == "recorded":
        await init_checkpointer(os.environ["DATABASE_URL_SYNC"], min_size=1, max_size=2)
        try:
            await record_interrupted_run(run_id, context)
        finally:
            await shutdown_checkpointer()
    async with tenant_session(context) as db:
        rows = list(
            await db.scalars(select(ApprovalRequest).where(ApprovalRequest.run_id == run_id))
        )
        assert len(rows) == (1 if args.approval == "recorded" else 0)
        assert not rows or rows[0].interrupt_id == ident
    return {
        "runId": str(run_id),
        "interruptId": ident,
        "journalEvents": len(journal) if args.journal == "retained" else 0,
        "approvalRows": len(rows),
    }


async def _issue(args: argparse.Namespace, context: OrgContext) -> dict[str, Any]:
    # The subprocess uses the archived app as PYTHONPATH. Its script directory
    # contains the graph fixture shared by both versions of the runtime.
    graph_fixture = import_module("ag_ui_legacy_child_graph")
    admitted = await _start(args, context)
    run_id = UUID(admitted["runId"])
    async with AsyncPostgresSaver.from_conn_string(os.environ["DATABASE_URL_SYNC"]) as saver:
        await saver.setup()
        captured = await graph_fixture.seed(
            saver,
            thread_id=args.conversation_id,
            run_id=run_id,
            tenant_id=context.tenant_id,
            child_name=args.child_name,
        )
    paused = await _interrupt(args, context, run_id, captured)
    return {**admitted, **captured, **paused}


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["start", "interrupt", "issue"])
    for name in ("conversation-id", "user-id", "tenant-id", "org-id"):
        parser.add_argument("--" + name, type=UUID, required=True)
    parser.add_argument("--root-id", type=UUID)
    parser.add_argument("--run-id", type=UUID)
    parser.add_argument("--child-name")
    parser.add_argument("--journal", choices=["retained", "absent"], default="retained")
    parser.add_argument("--approval", choices=["recorded", "absent"], default="absent")
    args = parser.parse_args()
    if args.mode in {"start", "issue"} and not args.root_id:
        parser.error("start and issue require --root-id")
    if args.mode == "interrupt" and not args.run_id:
        parser.error("interrupt requires --run-id")
    if args.mode == "issue" and not args.child_name:
        parser.error("issue requires --child-name")
    if version("deepagents") != "0.7.32" or hasattr(
        ConversationRunAgent, "identity_mode_at_accept"
    ):
        raise RuntimeError("Use the pre-F04 Moldy backend with Deep Agents 0.7.32")
    for key in ("DATABASE_URL", "DATABASE_URL_SYNC"):
        url = make_url(os.environ[key]).set(drivername="postgresql")
        if url != engine.url.set(drivername="postgresql") or not (url.database or "").startswith(
            "moldy_pg_lane_"
        ):
            raise RuntimeError("Expected a single disposable PostgreSQL integration database")
    context = OrgContext(user_id=args.user_id, tenant_id=args.tenant_id, org_id=args.org_id)
    try:
        if args.mode == "issue":
            result = await _issue(args, context)
        elif args.mode == "start":
            result = await _start(args, context)
        else:
            result = await _interrupt(args, context, args.run_id, json.load(sys.stdin))
        print(json.dumps(result))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
