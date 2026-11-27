"""Read a legacy file-producing chat through both transports and adjacent public APIs."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any
from uuid import UUID, uuid4

import httpx
from http_common import Gate, Participant, expect_status, require

FILE_NAME = "e2e-runtime-policy-artifact.md"
FILE_TEXT = "E2E runtime artifact final content.\n"
SCRIPTED_QUESTION = "E2E_TOKEN_USAGE_STREAM"
SCRIPTED_ANSWER = "E2E token usage isolated conversation response."


async def _legacy_turn(owner: Participant, cid: str, question: str) -> str:
    command = {
        "id": 1,
        "method": "run.start",
        "params": {
            "input": {
                "messages": [
                    {"role": "user", "content": question},
                ]
            }
        },
    }
    response = await owner.send(
        "POST", f"/api/conversations/{cid}/langgraph/threads/{cid}/commands", command
    )
    expect_status(response, 200)
    require(
        response.headers.get("x-stream-protocol") == "langgraph_v3",
        "legacy protocol changed",
    )
    payload = response.json()
    require(payload["type"] == "success", "existing command rejected")
    run_id = str(UUID(payload["result"]["run_id"]))
    async with asyncio.timeout(90):
        while True:
            run = await owner.json("GET", f"/api/conversations/{cid}/runs/{run_id}")
            if run["status"] == "completed":
                return run_id
            require(
                run["status"] in {"accepted", "queued", "running"},
                "legacy run failed or stalled",
            )
            await asyncio.sleep(0.25)


def _message_id(messages: list[dict[str, Any]], role: str, content: str) -> str:
    ids = [
        message["id"]
        for message in messages
        if message["role"] == role and message["content"].strip() == content.strip()
    ]
    require(len(ids) == 1, f"expected one {role} message with the scripted content")
    return str(ids[0])


async def _share_roundtrip(
    gate: Gate,
    cid: str,
    message: str,
    private_comment: str,
    artifact_id: str,
) -> None:
    route = f"/api/conversations/{cid}/share"
    link = await gate.owner_a.json("POST", route)
    token = link["share_token"]
    async with httpx.AsyncClient(
        base_url=gate.owner_b.client.base_url, trust_env=False
    ) as visitor:
        public = f"/api/shares/{token}"
        try:
            current = await gate.owner_b.json("GET", route)
            require(
                current["share_token"] == token,
                "share token not durable on other worker",
            )
            denied = await gate.other_b.send("POST", route)
            require(
                denied.status_code == 404, "foreign owner can publish the conversation"
            )
            view = await visitor.get(public)
            require(
                view.status_code == 200, "anonymous visitor could not open the share"
            )
            public_messages = view.json()["messages"]
            _message_id(public_messages, "assistant", message)
            require(
                private_comment not in view.text,
                "private feedback leaked into the public view",
            )
            require(
                (await visitor.get(f"/api/conversations/{cid}/artifacts")).status_code
                == 401,
                "anonymous visitor could open the owner's artifact route",
            )
            public_files = await visitor.get(f"{public}/artifacts")
            require(
                public_files.status_code == 200,
                "visitor cannot read the published file index",
            )
            visible = [a for a in public_files.json() if a["id"] == artifact_id]
            require(
                len(visible) == 1
                and visible[0]["preview_url"].startswith(f"{public}/artifacts/"),
                "public artifact does not have its token-scoped preview URL",
            )
            preview = await visitor.get(visible[0]["preview_url"])
            require(
                preview.status_code == 200 and preview.json()["text"] == FILE_TEXT,
                "shared file preview changed",
            )
            # A second valid token must not resolve a real file from this conversation.
            _, other_cid = await gate.create_chat("Unrelated public conversation")
            other_route = f"/api/conversations/{other_cid}/share"
            unrelated = await gate.owner_b.json("POST", other_route)
            other_public = f"/api/shares/{unrelated['share_token']}"
            try:
                require(
                    (await visitor.get(other_public)).status_code == 200,
                    "second share failed",
                )
                denied = await visitor.get(
                    f"{other_public}/artifacts/{artifact_id}/content"
                )
                require(
                    denied.status_code == 404,
                    "a valid token served a different chat's file",
                )
            finally:
                expect_status(await gate.owner_a.send("DELETE", other_route), 204)
        finally:
            require(
                (await gate.owner_a.send("DELETE", route)).status_code == 204,
                "share revoke failed",
            )
        require(
            (await visitor.get(public)).status_code == 404,
            "revoked share still served by worker B",
        )
        require(
            (await visitor.get(f"{public}/artifacts/{artifact_id}/content")).status_code
            == 404,
            "revoked share still serves a public file",
        )


async def _agent_api(gate: Gate, aid: str) -> None:
    """Exercise authenticated external execution on the local scripted model, separate from chat."""
    deployment = await gate.owner_a.json(
        "POST",
        "/api/agent-api/deployments",
        {
            "agent_id": aid,
            "allow_streaming": False,
            "allow_background": False,
            "approval_policy": "blocked",
        },
        expected=201,
    )
    did, public_id = deployment["id"], deployment["public_id"]
    other_deployments = await gate.other_b.json("GET", "/api/agent-api/deployments")
    require(
        all(d["id"] != did for d in other_deployments),
        "deployment exposed to another company",
    )
    created = await gate.owner_a.json(
        "POST",
        "/api/agent-api/keys",
        {
            "name": f"cutover-{uuid4().hex[:8]}",
            "scopes": ["invoke", "read"],
            "deployment_ids": [did],
        },
        expected=201,
    )
    auth = {"Authorization": "Bearer " + created["key"]}
    async with httpx.AsyncClient(
        base_url=gate.owner_b.client.base_url, trust_env=False, timeout=90
    ) as ext:
        try:
            start = await ext.post(
                "/v1/threads", headers=auth, json={"agent_id": public_id}
            )
            expect_status(start, 201)
            tid = start.json()["id"]
            reply = await ext.post(
                f"/v1/threads/{tid}/runs/wait",
                headers=auth,
                json={
                    "agent_id": public_id,
                    "input": {
                        "messages": [{"role": "user", "content": SCRIPTED_QUESTION}]
                    },
                },
            )
            expect_status(reply, 200)
            result = reply.json()
            require(
                result["status"] == "succeeded"
                and result["output"]["answer"] == SCRIPTED_ANSWER,
                "external API did not produce its existing native run result",
            )
            require(
                result["thread_id"] == tid and result["agent_id"] == public_id,
                "external result lost deployment or thread identity",
            )
            read = f"/v1/runs/{result['id']}"
            expect_status(await ext.get(read), 401)
            fetched = await ext.get(read, headers=auth)
            expect_status(fetched, 200)
            require(
                fetched.json()["output"] == result["output"],
                "persisted external output changed",
            )
            foreign = await gate.other_b.json(
                "POST",
                "/api/agent-api/keys",
                {
                    "name": f"foreign-read-{uuid4().hex[:8]}",
                    "scopes": ["read"],
                    "allow_all_deployments": True,
                },
                expected=201,
            )
            try:
                foreign_read = await ext.get(
                    read, headers={"Authorization": "Bearer " + foreign["key"]}
                )
                expect_status(foreign_read, 404)
            finally:
                await gate.other_b.json(
                    "POST", f"/api/agent-api/keys/{foreign['id']}/revoke"
                )
        finally:
            await gate.owner_a.json(
                "POST", f"/api/agent-api/keys/{created['id']}/revoke"
            )
        expect_status(await ext.get(read, headers=auth), 401)


async def _assistant_panel(gate: Gate, aid: str) -> None:
    # The Assistant uses the platform primary role independently of the conversation's model.
    rows = await gate.sql(
        "SELECT c.definition_key AS provider,s.model_name FROM system_llm_settings s "
        "LEFT JOIN credentials c ON c.id=s.credential_id WHERE s.role='text_primary' "
        "AND (s.tenant_id IS NULL OR s.tenant_id=%s)",
        (UUID(gate.tenant_id),),
    )
    require(
        bool(rows)
        and all(
            r["provider"] == "e2e_scripted"
            and r["model_name"] == "document-artifact-scripted"
            for r in rows
        ),
        "Assistant requires a local scripted text_primary role",
    )
    route = f"/api/agents/{aid}/assistant/message"
    request = {"content": SCRIPTED_QUESTION, "session_id": str(uuid4())}
    expect_status(await gate.other_b.send("POST", route, request), 404)
    response = await gate.owner_a.send("POST", route, request)
    expect_status(response, 200)
    require(
        "text/event-stream" in response.headers["content-type"],
        "Assistant expected native SSE",
    )
    records = [
        json.loads(line.removeprefix("data:").strip())
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]
    endings = [
        r
        for r in records
        if r.get("status") == "completed" and r.get("content") == SCRIPTED_ANSWER
    ]
    require(
        len(endings) == 1 and "event: message_end" in response.text,
        "Assistant lost the native completion event",
    )
    require("event: error" not in response.text, "Assistant emitted an error")


async def _legacy_branch_roundtrip(
    gate: Gate, cid: str, original: dict[str, Any]
) -> None:
    # An edit creates a sibling leaf. Selecting an ancestor alone follows its newest leaf.
    base = f"/api/conversations/{cid}/messages"
    selected = original["active_checkpoint_id"]
    original_id = _message_id(original["messages"], "user", SCRIPTED_QUESTION)
    revised = SCRIPTED_QUESTION + " after transport rollback"
    edit = await gate.owner_a.send(
        "POST", base + "/edit", {"message_id": original_id, "new_content": revised}
    )
    expect_status(edit, 200)
    require(
        "event: message_end" in edit.text,
        "legacy client could not edit an existing turn",
    )
    fork = await gate.owner_b.json("GET", base)
    fork_id = _message_id(fork["messages"], "user", revised)
    fork_checkpoint = fork["active_checkpoint_id"]
    require(
        fork_id != original_id and fork_checkpoint != selected,
        "edit failed to fork history",
    )
    _message_id(fork["messages"], "assistant", SCRIPTED_ANSWER)

    statement = "SELECT count(*) AS n FROM conversation_runs WHERE conversation_id=%s"
    before = await gate.sql(statement, (UUID(cid),))
    # Existing and edited branches should both remain selectable from either worker.
    for expected in (original, fork, original):
        checkpoint = expected["active_checkpoint_id"]
        expect_status(
            await gate.owner_b.send(
                "POST", base + "/switch-branch", {"checkpoint_id": checkpoint}
            ),
            204,
        )
        actual = await gate.owner_a.json("GET", base)
        require(
            actual["active_checkpoint_id"] == checkpoint
            and [m["id"] for m in actual["messages"]]
            == [m["id"] for m in expected["messages"]],
            "switching branch selected the wrong message IDs or duplicated a turn",
        )
    after = await gate.sql(statement, (UUID(cid),))
    require(
        before == after == [{"n": 3}],
        "reading or switching branches created an extra run",
    )


async def check_compatibility(gate: Gate) -> dict[str, object]:
    aid, cid = await gate.create_chat(
        "Retained file and message feedback",
        agent_overrides={
            "identity_mode": "fixed",
            "runtime_policy": {
                "version": 1,
                "filesystem": {"mode": "artifact_write"},
                "todo": {"enabled": True},
                "summarization": {"mode": "auto"},
            },
        },
    )
    base = f"/api/conversations/{cid}"
    question = f"E2E_RUNTIME_FILESYSTEM_ARTIFACT_WRITE in /conversations/{cid}/"
    await _legacy_turn(gate.owner_a, cid, question)
    old = await gate.owner_a.json("GET", base + "/messages")
    prior_checkpoint = old["active_checkpoint_id"]
    require(bool(prior_checkpoint), "file-producing turn has no branch checkpoint")
    message_id = _message_id(old["messages"], "assistant", FILE_TEXT)
    private_comment = f"private feedback {uuid4()}"
    rating = await gate.owner_a.json(
        "POST",
        f"/api/messages/{message_id}/feedback",
        {
            "conversation_id": cid,
            "rating": "up",
            "comment": private_comment,
        },
    )
    require(
        rating["message_id"] == message_id, "feedback attached to a different message"
    )
    files = await gate.owner_a.json("GET", base + "/artifacts")
    (artifact,) = [
        f for f in files if f["display_name"] == FILE_NAME and f["status"] == "ready"
    ]
    require(
        artifact["version_number"] >= 2,
        "scripted write and edit must both have persisted",
    )
    hash_value = hashlib.sha256(FILE_TEXT.encode()).hexdigest()
    require(artifact["sha256"] == hash_value, "initial artifact has unexpected bytes")
    modern_run = str(uuid4())
    reply = await gate.owner_b.send(
        "POST",
        base + "/ag-ui/runs",
        {
            "threadId": cid,
            "runId": modern_run,
            "protocolVersion": "1.0",
            "messages": [
                {"role": "user", "id": str(uuid4()), "content": SCRIPTED_QUESTION}
            ],
            "state": {},
            "context": [],
            "tools": [],
            "forwardedProps": {},
        },
    )
    frames = gate.check_sse(reply)
    require(
        frames[-1]["type"] == "RUN_FINISHED"
        and frames[-1].get("outcome", {"type": "success"})["type"] == "success",
        "old conversation did not resume under AG-UI",
    )
    current = await gate.owner_a.json("GET", base + "/messages")
    require(
        _message_id(current["messages"], "assistant", FILE_TEXT) == message_id,
        "public ID of historical assistant message changed",
    )
    current_id = _message_id(current["messages"], "assistant", SCRIPTED_ANSWER)
    new_checkpoint = current["active_checkpoint_id"]
    require(
        bool(new_checkpoint) and new_checkpoint != prior_checkpoint,
        "continuation did not advance branch",
    )
    snap = await gate.owner_b.json("GET", base + f"/ag-ui/snapshot?runId={modern_run}")
    require(
        snap["checkpointId"] == new_checkpoint,
        "AG-UI and existing history disagree on checkpoint",
    )
    for content, ui_id in ((FILE_TEXT, message_id), (SCRIPTED_ANSWER, current_id)):
        public_message_id = _message_id(snap["messages"], "assistant", content)
        match = [
            link
            for link in snap["messageLinks"]
            if link["messageId"] == public_message_id
        ]
        require(
            len(match) == 1 and match[0]["uiMessageId"] == ui_id,
            "public AG-UI message no longer maps to its protected message and feedback ID",
        )
    (old_rating,) = await gate.owner_b.json("GET", base + "/feedback")
    require(
        old_rating["id"] == rating["id"] and old_rating["comment"] == private_comment,
        "historical message feedback changed",
    )
    artifact_after = await gate.owner_b.json(
        "GET", base + f"/artifacts/{artifact['id']}"
    )
    require(
        (artifact_after["version_id"], artifact_after["sha256"])
        == (artifact["version_id"], hash_value),
        "file version or bytes changed after switching transports",
    )
    downloaded = await gate.owner_b.send("GET", artifact_after["download_url"])
    expect_status(downloaded, 200)
    require(
        hashlib.sha256(downloaded.content).hexdigest() == hash_value,
        "downloaded historical file changed",
    )
    for suffix in ("/messages", "/feedback", f"/artifacts/{artifact['id']}/content"):
        expect_status(await gate.other_b.send("GET", base + suffix), 404)
    await _legacy_branch_roundtrip(gate, cid, current)
    await _share_roundtrip(gate, cid, SCRIPTED_ANSWER, private_comment, artifact["id"])
    await _agent_api(gate, aid)
    await _assistant_panel(gate, aid)
    return {
        "historical_message_id": message_id,
        "artifact_version": artifact["version_id"],
        "artifact_hash": hash_value,
        "native_and_standard_turns": 2,
        "legacy_branch_edit": 1,
        "branch_switch_extra_runs": 0,
        "foreign_private_reads_denied": True,
        "public_share_revoked": True,
        "scripted_external_api_read_and_revoke": True,
        "native_assistant_response": True,
    }
