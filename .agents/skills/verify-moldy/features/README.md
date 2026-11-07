# Moldy verification map

The maintained source for verifying Moldy's user-facing behavior. Read this index, then
use the matching feature file as the recipe. Harness = `verify-moldy` (`../SKILL.md`).

## Baseline preconditions

- A run launched by `bash .agents/skills/verify-moldy/scripts/up.sh`, and
  `doctor.sh` reports `healthy`.
- Logged in as the per-run seed super user (`USER_EMAIL`/`USER_PASSWORD` in
  `runs/<RUN_ID>/state.env`, display name `Verify User`). `drive.mjs` does this unless
  the journey sets `options.loggedOut`.
- The keyless scripted model `e2e_scripted/document-artifact-scripted` is seeded
  alongside the normal model catalog. Chat and HITL journeys select it explicitly;
  a plain message on that model replies `E2E scripted document model is ready.`
- System skills (marketplace `source_kind=system_seed`, e.g. `docx-document`) are seeded
  but not installed.
- Onboarding/super-user welcome overlays are suppressed via sessionStorage
  (`moldy.onboarding_dismissed`, `moldy.super_user_welcomed`).

## Driving conventions

- Start each recipe from the baseline. Create the agents/conversations a recipe needs
  with fresh unique names; the DB is throwaway, so no fixture teardown is required.
- Prefer ARIA roles and Korean accessible names; fall back to the stable data
  attributes listed in `../SKILL.md`.
- Run browser actions as journey code through `node scripts/drive.mjs`.

## Proof and skip reporting

- Every journey run leaves `result.json`, numbered screenshots + ARIA snapshots,
  `console.jsonl`, and `trace.zip` under `runs/<RUN_ID>/evidence/`.
- Mutation proof includes a second read-only view: page reload, or an API read.
- Record the feature ID and entry point you used. A skipped entry point is reported as
  skipped with the reason, never as verified through another path.

## Feature entry contract

Each feature file has an H1, one paragraph of user-visible behavior, then exactly:
`Sub-features`, `How to get to it (user POV)`, `Driving it with verify-moldy`, `Gotchas`.

## Features

- [Login](./auth-login.md) — email/password login, wrong-password error, landing dashboard.
- [Create an agent](./agent-create.md) — manual creation form; template and conversational builder entry points listed.
- [Chat send and stream](./chat-send.md) — composer send, streamed assistant reply, persistence after reload.
- [Tool approval (HITL)](./hitl-approval.md) — approval card for `execute_in_skill`, reject path; approve path listed.
