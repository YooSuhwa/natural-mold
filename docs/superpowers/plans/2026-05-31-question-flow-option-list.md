# QuestionFlow / OptionList Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `ask_user` the single canonical user-input tool, with backward-compatible single questions plus step-based `question_flow` and bounded `option_list` render modes.

**Architecture:** Keep the current LangGraph HiTL `Decision(type="respond", message=...)` contract for this phase. Do not add separate `question_flow` or `option_list` tools; `ask_user` remains the stable tool name/interrupt policy, and `mode` selects the frontend renderer. Backend tool/interrupt payloads carry richer args, frontend normalizes legacy payloads into a single-question mode, and mappers serialize user choices into stable receipt text plus a JSON string message when structured answers are needed.

**Tech Stack:** FastAPI, LangChain/LangGraph/DeepAgents interrupt, React 19, Next.js 16, assistant-ui, Tailwind v4, Vitest, pytest.

## Implementation Status (2026-05-31)

- `ask_user` remains the only canonical user-input HiTL tool. `question_flow` and
  `option_list` are payload modes, not separate runtime tools.
- Backend `ask_user` accepts legacy `question/options` plus v2
  `mode/title/questions/minSelections/maxSelections` args.
- Native ask_user interrupts now preserve extended args through the streaming
  bridge.
- Frontend `UserInputUI` keeps legacy single-question rendering when `mode` is
  absent, and renders `QuestionFlowCard` or `OptionListCard` when mode is set.
- Decision mappers serialize selected ids into the existing `respond(message)`
  contract and keep label-based receipt text for the UI.
- Builder phase 2 is the first adopter: it asks for agent name, response tone,
  and output style as a stepped `question_flow`, then parses JSON-string resumes
  into intent fields.
- E2E-driven fixes after real app verification:
  - `useChatRuntime` now merges duplicate `ask_user` tool calls when Builder emits
    both a pending tool call and a standard interrupt event, so the user sees one
    actionable card.
  - Completed `option_list` results are rendered from label receipts instead of
    raw JSON strings, including after page reload.
- Verification completed:
  - `cd backend && uv run pytest tests/test_hitl_wire.py tests/test_builder_v3.py -q`
  - `cd backend && uv run ruff check .`
  - `cd frontend && pnpm test src/lib/chat/__tests__/use-chat-runtime-hitl.test.tsx src/lib/chat/__tests__/standard-interrupt.test.ts src/lib/chat/__tests__/decision-mappers.test.ts --run`
  - `cd frontend && pnpm lint` (passes with 3 pre-existing warnings in unrelated test files)
  - `cd frontend && pnpm build`
- Codex in-app browser E2E completed against the real logged-in local app:
  - `/agents/new` → Builder conversational flow: one `question_flow` card,
    3-step progression, receipt
    `에이전트 이름: 웹탐색 요약봇 | 답변 톤: 전문적으로 | 결과 스타일: 자세한 설명`,
    then Phase 3.
  - `잡담 에이전트` chat: legacy `ask_user(question, options)` rendered options
    `영화/음악`, selecting `음악` resumed the agent and left a label receipt.
  - `잡담 에이전트` chat: `mode="option_list"` rendered min/max copy and option
    descriptions, selecting `Web Search` + `Gmail` resumed the agent and left
    `Web Search, Gmail` as the completed receipt after reload.
- Full backend pytest was also run. Result: `1309 passed`, `2 deselected`, and one
  unrelated `tests/test_spend_writer.py::test_stop_drains_remaining_entries`
  fixture failure (`daily_spend_user` table missing during full-suite order);
  the same test passes in isolation.

---

## Current Source Findings

