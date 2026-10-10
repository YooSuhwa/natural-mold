# Organization and authorization execution record

Status: P0 complete; P1 in progress — full implementation goal active
Started: 2026-10-10 (Asia/Seoul)
Branch: `feature/org-authz`
Baseline: `9657ca60552e8f5e8c45ce6a8bd176c500c8992a`
Specification: [original HTML](org-authz-plan.html)
Handoff: [original README](org-authz-handoff.md)
Architecture: [ADR-023](../design-docs/adr-023-org-authz.md)

## Scope and execution contract

Implement P0–P7 and P8 implementation work in task order (17.1), with a commit
per task. Keep legacy behavior and RLS-disabled defaults for PR 1. Never apply
migrations to the shared/main database during development: disposable stacks
only. The worktree `.env` and `data` point to the main checkout as required;
tests override database and data paths before application startup.

P8 operational observation and cleanup remain required subsequent gates, after
deployment and actual traffic. No synthetic evidence is called 14-day production
observation. U-05 real-participant study is separate from automated UX testing.

## P0 checklist

- [x] A-01 — ADR-023 and supplied plan registered; decisions accepted through the
  user's instruction to implement the supplied plan. Source/design review recorded.
- [x] A-02 — OpenFGA Compose and settings; private network startup evidence.
- [x] A-03 — pinned SDK client; timeout, retry, metrics tests.
- [x] A-04 — model/tests and CI; 144 checks and six ListObjects cases.
- [x] A-05 — bootstrap/check/explain CLI; model ID persistence.
- [x] A-06 — fake authorization and live integration lane.
- [x] A-07 — route policy declarations and missing-policy ratchet.
- [x] A-08 — ownership predicate ratchet (stronger 173-occurrence AST baseline).
- [x] A-09 — CSRF presence and single Alembic head invariants; target order in P3.
- [x] A-10 — middleware ordering, durable auth interrupt, revocation prototypes.
- [x] A-11 — RLS 15 scenarios and two counterexamples in disposable PostgreSQL.
- [x] A-12 — boundary intersection benchmark with the supplied final model.
- [x] A-13 — complete 22-table schema and staged migration contract review.

## Verified baseline

- Clean detached checkout at the exact handoff commit; now on feature/org-authz.
- `bash scripts/worktree-setup.sh`: `.env` and `data` symlinks verified.
- Backend lock resolves deepagents 0.7.11, SQLAlchemy 2.0.48, asyncpg 0.31.0.
- Existing ownership dependency loads current users from DB; JWT authority claims
  do not grant operator permission. CSRF validates the token subject.
- `Conversation` has no private owner column; ownership joins through `Agent`.
- Current migration head is m77_side_chat_link; no org/authz implementation exists.
- Compose currently lacks OpenFGA; scheduler owns its advisory-lock leader.
- Handed-off model and prototype artifacts copied byte-for-byte. Supplied
  `scripts/p0/results` are historical planning evidence, not current execution.

## Decision changes and clarifications

2026-10-10: A-13's 19-table count is stale; use 22 from 6.1/20.1 (SSO included).
2026-10-10: Follow 17.4 two-PR sequence, resolving section 18's separate D-01 PR
wording by keeping D-01 in its own commit within PR 1. See ADR-023.

## A-02 verification (2026-10-10)

- Configuration red test: eight failures before new settings existed; after
  implementation `uv run pytest -q tests/authz/test_config.py`: eight passed.
- OpenFGA 1.22.0 and dedicated PostgreSQL 16 started in disposable Compose
  project `moldy-orgauthz-p0-5b15`; migration exited successfully.
- Network-only health request returned `{"status":"SERVING"}`. Container
  HostConfig.PortBindings is `{}`; no FGA host API/metrics port was exposed.
- Compose requires root-environment OPENFGA_DB_PASSWORD and OPENFGA_API_TOKEN;
  backend/.env is not a source for Compose variable interpolation. Do not edit
  the shared installation `.env` for disposable tests.
- Token uses Pydantic SecretStr; diagnostics redact it. Timeouts/poll periods
  reject zero and modes reject invalid values. Defaults remain legacy/single_tenant
  and RLS disabled.
- New authorization settings are isolated in app/authz/config.py; the existing
  single-responsibility Settings aggregate inherits them without moving unrelated
  installation settings.

## Next stage start conditions

P1 begins only after every P0 acceptance item and the phase CI-equivalent checks
pass. Each missing prerequisite remains unchecked. Required checks include
backend lint/types/tests/coverage, disposable PG integration and cleanup,
frontend lint/build/tests/coverage and scripted smoke.

## P0 evidence through A-12 (2026-10-10)

- Backend baseline suite: 4,665 passed after correcting the canonical document
  inventory. Frontend baseline: 324 files / 1,995 tests passed under Node 22.
