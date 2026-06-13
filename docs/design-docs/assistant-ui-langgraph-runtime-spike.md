# Assistant UI LangGraph Runtime Spike

Date: 2026-06-13

Purpose: verify the official assistant-ui LangGraph runtime contract and the local DeepAgents/LangGraph streaming shape before implementing the chat streaming migration.

## Scope

This spike did not modify project dependencies. The work used isolated package inspection:

- npm metadata and tarball inspection for `@assistant-ui/react-langgraph`.
- isolated Python target install under `/private/tmp/moldy-deepagents-site` for `deepagents==0.6.1`.
- source inspection of the installed packages.
- local source inspection of `/Users/chester/dev/langgraph`, `/Users/chester/dev/deepagents`, and `/Users/chester/dev/langgraphjs`.
- local source inspection of `/Users/chester/dev/streaming-cookbook`, `/Users/chester/dev/deep-agents-ui`, and `/Users/chester/dev/assistant-ui`.
- minimal fake-model streaming checks.

The current worktree did not have `frontend/node_modules`, `backend/.venv`, `uv`, or `mise` available in PATH, so this was intentionally a compatibility spike rather than a repo install.

## Frontend Findings

Current Moldy dependencies:

- `@assistant-ui/react`: `^0.12.24`
- `@assistant-ui/react-streamdown`: `^0.1.9`
- missing: `@assistant-ui/react-langgraph`
- missing: `@langchain/langgraph-sdk`

Latest package metadata observed during the spike:

- `@assistant-ui/react-langgraph`: `0.14.7`
- `@assistant-ui/react-langchain`: `0.0.13`
- `@assistant-ui/react`: `0.14.18`
- `@assistant-ui/react-streamdown`: `0.3.3`
- `@langchain/langgraph-sdk`: `1.9.21`

Local `langgraphjs` package metadata observed after the initial spike:

- `@langchain/react`: `1.0.22`
- `@langchain/langgraph-sdk`: `1.9.21`
- `@langchain/protocol`: `^0.0.16`
- local DeepAgents frontend examples use `deepagents ^1.8.3` on the JavaScript side.

`@assistant-ui/react-langgraph@0.14.7` peer dependencies:

- `react: ^18 || ^19`
- `@langchain/langgraph-sdk: ^1.8.0`

The actual implementation should first add the LangChain runtime packages. Add assistant-ui adapter packages only if the local Moldy wrapper imports their stable utility exports:

```bash
pnpm add @langchain/react@latest @langchain/langgraph-sdk@latest @langchain/core@latest
```

If importing from `@assistant-ui/react-langchain` or `@assistant-ui/react-langgraph` requires upgrading `@assistant-ui/react` / `@assistant-ui/react-streamdown`, upgrade those packages together and verify whether `@streamdown/mermaid` is required because the newer streamdown package declares the streamdown plugin set as peers.

## Official Adapter Exports

`@assistant-ui/react-langgraph@0.14.7` exports include:

- `useLangGraphRuntime`
- `unstable_createLangGraphStream`
- `convertLangChainMessages`
- `LangGraphMessageAccumulator`
- `appendLangChainChunk`
- `useLangGraphMessages`
- `useLangGraphInterruptState`
- `useLangGraphMessageMetadata`
- `useLangGraphSend`
- `useLangGraphSendCommand`
- `useLangGraphUIMessages`

## Runtime Contract

`useLangGraphRuntime` expects a `stream` callback:

```typescript
type LangGraphStreamCallback<TMessage> = (
  messages: TMessage[],
  config: {
    abortSignal: AbortSignal
    initialize: () => Promise<{ remoteId: string; externalId: string | undefined }>
    command?: { resume: string }
    runConfig?: unknown
    checkpointId?: string
  },
) => Promise<AsyncGenerator<LangGraphMessagesEvent<TMessage>>> | AsyncGenerator<LangGraphMessagesEvent<TMessage>>
```

The yielded event shape is:

```typescript
type LangGraphMessagesEvent<TMessage> = {
  event: string
  data: TMessage[] | unknown
}
```

Known event names:

- `messages`
- `messages/partial`
- `messages/complete`
- `metadata`
- `updates`
- `values`
- `info`
- `error`
- custom event names

Namespaced subgraph events use a pipe suffix:

