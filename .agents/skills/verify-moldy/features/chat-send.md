# Chat send and stream

A user opens an agent conversation, types in the composer, presses Enter, and sees their
message and the streamed assistant reply. Both survive a reload.

## Sub-features

- `chat-empty` a fresh conversation shows `대화를 시작해보세요.`
- `chat-send` Enter in the composer sends; the composer clears.
- `chat-stream` the assistant bubble appears with the reply.
- `chat-persist` both bubbles render again after reload; messages API returns both turns.
- `chat-menu` (not scripted yet) header `더보기 메뉴` → `새 대화`, `설정`.

## How to get to it (user POV)

- Sidebar agent → a conversation row.
- `/agents/<id>` redirects to its latest conversation.
- `/agents/<id>/conversations/<cid>` directly.

## Driving it with verify-moldy

Preconditions:

- Baseline run healthy, logged in.
- An agent on the scripted model and an empty conversation (created via
  `POST /api/agents` and `POST /api/agents/<id>/conversations` — setup, not the proven action).

- **Open.** `page.goto('/agents/<id>/conversations/<cid>')`. Heading with the agent name and
  `대화를 시작해보세요.` are visible.
- **Send.** Fill `textarea[data-moldy-composer-input="true"]` with a unique text, press Enter.
- **Stream.** Exactly one `[data-moldy-message-role="user"]` with the text and one
  `[data-moldy-message-role="assistant"]` with `E2E scripted document model is ready.`;
  composer value is empty.
- **Persist.** Reload; both bubbles again (count 1 each). `GET
  /api/conversations/<cid>/messages` contains both strings.
- **Run it.** `node .agents/skills/verify-moldy/scripts/drive.mjs chat-send`.

## Gotchas

- Never assert sending with page-wide `getByText(text)` — empty-state/hint panels can echo
  it. Scope to message-role bubbles and assert counts (also catches duplicate renders).
- The reply is a fixed sentinel; any marker phrase in your text (see
  `backend/app/agent_runtime/e2e_scripted_model.py`) switches it to a tool-call script.
- Chat attachments are linked to the conversation, not echoed in the messages API.