- A-03 native SDK wire tests cover retry, total timeout, reversed batch order
  and per-item errors. A-04 canonical model: 17 cases, 144 checks and six
  ListObjects cases passed; compiled model JSON matches the supplied final model.
- A-05 red collection failed before bootstrap existed. Empty-store deployment
  and repeated bootstrap pass with real OpenFGA against SQLite and PostgreSQL
  version storage. CLI defaults require deliberate DB selection and pinned IDs.
  Run from backend: `uv run python -m app.authz.cli --help`.
- A-10 native Deep Agents tests verify partial denial, authentication before
  approval, approval retained for permitted calls, retry/skip/cancel without
  duplicate tools, cancellation call completion, and repeated retry. A real
  PostgreSQL checkpointer survives a closed connection and reconstructed graph.
  The deny-overlay prototype covers grant and four organization-wide revocation
  scopes, committed markers, unaffected organizations and storage failure.
  These are P0 prototypes; production runtime integration remains P5/C-08 work.
- A-11 owned PostgreSQL fresh fixture: 15/15; two counterexamples: 2/2;
  concurrent 8 × 100 operations: zero cross-tenant contamination. Failed
  scenarios now exit nonzero; engines dispose even when scenario bodies raise.
- A-12 final-source two-round benchmark: CURRENT Check p95 3.34/3.51 ms,
  Batch50 p95 22.02/21.70 ms, cross-tenant grants 0/100 in both rounds.
  The deliberate baseline misgrants remain 100/100. Exact workloads, canonical
  model bytes and list cardinalities are preserved. Owned stores were deleted
  and independently returned 404. This artificial local workload does not
  establish production capacity or observation evidence.
- Canonical disposable PostgreSQL lane: 54 integration nodes passed, none
  skipped, including both bootstrap dialects, durable interrupt and source-row
  permission checks. Cleanup receipt validated, foreign containers preserved.
  Receipt: `.omo/evidence/project-restart-consolidated-roadmap/org-authz-p0-native.json`.
- Current outputs are ignored under `output/org-authz/p0/`; committed
  `scripts/p0/results/` remain clearly historical handoff evidence. Detailed
  refined RLS/benchmark logs and verification receipts are in that output folder.

Task commits: A-03 `84ba74f0`, A-04 `bf71a22b`, A-05 `9f18fe95`, A-06
`d29120f2`, A-07 `a83b924c`, A-08 `1228267e`, A-09 `7a94a0b5`, A-11
`37b0b964`, A-12 `af0260f3`. A-10 is committed with this evidence entry.

## A-13 implementation and review history (2026-10-10)

- Complete 22-table draft ORM/Core definitions are in
  `backend/schema_drafts/org_authz/`, isolated from live app metadata.
  All new constraints and indexes have stable names; partial indexes declare
  PostgreSQL and SQLite predicates, and organization scope uses composite FKs.
- All 15 staged draft files exist. Reversible m78–m89 upgrade/downgrade passes
  on seeded SQLite; deferred cleanup DDL passes only in explicitly disposable
  validation. The active app/Alembic head remains m77.
- Actual Alembic m77 PostgreSQL, in an owned UUID database, accepts the complete
  draft chain. A nonowner/no-BYPASSRLS role sees zero agents without context,
  correct private rows with context, and cannot update platform credentials.
  Extended platform-only RLS recheck passed against actual Alembic m77.
- API caller / side-parent / hidden-session ownership is preserved on backfill;
  repeat runs do not duplicate tenants, memberships or roles. Missing hidden
  source owners abort. Owner deletion is restricted and agent SQL deletion
  preserves conversations. Downgrade keeps content and refuses orphan histories.
- Marketplace private/restricted/public/unlisted/system projection is compared
  by exact grant set, including view/install/manage mapping and a repeat run.
  Historical migration results remain separate from these current executions.
- Clarifications: SSO creation is in m80; deferred order is m89→m92→m90→m91;
  source inventory counts active ORM tables separately from five preserved
  historical DB tables. See ADR-023 for rationale.
- Current focused gate: 48 unit tests passed, seven integration tests deselected
  by the normal lane. Ruff and Pyright passed for the draft and test modules.
  The complete-chain PG test creates and removes its own UUID database and role.
  A-13's final schema-manifest/event review and canonical full integration gate
  remain open; the checkbox is deliberately still unchecked.

Baseline phase checks: frontend lint:all and build passed under Node 22 in
isolated roots. Scripted smoke selected/executed 18 tests, no unexpected failures;
its owned cleanup/export receipt passed. A later independent port check rejected
`live_port` because a separately started main-checkout stack occupied 8101/3100
after this lane finished. The user explicitly authorized stopping the unused
server. After verifying and stopping those backend/frontend server processes,
the unchanged independent checker passed both receipts (`validated=2`);
`output/org-authz/p0/cleanup-independent-recheck.json` records this recheck.
Coverage gates
initially required a clean Git state and rejected the uncommitted draft before
measurement. At `4190d87b`, the complete backend coverage gate passed (4,692 tests,
57 integration tests deselected); frontend coverage also passed. The canonical
owned PostgreSQL lane passed all 57 integration tests and independent cleanup.
P0 is not closed and P1 has not begun.