```typescript
{ event: "messages|tools:tc-1", data: [messageChunk, metadata] }
{ event: "updates|tools:tc-1", data: updatePayload }
{ event: "values|tools:tc-1", data: valuesPayload }
```

For tuple message events, `data` is:

```typescript
[LangChainMessage | LangChainMessageChunk, LangGraphTupleMetadata]
```

`onMessageChunk` receives normalized `AIMessageChunk` plus metadata. When the event is namespaced, the runtime merges the namespace into the metadata as `metadata.namespace`.

## Runtime Options

Important `useLangGraphRuntime` options:

- `stream`: required callback described above.
- `load(threadId, { signal })`: returns `{ messages, interrupts?, uiMessages? }`.
- `create()`: returns `{ externalId }`.
- `delete(threadId)`.
- `getCheckpointId(threadId, parentMessages)`.
- `uiStateKey`: defaults to `"ui"`.
- `unstable_allowCancellation`.
- `unstable_enableMessageQueue`.
- adapters for attachments, speech, dictation, voice, and feedback.
- `eventHandlers`:
  - `onMessageChunk`
  - `onValues`
  - `onUpdates`
  - `onSubgraphValues`
  - `onSubgraphUpdates`
  - `onMetadata`
  - `onInfo`
  - `onError`
  - `onSubgraphError`
  - `onCustomEvent`

`unstable_createLangGraphStream` wraps the LangGraph SDK `client.runs.stream` and defaults to:

```typescript
streamMode: ["messages", "updates", "custom"]
onDisconnect: "cancel"
```

It forwards:

- assistant/thread id,
- input `{ messages }`,
- `config.abortSignal` as `signal`,
- `command`,
- checkpoint id,
- run config.

## Official Deep Agents Streaming Docs Recheck

The Deep Agents event streaming docs add two implementation constraints that matter for Moldy:

- `stream.subagents` is the user-facing Deep Agents projection. It represents delegated `task` calls as subagent stream handles with `name`, `path`, `status`, messages, tool calls, nested subagents, values, and output.
- `stream.subgraphs` is lower-level graph execution structure. It is useful for debugging, but the product UI should prefer `stream.subagents` when showing specialist work.
- A subagent discovery snapshot is intentionally lightweight. It tells the UI that a subagent exists and where it sits in the tree, but it does not eagerly contain all streamed messages or tool calls.
- Scoped streams such as `subagent.messages`, `subagent.tool_calls`, `subagent.values`, `subagent.subagents`, and `subagent.output` should be read only when the UI needs that detail.
- Coordinator and subagent output can interleave. When exact arrival order matters, consume the raw v3 protocol events and use `params.namespace` plus sequence/order metadata to identify the source.
- Selector-style consumption is still the right frontend model for detail panes and expandable cards because it avoids rendering every nested transcript by default.

The Deep Agents frontend docs show `@langchain/react` `useStream` as the greenfield frontend. The pattern is useful even though Moldy should keep assistant-ui:

- render coordinator messages from the root stream;
- read subagent discovery snapshots from `stream.subagents`;
- index subagents by the coordinator tool-call id that spawned them;
- attach subagent cards below the coordinator assistant turn that delegated the work;
- use scoped selectors such as `useMessages(stream, subagent)` and `useToolCalls(stream, subagent)` only when a card or side panel is mounted;
- show progress counts, collapsible cards, specialist names, scoped tool-call metadata, outputs, and per-subagent errors.

Moldy's implementation should therefore treat LangChain's stream runtime and the assistant-ui chat surface as two separable layers. The strongest LangChain-native runtime for DeepAgents subagent UI is `@langchain/react` v1. assistant-ui can remain the thread/composer surface, but it should consume a LangGraph stream state rather than own a hand-built Moldy event store.

One compatibility detail: official examples refer to lifecycle states with slightly different labels across Python and frontend examples, such as `started`/`running` and `completed`/`complete`. Moldy's BFF adapter should normalize these into the frontend activity status vocabulary rather than leaking package-specific spelling into UI components.

## Local Source Code Review

Checked local source trees on 2026-06-13:

- `/Users/chester/dev/langgraph`
- `/Users/chester/dev/deepagents`
- `/Users/chester/dev/langgraphjs`
- `/Users/chester/dev/streaming-cookbook`
- `/Users/chester/dev/deep-agents-ui`
- `/Users/chester/dev/assistant-ui`

