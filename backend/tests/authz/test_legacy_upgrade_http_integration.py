"""Plan 1.3: after the org-authz upgrade, an existing user keeps using their resources.

Given an m77 (pre-org-authz) database with one user's agent, credential and conversation,
the current Alembic chain upgrades it to head and the current app — a real uvicorn process
on that database — lets the user log in, land in the default company/organization, see and
run the agent (scripted model), and find the conversation and credential intact.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import sqlalchemy as sa
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.engine import URL

from alembic import command
from app.models.conversation_run import RUN_ACTIVE_STATUSES
from app.security import cipher
from tests.authz.legacy_upgrade_fixture import (
    API_KEY,
    ENCRYPTION_KEY,
    PASSWORD,
    LegacyRows,
    live_backend,
    seed_legacy_rows,
    upgrade_lab,
)

pytestmark = pytest.mark.integration
HISTORY_TEXT = "What did we decide last week?"
PROMPT = "Hello after the upgrade"


async def _seed_history(url: URL, conversation_id: UUID) -> None:
    """A LangGraph checkpoint written before the upgrade (no tenant metadata)."""
    conninfo = url.set(drivername="postgresql").render_as_string(hide_password=False)
    async with AsyncPostgresSaver.from_conn_string(conninfo) as saver:
        await saver.setup()
        config: RunnableConfig = {
            "configurable": {"thread_id": str(conversation_id), "checkpoint_ns": ""}
        }
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"] = {
            "messages": [
                HumanMessage(id="legacy-human-1", content=HISTORY_TEXT),
                AIMessage(id="legacy-ai-1", content="We chose the blue plan."),
            ]
        }
        checkpoint["channel_versions"] = {"messages": "1"}
        await saver.aput(config, checkpoint, {"source": "loop", "step": 1}, {"messages": "1"})


def _texts(messages: object) -> list[str]:
    assert isinstance(messages, list)
    texts: list[str] = []
    for message in messages:
        content = message.get("content")
        texts.append(content if isinstance(content, str) else json.dumps(content))
    return texts


def _assert_scoped_to_default_org(lab_engine: sa.Engine, rows: LegacyRows, org_id: str) -> None:
    with lab_engine.connect() as connection:
        for table, key in (
            ("agents", rows.agent_id),
            ("credentials", rows.credential_id),
            ("conversations", rows.conversation_id),
        ):
            scoped = connection.execute(
                sa.text(f"SELECT org_id FROM {table} WHERE id = :id"),  # noqa: S608 - fixed names
                {"id": key},
            ).scalar_one()
            assert str(scoped) == org_id, table
        blob = connection.execute(
            sa.text("SELECT data_encrypted FROM credentials WHERE id = :id"),
            {"id": rows.credential_id},
        ).scalar_one()
    plain = cipher.decrypt(blob, [cipher.CipherKey.from_hex(ENCRYPTION_KEY)])
    assert json.loads(plain) == {"api_key": API_KEY}


def _run_agent(client: httpx.Client, conversation_id: UUID, csrf: str) -> dict[str, object]:
    """Start a run the way the v3 chat does (agent-protocol run.start) and wait for it."""
    started = client.post(
        f"/api/conversations/{conversation_id}/langgraph/threads/{conversation_id}/commands",
        json={
            "id": 1,
            "method": "run.start",
            "params": {"input": {"messages": [{"role": "user", "content": PROMPT}]}},
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert started.status_code == 200, started.text
    assert started.json()["type"] == "success", started.text
    run_id = started.json()["result"]["run_id"]
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        run = client.get(f"/api/conversations/{conversation_id}/runs/{run_id}")
        assert run.status_code == 200, run.text
        if run.json()["status"] not in RUN_ACTIVE_STATUSES:
            return run.json()
        time.sleep(0.5)
    raise AssertionError(f"run {run_id} did not finish")


def test_legacy_user_keeps_agent_credential_and_conversation_after_upgrade(
    tmp_path: Path,
) -> None:
    with upgrade_lab() as lab:
        # Given: m77 data of an existing user, including chat history in the checkpointer.
        rows = seed_legacy_rows(lab.engine)
        asyncio.run(_seed_history(lab.url, rows.conversation_id))

        # When: the current chain upgrades the database and the current app starts on it.
        command.upgrade(lab.config, "head")
        with (
            live_backend(lab.url, tmp_path) as base,
            httpx.Client(base_url=base, timeout=30) as client,
        ):
            login = client.post("/api/auth/login", json={"email": rows.email, "password": PASSWORD})
            assert login.status_code == 200, login.text
            csrf = login.json()["csrf_token"]

            # Then: the user lands in the single default company/organization.
            orgs = client.get("/api/me/organizations")
            assert orgs.status_code == 200, orgs.text
            assert len(orgs.json()) == 1, orgs.text
            org = orgs.json()[0]
            assert org["is_active"] is True
            _assert_scoped_to_default_org(lab.engine, rows, org["id"])

            # Then: the agent, the credential and the conversation are all visible.
            agents = client.get("/api/agents")
            assert agents.status_code == 200, agents.text
            assert str(rows.agent_id) in {agent["id"] for agent in agents.json()}
            credentials = client.get("/api/credentials")
            assert credentials.status_code == 200, credentials.text
            mine = [c for c in credentials.json() if c["id"] == str(rows.credential_id)]
            assert len(mine) == 1 and mine[0]["field_keys"] == ["api_key"], credentials.text
            conversations = client.get(f"/api/agents/{rows.agent_id}/conversations")
            assert conversations.status_code == 200, conversations.text
            assert str(rows.conversation_id) in {c["id"] for c in conversations.json()}
            history = client.get(f"/api/conversations/{rows.conversation_id}/messages")
            assert history.status_code == 200, history.text
            assert HISTORY_TEXT in _texts(history.json()["messages"])

            # Then: the agent still runs in the old conversation (scripted model).
            run = _run_agent(client, rows.conversation_id, csrf)
            log = (tmp_path / "backend.log").read_text()[-6000:]
            assert run["status"] == "completed", f"{run}\n--- backend log ---\n{log}"
            after = client.get(f"/api/conversations/{rows.conversation_id}/messages")
            assert after.status_code == 200, after.text
            texts = _texts(after.json()["messages"])
            assert HISTORY_TEXT in texts and PROMPT in texts, texts
            assert len(texts) >= 4, texts