Five independent A-13 reviews at `4190d87b` yielded QA PASS and goal/context/code/
security FAIL. Blockers are the absent 19-event source crosswalk, omitted LDAP
configuration, global identity lifecycle tied to one tenant connection, incomplete
child/history RLS inventory and destructive m82 rollback for post-upgrade rows.
Corrections and targeted regressions are in progress; these findings are not
treated as final acceptance or whole-product completion.

A-13 correction evidence: the independent source crosswalk verifies all 19
original event rows and a fixed reflected contract for 56 tables / 802 columns;
the nine source-contract tests passed. The owned PostgreSQL RLS correction suite
passed 60 tests, including indirect payload read/write/reparenting denial and
the actual saver setup (95 physical tables when initialized). A separate owned
PostgreSQL recheck passed the initial/full migration chain and shared-issuer
identity lifecycle tests. LDAP transport/configuration and incoherent rollout
receipt regressions passed. Final re-review and clean-commit phase checks remain
required before A-13/P0 closure.

At `19c7bd88`, backend coverage passed 4,719 tests (117 integration tests
deselected), canonical PostgreSQL passed all 117 selected/executed tests,
scripted smoke passed all 18, and independent cleanup validated both receipts.
Whole-backend Pyright and the 93-file changed-Python type/format ratchets passed.
Frontend evidence is unchanged at tree `842189e5d083fc427957db42fa3a032152f9931c`.
Goal/context/QA/security re-reviews passed. Code re-review rejected a further
rollback gap: FK-only child rows could be created under existing scoped roots
without triggering the direct-column guard. A-13 remains unapproved until the
complete physical dependency guard and PostgreSQL concurrency regression are
verified and reviewed. The next correction also separates snapshot codec,
dependency discovery and restoration into modules below the review-size limit.

## P0 closure (2026-10-10)

Approved source: `53e1e6186d53fcd597fa3f0eb2637067df9a757d`.
All five fresh leaf reviews (goal, context, code, security, manual QA) passed at
that source. The earlier failures are retained in the evidence ledger rather
than overwritten. The final rollback guard includes physical FK descendants,
optional saver identities, version-2 provenance and a real concurrent-writer
regression. Original HTML/model planning inputs remain preserved.

- Backend coverage gate: 4,734 passed with four temporary workers; floor passed.
- Canonical disposable PostgreSQL lane: 118 selected/executed, zero failures or
  skips, independent cleanup passed (`validated=1`).
- Whole-backend Ruff and Pyright passed; changed-Python type/format ratchets
  passed for 100 files against the original baseline.
- Frontend lint:all, build, 1,995 Vitest tests and coverage passed at the unchanged
  frontend tree `842189e5d083fc427957db42fa3a032152f9931c`. No frontend changes in
  the final rollback delta required repeating those checks.
- Scripted smoke: 18 selected/executed at `19c7bd88`; no unexpected failures,
  owned cleanup/export passed. It remains applicable to the byte-identical
  application tree `29000575c4a931715bda595bf8770798da36283f` and frontend tree.
  Independent cleanup validated both smoke and PG receipts.
- Fetched and merged `origin/main`; it still points to baseline `9657ca60`, so
  no route/table/migration renumbering was needed. Git hooks were preserved.

Evidence is under ignored `output/org-authz/p0/` and
`.omo/evidence/project-restart-consolidated-roadmap/`. The final review reports
are `reviews/a13-{goal,context,code,security,qa}-r3.md`; their exact source and
verdicts are recorded in `reviews/ledger.jsonl`. Broad gate artifacts are
`backend-coverage-r3.log`, `backend-typecheck-r3.log`, and
`org-authz-p0-schema-r3.json`; predecessor frontend/smoke receipts are linked by
unchanged tree identity, not claimed as newly executed tests.

At P0 closure, live app metadata/Alembic remained m77; authorization
defaults remain legacy and RLS disabled. No main/shared database was migrated,
no deployment or PR was created, and full implementation/operational acceptance
remains unfinished. P0's CLI uses named query flags; C-07's complete operational
CLI must finish the plan's subject-aware diagnostic interface alongside rebuild
and reconciliation. The 14-day shadow observation, two-week stabilization and
real-participant U-05 study are future gates.

## P1 checklist