There is no assistant-ui sample in `/Users/chester/dev/langgraph` or `/Users/chester/dev/deepagents`. `langgraph/libs/sdk-js/README.md` says the JS SDK has moved to `langchain-ai/langgraphjs`.

`/Users/chester/dev/langgraphjs` does contain a direct assistant-ui example:

- `examples/assistant-ui-claude`
- dependencies: `@assistant-ui/react`, `@assistant-ui/react-markdown`, `@langchain/react`, `@langchain/langgraph`
- runtime pattern: use `@langchain/react` `useStream` for LangGraph state, convert LangChain messages/tool calls/reasoning into assistant-ui `ThreadMessageLike`, then pass them into assistant-ui via `useExternalStoreRuntime`.
- the README explicitly describes this as bridging `@langchain/react` state into assistant-ui.

This changes the frontend recommendation. `@assistant-ui/react-langgraph` remains a valid assistant-ui package and may still be useful for a pure assistant-ui LangGraph runtime. However, the local LangChain/LangGraph source and examples point to `@langchain/react` v1 as the first-party LangChain frontend runtime for DeepAgents streams.

Useful local source findings:

- `langgraphjs/libs/sdk-react/README.md` describes `@langchain/react` v1 as the React SDK for Deep Agents, LangChain, and LangGraph. Its mental model is one root `useStream` per thread plus companion selector hooks for scoped data.
- `@langchain/react` `useStream` exposes always-on root projections: `values`, `messages`, `toolCalls`, `interrupts`, `isLoading`, `error`, `threadId`, `subagents`, `subgraphs`, and `subgraphsByNode`.
- `@langchain/react` selector hooks include `useMessages`, `useToolCalls`, `useValues`, `useExtension`, `useChannel`, `useChannelEffect`, `useMessageMetadata`, `useSubmissionQueue`, and media selectors.
- `langgraphjs/libs/sdk-react/docs/subagents.md` documents the intended DeepAgents UI contract: subagent/subgraph discovery is eager and cheap, while scoped messages/tool calls/values are subscribed lazily by passing a discovery snapshot into selector hooks.
- `langgraphjs/examples/ui-react/src/views/DeepAgentView.tsx` implements that exact UI pattern: coordinator transcript from `stream.messages`, subagent discovery from `stream.subagents`, and expanded cards using `useMessages(stream, subagent)` plus `useToolCalls(stream, subagent)`.
- `langgraphjs/libs/sdk-react/src/tests/stream.subscriptions.test.tsx` verifies subscription invariants Moldy should preserve: a bare `useStream` mount opens no scoped subscription; root `useMessages(stream)` is free; each unique `(selector, namespace)` opens one ref-counted subscription; different subagent namespaces are isolated; unmounting the last consumer releases the subscription.
- `langgraphjs/libs/sdk-react/docs/transports.md` recommends `HttpAgentServerAdapter` for production custom backends. The React tree can use the stock adapter while the Moldy BFF implements Agent Streaming Protocol `/commands` and `/stream` endpoints.
- The same docs reserve a custom `AgentServerAdapter` for non-HTTP/SSE transports or application-specific buses. Moldy should start with `HttpAgentServerAdapter` unless the existing FastAPI streaming model makes the protocol endpoints impractical.

