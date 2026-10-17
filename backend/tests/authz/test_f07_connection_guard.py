"""F-07 ConnectionGuardMiddleware on a real Deep Agents graph (plan 11.4, 11.5).

Done-when: calls outside the plan are blocked (siblings still run) and every
connection problem of one model step becomes ONE ``auth_required`` interrupt raised
outside the ToolNode — before the calls (credential re-read) or after all siblings
finished (F-12 artifacts) — with ``retry``/``skip``/``cancel`` resumes and no
re-execution of finished siblings.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from app.agent_runtime.run_error_codes import denial_error_code
from app.agent_runtime.runtime.connection_guard_items import ConnectionBlockedError
from app.agent_runtime.runtime.connection_guard_policy import CredentialState
from app.authz.dispatch_block import block_reason
from tests.authz.f07_support import (
    ME,
    OTHER,
    Ledger,
    auth_failing_tool,
    calls,
    guard,
    guarded,
    interrupts,
    plain_tool,
    respond,
    run_graph,
    tool_messages,
)

DONE = AIMessage(content="done")
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _broken(ledger: Ledger, *ids: str, owner: str = ME) -> None:
    for cid in ids:
        ledger.states[cid] = CredentialState("expired", owner, "mcp_oauth2")


async def test_a_call_outside_the_plan_is_refused_and_its_sibling_runs() -> None:
    ledger = Ledger()
    tools = [plain_tool(ledger, "allowed"), plain_tool(ledger, "excluded")]
    middleware = [guard(ledger, denied={"excluded": "permission_required"})]
    result = await run_graph([calls("allowed", "excluded"), DONE], tools, middleware).start()

    assert ledger.ran == ["allowed"]
    refusal = tool_messages(result)["call-excluded"]
    assert refusal.status == "error"
    assert refusal.artifact == {"connection_guard": {"code": "permission_required"}}
    assert not interrupts(result)


async def test_pre_call_problems_are_one_bundled_interrupt_before_anything_runs() -> None:
    ledger = Ledger()
    _broken(ledger, "c1", "c2")
    tools = [plain_tool(ledger, n) for n in ("a", "b", "c", "free")]
    policy = {"a": guarded("a", "c1"), "b": guarded("b", "c1"), "c": guarded("c", "c2")}
    run = run_graph([calls("a", "b", "c", "free"), DONE], tools, [guard(ledger, guarded=policy)])

    first = await run.start()
    (payload,) = interrupts(first)
    assert ledger.ran == []
    assert payload["type"] == "auth_required" and payload["allowed_decisions"] == ["respond"]
    by_credential = {item["credential_id"]: item for item in payload["items"]}
    assert by_credential["c1"]["tool_names"] == ["a", "b"]  # same connection: one item
    assert by_credential["c2"]["tool_names"] == ["c"]
    assert by_credential["c1"]["fixable_by"] == "me"

    done = await run.resume(respond("skip"))
    assert ledger.ran == ["free"]
    messages = tool_messages(done)
    assert {messages[f"call-{n}"].artifact["connection_guard"]["code"] for n in "abc"} == {
        "skipped"
    }


async def test_retry_re_reads_the_credential_and_asks_again_until_connected() -> None:
    ledger = Ledger(flip_after=3)  # reads: start, re-run, retry
    _broken(ledger, "c1")
    tools = [plain_tool(ledger, "a")]
    run = run_graph([calls("a"), DONE], tools, [guard(ledger, guarded={"a": guarded("a", "c1")})])

    first = await run.start()
    again = await run.resume(respond("retry"))  # second read: still broken
    assert [p["type"] for p in interrupts(again)] == ["auth_required"]
    # Same LangGraph interrupt id (same task), so the re-ask carries a new attempt.
    assert [i.id for i in again["__interrupt__"]] == [i.id for i in first["__interrupt__"]]
    assert [interrupts(first)[0]["attempt"], interrupts(again)[0]["attempt"]] == [1, 2]
    assert ledger.ran == []
    await run.resume(respond("retry"))  # connected now
    assert ledger.ran == ["a"]


async def test_cancel_answers_every_call_and_ends_the_run() -> None:
    ledger = Ledger()
    _broken(ledger, "c1")
    tools = [plain_tool(ledger, "a"), plain_tool(ledger, "free")]
    model_steps = [calls("a", "free"), AIMessage(content="never")]
    run = run_graph(model_steps, tools, [guard(ledger, guarded={"a": guarded("a", "c1")})])
    await run.start()
    done = await run.resume(respond("cancel"))

    assert ledger.ran == []
    codes = {m.artifact["connection_guard"]["code"] for m in tool_messages(done).values()}
    assert codes == {"cancelled"}
    assert not any(getattr(m, "content", "") == "never" for m in done["messages"])


async def test_post_call_auth_errors_bundle_after_all_siblings_without_re_running_them() -> None:
    ledger = Ledger()
    tools = [
        auth_failing_tool(ledger, "slack_post", "c1"),
        auth_failing_tool(ledger, "slack_read", "c1"),
        plain_tool(ledger, "free"),
    ]
    run = run_graph([calls("slack_post", "slack_read", "free"), DONE], tools, [guard(ledger)])

    first = await run.start()
    (payload,) = interrupts(first)
    assert sorted(ledger.ran) == ["free", "slack_post", "slack_read"]  # all siblings finished
    (item,) = payload["items"]
    assert item["tool_names"] == ["slack_post", "slack_read"]

    done = await run.resume(respond("skip"))
    assert sorted(ledger.ran) == ["free", "slack_post", "slack_read"]  # nothing ran twice
    skipped = tool_messages(done)["call-slack_post"]
    assert skipped.artifact["connection_guard"] == {"code": "skipped"}
    assert skipped.artifact["mcp_auth_error"]["interrupt"] is None
    assert done["messages"][-1].content == "done"


async def test_owner_fixable_problems_notify_the_owner_instead_of_interrupting(caplog) -> None:
    ledger = Ledger()
    _broken(ledger, "c1", owner=OTHER)  # someone else's shared connection
    tools = [plain_tool(ledger, "a")]
    run = run_graph([calls("a"), DONE], tools, [guard(ledger, guarded={"a": guarded("a", "c1")})])
    with caplog.at_level("INFO"):
        result = await run.start()

    assert not interrupts(result)
    assert ledger.ran == []
    code = tool_messages(result)["call-a"].artifact["connection_guard"]["code"]
    assert code == "owner_reconnect_required"
    assert "owner_notification" in caplog.text


async def test_runs_without_a_user_end_blocked_with_a_recorded_reason() -> None:
    ledger = Ledger()
    _broken(ledger, "c1")
    middleware = [guard(ledger, mode="raise", guarded={"a": guarded("a", "c1")})]
    run = run_graph([calls("a"), DONE], [plain_tool(ledger, "a")], middleware)
    with pytest.raises(ConnectionBlockedError) as blocked:
        await run.start()
    assert denial_error_code(blocked.value) == "auth_required"
    assert block_reason(blocked.value) == "auth_required"  # F-08 BLOCKED trigger reason
    assert blocked.value.payload["items"][0]["credential_id"] == "c1"

    post = run_graph(
        [calls("slack_post"), DONE],
        [auth_failing_tool(ledger, "slack_post", "c2")],
        [guard(ledger, mode="raise")],
    )
    with pytest.raises(ConnectionBlockedError):
        await post.start()


async def test_the_guard_checks_connections_before_the_approval_step() -> None:
    ledger = Ledger()
    _broken(ledger, "c1")
    tools = [plain_tool(ledger, "risky"), plain_tool(ledger, "excluded")]
    approve = {"allowed_decisions": ["approve", "reject"]}
    middleware = [
        guard(
            ledger,
            denied={"excluded": "permission_required"},
            guarded={"risky": guarded("risky", "c1")},
        )
    ]
    run = run_graph(
        [calls("risky", "excluded"), DONE],
        tools,
        middleware,
        interrupt_on={"risky": approve, "excluded": approve},
    )

    first = await run.start()
    assert [p.get("type") for p in interrupts(first)] == ["auth_required"]
    ledger.flip_after = ledger.reads
    approval = await run.resume(respond("retry"))
    (hitl,) = interrupts(approval)
    assert [a["name"] for a in hitl["action_requests"]] == ["risky"]  # refused call not offered
    await run.resume({"decisions": [{"type": "approve"}]})
    assert ledger.ran == ["risky"]


async def test_the_last_check_refuses_unknown_tools_and_fresh_denials() -> None:
    ledger = Ledger()

    async def denied() -> bool:
        return False

    async def unavailable() -> None:
        return None

    tools = [plain_tool(ledger, "shared"), plain_tool(ledger, "down")]
    middleware = [guard(ledger, checks={"shared": denied, "down": unavailable})]
    result = await run_graph([calls("shared", "down", "ghost"), DONE], tools, middleware).start()

    codes = {k: m.artifact["connection_guard"]["code"] for k, m in tool_messages(result).items()}
    assert codes == {
        "call-shared": "permission_required",
        "call-down": "authz_unavailable",
        "call-ghost": "tool_not_allowed",
    }
    assert ledger.ran == []


async def test_auth_needed_and_unknown_states_do_not_block_before_the_call() -> None:
    ledger = Ledger()
    ledger.states["c1"] = CredentialState("auth_needed", ME, "mcp_oauth2")  # F-05 refresh path
    tools = [plain_tool(ledger, "refreshable"), plain_tool(ledger, "unknown")]
    policy = {"refreshable": guarded("refreshable", "c1"), "unknown": guarded("unknown", "gone")}
    run = run_graph([calls("refreshable", "unknown"), DONE], tools, [guard(ledger, guarded=policy)])
    result = await run.start()
    assert not interrupts(result)
    assert sorted(ledger.ran) == ["refreshable", "unknown"]


async def test_a_vanished_per_user_binding_asks_to_connect() -> None:
    ledger = Ledger()
    ledger.states["c1"] = CredentialState("active", ME, "mcp_oauth2", bound=False)
    tool = guarded("a", "c1", mode="per_user", account=ME)
    run = run_graph(
        [calls("a"), DONE], [plain_tool(ledger, "a")], [guard(ledger, guarded={"a": tool})]
    )
    (payload,) = interrupts(await run.start())
    assert payload["items"][0]["connect"] == {"kind": "binding"}
    assert payload["items"][0]["fixable_by"] == "me"


async def test_subagent_graphs_only_refuse_and_never_raise_or_interrupt() -> None:
    ledger = Ledger()
    _broken(ledger, "c1")
    middleware = [guard(ledger, mode="refuse", guarded={"a": guarded("a", "c1")})]
    pre = await run_graph([calls("a"), DONE], [plain_tool(ledger, "a")], middleware).start()
    assert not interrupts(pre) and ledger.ran == []
    assert tool_messages(pre)["call-a"].artifact["connection_guard"] == {"code": "auth_required"}

    post = await run_graph(
        [calls("slack_post"), DONE],
        [auth_failing_tool(ledger, "slack_post", "c2")],
        [guard(ledger, mode="refuse")],
    ).start()
    assert not interrupts(post)
    message = tool_messages(post)["call-slack_post"]
    assert message.artifact["mcp_auth_error"]["interrupt"] is not None  # left as the error
    assert post["messages"][-1].content == "done"


async def test_owner_notifications_are_sent_once_per_run_and_credential(
    caplog, monkeypatch
) -> None:
    from app.agent_runtime.runtime import connection_guard_items as items

    sent = AsyncMock()
    monkeypatch.setattr(items, "owner_needed", sent)  # H-02 delivery: test_h02_runtime_events
    item = {"resource_name": "Slack", "credential_id": "c-owner", "tool_names": ["a"]}
    with caplog.at_level("INFO"):
        await items.notify_owner([item, item], run="run-1")
        await items.notify_owner([item], run="run-1")
        await items.notify_owner([item], run="run-2")
    assert caplog.text.count("owner_notification") == 2
    assert sent.await_count == 2