- [x] B-01 — live m78–m80 models and immutable Alembic revisions.
- [x] B-02 — m81 scope, m82 default organization backfill, m83 constraints.
- [x] B-03 — organization context and active organization API.
- [x] B-04 — organization control APIs and focused/native/full backend gates; runtime fanout remains E-03.
- [x] B-05 — registration policies, atomic invitation acceptance/resend and signup scope/bootstrap.
- [ ] B-06 — organization filters for private data.
- [ ] B-07 — organization switching and administration UI.
- [ ] B-08 — ownership constraints and agent archive lifecycle.
- [ ] B-09 — frontend state reset and transport inspection.
- [x] B-10 — company context, suspension and deployment modes.
- [ ] B-11 — company administration API.
- [ ] B-12 — SaaS platform company administration API.
- [ ] B-13 — tenant/platform sessions and derived task context.
- [ ] B-15 — cookie, refresh revocation and token claim contracts.
- [ ] B-16 — company data keys and ciphertext version 2.

## B-01 implementation (2026-10-10)

The application now registers 13 organization/enterprise identity models (76
active tables) and the optional `users.last_active_org_id` pointer. The live
single Alembic head is `m80_org_capabilities_invitations`. Migration definitions
contain immutable DDL; they do not import review drafts or mutable app models.
The pointer uses `use_alter=True` to break the user/organization DDL cycle.

P0 draft migration tests retain their original 63-table m77 fixture and a frozen
user definition. Physical migration tests explicitly start at m77 instead of a
moving `head`, so live promotion cannot skip or double-create reviewed drafts.
The fixed m89 source contract still matches; original planning files are untouched.

- Full backend unit suite: 4,737 passed, three existing MCP deprecation warnings.
- Additional live SQLite upgrade/downgrade repetition: focused model suite 4 passed.
- All authz PostgreSQL integration tests: 71 passed, 93 unit tests deselected.
  Includes live m77→m80→m77 repetition, exact columns/FK actions/PK/unique/check
  names, mapped model operations, cross-company group membership rejection,
  deleted active organization pointer clearing, global identity preservation and
  plaintext LDAP rejection. Each test owns and removes its database or schema.
- Authz focused unit suite: 93 passed before the additional SQLite scenario.
- Whole-backend Ruff passed. Whole-tree format check reports 114 pre-existing
  files; the changed-file format ratchet passed for all 23 changed Python files.
  Changed-file type ratchet passed with zero errors/warnings for the same files.

Logs: ignored `output/org-authz/p0/b01-{unit-all,all-postgres,focused-r2,ruff,format}.log`.
Shared/main databases were not migrated; development server startup against an
unmigrated shared database is not an acceptance claim. Next work is B-02/B-03/B-10.

## P0 publication and B-03 implementation (2026-10-10)

P0 closure `f0c4d8a1c552619562c51705cd19171a8aab7930` is pushed to
`origin/feature/org-authz`; the branch now tracks that remote. The pre-push hooks
passed 4,734 backend and 1,995 frontend tests in a separate, owned checkout.
The first attempt's `DATA_ROOT` override conflicted with the default-path test;
the test was preserved and the isolated checkout used its own default data path.
A subsequent SSH connection closed during hook execution; retry with explicit
keepalive and no shared control connection completed successfully. Hooks were
never bypassed. Temporary review/push checkouts are archived, and all useful
reports/logs remain in this workspace's ignored output directory.

B-03 adds explicitly SELF-marked `GET /api/me/organizations` and CSRF-protected
`POST /api/me/active-org`, plus the separate `get_org_context` dependency.
Organization summaries expose company/status, direct or group-derived org roles,
and effective navigation capabilities. Both active company and organization
membership are required; platform operator status does not bypass membership.
Selection updates the saved preference and issues the HttpOnly organization
cookie with the authentication transport settings. Logout also clears it.

Decision clarification: explicit malformed/foreign cookies fail with 403, as
required by 16.6. Only an absent cookie with a stale saved preference can fall
back to another active membership; the broader fallback in the illustrative
8.1 snippet would violate the explicit-cookie test. Company suspension retains
the specific `tenant_suspended` error. Membership is checked from DB on every
request, with FastAPI request-level dependency caching. No process TTL is used
before the mutation invalidation protocol exists; a later cache must preserve
the tested immediate removal/suspension behavior.

- Full backend unit suite: 4,755 passed, three existing MCP warnings, with an
  owned temporary runtime/data root removed after execution.
- Authz and authentication regression selection: 139 passed; focused context,
  cookie security and HTTP scenarios: 17 passed.
- Native PostgreSQL HTTP flow: 1 passed. Real m80 migrations, JWT/CSRF cookies,
  foreign target denial, valid selection and a protected request denied after
  separately committed membership removal; owned database removed. This is API
  acceptance evidence, not browser/UI acceptance or production RLS enforcement.
- Whole-backend Ruff and Pyright passed. Changed-file type and format ratchets
  passed for 14 Python files; ownership ratchet remains 173, additions zero.

Evidence: ignored `output/org-authz/p0/b03-{unit-all,auth-regression-r2,
postgres-api-r2,ruff-all,pyright-all}.log`. B-01 is committed as `941e9247`.
P1 remains in progress; next tasks are B-02 scope/backfill and B-10 company context.

## B-02 migration promotion (2026-10-11)

