"""Guardrail middleware: check what goes to the model and to tools (guardrail-minimum-plan.md §3).

Stages covered here:

* ① ``input`` — the user's new message, once per run at the first model call; a block
  answers with a notice instead of calling the model, a warning is only recorded.
* ② ``llm_send`` — ``awrap_model_call`` changes only this call's copy of the messages;
  the stored history, checkpoint and screen keep the original values (plan §3.1). A block
  answers with a notice instead of calling the model.
* ③ ``tool_args`` / ④ ``tool_result`` — ``awrap_tool_call`` checks the arguments before the
  tool runs (a block returns a tool error the model can read) and the result before the
  model or the stored history sees it.

Deep Agents puts custom middleware *inside* its summarizer, whose summary call goes to the
model on its own. ``GuardedSummarizationMiddleware`` takes the summarizer's name, so it
replaces it in place and runs the same ② check first (plan §3.4.1). Only the async agent
API is guarded; the sync hooks refuse rather than run unchecked.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import Any, NamedTuple

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from deepagents.middleware.summarization import SummarizationState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import (
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langgraph.types import Command

from app.guardrails.actions import Applied, Decision, apply, block_notice
from app.guardrails.detectors import Finding
from app.guardrails.events import EventLog, RunScope, trace
from app.guardrails.model_pack import detect_texts
from app.guardrails.pseudonyms import Pseudonyms
from app.guardrails.rules import RuleSet, Stage
from app.guardrails.stream import GuardrailStreamTransformer
from app.security import key_provider

logger = logging.getLogger(__name__)

SUMMARIZATION_MIDDLEWARE_NAME = "SummarizationMiddleware"
_CACHE_SIZE = 2048
_SKIPPED_BLOCKS = frozenset({"thinking", "redacted_thinking", "reasoning"})
_SYNC_REFUSAL = "guardrails run on the async agent API only (ainvoke / astream)"


def hash_key(tenant_id: uuid.UUID | None) -> bytes:
    """The company's HMAC key for the ``hash`` action, derived from the active master key.

    Per company, so the same value hashes differently in two companies; a small value space
    (RRN) cannot be reversed by hashing every candidate without the key. Rotating the master
    key changes the tokens.
    """
    info = f"moldy-guardrail-hash|{tenant_id or 'platform'}".encode()
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(
        key_provider.get_active_key().raw
    )


class _Slot(NamedTuple):
    message: int
    kind: str  # "content", "block", "arg"
    key: tuple[Any, ...]


def _leaves(value: Any, path: tuple[Any, ...] = ()) -> list[tuple[tuple[Any, ...], str]]:
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, dict):
        return [leaf for k, v in value.items() for leaf in _leaves(v, (*path, k))]
    if isinstance(value, list | tuple):
        return [leaf for i, v in enumerate(value) for leaf in _leaves(v, (*path, i))]
    return []


def _replace_leaf(value: Any, path: tuple[Any, ...], text: str) -> Any:
    if not path:
        return text
    head, rest = path[0], path[1:]
    if isinstance(value, dict):
        return {**value, head: _replace_leaf(value[head], rest, text)}
    items = list(value)
    items[head] = _replace_leaf(items[head], rest, text)
    return items if isinstance(value, list) else tuple(items)


def _message_texts(index: int, message: BaseMessage) -> list[tuple[_Slot, str]]:
    found: list[tuple[_Slot, str]] = []
    content = message.content
    if isinstance(content, str):
        found.append((_Slot(index, "content", ()), content))
    elif isinstance(content, list):
        for i, block in enumerate(content):
            if isinstance(block, str):
                found.append((_Slot(index, "block", (i,)), block))
            elif isinstance(block, dict) and block.get("type") not in _SKIPPED_BLOCKS:
                if isinstance(block.get("text"), str):
                    found.append((_Slot(index, "block", (i, "text")), block["text"]))
                if isinstance(block.get("input"), dict):  # Anthropic tool_use blocks
                    for path, text in _leaves(block["input"]):
                        found.append((_Slot(index, "block", (i, "input", *path)), text))
    if isinstance(message, AIMessage):
        for c, call in enumerate(message.tool_calls):
            for path, text in _leaves(call.get("args")):
                found.append((_Slot(index, "arg", (c, *path)), text))
    return found


def _rebuild[M: BaseMessage](message: M, changes: dict[_Slot, str]) -> M:
    update: dict[str, Any] = {}
    content = message.content
    for slot, text in changes.items():
        if slot.kind == "content":
            content = text
        elif slot.kind == "block":
            blocks = list(content) if isinstance(content, list) else []
            i, *path = slot.key
            blocks[i] = text if not path else _replace_leaf(blocks[i], tuple(path), text)
            content = blocks
    if content is not message.content:
        update["content"] = content
    if isinstance(message, AIMessage):
        calls: list[Any] = list(message.tool_calls)
        for slot, text in changes.items():
            if slot.kind == "arg":
                c, *path = slot.key
                calls[c] = {**calls[c], "args": _replace_leaf(calls[c]["args"], tuple(path), text)}
        if any(slot.kind == "arg" for slot in changes):
            update["tool_calls"] = calls
    return message.model_copy(update=update)


@dataclass
class Guard:
    """One run's rules, company key and detection cache, shared by every hook of the run."""

    rules: RuleSet
    tenant_id: uuid.UUID | None
    # Where records go (plan §3.5); None writes nothing (tests, previews).
    scope: RunScope | None = None
    decisions: list[Decision] = field(default_factory=list)
    _cache: OrderedDict[str, list[Finding]] = field(default_factory=OrderedDict)
    _key: bytes | None = None
    _log: EventLog | None = None
    pseudonyms: Pseudonyms = field(init=False)
    # ① Human messages already checked this run (a run checks each new message once).
    _inputs_checked: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.scope is not None:
            self._log = EventLog(self.scope)
        self.pseudonyms = Pseudonyms(lambda: self.key, self.scope)

    @property
    def key(self) -> bytes:
        if self._key is None:
            self._key = hash_key(self.tenant_id)
        return self._key

    async def _findings(self, texts: Sequence[str], stage: Stage) -> list[list[Finding]]:
        """Detection per text; a text already seen this run is not detected again."""
        packs = self.rules.packs_for(stage)
        digests = [hashlib.sha256(f"{stage}\x00{t}".encode()).hexdigest() for t in texts]
        missing = [i for i, d in enumerate(digests) if d not in self._cache]
        if missing and packs:
            fresh = await detect_texts([texts[i] for i in missing], packs)
            for i, found in zip(missing, fresh, strict=True):
                self._cache[digests[i]] = found
                while len(self._cache) > _CACHE_SIZE:
                    self._cache.popitem(last=False)
        return [self._cache.get(d, []) for d in digests]

    async def check(
        self, texts: Sequence[str], stage: Stage, *, record: bool = True
    ) -> list[Applied]:
        """``record=False`` for display copies (the stream) of something already recorded."""
        if any(r.action == "pseudonymize" and stage in r.stages for r in self.rules.rules):
            await self.pseudonyms.load()
        if not record:
            return self._apply(texts, stage, await self._findings(texts, stage))
        run_id = self.scope.run_id if self.scope is not None else None
        with trace(run_id, stage, self.rules.template) as summary:
            results = self._apply(texts, stage, await self._findings(texts, stage))
            summary["decisions"] = sum(len(r.decisions) for r in results)
            summary["blocked"] = any(r.blocked for r in results)
        if self._log is not None:
            self._log.add(self.rules, stage, texts, results, self.key)
        for result in results:
            self.decisions.extend(result.decisions)
        if any(result.decisions for result in results):
            # Rule ids, entities and actions only; the values never reach the log.
            logger.info(
                "guardrail decisions stage=%s template=%s %s",
                stage,
                self.rules.template,
                sorted({(d.rule, d.entity, d.action) for r in results for d in r.decisions}),
            )
        return results

    def _apply(
        self, texts: Sequence[str], stage: Stage, findings: list[list[Finding]]
    ) -> list[Applied]:
        return [
            apply(
                text,
                found,
                self.rules,
                stage,
                self.key,
                lambda entity, value, rule, text=text: self.pseudonyms.token_for(
                    entity, value, style=rule.pseudonym_style, context=text
                ),
            )
            for text, found in zip(texts, findings, strict=True)
        ]

    async def flush(self) -> None:
        """Write this hook's records and new pseudonyms (best effort)."""
        if self._log is not None:
            await self._log.flush()
        await self.pseudonyms.flush()

    async def restore(self, text: str, *, for_tools: bool = False) -> str:
        """Pseudonyms back to values: always for the user; for tools only where a rule says so."""
        await self.pseudonyms.load()
        if not for_tools:
            return self.pseudonyms.restore(text)
        return self.pseudonyms.restore(
            text, keep=lambda entity: not self._restores_for_tools(entity)
        )

    def _restores_for_tools(self, entity: str) -> bool:
        return any(
            rule.action == "pseudonymize"
            and rule.restore_for_tools
            and (rule.entities is None or entity in rule.entities)
            for rule in self.rules.rules
        )

    async def guard_messages(
        self, messages: Sequence[AnyMessage]
    ) -> tuple[list[Any], tuple[Decision, ...]]:
        """② Return a changed copy of ``messages`` and the decisions; the input is untouched."""
        slots = [pair for i, m in enumerate(messages) for pair in _message_texts(i, m)]
        results = await self.check([text for _, text in slots], "llm_send")
        changes: dict[int, dict[_Slot, str]] = {}
        decisions: list[Decision] = []
        for (slot, text), result in zip(slots, results, strict=True):
            decisions.extend(result.decisions)
            if result.text != text:
                changes.setdefault(slot.message, {})[slot] = result.text
        guarded = [_rebuild(m, changes[i]) if i in changes else m for i, m in enumerate(messages)]
        return guarded, tuple(decisions)

    async def check_input(self, messages: Sequence[AnyMessage]) -> tuple[Decision, ...]:
        """① The latest user message, once per run, before the model sees anything.

        Checked at the model call (not ``before_agent``) so a block answers the way a
        blocked request does, through the model node the screen already renders.
        """
        if not self.rules.packs_for("input"):
            return ()
        human = next((m for m in reversed(messages) if isinstance(m, HumanMessage)), None)
        if human is None:
            return ()
        text = "\n".join(t for _, t in _message_texts(0, human))
        digest = hashlib.sha256(text.encode()).hexdigest()
        if not text or digest in self._inputs_checked:
            return ()
        self._inputs_checked.add(digest)
        [result] = await self.check([text], "input")
        return result.decisions

    async def guard_request(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[Any]],
    ) -> Any:
        attempted = await self.check_input(request.messages)
        if any(d.action == "block" for d in attempted):
            return ModelResponse(result=[AIMessage(content=block_notice(attempted))])
        messages, decisions = await self.guard_messages(request.messages)
        if any(d.action == "block" for d in decisions):
            return ModelResponse(result=[AIMessage(content=block_notice(decisions))])
        return await handler(request.override(messages=messages))