- `langgraph/libs/sdk-py/langgraph_sdk/_async/stream.py` is the most useful implementation reference for Moldy's BFF event adapter. Its raw event surface subscribes to `values`, `updates`, `messages`, `tools`, `lifecycle`, `input`, `checkpoints`, `tasks`, and `custom`.
- The v3 wire event fixtures in `langgraph/libs/sdk-py/tests/streaming/_events.py` use `{type, method, params.namespace, params.data, seq, event_id}`. Moldy should persist the upstream `event_id` separately from its own database event id when available.
- The SDK's scoped stream handles expose `path`, `namespace`, `graph_name`, `trigger_call_id`, `status`, `error`, `messages`, `tool_calls`, `subgraphs`, `subagents`, and `extensions`.
- In the current SDK tests, `thread.subagents is thread.subgraphs` until the protocol distinguishes them. The product UI should still use the Deep Agents `stream.subagents` concept, but the adapter must accept `tasks`/`subgraphs` wire metadata as equivalent discovery input.
- Scoped handles subscribe to `messages`, `tasks`, `tools`, and `lifecycle` together so child and grandchild messages/tool calls are routed without crossing into the root transcript.
- The SDK has explicit tests for root/child projection isolation, grandchild event order preservation, sibling routing, bounded queues, force-finishing unfinished child scopes, and failed/interrupted child statuses. These tests are good templates for Moldy's adapter tests.
- `deepagents/libs/deepagents/tests/unit_tests/test_deep_agent_streaming.py` now drives a real `create_deep_agent().astream_events(..., version="v3")` run and verifies native projections: `subagents`, `subgraphs`, `messages`, `tool_calls`, and `values`.
- The DeepAgents test asserts subagent handles carry `name`, `cause={"type": "toolCall", "tool_call_id": ...}`, `status`, `path`, `output()`, scoped messages, scoped tool calls, and errors.
- The same test consumes parent messages and subagents concurrently with `asyncio.gather`; Moldy's backend runner should do the same when it consumes typed projections. If Moldy consumes raw v3 events directly, raw event order already carries the interleaving.
- `deepagents/graph.py` auto-adds the default `general-purpose` inline subagent unless disabled by profile. Moldy tests that assert subagent names should either control this default or accept it explicitly.
- `AsyncSubAgentMiddleware` is a different model from inline `task` subagents. It exposes tool-driven background tasks (`start_async_task`, `check_async_task`, `update_async_task`, `cancel_async_task`, `list_async_tasks`) with statuses such as `running`, `success`, `error`, and `cancelled`. These should render as background task activity, not as v3 inline subagent handles.
- `examples/async-subagent-server` is a useful self-hosted Agent Protocol reference, but it is polling/status-oriented and does not provide the same live v3 subagent stream as inline DeepAgents.

`/Users/chester/dev/streaming-cookbook` adds the most concrete local custom-backend reference:

- The root README frames the new streaming model as typed Agent Streaming Protocol events plus SDK projections, not low-level `stream_mode` tuple handling for large interactive apps.
- `python/react-custom-backend` and `typescript/react-custom-backend` both expose the complete custom backend surface used by `HttpAgentServerAdapter` and the LangGraph SDK: `POST /threads/:thread_id/commands`, `POST /threads/:thread_id/stream`, `GET|POST /threads/:thread_id/state`, and `POST /threads/:thread_id/history`.
- The Python `LocalThreadSession` is the closest reference for Moldy's FastAPI BFF: it buffers protocol events by `seq`, replays matching buffered events before live delivery, filters subscriptions by `channels`, `namespaces`, `depth`, and `since`, encodes SSE as `event: message`, and mirrors `event_id` or `seq` into the SSE `id:` field.
- The Python backend normalizes unknown event methods to `custom`, supports `custom:{name}`-style channel semantics, and sanitizes LangChain messages, commands, sends, dataclasses, tuples, dicts, and lists before putting them on the wire.
- The Python backend unwraps Python v3 `(payload, metadata)` tuples for `messages` and `tools` because JS message/tool assemblers expect the payload object. Moldy's adapter should implement and test this explicitly.
- The Python backend synthesizes `tools` channel lifecycle events from root `values.messages` when tool activity is only visible through AI `tool_calls` and `ToolMessage` snapshots. Moldy should prefer raw `tools` events, but keep this deduplicated fallback for Python runtime compatibility.
- The TypeScript custom backend uses `StreamChannel.local<ProtocolEvent>()` and shared `matchesSubscription` semantics. Moldy's backend tests should mirror those semantics even though the server is Python.
- The `typescript/ui-react` reconnect example persists thread identity across refresh and consumes token-level projection through `useMessages(stream)`. Moldy should use DB-owned conversation/thread ids instead of browser-generated ids, but E2E should verify refresh during active streaming does not duplicate tokens or submit a second run.
- The subagent and subagent-status examples confirm the UI split already chosen in the plan: root coordinator messages, subagent discovery/status through `thread.subagents`/`run.subagents`, and scoped subagent messages/tool calls only when detail is needed.
- The custom transformer and A2A examples confirm Moldy-specific artifacts, memory, trace, and domain events should travel as `custom` or `custom:{name}` channels without degrading core `messages`, `tools`, `values`, `updates`, `tasks`, `lifecycle`, `input`, or `checkpoints` semantics.
- The cookbook examples are single-process demos. Moldy's BFF must treat any in-memory event broker as a live optimization and rely on persisted protocol events for reconnect/replay. Multi-worker production needs either sticky routing or a shared broker/store.
- Scoped selectors are intentionally lazy, but each unique selector/namespace can become an extra subscription. Moldy's UI should cap simultaneously expanded live subagent detail cards and prefer HTTP/2 in production if many scoped SSE subscriptions can be open.