The live linear Alembic chain now reaches `m83_org_scope_not_null`: m81 adds
scope to exactly 20 organization tables and three tenant catalogs, m82 resolves
private conversation owners and seeds the default company/organization, and
m83 enforces scope and preserves conversations after agent deletion. Shared
resource owners use RESTRICT; conversation agents use nullable SET NULL with
name/activity snapshots. Legacy scope uniqueness is replaced by scoped indexes.

Historical helpers live in the frozen `app/migrations/org_authz_v1` package.
They reflect physical tables, use a frozen SQLite UUID manifest, and read only
the three original migration environment settings. No helper imports current
app models, Base metadata, runtime settings, or review drafts. The unchanged v2
`_migration_m82` record contains original/expected values and transitive physical
FK/checkpoint identities; payloads and ciphertext are excluded. Referenced
natural keys are also captured so reparenting cannot evade rollback checks.
Sorted PostgreSQL table locks cover verification through restoration.

- Native PostgreSQL acceptance: four passed, including actual m80→m83→m80
  twice, unresolved-owner transaction rollback, second-writer lock timeout, and
  actual PostgresSaver setup/put/put_writes followed by rollback refusal.
- All authorization PostgreSQL integration scenarios: 76 passed, 141 unit cases
  deselected. Owned databases/schemas/FGA processes are absent after cleanup.
- Live SQLite chain: two passed. Tests explicitly adapt PostgreSQL `now()` and
  the legacy m40 partial indexes rather than claiming SQLite is production.
- Prototype/promotion parity passed for DDL semantics and all v2 data/identity
  fields, excluding four generated creation/grant/membership timestamps and
  column ordering. Separate round-trip tests preserve original timestamps.
- Worker-focused production helper suite: 25 passed. Whole-backend Ruff/Pyright
  and changed-file format/type ratchets passed (31 files, zero type errors).
  Ownership ratchet remains 173, additions zero. Helper modules are at most 233
  nonblank/noncomment lines, with no function above three declared parameters.
- Complete B-02 backend unit regression: 4,785 passed, three existing MCP warnings,
  with the official isolated command runner and four pytest workers (190 seconds).
  Independent B-10 draft tests were excluded from this B-02 gate. The first
  manually prepared runner placed TMPDIR under the checkout, failing the existing
  system-temp isolation assertion (4,784 passed, one failed). No source/test was
  weakened; the official runner corrected the environment and the entire suite
  passed. The owned temporary tree from the initial attempt was removed.
- A strengthened native round-trip scenario additionally compared every column
  and row of all original physical tables, including ciphertext and timestamps;
  both full-history migration cycles passed. No legacy content was changed.

Evidence: ignored `output/org-authz/p1/b02/` logs. Semantic legacy constraints
survive downgrade; unnamed legacy constraints may acquire deterministic names.
Original planning/prototype files were not modified. No shared database migration
or app startup occurred. B-06/B-08 model/writer changes are required before
deploying this schema: migration promotion alone does not make existing resource
writers compatible with required scope or complete the archive lifecycle.

## B-10 company context and mode foundation (2026-10-11)

`app/tenancy` now resolves fresh company membership/status and scoped admin or
auditor roles. `get_tenant_context` derives the company from a validated current
organization and revalidates the org/company pair. Explicit company lookups
collapse foreign/missing targets to the same 404; an operator flag adds neither
membership nor company roles. Governance permissions exclude resource-content
permissions. Queries remain inside the shared authorization boundary, preserving
the ownership ratchet without increasing its baseline.

Company suspension returns `tenant_suspended`, preserves refresh tokens and
allows the existing login to work after resume. Deletion-scheduled companies are
unavailable. Physical status is refreshed even in an existing SQLAlchemy identity
map; a separate PostgreSQL transaction can invalidate the next check immediately.
Suspension/unavailability/permission denials use the existing best-effort audit
path. B-13 will finish tenant-scoped audit/session propagation.

Implementation detail supporting the planned mode-sensitive navigation:
`GET /api/me/installation` is an authenticated SELF endpoint returning deployment
mode, multi-org availability and SaaS operator management availability. It does
not require company membership, so a SaaS operator can navigate without content
access. `require_saas_platform_operator` hides management in installation mode
(404) and denies nonoperators in SaaS (403). B-12 consumes this dependency for
actual company lifecycle routes; B-07 consumes the navigation flags for UI.
The OpenAPI fragment and referenced schemas for all three new self-navigation
routes are frozen in `tests/authz/openapi_new_routes.json`; frontend type comparison
remains part of B-07. Switching deployment mode does not alter stored memberships.

- Focused company context/mode cases: nine passed. HTTP navigation/dependency
  cases: ten passed, including DB authority differing from the original JWT,
  suspension/resume with unchanged login, refresh-token preservation and an
  actual recorded denial audit. OpenAPI snapshot case passed.
