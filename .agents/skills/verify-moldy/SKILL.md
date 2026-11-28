---
name: verify-moldy
description: Drive the real Moldy web app (Next.js frontend + FastAPI backend + Postgres) in a throwaway, port/DB-isolated stack and prove a change works the way a user sees it — login, agent creation, chat streaming, HITL approval — with screenshots, ARIA snapshots, traces and API cross-checks. Use after changing UI or backend behavior, when asked to "verify in the app", "show it working", or when unit/E2E tests pass but you need user-path proof.
---

# verify-moldy

Throwaway Moldy stack + Playwright journeys. Everything a run creates lives under
`.verify-moldy/runs/<RUN_ID>/` (gitignored) and `frontend/.next-verify/<RUN_ID>/`.
It never touches the developer's own dev servers (3000/8001), `backend/.env`,
`backend/data/`, `frontend/.next`, or the docker-compose Postgres. The frontend runs
from a source snapshot under `frontend/.next-verify/<RUN_ID>/app`, so Next's generated
`tsconfig.json` / `next-env.d.ts` changes and asset preparation also stay in that run.

What makes it keyless: the backend runs with `MOLDY_DISABLE_ENV_FILE=true`, a fresh
`ENCRYPTION_KEYS`/`JWT_SECRET`, `E2E_SCRIPTED_MODEL_ENABLED=true` (model
`e2e_scripted/document-artifact-scripted`) and `E2E_SEED_USER_ENABLED=true` (a
per-run super user). No real LLM is called. A plain chat message gets the sentinel reply
`E2E scripted document model is ready.`; marker phrases trigger tool calls (see
`backend/app/agent_runtime/e2e_scripted_model.py`, e.g. `mcp 도구 사용 승인 HITL`).

All commands below are run from the repo root. `S=.agents/skills/verify-moldy/scripts`.

## Requirements

- Postgres 16 binaries (`brew install postgresql@16`; override path with `VERIFY_PG_BIN`).
  Each run gets a private cluster in `runs/<RUN_ID>/pg`, loopback TCP only. No docker.
- **Sandbox:** Claude Code's macOS sandbox blocks SysV shared memory (Postgres
  `initdb`/`postgres` fail with `shmget ... Operation not permitted`) and Chromium
  launch (`browserType.launch: Target page, context or browser has been closed`).
  So `up.sh` and `drive.mjs` must run unsandboxed. `doctor.sh` and `down.sh` work in the
  sandbox. Do not retry these in the sandbox, and do not add `excludedCommands` yourself
  (org policy). Either ask the user to approve the unsandboxed call (reason: local-only
  Postgres + dev servers + headless Chromium on 127.0.0.1, no external network), or ask
  them to run `! bash .agents/skills/verify-moldy/scripts/up.sh` / `! node
  .agents/skills/verify-moldy/scripts/drive.mjs <journey>` and then read the evidence.
- `backend/.venv` (`cd backend && uv sync`), `frontend/node_modules` (`pnpm install`),
  Playwright chromium (`cd frontend && pnpm exec playwright install chromium`).
- `curl`, `lsof`, `python3` on PATH.

The backend environment must match the current lockfile, not merely exist. For example,
`ModuleNotFoundError: ko_pii` during migration means dependencies need syncing; clean up
the failed run before retrying. Dependency installation is separate from `up.sh`.

## Launch

```bash
bash $S/up.sh
```

Steps: `initdb` + `pg_ctl start` of a private cluster in `runs/<RUN_ID>/pg` on a free
127.0.0.1 port (password auth, no unix socket) → `alembic upgrade head` on that DB only → uvicorn on a free
port with cwd `runs/<RUN_ID>/data` (all `./data/*` storage stays in the run) → `next dev`
on a free port from the frontend snapshot, with its `.next` inside that snapshot → `doctor.sh`.

Ready = `up.sh` exits 0 after printing `healthy:` and `RUN_ID=...`. It takes 1–3 min
(the first Next compile dominates). It links `.verify-moldy/current` to the run; every
script defaults to that run, or takes an explicit `<RUN_ID>`. Run state (ports, process
groups, Postgres data dir, seed credentials) is in `runs/<RUN_ID>/state.env` (mode 600).

Several runs can coexist (distinct ports/DB/dist dirs). Never point a journey at a
server you did not start with `up.sh`.

## Doctor

```bash
bash $S/doctor.sh [RUN_ID]
```