class GuardrailMiddleware(AgentMiddleware):
    """② and ⑤ around the model call (inside the attachment middleware), ③④ around tools.

    ⑤ is checked twice: the stream transformer guards what streams to the user, and the
    model response is checked here before the graph stores it.
    """

    def __init__(self, guard: Guard) -> None:
        super().__init__()
        self.guard = guard
        if guard.rules.packs_for("output") or guard.rules.packs_for("tool_result"):
            self.transformers = (partial(GuardrailStreamTransformer, guard=_StreamGuard(guard)),)

    @property
    def name(self) -> str:
        return "GuardrailMiddleware"

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        raise RuntimeError(_SYNC_REFUSAL)

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[Any]]
    ) -> Any:
        try:
            response = await self.guard.guard_request(request, handler)
            if isinstance(response, ExtendedModelResponse):
                checked = await self._guard_response(response.model_response)
                return ExtendedModelResponse(model_response=checked, command=response.command)
            if isinstance(response, ModelResponse):
                return await self._guard_response(response)
            return response
        finally:
            await self.guard.flush()

    async def _guard_response(self, response: ModelResponse) -> ModelResponse:
        """⑤ The answer as the graph stores it: AI text only (tool-call args go through ③)."""
        result: list[BaseMessage] = []
        changed = False
        for message in response.result:
            if isinstance(message, AIMessage):
                slots = [
                    (slot, text) for slot, text in _message_texts(0, message) if slot.kind != "arg"
                ]
                # Pseudonyms the model wrote come back as the values before the output check.
                restored = [await self.guard.restore(text) for _, text in slots]
                checked = await self.guard.check(restored, "output")
                if any(r.blocked for r in checked):
                    notice = block_notice(tuple(d for r in checked for d in r.decisions))
                    message = message.model_copy(update={"content": notice})
                    changed = True
                else:
                    edits = {
                        slot: r.text
                        for (slot, text), r in zip(slots, checked, strict=True)
                        if r.text != text
                    }
                    if edits:
                        message = _rebuild(message, edits)
                        changed = True
            result.append(message)
        if not changed:
            return response
        return ModelResponse(result=result, structured_response=response.structured_response)

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        raise RuntimeError(_SYNC_REFUSAL)

    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        try:
            return await self._guard_tool_call(request, handler)
        finally:
            await self.guard.flush()

    async def _guard_tool_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        call = request.tool_call
        leaves = _leaves(call.get("args"))
        restored = [await self.guard.restore(text, for_tools=True) for _, text in leaves]
        results = await self.guard.check(restored, "tool_args")
        decisions = tuple(d for r in results for d in r.decisions)
        if any(d.action == "block" for d in decisions):
            return ToolMessage(
                content=block_notice(decisions) + " 같은 인자로 다시 시도하지 마세요.",
                tool_call_id=call["id"],
                name=call.get("name"),
                status="error",
            )
        args = call.get("args")
        for (path, text), result in zip(leaves, results, strict=True):
            if result.text != text:
                args = _replace_leaf(args, path, result.text)
        if args is not call.get("args"):
            # A new call: the dict in the state's AIMessage.tool_calls stays as the model wrote it.
            request = request.override(tool_call={**call, "args": args})
        return await self._guard_result(await handler(request))

    async def _guard_result(self, response: Any) -> Any:
        if isinstance(response, ToolMessage):
            return await self._guard_tool_message(response)
        if isinstance(response, Command) and isinstance(response.update, dict):
            messages = response.update.get("messages")
            if isinstance(messages, list):
                guarded = [
                    await self._guard_tool_message(m) if isinstance(m, ToolMessage) else m
                    for m in messages
                ]
                return Command(
                    graph=response.graph,
                    update={**response.update, "messages": guarded},
                    resume=response.resume,
                    goto=response.goto,
                )
        return response

    async def _guard_tool_message(self, message: ToolMessage) -> ToolMessage:
        slots = _message_texts(0, message)
        results = await self.guard.check([text for _, text in slots], "tool_result")
        if any(r.blocked for r in results):
            notice = block_notice(tuple(d for r in results for d in r.decisions))
            return message.model_copy(update={"content": notice})
        changes = {
            slot: result.text
            for (slot, text), result in zip(slots, results, strict=True)
            if result.text != text
        }
        return _rebuild(message, changes) if changes else message