`/Users/chester/dev/deep-agents-ui` is useful as a DeepAgents product UI reference, not as a runtime recommendation:

- It is not assistant-ui based and uses `@langchain/langgraph-sdk/react` rather than the newer `@langchain/react` v1 runtime chosen for Moldy.
- `src/app/hooks/useChat.ts` reads `stream.values.todos`, `stream.values.files`, `stream.values.ui`, `stream.messages`, `stream.interrupt`, and `stream.getMessagesMetadata`; this supports treating DeepAgents todos/files as state-backed UI surfaces.
- The hook also uses `reconnectOnMount`, `fetchStateHistory`, query-param `threadId`, optimistic human messages, `stream.stop()`, `command.resume`, checkpoint-based single-step reruns, and `client.threads.updateState(threadId, { values: { files } })` for file edits.
- `src/app/components/ChatInterface.tsx` places a compact tasks/files panel above the composer. Collapsed mode shows the active task or all-complete summary plus file count; expanded mode shows grouped todos and file cards.
- `src/app/components/TasksFilesSidebar.tsx` groups todos by `pending`, `in_progress`, and `completed`, auto-expands when tasks/files first appear, and disables file edits while loading or interrupted.
- `src/app/components/FileViewDialog.tsx` provides a useful file UX: Markdown preview, syntax-highlighted code/plain-text preview, copy, download, edit, and save.
- `src/app/components/ChatMessage.tsx` detects subagents from `task` tool calls with `subagent_type`. Moldy can keep this as fallback display logic, but should prefer native v3 `stream.subagents` / scoped selectors.
- `src/app/components/ToolCallBox.tsx` shows inline tool expansion, `LoadExternalComponent` for LangGraph UI messages, and inline HITL approve/reject/edit controls.

`/Users/chester/dev/assistant-ui` changes the assistant-ui bridge recommendation:

- `packages/react-langchain` is an official assistant-ui adapter for the same upstream runtime Moldy is choosing. It wraps `@langchain/react` `useStream`, converts LangChain messages with `convertLangChainBaseMessage`, and feeds assistant-ui via `useExternalStoreRuntime`.
- Local package metadata: `@assistant-ui/react-langchain@0.0.13`, peer `@langchain/react@^1.0.2`, dev dependency `@langchain/react@^1.0.18`.
- `useStreamRuntime` accepts upstream `UseStreamOptions`, so it should be compatible with either `assistantId/apiUrl` or a custom `@langchain/react` transport such as `HttpAgentServerAdapter`, subject to installed type verification.
- Runtime extras expose `interrupt`, `interrupts`, `submit`, `values`, and `messagesKey`.
- Public hooks include `useLangChainState<T>(key, defaultValue?)`, `useLangChainInterruptState`, `useLangChainSubmit`, `useLangChainSend`, and `useLangChainSendCommand`.
- `useLangChainState<T>` reads `stream.values[key]` from assistant-ui runtime extras. This directly addresses DeepAgents `todos`/`files` and avoids reconstructing state from partial tool-call streams.
- `useStreamRuntime` already handles root messages, basic tool calls/results, auto-cancelling pending tool calls, attachment/feedback adapters, raw state submission, command submission, interrupt state, and `stream.stop()` cancellation wiring.
- Verify installed helper exports before importing optional assistant-ui utilities. In particular, `makeAssistantDataUI` should be treated as version-dependent; if it is not exported, reasoning summaries should use the installed message part/data renderer extension point instead of adding a package fork.
- The local docs explicitly compare `react-langchain` and `react-langgraph`. `react-langchain` is newer and thinner, while `react-langgraph` still has broader coverage for subgraph events, generative UI messages, message metadata, and event handler hooks.
- `examples/with-langchain` demonstrates the intended minimal shape: `useStreamRuntime(...)` in a provider and `useLangChainState<Todo[]>("todos", [])` in a side panel.