Read-only. Checks: Postgres cluster running and accepting TCP, backend/frontend process groups alive, each port's
listener belongs to *our* process group, `/api/health`, seed-user login, scripted model
seeded, `/login` renders, repo HEAD unchanged since launch. Run it first whenever
anything looks off; on failure read `runs/<RUN_ID>/logs/{migrate,backend,frontend}.log`.
A HEAD mismatch means the backend (no `--reload`) is serving old code: `down.sh` + `up.sh`.
Both backend and frontend verification use the code at launch; restart the stack after
changing source files. Frontend hot reload only observes the disposable snapshot.

## Drive

```bash
node $S/drive.mjs <journey> [--run RUN_ID] [--headed]
```

`<journey>` is a name from `journeys/` or a path to your own `.mjs`. Shipped journeys:

| journey | feature file | proves |
| --- | --- | --- |
| `auth-login` | `features/auth-login.md` | wrong password rejected, seed user lands on dashboard |
| `agent-create-manual` | `features/agent-create.md` | `/agents/new` → 직접 만들기 → 저장 persists |
| `chat-send` | `features/chat-send.md` | composer send → streamed reply → survives reload + API |
| `hitl-reject` | `features/hitl-approval.md` | approval card → 거부 → tool never ran |

For a change not covered, write a journey file (put scratch ones anywhere; promote
durable ones into `journeys/` and the feature map). Contract:

```js
export const options = { loggedOut: false }          // optional
export default async function ({ page, api, step, expect, scriptedModelId, state }) {
  // page: Playwright Page, baseURL = APP_URL, already logged in as the seed user
  // api.get/post/patch/delete('/api/...', body) -> JSON; CSRF header is added for you
  // step('name') -> numbered screenshot + ARIA snapshot + steps.jsonl line
  // return value is stored in result.json as `output`
}
```

Selectors: prefer `getByRole`/`getByText` with the Korean UI copy (locale `ko-KR`),
`#login-email`/`#login-password`, `textarea[data-moldy-composer-input="true"]`,
`[data-moldy-message-role="user|assistant"]` filtered by `hasText`. Copy lives in
`frontend/messages/ko.json`; existing specs in `frontend/e2e/` are the best selector source.
Give backend-dependent assertions 15–60 s timeouts.

## Evidence

Each drive writes `runs/<RUN_ID>/evidence/<journey>-<timestamp>/`:
`result.json` (status, error, output, rev), `NN-<step>.png`, `NN-<step>.aria.yml`,
`steps.jsonl`, `console.jsonl` (console errors/warnings, page errors, HTTP 5xx),
`trace.zip` (`cd frontend && pnpm exec playwright show-trace <path>`). Exit code 0 = pass.

Proof standards:
- Drive the real user path in the browser. API calls are for preconditions and for a
  second, read-only view of the result — never for the action being proven.
- Capture the action and the resulting state (`step()` before and after), not only the end.
- Confirm side effects: reload and re-read, or check the API (rows persisted, artifact
  absent/present, run idle).
- Do not use `E2E_TEST_HELPERS` endpoints or internal setters as the action.
- An empty `console.jsonl` is part of a clean pass; report anything in it.
- `step()` preserves the caret instead of temporarily changing input styles during
  screenshots, which can race React hydration. Expected login 401 console errors are
  retained too; distinguish them from unexpected application errors in the report.
- The scripted model replaces the LLM only. Behavior that depends on real model output
  needs the live lane (`frontend/e2e`, `E2E_LANE=live`), not this skill.

## Cleanup

```bash
bash $S/down.sh [RUN_ID]
```

Kills only the process groups recorded in `state.env`, stops this run's Postgres cluster
(`pg_ctl -m fast stop`), deletes `runs/<RUN_ID>/{data,pg}` and the frontend snapshot/build
at `frontend/.next-verify/<RUN_ID>`, and writes
`runs/<RUN_ID>/CLEANED`. `evidence/` and `logs/` survive. Idempotent; run it after a
failed `up.sh` too. A stop or deletion failure exits nonzero without claiming cleanup.
Never `pkill node`/`uvicorn` or `docker rm` by pattern.
Old evidence: `rm -rf .verify-moldy/runs/<RUN_ID>` when no longer needed.

## Feature map

`features/README.md` indexes the user-facing features and their recipes. When you change
a feature, update its file; when a journey breaks because the UI changed on purpose, fix
the journey and the map together.