class GuardedSummarizationMiddleware(AgentMiddleware):
    """The Deep Agents summarizer behind the same ② check (replaces it by name, in place)."""

    state_schema = SummarizationState

    def __init__(self, inner: AgentMiddleware, guard: Guard) -> None:
        super().__init__()
        self.inner = inner
        self.guard = guard

    @property
    def name(self) -> str:
        return SUMMARIZATION_MIDDLEWARE_NAME

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        raise RuntimeError(_SYNC_REFUSAL)

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[Any]]
    ) -> Any:
        try:
            return await self.guard.guard_request(
                request, lambda guarded: self.inner.awrap_model_call(guarded, handler)
            )
        finally:
            await self.guard.flush()


class _StreamGuard:
    """The run's guard for display copies: same rules and cache, decisions not recorded twice."""

    def __init__(self, guard: Guard) -> None:
        self._guard = guard

    async def check(self, texts: Sequence[str], stage: Stage) -> list[Applied]:
        restored = [await self._guard.restore(text) for text in texts]
        return await self._guard.check(restored, stage, record=False)


def middleware_name(item: Any) -> str | None:
    name = getattr(item, "name", None)
    return name if isinstance(name, str) else None


def guard_stack(
    middleware: Sequence[Any],
    guard: Guard,
    *,
    create_summarizer: Callable[[], AgentMiddleware],
) -> list[Any]:
    """``middleware`` with the guardrail appended and the summarizer guarded.

    A summarizer already in the list (Moldy's actor-scoped child summarizer) is wrapped;
    otherwise one is created and wrapped, and it replaces Deep Agents' own by name.
    """
    stack = list(middleware)
    for i, item in enumerate(stack):
        if middleware_name(item) == SUMMARIZATION_MIDDLEWARE_NAME:
            stack[i] = GuardedSummarizationMiddleware(item, guard)
            break
    else:
        stack.append(GuardedSummarizationMiddleware(create_summarizer(), guard))
    stack.append(GuardrailMiddleware(guard))
    return stack
