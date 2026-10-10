# Organization and authorization execution record

Status: P0 in progress — full implementation goal active
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
- [ ] A-13 — complete 22-table schema and staged migration contract review.

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

## A-13 work in progress (2026-10-10)

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
`live_port` because a separately started main-checkout uvicorn occupied 8101
after this lane finished; that foreign process was preserved. Coverage gates
require a clean Git state and rejected the still-uncommitted A-13 draft before
measurement, so coverage is not claimed yet. P0 is not closed and P1 has not begun.