- `backend/app/agent_runtime/tools/ask_user.py` only accepts `question: str` and `options: list[str] | None`; `_extract_respond_message` already unwraps standard `{"decisions":[{"type":"respond","message":...}]}` resume payloads.
- `ask_user` should remain as the public tool name because `executor.py` interrupt policy, `standard-interrupt.ts`, persisted tool calls, and Builder pending cards are all keyed to `ask_user`. The duplicate to remove is not the tool; it is the old one-off UI/payload handling.
- `backend/app/agent_runtime/streaming.py` preserves standard HiTL `action_requests/review_configs`, but the native `{"type":"ask_user", ...}` fallback currently reconstructs args as only `{question, options}`. This would drop `mode`, `title`, `questions`, `minSelections`, and `maxSelections`.
- `frontend/src/components/chat/tool-ui/user-input-ui.tsx` already has a hidden `questions?: UserInputQuestion[]` path, but it renders every question in one card, indexes answers by array position, and returns `answers[0]` in submitted state, so multi-question receipt quality is weak.
- `frontend/src/lib/chat/standard-interrupt.ts` spreads `action.args` into the synthetic `ask_user` tool call, so the frontend interrupt bridge is mostly ready once backend args are preserved.
- `frontend/src/lib/chat/decision-mappers.ts` only has primitive `toRespond(message)`. The new work belongs here as helper functions that convert selected ids/labels into the current `respond(message)` wire.
- Builder phase 2 currently emits a pending `ask_user` card with a single name question and then treats resume as a plain string. If a question flow returns JSON, `phase2_intent_wait` would currently set the agent name to the full JSON string unless it is updated.
- Fix Assistant uses `ask_clarifying_question`, not `ask_user`/HiTL. It can reuse OptionList visuals later, but it does not share the pause/resume path today.

## File Structure

- Modify `backend/app/agent_runtime/tools/ask_user.py`: extend tool args schema and native interrupt payload.
- Modify `backend/app/agent_runtime/streaming.py`: preserve full native ask_user args.
- Modify `backend/app/agent_runtime/builder_v3/nodes/_helpers.py`: add JSON/string parser for structured ask_user responses.
- Modify `backend/app/agent_runtime/builder_v3/nodes/phase2_intent.py`: optional first builder adopter for 3+ step confirmation.
- Modify `frontend/src/lib/types/index.ts`: add v2 ask_user option/question/payload types.
- Modify `frontend/src/lib/chat/decision-mappers.ts`: add serializers for QuestionFlow and OptionList receipts/messages.
- Modify `frontend/src/components/chat/tool-ui/user-input-ui.tsx`: branch rendering by `args.mode`.
- Create `frontend/src/components/chat/tool-ui/question-flow-card.tsx`: Moldy-adapted QuestionFlow wrapper.
- Create `frontend/src/components/chat/tool-ui/option-list-card.tsx`: Moldy-adapted OptionList wrapper.
- Modify `frontend/messages/ko.json` and `frontend/messages/en.json`: add labels for step, back, next, complete, selected count, and fallback titles.
- Add/modify tests in `backend/tests/test_hitl_wire.py`, `backend/tests/test_builder_v3.py`, `frontend/src/lib/chat/__tests__/standard-interrupt.test.ts`, `frontend/src/lib/chat/__tests__/decision-mappers.test.ts`, and a new component test for `UserInputUI`.

## Task Plan

### Task 1: Backend AskUser Payload V2

- [ ] Keep the tool name `ask_user`; do not create separate runtime tools named `question_flow` or `option_list`.
- [ ] Add a Pydantic args schema for `ask_user` that accepts legacy `question/options` and new `mode/title/questions/minSelections/maxSelections`.
- [ ] Keep `question` optional only when `mode` is `question_flow` or `option_list`; otherwise require a non-empty legacy question.
- [ ] Support option items as either strings or objects with `id`, `label`, `description`, and `disabled`.
- [ ] Include all non-`None` args in the native `interrupt({ "type": "ask_user", ... })` payload.
- [ ] Keep `_extract_respond_message` unchanged except for a regression test proving JSON string responses pass through intact.

### Task 2: Streaming Preservation

- [ ] Change `_interrupt_to_standard_chunk()` so native `type="ask_user"` uses the full interrupt dict minus `type` as `action_requests[0].args`.
- [ ] Preserve legacy behavior exactly for `{"question":"...", "options":["A","B"]}`.
- [ ] Add pytest coverage for an extended native payload with `mode="question_flow"` and for `mode="option_list"`.

### Task 3: Frontend Types And Mappers

- [ ] Extend `UserInputQuestion` to include `id`, `label`, `required`, and object options with `id`.
- [ ] Add `AskUserMode = "question_flow" | "option_list"` and payload types in `frontend/src/lib/types/index.ts`.
- [ ] Add mapper helpers:
  - `serializeQuestionFlowResponse(questions, answers)` returns `{ message, displayText, summary }`.
  - `serializeOptionListResponse(options, selection)` returns `{ message, displayText }`.
