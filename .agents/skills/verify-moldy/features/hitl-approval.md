# Tool approval (HITL)

When an agent with a skill calls a risky tool (`execute_in_skill`), the run pauses on an
approval card. The user approves, edits, or rejects; a rejected tool never executes.

## Sub-features

- `hitl-card` the card shows `승인이 필요합니다` before the tool runs.
- `hitl-reject` `거부` → `거부 확인` → badge `거부됨`, no retry error, no artifact produced.
- `hitl-approve` (not scripted yet) approve → tool runs → `moldy-docx-demo.docx` artifact appears.
- `hitl-inbox` (not scripted yet) the same request is decidable from the approval inbox (`/api/approvals`).

## How to get to it (user POV)

- Chat with an agent that has a skill attached; the model requests `execute_in_skill`.
- Approval inbox page for pending requests.

## Driving it with verify-moldy

Preconditions:

- Baseline run healthy, logged in.
- Seeded skill `docx-document` installed (`POST /api/marketplace/items/<id>/install`,
  `install_mode: overwrite_existing`) and attached to a scripted-model agent (`skill_ids`).

- **Trigger.** Send `mcp 도구 사용 승인 HITL` in the composer.
- **Card.** Text `승인이 필요합니다` is visible (≤30 s).
- **Reject.** Click button `거부` (exact, last) then `거부 확인` (last). `거부됨` is visible and
  `승인 응답을 전송하지 못했습니다. 다시 시도하세요.` is absent.
- **Side effect.** `GET /api/conversations/<cid>/runs/active` settles to idle and
  `GET /api/conversations/<cid>/artifacts` has no `moldy-docx-demo.docx`.
- **Run it.** `node .agents/skills/verify-moldy/scripts/drive.mjs hitl-reject`.

## Gotchas

- The marker phrase is matched by the scripted model; natural-language requests do not
  trigger the tool call.
- Approving runs a real Node subprocess and needs `backend/skill-node/node_modules`
  (`docx`, `xlsx`, `pptxgenjs`; `pnpm install` at the repo root).
- A retry message after rejection is a regression (resume race), not flake.