- Isolated authz/authentication/CSRF/profile regression: 196 passed with four
  workers, 49 seconds; the official runner reports cleanup removed. An initial
  selection used nonexistent test filenames and collected zero tests; the actual
  filenames were selected for the successful run.
- Native PostgreSQL cross-transaction suspension, resume and removal: one passed;
  the owned database was removed. This is context/API evidence, not browser or
  global enforcement evidence for the later resource route conversion.
- Whole-backend Ruff/Pyright and changed-file type/format ratchets passed (15
  Python files, zero errors). Ownership ratchet stays 173, additions zero.

Evidence: ignored `output/org-authz/p1/b10-*.log`. Current live Alembic remains
m83; no shared database migration, deployment or UI acceptance is claimed.
Next: B-04 organization administration, B-06 private scope and B-11/B-12 company
administration. The full P0–P8 goal remains active.

## B-04 working checkpoint (2026-10-11, not complete or committed)

Current uncommitted control API work includes organization metadata/policy,
manual group CRUD and group rosters, role/capability deltas, membership
add/suspend/remove/rejoin, and invitation create/list/revoke. Router/service
permission checks use fresh DB memberships and scoped roles; operator flags do
not bypass the company boundary. Mutations use the locked organization timestamp
and increment its epoch. Last effective admin protection covers direct and group
admins and rolls back rejected changes with a savepoint. Member removal preserves
the global account/history, requires resource handoff first, clears scoped
membership grants, and stops scoped triggers/API keys. Suspension trigger handling
still needs finishing; resource revocation/outbox integration remains C-04/E-02.

Invites use a domain-separated MAC over a random invitation UUID and the resolved
installation signing secret. Authorized idempotent creation can reproduce the
link while only SHA-256 is stored. List responses exclude both token and hash.
After secret rotation, existing token hashes remain usable for B-05 acceptance,
but re-copy returns no substitute token when its hash differs. This is an
implementation choice supporting hash-only storage plus idempotent retry; no
email was sent. Acceptance and registration policies remain B-05.

Audit names now follow section 14.1, with constants in `authz/audit_actions.py`.
Role/capability deltas record grant and revoke events separately; metadata and
policy edits use separate events. Scope propagation to actual audit columns is
still H-03/B-13. The new-route snapshot automatically discovers additions from
the frozen legacy identities and includes all referenced schemas; updates remain
reviewed source changes rather than silent acceptance of API drift.

Current evidence: 25 focused control/API/contract cases passed; whole-backend
Ruff/Pyright passed; ownership ratchet remains 173, additions zero. One native
m83 scenario passed with real JWT/CSRF, concurrent edits (one 200/one 409), scoped
group/role/capability operations, and owned-resource offboarding refusal followed
by removal after handoff/deletion. Logs are `output/org-authz/p1/b04-*.log`.
These are focused API gates; no B-04 full-regression or UI closure is claimed.

Before B-04 closure, finish principal search (current-org equality, two-character
query, 20 results, 30/user/minute, email privacy), catalog policy API and impact
handoff to E-03, suspension/background handling, invitation expiry/revocation
edge cases, additional scope/concurrency tests, OpenAPI/type/format checks, and
the appropriate regression selection. Replace interim physical m81 scope-column
projections in member-exit queries when B-06 promotes ORM scope fields. No B-04
commit, shared database migration, deployment or goal completion is claimed.

### B-04 search/suspension follow-up (2026-10-11)

Principal search now enforces current-org equality, stripped two-character
queries, whitelisted comma-separated types and a combined 20-result ceiling.
Only active users/company/org members are searched. Member email matching is
exact and its response email is hidden; member managers can match/disclose email.
Group results count active memberships. LIKE wildcard characters are escaped.
The live SlowAPI endpoint uses `30/minute` keyed by the authenticated DB user.
Four HTTP cases passed, including 30 successes/31st rejection and a second user
on the same browser IP succeeding. The new schema is included in the snapshot.

Org member suspension now pauses scoped triggers without revoking API keys or
refresh tokens. A new native red case observed an active trigger after suspension;
the shared pause helper corrected it. The expanded actual-m83/JWT/CSRF scenario
passed (4.91s), including resume, owned-resource removal refusal and account
preservation. API-key revocation remains permanent only for member removal.

Current focused control/contract selection: 29 passed. Whole-backend Ruff and
Pyright passed; ownership ratchet remains 173, additions zero. Logs:
`b04-principal-search-rate.log`, `b04-suspension-trigger-{red,green}.log`,
`b04-core-search-suspend.log`, `b04-{ruff,pyright}-search-suspend.log` under the
ignored P1 output directory. B-04 remains uncommitted and incomplete: catalog
policy API/impact handoff, invite edge cases, wider scope/concurrency evidence,
regression closure and later C-04/E-02 effects still remain. Next work must retain
the full P0–P8 objective; these focused gates do not close the whole feature.

### B-04 API implementation closure (2026-10-11)

