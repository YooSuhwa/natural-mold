# Organization and authorization execution record

Status: P0 complete; P1 ready — full implementation goal active
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

P1 may now begin with B-01. Live app metadata/Alembic remains m77; authorization
defaults remain legacy and RLS disabled. No main/shared database was migrated,
no deployment or PR was created, and full implementation/operational acceptance
remains unfinished. P0's CLI uses named query flags; C-07's complete operational
CLI must finish the plan's subject-aware diagnostic interface alongside rebuild
and reconciliation. The 14-day shadow observation, two-week stabilization and
real-participant U-05 study are future gates.