- [ ] Use option ids in JSON messages and human labels in display receipts.
- [ ] Keep `toRespond(message)` as the only resume wire builder for this phase.

### Task 4: Tool UI Components

- [ ] Copy/adapt the necessary parts of `/Users/chester/dev/tool-ui/apps/www/components/tool-ui/question-flow/question-flow.tsx` into `question-flow-card.tsx`, but replace hardcoded English labels with `next-intl`.
- [ ] Copy/adapt the minimal OptionList behavior from `/Users/chester/dev/tool-ui/apps/www/components/tool-ui/option-list/option-list.tsx` into `option-list-card.tsx`; avoid bringing the whole shared action config stack unless it is needed.
- [ ] Use existing Moldy `Button`, `Separator`, `cn`, `CountdownBadge`, and `useApprovalDeadline`.
- [ ] Add receipt states that show submitted labels after local submit even before backend refetch.
- [ ] Normalize legacy `{question, options}` payloads into an internal single-question render mode, so `UserInputUI` has one routing surface instead of parallel old/new implementations.

### Task 5: Builder First Adopter

- [ ] Update phase 2 pending card to send `mode="question_flow"` with at least three questions, for example agent name, response tone, and answer depth.
- [ ] Add a parser in `_helpers.py` that accepts plain strings, JSON strings, and dict-like responses.
- [ ] Update `phase2_intent_wait` to set `agent_name_ko` from the selected/custom name, and store tone/depth only if the current `BuilderState` has an intended place for them; otherwise fold them into prompt context without schema churn.
- [ ] Keep router fallback ask_user as legacy single-select unless product wants that flow stepped too.

### Task 6: Fix Assistant Follow-Up

- [ ] Treat Fix Assistant as a second integration pass because it uses `ask_clarifying_question` and appends a new user message rather than resuming a paused graph.
- [ ] Reuse `OptionListCard` styling for its option buttons after the HiTL path is stable.
- [ ] Do not convert Fix Assistant to `ask_user` in this task unless the product explicitly wants paused HiTL in the assistant panel.

### Task 7: Verification

- [ ] Run `cd backend && uv run pytest tests/test_hitl_wire.py tests/test_builder_v3.py`.
- [ ] Run `cd frontend && pnpm test frontend/src/lib/chat/__tests__/standard-interrupt.test.ts frontend/src/lib/chat/__tests__/decision-mappers.test.ts`.
- [ ] Run `cd frontend && pnpm lint`.
- [ ] Run `cd frontend && pnpm build`.
- [ ] Manually verify in browser:
  - legacy `ask_user(question, options)` auto-select path still resumes.
  - builder phase 2 shows a stepped 3-question flow.
  - submitted receipt remains visible until backend closes the pending tool card.

## Risks And Mitigations

- **Accidental tool split:** Adding new runtime tools named `question_flow` or `option_list` would duplicate HiTL policy, break older persisted `ask_user` cards, and widen the frontend registry. Keep `ask_user` as the only user-input tool and branch only on payload `mode`.
- **Native interrupt arg loss:** Current `streaming.py` drops all extended keys for native `ask_user`. Fix this first or builder direct interrupts will silently render legacy UI.
- **Builder JSON response bug:** Current phase 2 string handling will misinterpret structured answers as the agent name. Add parser before enabling builder question_flow.
- **Receipt mismatch:** Current `CompletedBadge` uses `answers[0]`; multi-question and option list flows need a dedicated submitted receipt state.
- **Option ids vs labels:** Existing UI serializes labels. New flows should send ids in JSON for stability, but display labels to users. Legacy mode can keep label strings.
- **Tool UI dependency creep:** The upstream OptionList pulls shared action schemas. Use a local minimal wrapper first to avoid adding zod/shared action dependencies just for confirm/cancel.
- **Assistant path mismatch:** Fix Assistant is not HiTL. Sharing visuals is safe; sharing resume logic is not.
- **i18n and hardcoded English:** Upstream QuestionFlow includes `Step`, `Back`, `Next`, and `Complete`; local components should translate them.
- **Min/max validation split:** Backend should validate impossible payloads (`minSelections > maxSelections`), and frontend should enforce disabled submit for under-selection.