The organization control API surface now includes catalog policy GET/PUT, in
addition to the metadata, directory, membership, invitation, role/capability and
principal-search routes described above. Catalog overrides are stored in
`organizations.settings.catalog_policy` as typed `{items: [{type, id, enabled}]}`
records. Company policy uses the same format in `tenants.settings.catalog_policy`.
Omitted items inherit enabled status; effective availability is the intersection
of platform visibility, company and organization policy. PUT replaces this
organization's overrides, preserves unrelated settings, checks the locked ETag,
increments the authorization epoch and commits `org.catalog_policy_update` in
the same transaction. Inaccessible/missing/non-system items share the same 404.
Company-disabled or platform-hidden items cannot be explicitly enabled.

Inventory contains only platform system tools/skills/MCP and platform/current-
company models. The model company filter uses the actual m81 physical column;
B-08 must replace this interim projection with its promoted ORM mapping.
Catalog read/write locks the company row as well as the already locked org row,
so concurrent company policy mutation cannot validate against a stale policy.

**E-03 integration contract remains open:** consume the stored policy, add the
impact preview (affected agents and required connections), transactionally
write/revoke `system_fanout` grants through C-04, invalidate connection summaries,
and enforce the effective policy in candidate lists and runtime/model resolution.
This API storage gate does not establish `can_use=false` or runtime exclusion.
Company policy editing is B-11; invitation acceptance/registration is B-05.
Membership removal still needs C-04/E-02 resource-grant/outbox/retention effects.
Audit tenant/org column propagation remains H-03/B-13. The full goal stays active.

Evidence under `output/org-authz/p1/`:

- `b04-focused-catalog-invites.log`: **38 passed** including catalog boundaries,
  expiry/accepted/revoked invite retries, secret rotation without substituting a
  mismatched token, repeat-revocation timestamp preservation and principal rates.
- `b04-native-control-current.log`: **3 passed, 11.83s** on owned, fully migrated
  PostgreSQL databases. Real JWT/CSRF HTTP verifies stale edits, member suspension,
  owned-resource exit refusal, company model isolation, atomic catalog/audit writes
  and concurrent invitation retries (one 201, one 200, one row/token). A separate
  direct-service database scenario verifies that suspension pauses only the
  selected org, preserves both API keys, and removal revokes only the selected
  org's key while resources/entry points in another org survive. Each lab database
  was removed in its context-manager cleanup.
- `b04-full-backend.log`: **4,842 passed, 3 existing warnings, 233.72s** through
  the official isolated runner; child exit 0 and cleanup `removed`.
- Whole-backend Ruff/Pyright pass, changed Python type/format gates select 56
  files, and the ownership ratchet remains **173, additions 0**. The new-route
  OpenAPI snapshot includes the catalog surface and reference closure.

There is no frontend/browser acceptance, shared database migration, deployment,
PR or full P1/P0–P8 completion claim. Continue with B-06 private ORM/query/writer
scoping and the remaining company/session/crypto tasks before closing P1.

## B-05 invitation acceptance/resend checkpoint (2026-10-11, registration still open)

`POST /api/invitations/accept` now requires an authenticated active account and
CSRF. Its write-only `SecretStr` token is looked up by stored SHA-256, so a valid
link remains usable after the installation signing secret rotates. The service
locks the organization, company and invitation in the same order as catalog
mutations, reloads account/status facts and refuses revoked, used, expired or
email-mismatched links. A suspended company/org/member cannot be restored through
the link; an invitation predating an org removal cannot rejoin that member.
Removed/changed groups are validated before any membership write.

Acceptance atomically creates company/org membership as needed, adds the declared
role/manual groups without duplicating existing facts, consumes the invitation,
updates the user's active org and increments the org epoch. Separate acceptance,
member-add, role-grant and group-member events describe actual changes. The router
commits once and then issues the existing HttpOnly active-org cookie. A concurrent
or later replay receives `invitation_used` and creates no duplicate grant.

`POST /api/orgs/{org_id}/invitations/{invitation_id}/resend` implements section
13.12.2's requirement that reissuing invalidates older links. It revokes pending
links for that address and commits the replacement/hash and audits together.
Repeated requests for the original row return its newer live replacement when
the current signer can reproduce that replacement's token. After signer rotation,
explicit resend creates a usable new link rather than substituting a token which
cannot match an old stored hash. Authorization/CSRF and source/group guards remain
in the existing organization control service. No mail was sent.

A focused red test also exposed plaintext token echo in FastAPI's validation
error `input` field. The shared validation handler now omits raw input values,
retaining the field locations/messages needed by clients. This prevents schema
length/type errors from copying submitted secrets into API error responses.

Evidence in the ignored `output/org-authz/p1/` directory:

- `b05-invitation-focused-final.log`: **23 passed** for acceptance, resend,
  existing invitation behavior and the reviewed OpenAPI snapshot. Includes
  current-key login accepting an older link, blocked/removed membership, deleted
  groups, secret-free validation errors and original-link invalidation.
