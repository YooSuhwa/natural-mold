"""Exercise Skill Builder's private chat through actual tenant accounts and HTTP workers."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any
from uuid import UUID, uuid4

import httpx
from http_common import Gate, Participant, expect_status, open_gate, require


def _command() -> dict[str, Any]:
    return {
        "id": 1,
        "method": "run.start",
        "params": {"input": {"messages": [{"role": "user", "content": "E2E_TOKEN_USAGE_STREAM"}]}},
    }


async def _can_run(agent: str, user: str) -> bool:
    url = os.environ["OPENFGA_API_URL"]
    store = os.environ["OPENFGA_STORE_ID"]
    async with httpx.AsyncClient(base_url=url, trust_env=False) as client:
        answer = await client.post(
            f"/stores/{store}/check",
            headers={"Authorization": f"Bearer {os.environ['OPENFGA_API_TOKEN']}"},
            json={
                "authorization_model_id": os.environ["OPENFGA_MODEL_ID"],
                "tuple_key": {
                    "user": f"user:{user}",
                    "relation": "can_run",
                    "object": f"agent:{agent}",
                },
                "consistency": "HIGHER_CONSISTENCY",
            },
        )
        expect_status(answer, 200)
        return answer.json()["allowed"] is True


async def _terminal(owner: Participant, conversation: str, run_id: str) -> str:
    async with asyncio.timeout(80):
        while True:
            data = await owner.json("GET", f"/api/conversations/{conversation}/runs/{run_id}")
            if data["status"] not in {"accepted", "queued", "running"}:
                return data["status"]
            await asyncio.sleep(0.25)


async def _exercise(gate: Gate, mode: str, source: str | None) -> dict[str, Any]:
    owner, foreign = gate.other_b, gate.owner_a
    payload = {"mode": mode, "user_request": "E2E_TOKEN_USAGE_STREAM"}
    if source:
        payload["source_skill_id"] = source
    response = await owner.json("POST", "/api/skill-builder", payload, expected=201)
    agent = str(UUID(response["agent_id"]))
    conversation = str(UUID(response["conversation_id"]))
    session = str(UUID(response["id"]))
    path = f"/api/skill-builder/{session}"
    await owner.json("GET", path)
    await owner.json("GET", f"{path}/files")
    denied_session = await foreign.send("GET", path)
    absent_session = await foreign.send("GET", f"/api/skill-builder/{uuid4()}")
    endpoint = f"/api/conversations/{conversation}/langgraph/threads/{conversation}/commands"
    other = await foreign.send("POST", endpoint, _command())
    absent = str(uuid4())
    missing = await foreign.send(
        "POST",
        f"/api/conversations/{absent}/langgraph/threads/{absent}/commands",
        _command(),
    )
    share_path = f"/api/resources/agent/{agent}/grants"
    grant_body = {
        "principal": {
            "type": "organization",
            "id": os.environ["MOLDY_AGUI_OTHER_ORG_ID"],
        },
        "level": "consumer",
    }
    grants = await owner.send("GET", share_path)
    sharing = await owner.send("POST", share_path, grant_body)
    missing_share_path = f"/api/resources/agent/{uuid4()}/grants"
    missing_grants = await owner.send("GET", missing_share_path)
    missing_sharing = await owner.send("POST", missing_share_path, grant_body)
    transfer_body = {"new_owner_id": foreign.user_id}
    transfer = await owner.send("POST", f"/api/resources/agent/{agent}/transfer", transfer_body)
    missing_transfer = await owner.send(
        "POST", f"/api/resources/agent/{uuid4()}/transfer", transfer_body
    )
    owner_command = await owner.send("POST", endpoint, _command())
    command_body = owner_command.json()
    result = {
        "mode": mode,
        "conversation_id": conversation,
        "hidden_agent_id": agent,
        "owner_grants_get": grants.status_code,
        "owner_grants_post": sharing.status_code,
        "owner_grants_match_absent": (
            grants.json() == missing_grants.json()
            and missing_grants.status_code == 404
            and sharing.json() == missing_sharing.json()
            and missing_sharing.status_code == 404
        ),
        "session_created": 201,
        "session_read": 200,
        "files_read": 200,
        "owner_command_status": owner_command.status_code,
        "foreign_command_status": other.status_code,
        "foreign_command_matches_absent": other.json() == missing.json()
        and missing.status_code == 404,
        "foreign_session_status": denied_session.status_code,
        "foreign_session_matches_absent": denied_session.json() == absent_session.json()
        and absent_session.status_code == 404,
        "owner_transfer_status": transfer.status_code,
        "owner_transfer_matches_absent": transfer.json() == missing_transfer.json()
        and missing_transfer.status_code == 404,
        "owner_fga_can_run": await _can_run(agent, owner.user_id),
        "foreign_fga_can_run": await _can_run(agent, foreign.user_id),
    }
    for key, url in (
        ("owner_in_agent_index", "/api/agents"),
        ("owner_in_agent_summary", "/api/agents/summary"),
    ):
        agents = await owner.json("GET", url)
        result[key] = any(row["id"] == agent for row in agents)
    if owner_command.status_code == 200 and command_body.get("type") == "success":
        run_id = str(UUID(command_body["result"]["run_id"]))
        result["physical_terminal"] = await _terminal(owner, conversation, run_id)
    else:
        details = command_body.get("error", command_body.get("detail", {}))
        if isinstance(details, dict):
            code = details.get("code", "")
            if isinstance(code, str) and re.fullmatch(r"[A-Za-z_]{1,96}", code):
                result["error_code"] = code
    return result


async def main() -> None:
    async with open_gate() as gate:
        skill = await gate.other_b.json(
            "POST",
            "/api/skills",
            {
                "name": f"builder-private-{uuid4().hex[:12]}",
                "content": "---\nname: builder-private\ndescription: Test private drafts.\n---\n\nOriginal.\n",
            },
            expected=201,
        )
        rows = [
            await _exercise(gate, "create", None),
            await _exercise(gate, "improve", skill["id"]),
        ]
        receipt = {
            "builder_account": "ordinary",
            "foreign_account": "other_company_operator",
            "cases": rows,
        }
        (gate.evidence_dir / "skill-builder-private-chat.json").write_text(
            json.dumps(receipt, indent=2) + "\n"
        )
        require(
            rows[0]["hidden_agent_id"] == rows[1]["hidden_agent_id"],
            "existing private builder was replaced",
        )
        for row in rows:
            require(
                row["owner_command_status"] == 200
                and row.get("physical_terminal") == "completed"
                and row["owner_fga_can_run"],
                f"{row['mode']} private owner command or physical run failed",
            )
            require(
                row["foreign_command_status"] == row["foreign_session_status"] == 404
                and row["foreign_command_matches_absent"]
                and row["foreign_session_matches_absent"]
                and not row["owner_in_agent_index"]
                and not row["owner_in_agent_summary"]
                and not row["foreign_fga_can_run"]
                and row["owner_grants_get"] == row["owner_grants_post"] == 404
                and row["owner_grants_match_absent"]
                and row["owner_transfer_status"] == 404
                and row["owner_transfer_matches_absent"],
                f"{row['mode']} private scope or hidden listing changed",
            )


if __name__ == "__main__":
    asyncio.run(main())