`/Users/chester/dev/assistant-ui/packages/react-langgraph` is also official source and materially changes the adapter choice:

- `@assistant-ui/react-langgraph@0.14.7` is the fuller assistant-ui LangGraph adapter. It exports `useLangGraphRuntime`, `useLangGraphMessages`, `LangGraphMessageAccumulator`, `convertLangChainMessages`, `useLangGraphMessageMetadata`, `useLangGraphUIMessages`, and `unstable_createLangGraphStream`.
- It has stronger built-in assistant-ui support than `react-langchain`: message tuple metadata, generative UI messages, event handlers, checkpoint-aware edit/regenerate, and cancellation.
- Its `useLangGraphMessages` parser treats pipe-suffixed events as namespaced subgraph events, but `messages|...` tuple events are still accumulated into the same message list as root messages. The accumulator intentionally preserves tuple-only subgraph messages through final `values` reconciliation.
- That behavior is useful for generic assistant-ui LangGraph integration, but it conflicts with Moldy's desired DeepAgents UX: root coordinator transcript stays clean, while subagent messages/tool calls render lazily inside subagent cards or panels.
- Therefore the best Moldy primary frontend runtime is not the full `react-langgraph` hook and not the full `react-langchain` hook. The best path is a local Moldy single-stream wrapper that owns `@langchain/react useStream`, feeds assistant-ui root messages through `useExternalStoreRuntime`, and lets DeepAgents panels use `@langchain/react` scoped selectors against the same stream.

## Backend Findings

The isolated backend install resolved:

- `deepagents`: `0.6.1`
- `langchain`: `1.3.9`
- `langchain-core`: `1.4.7`
- `langgraph`: `1.2.4`

`create_deep_agent()` returns a `CompiledStateGraph`. The graph supports both:

- `stream(...)` / `astream(...)`
- `stream_events(..., version="v3")` / `astream_events(..., version="v3")`

### `stream_events(version="v3")` Result

With a tool-bindable fake model, plain `langchain.create_agent()` works:

- stream transformers: `ToolCallTransformer`, `SubagentTransformer`
- v3 extensions: `lifecycle`, `messages`, `subagents`, `subgraphs`, `tool_calls`, `values`
- emitted `values` and `messages` protocol events.

The same fake model with `deepagents.create_deep_agent()` fails before model execution:

```text
ValueError: Transformer SubagentTransformer returned projection keys that conflict with already-registered keys: 'subagents' (owned by SubagentTransformer)
```

The compiled DeepAgents graph had these stream transformers:

```text
ToolCallTransformer
langchain.agents._subagent_transformer.SubagentTransformer
deepagents.create_deep_agent.<locals>._subagent_factory
```

Both LangChain and DeepAgents register a `subagents` projection, so LangGraph v3's transformer mux rejects the duplicate key. This means Moldy must not assume `agent.stream_events(..., version="v3")` is production-ready for DeepAgents until this dependency interaction is resolved or verified against a newer compatible set.

### Latest DeepAgents Recheck

After the initial `0.6.1` check, PyPI showed `deepagents 0.6.8` as the latest release on 2026-06-13. A second isolated install resolved:

- `deepagents`: `0.6.8`
- `langchain`: `1.3.9`
- `langchain-core`: `1.4.7`
- `langgraph`: `1.2.4`

With the same tool-bindable fake model, `deepagents.create_deep_agent()` no longer registered the duplicate DeepAgents-local subagent transformer:

```text
ToolCallTransformer
SubagentTransformer
```

`agent.stream_events(..., version="v3")` opened successfully and emitted v3 protocol events:

```python
{"method": "values", "params": {"namespace": [], "data": {"messages": [...], "files": {}}}}
{"method": "messages", "params": {"namespace": [], "data": ({"event": "message-start", ...}, metadata)}}
{"method": "messages", "params": {"namespace": [], "data": ({"event": "content-block-delta", "delta": {"type": "text-delta", "text": "hello"}}, metadata)}}
```

This changes the backend implementation recommendation: after upgrading Moldy's backend dependency floor to at least `deepagents>=0.6.8,<0.7.0`, direct `stream_events(version="v3")` is viable again. After the later `langgraphjs` review, the remaining work is not the duplicate transformer conflict; it is preserving v3 protocol events through Moldy's BFF so `@langchain/react` can consume them, with assistant-ui `{ event, data }` projection kept only as an optional view adapter.

