"""Exercise goal settlement and immutable artifacts on an isolated PostgreSQL + local store.

From backend run python -m evals.continuous_work.measure_documents --output PATH.
Requires local PostgreSQL 16. All databases, roles and artifact files are temporary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.authz.org_context import OrgContext
from app.config import settings
from app.database import tenant_session
from app.models import (
    ArtifactVersion,
    Conversation,
    ConversationArtifact,
    ConversationRun,
    GoalTransitionIntent,
    OrganizationMember,
    TenantMember,
)
from app.schemas.conversation_goal import GoalWriteRequest
from app.services import conversation_goal_service
from app.services.agent_execution_owner import OwnedExecutionRequest, accept_owned_execution
from app.services.artifact_storage import get_artifact_storage_backend
from app.services.artifacts.recorder import ArtifactDeltaRecorder, ArtifactRuntimeContext
from app.services.conversation_goal_evidence import read_goal_evidence_version
from app.services.conversation_goal_worker import reconcile_goal_run
from app.services.conversation_run_service import transition_run
from evals.continuous_work.cases import (
    DOCUMENT_CASES,
    FILE_NAME,
    REFERENCE,
    as_csv,
    goal_request,
    meets_task,
)
from evals.continuous_work.paired_stack import temporary_postgres
from tests.authz.i06_rls_lab import RlsLab, async_url, rls_lab
from tests.authz.test_goal_rls_integration import _actor  # noqa: PLC2701


async def record_file(
    session: Callable[[], AsyncSession],
    context: ArtifactRuntimeContext,
    contents: str,
    fault: str | None,
) -> None:
    context.output_dir.mkdir()
    recorder = ArtifactDeltaRecorder(session_factory=session, context=context)
    await recorder.prepare()
    (context.output_dir / FILE_NAME).write_text(contents, encoding="utf-8")
    await recorder.collect_after_tool_result(tool_name="write_file", tool_call_id="local-quote")
    if not fault:
        return
    async with session() as db:
        version = (
            await db.scalars(
                sa.select(ArtifactVersion)
                .join(ConversationArtifact)
                .where(ConversationArtifact.conversation_id == context.conversation_id)
            )
        ).one()
    stored = await get_artifact_storage_backend().local_path(
        object_key=version.object_key, tenant_id=context.org_context.tenant_id
    )
    await asyncio.to_thread(damage_object, stored, fault)


def damage_object(stored: Path, fault: str) -> None:
    if not stored.resolve().is_relative_to(Path(settings.artifact_storage_dir).resolve()):
        raise ValueError("refusing to change files outside the temporary object store")
    if fault == "deleted_after_write":
        stored.unlink()
    elif fault == "tampered_after_write":
        data = stored.read_bytes()
        altered = data.replace(b"13500", b"99999")
        if altered == data or len(altered) != len(data):
            raise ValueError("test requires a mutation with equal byte length")
        stored.write_bytes(altered)
    else:
        raise ValueError(f"unknown fault: {fault}")


_SINGLE_SOURCE = "https://quotes.example.invalid/Quotes/2026Q4?sheet=K"
_SINGLE_SOURCE_CSV = as_csv(tuple((*row[:3], _SINGLE_SOURCE) for row in REFERENCE))


async def measure(
    session: Callable[[], AsyncSession],
    scope: OrgContext,
    agent_id: UUID,
    directory: Path,
    case: str,
    contents: str | None,
    *,
    mode: str,
    fault: str | None = None,
) -> dict[str, object]:
    request = GoalWriteRequest.model_validate(goal_request())
    criterion = request.criteria[0]
    if criterion.kind != "csv_table":
        raise AssertionError("benchmark requires a CSV table")
    if mode == "shape_only":
        request.criteria = [criterion.model_copy(update={"reference_rows": []})]
    elif mode == "single_source":
        # This separate user instruction specifies a single exact source for the entire table.
        request.criteria = [
            criterion.model_copy(
                update={"reference_rows": [], "column_equals": {"source": _SINGLE_SOURCE}}
            )
        ]

    def matches_key(content: str | None) -> bool:
        return content == _SINGLE_SOURCE_CSV if mode == "single_source" else meets_task(content)

    async with session() as db:
        chat = Conversation(
            agent_id=agent_id,
            tenant_id=scope.tenant_id,
            org_id=scope.org_id,
            user_id=scope.user_id,
            title=case,
            agent_name_snapshot="Quote document fixture",
            last_activity_at=datetime.now(UTC),
            source="ui",
        )
        db.add(chat)
        await db.flush()
        goal = await conversation_goal_service.save_goal(db, chat, request)
        await db.commit()
        accepted = await accept_owned_execution(
            db, OwnedExecutionRequest(chat.id, agent_id, scope, "chat", "start")
        )
        run = await db.get(ConversationRun, accepted.run_id)
        if run is None:
            raise RuntimeError("ordinary chat admission returned a missing run")
        await transition_run(db, run, "running", worker_instance_id="document-evaluation")
        await db.commit()
        if contents is not None:
            context = ArtifactRuntimeContext(chat.id, scope, agent_id, str(run.id), directory)
            await record_file(session, context, contents, fault)
        await transition_run(db, run, "completed")
        await db.commit()
        await reconcile_goal_run(db, run.id, chat.id)
        await db.commit()
        await db.refresh(goal)
        evidence = (goal.last_evidence or {})["checks"][0]
        intent = await db.scalar(
            sa.select(GoalTransitionIntent.kind).where(GoalTransitionIntent.goal_id == goal.id)
        )
        readback = None
        if goal.status == "completed":
            _, data = await read_goal_evidence_version(
                db,
                context=scope,
                conversation_id=chat.id,
                artifact_id=UUID(evidence["artifact_id"]),
                version_id=UUID(evidence["version_id"]),
            )
            readback = matches_key(data.decode("utf-8"))
        return {
            "case": case,
            "mode": mode,
            "answer_key_pass": not fault and matches_key(contents),
            "goal_status": goal.status,
            "check_reason": evidence["reason"],
            "sha256": evidence.get("sha256"),
            "download_matches_expected_document": readback,
            "transition": intent,
        }


async def collect(lab: RlsLab, directory: Path, model_csv: str | None) -> list[dict[str, object]]:
    with lab.admin.begin() as setup:
        tenant_id, org_id, user_id, agent_id = _actor(setup, lab.tenant_a)
        setup.execute(sa.insert(TenantMember).values(tenant_id=tenant_id, user_id=user_id))
        setup.execute(
            sa.insert(OrganizationMember).values(
                tenant_id=tenant_id, org_id=org_id, user_id=user_id
            )
        )
    scope = OrgContext(org_id, tenant_id, user_id)
    settings.db_rls_enabled = True
    settings.conversation_goal_org_ids = frozenset({org_id})
    settings.artifact_storage_dir = str(directory / "objects")
    engine = create_async_engine(async_url(lab.app_url))
    maker = async_sessionmaker(engine, expire_on_commit=False)

    def session() -> AsyncSession:
        return tenant_session(scope, maker=maker)

    rows = []
    try:
        for case, contents in DOCUMENT_CASES.items():
            rows.append(
                await measure(
                    session, scope, agent_id, directory / case, case, contents, mode="known_facts"
                )
            )
        for fault in ("deleted_after_write", "tampered_after_write"):
            rows.append(
                await measure(
                    session,
                    scope,
                    agent_id,
                    directory / fault,
                    fault,
                    DOCUMENT_CASES["exact"],
                    mode="known_facts",
                    fault=fault,
                )
            )
        for case in ("wrong_value", "wrong_source"):
            rows.append(
                await measure(
                    session,
                    scope,
                    agent_id,
                    directory / f"shape-{case}",
                    case,
                    DOCUMENT_CASES[case],
                    mode="shape_only",
                )
            )
        for case, contents in (
            ("matching_explicit_source", _SINGLE_SOURCE_CSV),
            (
                "different_explicit_source_path",
                _SINGLE_SOURCE_CSV.replace("/Quotes/", "/quotes/", 1),
            ),
        ):
            rows.append(
                await measure(
                    session, scope, agent_id, directory / case, case, contents, mode="single_source"
                )
            )
        if model_csv is not None:
            rows.append(
                await measure(
                    session,
                    scope,
                    agent_id,
                    directory / "live-model",
                    "bounded_provider_csv",
                    model_csv,
                    mode="single_live_sample",
                )
            )
    finally:
        await engine.dispose()
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--replay-model-output",
        type=Path,
        help="replay the CSV in a saved synthetic model receipt without any network calls",
    )
    args = parser.parse_args()
    model_csv = None
    if args.replay_model_output:
        model_csv = json.loads(args.replay_model_output.read_text(encoding="utf-8"))["content"]
    with TemporaryDirectory(prefix="moldy-goal-documents-") as temporary:
        directory = Path(temporary)
        with temporary_postgres(
            directory, dict(os.environ), database="moldy_pg_lane_goal_documents"
        ) as (dsn, _):
            admin_dsn = dsn.replace("postgresql://", "postgresql+psycopg://", 1)
            with patch.dict(os.environ, {"INTEGRATION_DATABASE_URL": admin_dsn}), rls_lab() as lab:
                with lab.admin.connect() as db:
                    pg_version = db.scalar(sa.text("SHOW server_version"))
                rows = asyncio.run(collect(lab, directory, model_csv))
    exact = [r for r in rows if r["mode"] == "known_facts"]
    completed = [r for r in exact if r["goal_status"] == "completed"]
    incorrect = [r for r in exact if not r["answer_key_pass"]]
    result = {
        "postgres": pg_version,
        "registered_exact_goals": len(exact),
        "reviewed_completions": len(completed),
        "false_completions": sum(
            r["download_matches_expected_document"] is not True for r in completed
        ),
        "counterexamples": len(incorrect),
        "counterexamples_marked_complete": sum(r["goal_status"] == "completed" for r in incorrect),
        "rows": rows,
    }
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if any(
        (r["goal_status"] == "completed") != r["answer_key_pass"]
        for r in rows
        if r["mode"] != "shape_only"
    ):
        raise SystemExit("goal status does not match known quotations")
    if any((r["transition"] == "completed") != (r["goal_status"] == "completed") for r in rows):
        raise SystemExit("outbox does not match goal state")
    if any(r["download_matches_expected_document"] is not True for r in completed):
        raise SystemExit("completed goal returned a different result")


if __name__ == "__main__":
    main()