- `b05-invitation-postgres.log`: **1 passed, 4.32s**, actual owned m83 database
  and real JWT/CSRF requests. Concurrent create/resend each return one 201/one 200;
  concurrent acceptance returns one 200/one 409. Exactly one recipient role and
  acceptance audit remain. The original resent link fails, and the lab is removed.
- `b05-full-backend-invitations.log`: **4,855 passed, 3 existing warnings,
  208.92s** with the official isolated runner, child exit 0 and cleanup `removed`.
  The signing-secret-rotation case was added after that isolated copy was created
  and is covered separately by the final 23-case focused run; production behavior
  did not change between those runs.
- Whole-backend Ruff/Pyright pass; changed-file type/format gates select **14**
  Python files; the ownership ratchet remains **173, additions 0**.

B-05 is not complete: `auth_service.register` still needs the three installation
registration policies, invitation-backed account creation, default-org membership
and first-user org/company bootstrap roles, with atomic failure/concurrency tests.
Default signup must fail visibly if the migrated installation default is missing,
rather than create an unscoped account. Update unit fixtures to represent m82's
seeded default company/org; preserve explicit no-membership scenarios by removing
membership in those fixtures. Actual API integration fixtures should start at m83
before signup. This prerequisite comes before B-06's private writer/query scoping.
Pending-invitation revocation on issuer offboarding, grant outbox fanout and audit
scope columns still require C-04/E-02/H-03/B-13. No UI/deployment/full-goal acceptance
is claimed.

## B-05 signup implementation and final gates (2026-10-11)

The existing register endpoint now delegates to typed registration policy,
membership and account services. `default_org` enrolls the new account in the
migrated installation default; `invite_only` requires `invitation_token` and
joins the stored invitation's company/org/role/groups; `closed` rejects new
accounts even with a valid invitation. Installation, company and organization
restrictions intersect, so an org cannot reopen company/installation signup.
Existing-account invitation acceptance is separate from account creation.

The migrated default company is selected by its canonical UUID namespace rather
than its mutable slug. Missing initialization returns 503 without an unscoped
account. Account insertion, membership/role changes, invitation consumption,
preference, auth refresh whitelist and successful audit records commit once in
the existing router. Cookies, including the HttpOnly org selection, are issued
after commit. Password hashing uses the existing bcrypt helper off the event loop.
Group/link/status failures after account flush roll the account back as well.

The default organization row serializes signup until that transaction commits,
including the first-user count. When first-user promotion is enabled, exactly
one first account becomes platform operator and receives explicit default-org
and default-company admin grants. Invitations to another company retain their
declared role; operator flags do not substitute for company/content authority.
When promotion is disabled, the org's configured member/builder default applies.
Grant audits target actual org/company IDs; member audits target the account.
Invite-driven company membership now also emits `tenant.member_add`.

The raw unit auth fixture now represents m82's seeded default company/org.
Scenarios intended to have no membership explicitly remove it; the two-company
context fixture clears the initial org cookie before arranging its own context.
The search-rate test now selects the second account's intended org through the
real switch API before probing the per-user limit. Production cookie checks were
kept strict. Native API fixtures start at actual m83 before signup.

Current final evidence under `output/org-authz/p1/`:

- `b05-control-registration-final.log`: **63 passed** across organization controls,
  registration, invitation acceptance/resend, principal rate and schema contracts.
- `b05-signup-invitation-audit-final.log`: **47 passed** in signup/invitation/auth
  and audit scenarios, including company-member audit scope.
- `b05-registration-postgres-final.log`: **5 passed, 22.24s**, actual owned m83
  PostgreSQL and JWT/CSRF. Concurrent signup creates exactly one operator/org admin/
  company admin, while failed invite-backed signup leaves no orphan account.
  Existing org switching/removal, policy, suspension and concurrent invite flows
  remain valid. Each lab database is removed on exit.
- Whole-backend Ruff/Pyright pass; changed type/format gates select **22** files;
  ownership ratchet remains **173, additions 0**.
- `b05-registration-full-backend-audit-final.log`: **4,867 passed, 3 existing
  warnings, 213.88s**, final production source through the official isolated
  runner; child exit 0 and cleanup `removed`. The preceding full run exposed the
  stale org selection in the search fixture; after its actual switch was added,
  both the repeat and the final post-audit source run passed.

No shared/main database was migrated and no deployment/UI acceptance is claimed.
The signup bootstrap and token lookup still need B-13's named scoped sessions;
membership tuple/outbox effects require C-04 and audit scope columns H-03. Before
B-06, freeze all 63 legacy table definitions used by P0 fixtures (not merely the
user table), then promote private ORM scope fields and query/writer context.
Also enroll newly created dev E2E seed accounts in the default scope before
private endpoints require it; that seed path bypasses public registration and
must keep its production skip guard. The full P0–P8 objective remains active.