### `stream(..., stream_mode=[...])` Result

DeepAgents did work with LangGraph stream modes:

```python
agent.stream(
    {"messages": [{"role": "user", "content": "hi"}]},
    stream_mode=["messages", "updates", "values", "custom"],
    subgraphs=True,
)
```

Observed event shape:

```python
((), "values", {"messages": [...], "files": {}})
((), "updates", {"PatchToolCallsMiddleware.before_agent": None})
((), "messages", (AIMessageChunk(...), metadata))
((), "updates", {"model": {"messages": [AIMessage(...)]}})
((), "values", {"messages": [...], "files": {}})
```

This shape maps cleanly to assistant-ui's expected stream callback events:

```python
event_name = mode if not namespace else f"{mode}|{'|'.join(namespace)}"
yield {"event": event_name, "data": serialized_data}
```

This is still a useful fallback because it already matches the assistant-ui LangGraph runtime event family. However, with `deepagents==0.6.8`, the direct v3 path should be evaluated as the primary path because it preserves richer protocol events.

## Decision

After the `langgraphjs` source review, the preferred implementation direction is:

- Use `@langchain/react` v1 as the primary frontend stream runtime for DeepAgents/LangGraph state.
- Implement a local Moldy single-stream wrapper around one `@langchain/react useStream` instance per conversation/thread.
- Feed assistant-ui root chat from that same stream through a local `useExternalStoreRuntime` bridge. Reuse stable official assistant-ui converter/accumulator utilities where useful, but do not mount either full assistant-ui runtime hook as the primary path.
- Treat `@assistant-ui/react-langchain` as a lightweight source reference because its `useStreamRuntime` hides the underlying stream needed by Moldy's scoped DeepAgents selectors.
- Treat `@assistant-ui/react-langgraph` as a fuller source reference/fallback because its full runtime accumulates namespaced subgraph tuple messages into the root message list, while Moldy needs coordinator and subagent transcripts separated.
- Implement the Moldy BFF as an Agent Streaming Protocol compatible HTTP/SSE server where practical, so the frontend can use `HttpAgentServerAdapter` instead of a fully custom browser transport.
- Treat the custom-backend HTTP surface as `commands`, `stream`, `state`, and `history`; `commands` plus `stream` alone is not sufficient for SDK hydration, refresh, checkpoint/history, edit, or regenerate flows.
- Capture the installed SDK's actual state/history fetch URLs during implementation. `HttpAgentServerAdapter` covers transport wiring, but history may be fetched through SDK thread history helpers rather than the same command/stream path builder.
- Use `deep-agents-ui` only for DeepAgents-specific state UI patterns: `todos` progress panels, `files` panels, file preview/edit actions, and inline HITL/tool expansion. Do not copy its runtime stack over the `@langchain/react` + local assistant-ui bridge decision.
- Keep full `@assistant-ui/react-langgraph` runtime adoption only as a fallback if the local `@langchain/react` wrapper proves impossible and the root/subagent transcript mixing can be solved explicitly.

Do not introduce a broad Moldy-only `AgentStreamEvent` as the primary protocol. The BFF should preserve LangGraph protocol events (`method`, `params.namespace`, `params.data`, `seq`, `event_id`) and expose command/subscription semantics close to Agent Streaming Protocol. If assistant-ui-specific `{ event, data }` projection is still needed, derive it from the preserved protocol events rather than making it the source of truth.

Initial backend source should be direct v3 when Moldy upgrades to `deepagents>=0.6.8,<0.7.0`:

```python
agent.astream_events(
    input,
    config=config,
    version="v3",
)
```

If an assistant-ui-only view adapter is still needed, the BFF can derive assistant-ui LangGraph stream events from the canonical protocol events:

```typescript
type MoldyLangGraphStreamEvent = {
  event: string
  data: unknown
}
```

That projection is now considered a compatibility/view adapter, not the canonical event store. The canonical persisted stream should retain the protocol fields needed by `@langchain/react`, `HttpAgentServerAdapter`, replay, subagent namespace routing, and debugging.

For Python-backed direct v3, the BFF adapter also needs the compatibility behavior shown in the streaming-cookbook: unwrap `(payload, metadata)` tuples for JS assemblers, synthesize missing `tools` lifecycle events from `values.messages` when raw `tools` events are absent, preserve `custom:{name}` channels, and serialize SDK-compatible thread state/history.

The BFF adapter also needs to preserve Moldy's non-chat side effects. UI usage display can be driven from final message metadata or protocol usage events, but spend aggregation must still flow through the hook path that feeds `SpendHook` and `spend_queue`. Public share pages are another non-chat consumer: `TurnTrace.events` and `frontend/src/lib/share/extract-chips.ts` must continue to render tool/subagent/artifact chips from both historical Moldy SSE traces and new canonical protocol traces.

For DeepAgents state UI, preserve both event streams and state snapshots. `todos` should be read from `values.todos` when present. Moldy file UI is artifact-first because the current runtime uses a filesystem backend; custom artifact/file events and persisted `conversation_artifacts` / `artifact_versions` are the primary durable file source, while `values.files` is optional reconciliation input only when the real runtime state includes it.

For assistant-ui integration, implementation should build the local Moldy wrapper first. The wrapper owns the `@langchain/react` stream, exposes it through typed context, feeds assistant-ui root messages through `useExternalStoreRuntime`, and lets DeepAgents-specific panels consume subagents, scoped messages/tool calls, metadata, values, and custom channels through `@langchain/react` selectors against that same stream.

Do not start by forking `@assistant-ui/react-langchain` or `@assistant-ui/react-langgraph`. Import stable utility exports where useful; fork only if a package-level bug blocks the local wrapper and the fork scope is documented.

Keep LangGraph multi-mode streaming as the fallback:

```python
agent.astream(
    input,
    config=config,
    stream_mode=["messages", "updates", "values", "custom"],
    subgraphs=True,
)
```

The implementation must include a regression test for both paths: direct v3 with `deepagents>=0.6.8`, and multi-mode fallback.

## Closed Implementation Decisions

- Default live subscriptions must include `values` because Moldy's runtime UI depends on state-backed `todos`, interrupts, and checkpoint-aware reconciliation. The default UI channel set is `messages`, `tools`, `updates`, `values`, `tasks`, `lifecycle`, `checkpoints`, and `custom`. Do not include `input` by default because it can carry prompt/input payloads that are useful for debug views but unnecessary for the chat surface.
- The LangGraph checkpointer is the canonical source for current thread state and history. Moldy's `message_events` table should store canonical protocol events, `upstream_event_id`, `seq`, run ids, conversation ids, and checkpoint references for replay/debugging, but it should not become a second full SDK state/history store. `GET|POST state` and `POST history` should read/write through the graph/checkpointer API when available.
- Canonical namespace storage stays as `params.namespace: string[]`. Only compatibility/view projections for assistant-ui `{ event, data }` should serialize namespaces into event names, using one helper that emits `event|${encodeURIComponent(segment)}` for each namespace segment. The serialized string must never be the source of truth for replay or subagent routing.
- `getCheckpointId(threadId, parentMessages)` should resolve the checkpoint attached to the last parent message. Resolution order: live `useMessageMetadata`/LangGraph message metadata, then the persisted protocol event checkpoint reference or an explicit future message-checkpoint mapping, then `conversation.active_branch_checkpoint_id` only for latest-turn fallback. Empty parent messages should resolve to the initial/root checkpoint when one exists, otherwise `null`.
- Reasoning UI should be provider-agnostic. Render only explicit displayable reasoning/thinking blocks or summaries marked displayable by the adapter. Unknown provider reasoning payloads and private chain-of-thought must be redacted before they enter assistant-ui message parts. Tests should use synthetic fixtures for displayable and private reasoning rather than relying on one provider's current payload spelling.
- Browser SSE disconnects should detach only. Explicit user cancellation should route to Moldy's run cancel semantics, while detach/reconnect should keep the backend run alive for replay/reattach.
- Persist selected `values` snapshots or checkpoint references, not every full `values` payload. Long conversations can make full-state event persistence unbounded.
- Use `multitask_strategy: "reject"` by default to match Moldy's single-active-run UX.
- Keep checkpointer-backed state/history routes mindful of the current small psycopg connection pool. The pool size should become configurable before the new runtime adds more state/history traffic.
- Keep public share trace chip rendering backward compatible while introducing canonical protocol event persistence.
