# Organization and authorization execution record

Status: P0–P7 and P8 implementation committed (HEAD `cb16862b`); native PostgreSQL/OpenFGA/TLS/E2E lanes, I-01–I-05 operations and U-05 user study pending
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
- [x] B-06 — organization filters for private data.
- [ ] B-07 — organization switching and administration UI. 1차 implemented (switcher, org settings, company directory, members, groups, roles/capabilities; Vitest/lint:i18n pass) - E2E and invitation-accept page pending; org policy/catalog settings UI later.
- [x] B-08 — ownership constraints and agent archive lifecycle.
- [ ] B-09 — frontend state reset and transport inspection. implemented — E2E pending (OrgContextProvider: request gate + unmount placeholder → query/storage/module-cache purge → landing → new Jotai store; org-scoped abort signal on apiFetch/upload/SSE/LangGraph transport, apiFetchBinary + object-URL loader for chat images and state-file downloads; harness org-switch-reset.test.tsx). Follow-up: UserAvatar still loads its image by direct URL (user-scoped, not org-scoped).
- [x] B-10 — company context, suspension and deployment modes.
- [ ] B-11 — company administration API. candidate - verification pending (아래 B-11/B-12 candidate 절 참조).
- [ ] B-12 — SaaS platform company administration API. candidate - verification pending (아래 B-11/B-12 candidate 절 참조).
- [ ] B-13 — tenant/platform sessions and derived task context. implemented (behind DB_RLS_ENABLED, default off) - native PostgreSQL verification pending; interim platform scopes (heartbeat/shutdown, queue, eval worker, spend writer, health polls) and request-path identity scope + checkpointer gate handed to H-03/H-07/I-06.
- [ ] B-15 — cookie, refresh revocation and token claim contracts. implemented — native verification pending (last-company exit revokes refresh tokens in `exit_tenant_member`; `authz/session_revocation.revoke_sessions_for_deleted_tenant` hook for E08/I07 purge; only for members with no other company; refresh rotation share-locks the user row before the token row (user→token order shared with exit; native race test `tests/integration/test_refresh_company_exit_race.py`, registered in the company lane, not yet run); suspension keeps tokens; claim-set/claim-read/raw-PyJWT ratchet + active-org cookie attribute parity + logout clearing tests).
- [ ] B-16 — company data keys and ciphertext version 2. implemented (TENANT_DATA_KEYS_ENABLED, default off; keys in tenants.data_keys, format 0x02, 0x01 closure marker) - native PostgreSQL verification pending; key destruction caller is E08/I07.

## P2 checklist

- [ ] C-01 — m84 authz core tables, m85 marketplace ACL copy. implemented — native verification pending. Revisions `c01_authz_core` (down `b11_company_administration`; `resource_grants`, `authz_outbox`, `authz_revocations`, `authz_model_versions`, `authz_shadow_diffs`) and head `c01_marketplace_acl_grants` (10.5 mapping: restricted ACL row → one user grant, public/unlisted → org installer, system → per-org installer fanout, private → none (ACL `manage` rows included: a manager grant would imply viewer/installer); deviation decision (C-01 review): `manage` rows on public/unlisted items are copied as user manager grants because legacy `can_manage_item` honours them — D-05 10.5 mapping tests must include this case; other public/unlisted ACL rows not copied; system fanout rows use `grant_kind=migration`, so D-05 catalog on/off writes match on relation/principal; a system item with a `manage` row aborts with an actionable message (delete the ACL row or change visibility); restricted ACL users with no active membership in the item's company are still copied and counted in a migration warning for reconcile; deterministic ids, rerun identical; downgrade deletes only migration-kind marketplace grants, ACL kept until I-04). ORM `app/models/authz.py`. SQLite up/down + exact-set tests (`tests/test_migration_c01_*.py`); native roundtrip `tests/authz/test_authz_core_migration_integration.py` registered in the company lane, collected but not run (Docker blocked).
- [x] C-02 — `app/authz/tuples.py` (7.2 row → tuple rules; `tuples_for(row, now=)` model-guards every tuple, `expected_tuples(snapshot, now=)` merges same-key tuples and sorts), `tuple_types.py` (frozen `RelationTuple`/`Condition`, canonical JSON context, `TupleRuleError`), `tuple_rows.py` (plain row snapshots + `Snapshot`; C-04/C-07 loaders map ORM rows here), `tuple_filters.py` (cross-row admission), `tuple_model_guard.py` (relation/subject/condition assignability and condition parameters from `model.json`), `tuple_merge.py`. committed `9a9685cd`; review R1 changes applied. Decisions: one `resource_grants` row → its principal tuple (system/tenant-credential fanout needs both `allowed_org` and `consumer` rows); an organization-principal marketplace grant also projects `tenant:<grant tenant> tenant marketplace_item` so system items satisfy `member from tenant` (7.2 says system fanout `viewer`, C-01 writes `installer`; both projected as stored); same-key merge: unconditioned beats conditioned, latest `non_expired_grant` expiry wins, `allowed_run_source` sources are unioned, other mixes → `TupleRuleError` — C-04 outbox deletes must recompute the key from the remaining rows; archived agent keeps org/owner/resident tuples per 7.2.1 (overrides 7.2 "튜플 없음") and only `share`/`delegation` grants on or by it are dropped; archived org / suspended tenant keep tuples; user-principal roles/capabilities/group members/grants require an active membership in that org (tenant roles: active tenant membership) because `admin`→`member` would re-derive boundary for a suspended user; agent principals must be residents of the grant org (a delegation row records the agent's org); `llm_model` grants require a visible model; `expires_at` → `non_expired_grant` (grant_time=granted_at, Go duration), expired at `now` → none, combined with a condition or on anything but `agent#consumer` → `TupleRuleError`; MCP-server agent delegation without stored `allowed_run_source` → `TupleRuleError` (least privilege); company-public (`tenant_publication='approved'`) → `tenant#member installer`. Tests: `tests/authz/test_tuples_{rules,grants,expected,merge}.py` (91, each tuple also checked by an independent `model.json` validator in `tuple_testkit.py`; 19 mutations all caught); predicates 67/0 additions; dependency guard green. model.json↔model.fga equality is checked only in CI (`fga model transform` + diff); no local converter. Open: ORM `MarketplaceItem` lacks the m81 `tenant_publication` column; `GrantRow.tenant_id` must be loaded from `resource_grants.tenant_id`.
- [ ] C-04 — `app/authz/grants.py` (`GrantService.grant/revoke/list_grants/apply`), `grant_validation.py` (`GrantTarget(resource_type, resource_id)`, `GrantRequest`; target scope read from its table via `tuple_loaders.resource_scope` — unknown/other company/other org → 404, archived agent → 422; principal boundary: active member, org group (hard-deleted, existence = live), resident non-archived agent, the org itself; `delegation` ⇔ agent; `migration` not writable; relation/condition/expiry via `tuples_for` model guard → `grant_invalid` 422), `outbox.py` (`enqueue` one INSERT per row with stored `tuple_key`; `process_outbox` one batch for C-05 in `seq` order with `FOR UPDATE SKIP LOCKED`, any per-row exception → exponential backoff (cap 5 min) + key held, completion isolated in a savepoint, alert log at attempt 10/20/…; `apply_now` best effort 2 s, never raises except cancellation), `grant_projection.py` (`lock_object`: marketplace item row then live grants by id, after any org row lock; per-key before/after diff of merged projections: delete / `replace` write / no-op), `grant_completion.py` (`complete_sources`: grant sources → revoking→revoked + pair marker finalize when no other grant of the pair is revoking and no narrowing of the pair is in flight; `authz_revocations` sources → narrowing marker finalized when its rewrite landed), `tuple_loaders.py` (ORM → `GrantRow`/`MarketplaceItemRow`, `resource_scope`; for C-07), `FgaClient.write/delete` (IGNORE duplicates/missing; deletes drop the condition), `FakeTupleStore`, audit `grant.create|update|revoke` (update carries `before` expiry/condition), `delegation.create|revoke`, error `grant_invalid`. Revision `c04_outbox_sequence` (down `c01_marketplace_acl_grants`, single head): `authz_outbox.seq` (PG identity BY DEFAULT, ORM default compiles to `nextval(pg_get_serial_sequence)` on PG and `max+1` in the INSERT on SQLite), `tuple_key` (`user relation object`), backfill by `(created_at, id)`, `uq_authz_outbox_seq`, partial `ix_authz_outbox_key_pending(tuple_key, seq)`. committed `360dbf76`; review R1 (REQUEST_CHANGES, 8 items) applied. Decisions: caller calls `apply(change)` after commit; revoke = plain read → `record_revocation` (org row) → `lock_object` → re-read → `revoking` + ops; an in-place change of an active grant (any expiry/condition change) records a `scope=grant` marker first and queues its rewrite with the marker as outbox source; an active row of the same key owned by another org, a key that changed while waiting for the lock, or a unique violation on first insert → 409 `conflict` (no savepoint: the caller rolls back); recompute uses remaining active grants on the object plus the marketplace item row without `tuple_filters` admission. Policy (stage caps, org-wide capability, owner self-revocation, 8.2 transfer/delegate/on_resource_*) is E-01/E-02. Tests: `tests/authz/test_grants_{outbox,revoke,validation}.py` (26) + `tests/test_migration_c04_outbox_sequence.py` (3); c01 migration contract fixture also applies c04; head pins updated in `test_agent_lifecycle_migration.py`/`test_authz_core_migration_integration.py`. 16.4 coverage: commit/rollback atomicity, immediate apply success/failure, backoff retry + idempotent replay, INSERT order under clock inversion, revoke marker order with FGA failure and DB rollback, shared-key recompute incl. back-to-back revokes, multi-key revoke closes last, narrowing marker, partial batch failure, same-key order across/within batches, writer bug/completion failure isolation; 16 mutations all caught. Not covered locally: real lock contention (needs PostgreSQL) and the native 16.4 run (`expected_tuples()` == OpenFGA Read per 7.2.1 event) — Docker blocked. — native verification pending
  - C-04 follow-up, membership tuple sync (I-05 defect; implemented, uncommitted; plan 7.2/7.2.1/8.6): `app/authz/membership_projection.py` (subject slices of `expected_tuples()`: `UserSubject(tenant, user, orgs)`, `GroupSubject`, `OrgSubject`; same `admitted_rows`/`tuples_for`/merge as reconcile, direct user/group grants included so suspension drops them) and `membership_sync.py` (`MembershipSync.capture` before the change, `.queue` after it in the caller's transaction: per-key diff, deletes behind one org-wide marker per (org, user) — membership > role > capability > group_member; group-principal deletes name every group member, `organization:O#member` deletes block the org object (`*`); company admin/auditor revocations and company suspensions mark every organization of the company (a company exit marks the organizations the user belonged to), with company role/status writes now locking tenant → all organizations (UUID-byte order) first; a suspended member's direct-grant deletes finalize with that grant organization's marker; markers refresh the loaded org version columns; `apply_membership_outbox`/`membership_apply.apply_for_request` after commit). Wired: signup default enrollment, org/company invitation joins (incl. invited signup), admin add/suspend/reactivate/remove, org role/capability replace (user/group/org principals), group member add/remove, group create/delete, company member add/suspend/roles/exit, company-admin org creation + initial admin, platform company creation, JIT/SSO joins (captured lazily, only when a join/mapping changes rows), account erasure, E2E seed. Every mode queues and records markers, as `GrantService` revocations do in legacy too; rows are inert in legacy. Org archive / company suspension keep tuples per 7.2.1 (no deletes). Tests: `tests/authz/test_membership_tuple_sync_{api,company,joins,ratchet}.py` (store drained via `process_outbox` must equal `expected_tuples()`; fresh signup sees catalog models in enforce after the drain with OpenFGA down during signup; writer ratchet). Test fixture follow-ups: native cleanups delete company markers (`tests/authz/native_marker_cleanup.py`), legacy catalog PUT test counts the delta, company-admin role race expects epoch +3 (revocation bump). Native lane `company-administration` 119/119 passed. Review R1 (marker coverage of company tuples, per-org grant markers, lazy SSO capture) applied; enforce check denied by the markers before any drain is tested. Group deletion revokes the group's `resource_grants` through `GrantService.revoke` in the same transaction (audit `grant.revoke`, reason `group_deleted`, grant markers + outbox), before the membership capture.
- [x] C-06 — `app/authz/shadow.py` (`ShadowRecorder.submit` is the `AuthzService` shadow hook: synchronous O(1), token bucket at `AUTHZ_SHADOW_MAX_RPS` (0 admits nothing), bounded `asyncio.Queue` (1000) — over-rate/full/after-stop samples are counted (`rate_limited`/`dropped`), never awaited; one background worker drains batches of 100 through `shadow_decide`; `overlay_vetoed` samples are counted and excluded (C-08); OpenFGA failures → `errors`, DB failures → `write_errors`, never raised; `authz_shadow_mismatch` log line + `mismatch_by_type[(org, type)]` (14.3); `stop(drain_seconds)` refuses new samples, drains, cancels and counts leftovers as dropped; `bind_shadow_request(route, request_id)` context var stamps route/request id (default route `-`, wiring is a later route task); `start_shadow_recorder(settings, decide)`/`stop_shadow_recorder()` for lifespan, no-op unless `AUTHZ_MODE=shadow`, not yet registered in `main.py` because no app-level `AuthzService` exists), `shadow_store.py` (`diff_key`: allow/deny disagreement is keyed on the requested permission; 404-vs-403-only disagreement on the `min_visible` relation with visible bits, since the table stores booleans; `upsert_diffs` ON CONFLICT on the `uq_authz_shadow_diffs_key` columns adds `count`, latest request id wins; platform session reason `authz.shadow_diffs` pinned in the B-13 ratchet). Review fixes: `counters()` snapshot accessor + `authz_shadow_summary` log every 60 s and at stop (`shadow_metrics.py`); in-flight batch counted as dropped when stop cancels after the drain timeout; worker done-callback logs and closes the recorder (no restart); batch decisions run concurrently (semaphore 8) with a 2 s per-decision timeout (→ `errors`); key/request id truncated to column widths and upsert rows sorted by key (`diff_rows`). committed `bea97f2f`. Tests: `tests/authz/test_shadow_recorder.py` + `test_shadow_recorder_hardening.py` (23; 15 mutations all caught); predicates 67/0 additions; dependency guard green.
- [ ] C-05 — `app/authz/jobs.py` (leader jobs registered by `app.scheduler.register_authz_jobs` from `activate_scheduler`, dispatched through the fenced `run_leader_scheduler_job` via the `app.scheduler.authz_job_run` wrapper (persisted target stays `app.scheduler_runtime:run_leader_scheduler_job` + `LeaderSchedulerJobId`): `authz_outbox_drain` every `AUTHZ_OUTBOX_POLL_SECONDS` (whole seconds, min 1; one `process_outbox` batch of 100 per tick), `authz_reconcile` on `AUTHZ_RECONCILE_CRON` (`run_reconcile_job`, queue only; invalid cron → logged, not scheduled), `authz_expiry_cleanup` every 60 s (`expire_grants`: active grants with `expires_at <= now`, 100 per tick, one transaction each, then immediate apply; `prune`: processed outbox rows and finalized markers older than 7 days), `authz_backlog_alert` every 60 s (`check_backlog`: `authz_outbox_lag_seconds` log with pending/failing(attempts ≥ 10)/lag/oldest-marker age, `authz_revocation_stuck` error when a marker is pending > 10 min). Nothing is scheduled and every run is a no-op (no DB session, no OpenFGA client) unless `AUTHZ_MODE` is shadow/enforce with pinned `OPENFGA_STORE_ID`/`OPENFGA_MODEL_ID` (unpinned → warning at registration); registration in legacy removes persisted authz jobs. `GrantService.expire(db, grant_id)` closes an expired grant through the shared revocation path (`_close`: marker → `lock_object` → re-read active and still expired → `revoking` + deletes; `revoked_by` NULL, audit `grant.expire` actor_type `system`); `None` = closed or extended meanwhile (caller rolls back). Platform session reasons `authz.outbox_drain`, `authz.expiry_cleanup`, `authz.backlog_alert` pinned in the B-13 ratchet. Leader handoff (완료 조건): old leader cancelled after its OpenFGA write but before commit → rows stay unprocessed, new leader re-applies idempotently once, a further run processes nothing; a second leader's expiry finds nothing and a stale expire returns `None` (one marker, one audit, one delete row). committed `c8739d49`. Review fixes (APPROVE follow-ups): `reconcile_store(mode=repair)` takes `pg_try_advisory_xact_lock` in a guard session held for the run (no-op off PostgreSQL), a held lock → `authz_reconcile_skipped` log + `ReconcileReport.skipped` (CLI exit 1); expiry pages by `(expires_at, id)` so a failing grant is never retried in the same run and later grants are still reached; prune deletes at most 1000 rows per table per tick; the drain opens no OpenFGA client when no row is due; `check_backlog` logs `authz_backlog` with gauges `authz_outbox_pending`/`authz_outbox_failed`/`authz_outbox_lag_seconds`/`authz_revocation_oldest_seconds`; reconcile cron in UTC; TODO hook for `delegation_validator` after expiry (E-02, 10.6). Tests: `tests/authz/test_authz_jobs.py` + `test_authz_jobs_hardening.py` (20 cases; 17 mutations all caught); predicates 67/0 additions; dependency guard green. Deviations: shadow diffs are not pruned (table is aggregated per key and dropped by m91); shadow recorder still not started in lifespan (no app-level `AuthzService`, C-06 note); store/model id resolution from `authz_model_versions` not implemented (jobs require pinned ids like the CLI); one SDK client opened per job run. Not covered locally: real `SKIP LOCKED` contention between two concurrent PostgreSQL workers (needs the native lane). — native verification pending
- [ ] C-07 — `app/authz/tuple_snapshot.py` (`load_snapshot(db, tenant_id=<uuid>|PLATFORM|None)`: every 7.2 source table streamed column-wise with `yield_per`; `PLATFORM` = `tenant_id IS NULL` rows; model catalog always; grants `state='active'` only; loads `tenant_publication`), `reconcile_diff.py` (pure per-key diff missing/extra/changed-condition; `StoredIndex` ref index so each stored tuple is claimed by one partition — keys the partition expects or whose object / user object part is one of its refs; `hold` (uses `outbox.tuple_key`), `recheck`), `reconcile_holds.py` (partitions: one company/org, or every company then the platform partition claiming the remainder, so tuples of deleted rows are found store-wide only; time-based hold window `started = run start - 60 s` taken before any read: keys with an outbox row unprocessed or created/processed since `started` (the run's own job rows excluded), markers pending or finalized since `started` block writes), `reconcile.py` (`reconcile_store(sessions, store, mode=dry_run|fill|repair, tenant_id=, org_id=, drain=)`: store read once outside any transaction, then per partition short transactions — snapshot; hold check + fresh re-read (`recheck`: drop writes no longer expected and deletes now expected) + enqueue; every repair (deletes too, every mode incl. bootstrap `fill`) goes through `authz_outbox` (`source_table='authz_reconcile'`) so per-key `seq` ordering applies; `drain=True` applies the run's own rows; `ReconcileReport` counts/queued/failed + 20-key samples, `authz_reconcile_diff` log; sizing note ~0.5 KB per stored tuple; `reconcile_sessions()` platform reason `authz.reconcile` pinned in the B-13 ratchet; `run_reconcile_job` = C-05 daily body, queue only), `FgaClient.read_all` (paged Read) + `FakeTupleStore.read_all`, `cli.py` (`bootstrap` = model deploy, then `fill` against the store/model id just deployed, prints `{model, tuples}`; `rebuild`/`reconcile` `[--tenant|--org] [--apply|--dry-run] [--json]`, dry run by default, `--apply` repairs through the outbox and drains; exit 0 = no remaining difference, 1 = dry run found a difference or queued repairs failed to drain, 2 = usage; held tuples never change the exit code). committed `f261805a`; review R1 (REQUEST_CHANGES: seq-gap over-grant race, direct writes bypassing outbox ordering, memory, long transactions) applied. Tests: `tests/authz/test_reconcile.py` + `test_reconcile_races.py` + `test_reconcile_cli.py` (fixture `reconcile_testkit.seed_fixture` = C-04 grant world + tenant members/role, org role, group member, capability, credential, MCP server/tool, three grants projected through the real outbox; bootstrap then reconcile = diff 0; races: revoke committed+applied+finalized after the run's snapshot is not rewritten, a grant applied during the run is not deleted — each also with the activity aged out of the hold window so only the re-read protects it; mutations of every hold clause, the re-read and delete queuing all caught). Open: subject-aware `check`/`explain` and `outbox [--retry-failed]` CLI are not part of this task; no native OpenFGA+PG integration source. — native verification pending

## P3 checklist

- [x] P3-0 composition root — `app/authz/runtime.py`: `authz_runtime(app, config=None, *, opener=open_client)` (entered by `open_authz_runtime` in `main._lifespan_started`, exited by the `close_authz_runtime` step of `shutdown_runtime_resources` after scheduler stop and run-registry drain, before checkpointer/database) publishes the process `AuthzService` on `app.state.authz`; `get_authz` (async FastAPI dependency, override via `app.dependency_overrides[get_authz]`) returns it and stamps `bind_shadow_request(route.path_format, request.state.request_id)` (existing `request_context_middleware` id). `legacy` (default): service over `UnconsultedClient`, no client opened, no overlay/recorder, `AUTHZ_ENFORCE_TYPES` not parsed — zero network and zero extra queries per request; without a lifespan (ASGITransport tests) `get_authz` falls back to the legacy service only when `AUTHZ_MODE=legacy`, otherwise raises. `shadow`/`enforce`: enforce types parsed before any client opens, warning when store/model ids are unpinned, one long-lived client from `open_client`, `DbRevocationOverlay(platform_sessions("authz.revocation_overlay"))` (reason pinned in the B-13 ratchet, 8.6), recorder started only in shadow with `submit` as the hook; cycle broken by a hook-less `AuthzDecisions` (same client/overlay) as the recorder's `decide`. Shutdown: `stop_shadow_recorder()` then client close, also on a body/startup error. `runtime_authz()` gives workers/triggers/queue jobs the same service (same legacy-fallback rule). Tests `tests/authz/test_authz_runtime.py` (12) + `tests/test_main_shutdown.py` order. committed `08269288`.
- [x] P3 foundation (shared by D-01..D-08; rebase onto it) — **policies** (`app/authz/policies.py`, plan 8.3): tagged union `RoutePolicy = KindPolicy | ResourcePolicy | CapabilityPolicy | PrivatePolicy | KeyPolicy | ShareTokenPolicy | CandidatesPolicy` of frozen slotted dataclasses, each with `kind: Literal[...]` (`KindPolicy.kind` ∈ LEGACY/E2E_ONLY/PUBLIC/SELF/CATALOG/SUPER/GOVERNANCE; the others "RES"/"CAP"/"PRIVATE"/"KEY"/"SHARE_TOKEN"/"CANDIDATES", not settable). Constants `LEGACY, E2E_ONLY, PUBLIC, SELF, CATALOG, SUPER, GOVERNANCE` are `KindPolicy` values; factories `RES(type_, permission, *, param, min_visible)` (min_visible required), `CAP(cap)`, `PRIVATE(model: str, *, param=None)`, `KEY(permission="can_deploy")`, `SHARE_TOKEN()`, `CANDIDATES(type_, permission)`. `authz_policy(policy: RoutePolicy)` raises `TypeError` on a bare string; `policy_of(endpoint)` / `policy_kind(endpoint)` are what ratchets compare (`tests/authz/test_route_policies.py`, `company_route_inventory.py` now match on `kind`; existing `authz_policy(SELF)` call sites unchanged). **candidates** (`app/authz/candidates.py`, 8.10): `granted_resource_ids(resource_type, ctx)`, `candidate_predicate(model, resource_type, ctx, *, platform=False)`, `async candidate_ids(db, model, resource_type, ctx, *, platform=False, where=(), order_by=())` = owned in current org+company ∪ current-org non-system rows with an active grant to me / my groups (`GroupMember.org_id == ctx.org_id`) / my org ∪ same-company company rows (org NULL) granted to my org (`org_granted_resource_ids`, allowed_org/consumer fanout) ∪ (platform=True) system rows with org/tenant NULL; owner column `owner_user_id` when present. Another org's granted row is never a candidate (D-04 integration fix; `skill_access` no longer narrows locally). **lifecycle** (`app/authz/resource_lifecycle.py`, 7.2.1): `on_resource_created(db, row, *, mode=None, now=None)`, `on_owner_changed(db, row, *, previous_owner_id, mode=None, now=None)` (after assigning the new owner; `ownership` marker), `on_resource_deleted(db, row, *, mode=None, now=None)` (before/with `db.delete`; deletes own tuples, MCP server's tool tuples, active grants on the object and an agent's delegations, marks those grants `revoked`; `resource` marker `*`), all returning `LifecycleChange(outbox_ids)` and queuing via `tuples_for` + `outbox.enqueue` in the caller's transaction; `mode=None` reads `settings.authz_mode` — pass `mode=authz.mode` from routes. Legacy: no rows, no flush, no query. After commit call `await apply_lifecycle(change, authz, *, writer=None, sessions=None)` before responding (8.6 item 2 apply-now = the read-your-writes path; plan has no pending-tuple owner bypass, so none is provided); writer defaults to the new `AuthzDecisions.tuple_writer` (None in legacy). Types: agent, skill, tool, mcp_server, credential, marketplace_item, mcp_tool (7.2 has no conversation tuples). Not covered: agent archive (7.2.1 "에이전트 보관" = share/delegation revocation through GrantService), system-row org fanout (catalog/bootstrap). **OpenAPI snapshot regen** after a D merge that removes `legacy_routes.txt` entries (keep `app/authz/legacy_routes.txt` and `tests/authz/legacy_routes.txt` identical): `cd backend && PYTHONPATH=. .venv/bin/python -c 'import json; from tests.authz.new_route_contract import new_route_contract; open("tests/authz/openapi_new_routes.json","w").write(json.dumps(new_route_contract(), indent=2, sort_keys=True) + "\n")'` then review the diff (only the converted routes/schemas may appear) and run `tests/authz/test_new_route_contract.py`. Tests `tests/authz/test_policy_markers.py` (4), `test_candidates.py` (2), `test_resource_lifecycle.py` (11). committed `730016ac`.
- [x] D-01 conversation family (9.3) — all 89 routes of the 19 conversation-family routers declare a policy (`PRIVATE(model, param=...)` on `conversations` / `conversation_artifacts` / `message_feedbacks` / `message_attachments` / `user_memory_settings` / `memory_records` / `memory_proposals`; `SHARE_TOKEN()` for the 6 public share reads; `RES("agent", "can_edit", param="agent_id", min_visible="can_run")` for `GET/PATCH /api/agents/{agent_id}/memory-settings`), none LEGACY. Both `legacy_routes.txt` copies pruned by those 89 identities (275→186, kept identical; ratchet cap lowered to 186) and `tests/authz/openapi_new_routes.json` regenerated (additions only: +73 paths, +80 schemas, no existing entry changed). New `app/authz/conversation_agent_checks.py`: one `agent#can_run` decision per run — before any write for conversation create/draft/start and side-chat create (start then skips it in `prepare_executable_conversation(agent_checked=True)`), otherwise in `prepare_executable_conversation` (send/resume/edit/regenerate/protocol run.start+input.respond; no extra check in `handle_thread_command`), `run-inputs/resume`, and at queue dispatch in `claim_next_input` (denied/undecidable input stays pending). Run-starting routes declare `bind_request_authz`, so service-level checks use the route's `get_authz` service (overrides, shadow request stamp); background work falls back to `runtime_authz()`. `agent#can_view_config` on the 2 debug trace routes; `agent#can_edit` pre-check on agent memory settings (legacy verdict = owner/org scope, looked up only outside legacy mode). Legacy mode returns before any query or sample (byte-identical); shadow records the legacy answer; enforce answers 404 `AGENT_NOT_FOUND` / 403 `permission_required` / 503 `authz_unavailable`. Predicates: queue/run owner predicates relabelled into `owned_input_predicate`/`owned_run_predicate` (identical SQL, still `user_id ==` inside `app/authz` — a move, not a semantic change); predicate baseline −5 rows (67→61). Deviations (TODO comments in code): memory-settings `RES` checks `can_edit` but `memory_service` stays owner-scoped until F-09; feedback `PRIVATE` keeps `MessageFeedback.user_id` scope, the 9.3 conversation-ownership + message-membership fix is D-10; shares `allow_public_share_links` enforcement/cache keying is a 9.3 follow-up; `mcp_apps` `can_invoke` is D-08. Tests `tests/authz/test_conversation_route_authz.py` + `test_conversation_queue_authz.py` (18). committed `242fa5de`.
- [x] D-03 skill-family routes (9.4) — 47 routes in 9 routers (`skills`, `skill_files`, `skill_revisions`, `skill_uploads`, `skill_credentials`, `skill_evaluations`, `skill_evaluation_prepare`, `skill_builder`, `skill_builder_trigger`) declare policies and leave both `legacy_routes.txt` copies (186→139, ratchet cap 139; OpenAPI snapshot regenerated, additions only: +33 paths, +55 schemas): `RES(skill, can_view|can_edit|can_delete|can_use, min_visible=can_use)` ×36, `CANDIDATES(skill, can_use)` (list), `CAP(cap_create_skill)` (create, upload), (the 3 per-user credential-binding routes are `RES(skill, can_use)` with SELF row semantics kept in the service; `credential#can_use` on the bound credential is a TODO for D-04/F-05), `PRIVATE(skill_builder_sessions)` ×8. `app/authz/skill_access.py`: `authorize_skill` (legacy = unchanged private `get_skill` + `SKILL_NOT_FOUND`; shadow adds the org/platform candidate row only for the sample's `resource_org_id`; enforce loads org/platform candidates and answers via `AuthzService.require`, 404/403/503 per 8.4), `visible_skills` (legacy list order preserved, candidates confirmed with `filter_visible`), `require_create_skill`, `authorize_builder_target` (new skill → cap, improve → `can_edit`; runs on confirm before the idempotent completed-skill return; skipped in legacy so the builder's own source lookup keeps its timing). Uses the P3 foundation: policy factories from `policies.py`; candidates are `candidates.candidate_predicate(Skill, "skill", ctx, platform=True)` (owned ∪ actively granted ∪ platform) with the legacy list order kept; another org's row stays 404 even when granted, platform rows use the caller's org (OpenFGA `allowed_org` boundary). Lifecycle: `skill_created` (create, upload; after flush, `apply_lifecycle` after the final commit before responding) and `skill_deleted` (before `delete_skill`, applied after commit) wrap `resource_lifecycle` with `mode=authz.mode` (legacy no-op). Not hooked yet: skill builder confirm (new skill) and marketplace install — they also create skills. Revision reads and `prune_revisions_for_skill` are skill-scoped (owner guards dropped; 2 predicates removed, 67→65). Tests `tests/authz/test_skill_routes_authz.py` (23, incl. enforce create → immediate GET via applied outbox tuples and delete removing them, queued-not-written before apply, incl. other-org fully granted → 404/absent and platform skill only via the allowed_org answer). Deferred to F-13 (skill builder/evaluation path decisions): owner-scoped evaluation sets/runs/`version_stats` and builder session services; feedback and credential bindings stay per-user rows; service-level private writes (update/content/files/rollback/delete/eval) still require ownership, so enforce-mode non-owner editors pass the route check but are refused by those services until ownership-aware writes land. committed `773fce1b`.
- [x] D-04 tools, MCP, credentials, models, health — all 53 routes of 9.5 (tools 8, mcp 14, credentials 18, models 7, health 4, system_llm_settings 2) plus `GET /api/health` (PUBLIC) declare reviewed policies (CATALOG 6, CANDIDATES 8, CAP 5, RES 19, SELF 4, SUPER 10, PUBLIC 2); 54 identities left both `legacy_routes.txt` copies (139 → 85 on main, ratchet cap 85); OpenAPI snapshot regenerated with the documented command (additions only: +39 paths, +45 schemas). `app/authz/resource_gate.py`: `gate_row` runs the route's existing owner/platform loader as the legacy verdict (legacy re-raises the same 404 object, zero extra queries; shadow reads the row by id only for the sample's org; enforce = OpenFGA, foreign/hidden → existing 404 code, visible-without-permission → 403 `permission_required`, OpenFGA down → 503), platform rows (system tools, models) decided in the caller's org, system credentials never reachable on user routes; `gate_list` (legacy rows, shadow samples the grant-candidate page, enforce = `grant_candidates` + BatchCheck), `gate_cap` (legacy allow), `gate_unchecked` (routes with no legacy check — model health history), `org_context_when_consulted` (model/health-model routes stay org-free in legacy). List candidates use `candidates.candidate_predicate` (tools `platform=True`; MCP servers and credentials without platform rows). Lifecycle: `on_resource_created`/`on_resource_deleted` on tool, MCP server (create, from-registry, import, delete; delete also drops its tools' tuples) and credential create/delete, MCP discover queues each upserted `mcp_tool` (idempotent writes), `apply_lifecycle` after commit before responding (legacy: nothing queued); system credentials (SUPER routes) are not hooked. `AuthzDecisions.mode` is annotated `AuthzMode` (D-03 cast removed). `credential#can_use` on attach via `credentials.validation.require_credential_use` (tools create/update, MCP create/registry/update/probe, model test-preview, health check). `Tool.visible_to` removed (no callers since B-06); stale predicate-baseline rows for the five D-04 files dropped (66 predicates, 0 additions). MCP header/env values are masked (`<redacted>`) for non-owners without enforced `can_edit` on list/detail/export (`services/mcp_server_view.py`); export keys granted rows `name (owner)`; system MCP servers stay unreachable until E-03; `org_context_when_consulted` tolerates a caller without org unless `llm_model` is enforced; health check gates the explicit model credential and requires credential org = target org when enforced; router-gated rows are not re-checked in services (one sample/check). Tests `tests/authz/test_d04_*.py` (26, incl. legacy zero-added-SQL and enforce create→GET→delete for tool/MCP/credential), `test_candidates.py` +1 (other org's granted row excluded). Deferred: personal-account credential sharing restriction (10.4, E-05); `import_servers` authz (overwrite of granted same-name rows, credential validation stays owner-scoped `private_scoped`); service-internal owner reloads in credentials service/tool runner/MCP auth (F-05/F-06); model-test credential resolution and health legacy gaps (D-10). committed `dc418d06`.
- [x] D-02 agents (9.1) — `app/routers/agents.py` 10 routes off LEGACY (both `legacy_routes.txt` copies 85→75, cap 75): list/summary `CANDIDATES("agent", "can_run")`, create `CAP("cap_create_agent")` (legacy answer allow), detail/favorite/image GET `RES(agent, can_run, min_visible=can_run)`, PUT and image POST `RES(agent, can_edit, min_visible=can_run)` (plan 9.1 lists image POST as `RES(agent, can_edit)` without min_visible; implemented with can_run so a runner gets 403, not 404), `/api/middlewares` `CATALOG`; DELETE keeps its GOVERNANCE lifecycle gate (stale legacy line removed). Decisions in `app/authz/agent_access.py` (`require_agent`: the owner-scoped row is the legacy answer, outside legacy mode the in-org row is also loaded for shadow samples/enforce; `candidate_agent_ids` = `candidates.candidate_ids(..., where=org, standard profile, not archived, order_by=created_at desc)`; `agents_permissions` = one BatchCheck per page in enforce, legacy owner answer shadow-sampled otherwise; `visible_agent_ids` via `filter_visible`). Responses in `services/agent_access_views.py`: legacy returns the unchanged bodies and queries; shadow/enforce add `my_permissions`; enforce serves `AgentRunnerView` (`view="runner"`, name, description, image, opener questions, owner, favorite, unread, `my_level`, `my_permissions`) without `can_view_config`. Response unions are `Field(union_mode="left_to_right")` ordered AgentAccessResponse, AgentRunnerView, AgentResponse (summary: access, plain) — OpenAPI schema change only. Lifecycle: create queues `on_resource_created` and `apply_lifecycle` before responding (enforce create→GET reads its own write); purge queues `on_resource_deleted` from `purge_agent` via `services/agent_purge_authz.py` in the transaction that deletes the Agent row and applies it after commit (B-08 hash-preserved purge modules untouched; the resource marker denies at once, the C-05 drain retries); archive leaves tuples (foundation note: 7.2.1 보관 = GrantService revocation); no agent owner-transfer path exists yet. Favorites: revision `d02_agent_user_preferences` (down `c04_outbox_sequence`) creates `agent_user_preferences` without backfill — owners keep `agents.is_favorite` as the source of truth until P8 (whose migration must copy the column), non-owners toggle and read only their own row; PUT/PATCH/GET agree. Agent-service ownership rows (10) removed from the predicate baseline. Deferred: runner-view connection-status summary and `readiness_summary` (12.3); shared presentations load one per row (owner-scoped links differ per owner); non-owner PUT / image POST pass the gate but services still bind/validate in the caller's private scope (F-02); shared summary tool counts use the viewer's link scope. Tests `tests/authz/test_agent_route_authz.py` (10) + `test_agent_favorites_and_lists.py` (7) + `test_agent_purge_authz.py` (3). committed `7c11e77b`.
- [x] D-07 audit, usage, builder, blueprints, templates, auth (9.1, 9.6) — 24 routes in 6 routers off LEGACY (both `legacy_routes.txt` copies 75→51, ratchet cap 51; OpenAPI snapshot regenerated, additions only: +22 paths, +22 schemas): auth register/login/refresh `PUBLIC`, logout/me/profile/avatar ×3 `SELF`; `GET /api/audit-events` `SELF` (scope=mine default, scope=all stays super-user-gated in `audit_service`; scope=org is H-03); usage summary/daily `SELF`; `GET /api/agents/{agent_id}/usage` `RES(agent, can_view_config, min_visible=can_run)` via `app/authz/agent_usage_access.py` (legacy verdict ALLOW — the legacy route has no session check, closing it is D-10; legacy returns before reading the session or org — zero added SQL, statement-count test; shadow answers legacy and samples when the caller resolves an org, enforce: 401 without session, 404 `AGENT_NOT_FOUND` / 403 `permission_required` / 503); templates `CATALOG` ×2; builder `CAP(cap_create_agent)` (start) and `PRIVATE(builder_sessions, param=session_id)` ×5 (confirm also asks `cap_create_agent` after the private session lookup, legacy allow); blueprints `PRIVATE(agent_blueprints)` ×3 (create-agent also asks `cap_create_agent`). Lifecycle: builder confirm (after `confirm_build`'s own commit, non-legacy only) and blueprint create-agent (before the route commit) queue `on_resource_created` for the new agent and `apply_lifecycle` before responding. Deviations: usage "내 사용분은 누구나" (runner sees own usage) not implemented — `token_usages` has no user column, enforce requires `can_view_config`; connected-resource `can_use` on builder confirm / blueprint credential bindings stays owner-scoped (F-02); stale predicate-baseline rows (expressions absent from the scan) for `routers/agent_blueprints.py`, `services/agent_blueprint_service.py`, `services/builder_service.py` dropped (113→104 rows, 58 predicates, 0 additions). Tests `tests/authz/test_d07_route_authz.py` (10). committed `7484ef03`.
- [x] D-08 Assistant tools, chat resource context, MCP Apps bindings (11.7, 9.3) — `POST /api/agents/{agent_id}/assistant/message` and `/resume` declare `RES("agent", "can_edit", param="agent_id", min_visible="can_run")` via `agent_access_views.authorized_agent` (legacy = the same owner-scoped `get_agent` + `AGENT_NOT_FOUND`); both `legacy_routes.txt` copies 51→49 (cap 49), OpenAPI snapshot regenerated (additions only: +2 paths). New `app/authz/assistant_scope.py`: every Assistant tool call re-checks the actor through `runtime_authz()` — reads `can_view_config` (`helpers.get_agent_with_eager_load`, default), writes `can_edit` (`write_tools/context.get_agent_with_session`, `composition_lifecycle.get_executable_agent`, `assistant_agent.build_assistant_agent` gate); legacy returns the actor's scope with no query (the existing owner-scoped presentation stays the decision), shadow uses the owner-scoped row as the legacy verdict and samples, enforce asks OpenFGA and lets an allowed non-owner load/mutate the agent in its owner's scope; denial = the tools' existing "에이전트를 찾을 수 없습니다." message, OpenFGA outage raises 503. Links added by tools (10.6) keep the actor-scoped lookup as candidates and are confirmed with `keep_linkable` (`tool#can_use`, `skill#can_use`, `mcp_tool#can_invoke`, sub-agent `agent#can_run`; also the `list_available_subagents`/`list_available_skills` read lists) — narrowing only until delegation grants (F-02/m86). The identity-mode guard moved to `actor_active_trigger_count`: outside legacy it counts every active trigger on the agent (as `agent_update_service._count_active_triggers`), so a grantee cannot switch to `per_user` while the owner holds an active schedule; legacy keeps the owner-only SQL unchanged. New `app/authz/resource_context_scope.py`: a referenced skill needs `skill#can_view` (legacy = existing owned+linked row; enforce loads the in-org linked candidate via `candidate_predicate(platform=True)`; denial = `ResourceContextNotFoundError`); files/artifacts/conversations stay PRIVATE. MCP Apps: `linked_tool` moved into `app/authz/mcp_app_scope.py` (re-exported from `mcp/apps_binding_store.py`) and returns a tool only when `actor_can_invoke` (`mcp_tool#can_invoke`, legacy allow) holds — covers command authorization, `tools/call` and runtime binding minting (the D-01 deferral). Predicate baseline: 21 stale rows removed (25 predicates, 0 additions); the predicates themselves had already moved into `app/authz` in earlier B-phase work. This includes stale rows of files outside the D-08 plan table: `services/chat/runtime_context.py` (F-05) and `services/chat_resource_context_integration.py`. Narrowing only: Assistant link checks and MCP Apps `can_invoke` can only remove candidates from the existing actor-scoped lookup, so a grantee cannot yet link a granted org-shared tool/skill/MCP tool/sub-agent or use an MCP App on a shared agent — that needs delegation grants (10.6, F-02/m86). Deferred: `services/chat/runtime_context.py` agent loading for shared agents (D-01/F-05 run path); 403 vs 404 is not distinguished in tool messages. Tests `tests/authz/test_d08_assistant_authz.py` (10) + `test_d08_context_and_mcp_apps_authz.py` (5; MCP App deny cases for command authorization, runtime binding minting and `tools/call`). committed `bde3d4dc`.
- [x] D-06 Agent API deployments/keys, runtime API, triggers (9.1, 9.2, 11.6) — 28 routes off LEGACY (both `legacy_routes.txt` copies 49→21 on main, ratchet cap 21; OpenAPI snapshot regenerated, additions only: +23 paths, +18 schemas): `/v1/health` `PUBLIC`, the other 10 `/v1/*` routes `KEY("can_deploy")`; `GET /api/agent-api/deployment-candidates` `CANDIDATES("agent", "can_deploy")`; deployments `PRIVATE("agent_deployments"[, param="deployment_id"])` ×3, keys `PRIVATE("agent_api_keys"[, param="api_key_id"])` ×3; the 10 trigger routes `PRIVATE("agent_triggers"[, param="trigger_id"])`. New `app/authz/agent_api_access.py`: `require_can_deploy(context, agent_id, *, not_found)` (legacy: return before any sample/query; shadow: legacy ALLOW sampled; enforce: management routes split no `can_run` → the caller's existing 404 / `can_run` without `can_deploy` → 403 `permission_required` (`request_access=false`), runtime `/v1` call sites pass `opaque=True` so both denials are the caller's existing 404; OpenFGA down → 503; one decision and one shadow sample per (principal, agent) per request via a per-request memo) and `deployable_agent_ids` (`filter_visible`); `bind_agent_api_authz` is a router-level dependency on both Agent API routers so the deep service checks use the route's `get_authz` service; detached work falls back to `runtime_authz()`. KEY check runs on every runtime request after the private key/deployment gate: `resolve_deployment_for_agent_id`, `authorize_deployment` (thread create, run acceptance, run config — memo hits within a request). The I17 prepared path makes a separate `fresh=True` decision in `materialize_api_run` inside `fresh_decision_scope()`, before `materialize_prepared_execution` takes the gateway locks; a denial durably fails the still-queued row (`AGENT_DEPLOYMENT_NOT_FOUND`) and the locked resolver's `authorize_deployment` reuses that decision (no OpenFGA call under locks) and `get_thread_for_public_id` (`AGENT_API_THREAD_NOT_FOUND`); `/v1/agents` drops deployments whose agent the key owner can no longer deploy. A key whose owner lost `can_deploy` is refused (opaque 404) while the key stays valid; invalid/revoked/expired keys keep the opaque 401 (key state is checked first). Deployment create (`AGENT_NOT_FOUND`) / update (`AGENT_DEPLOYMENT_NOT_FOUND`) and key create (each linked deployment's agent, 422 `AGENT_API_DEPLOYMENT_NOT_FOUND`) check `can_deploy`; candidates are filtered by it. Triggers: create/update (PATCH, PUT)/run-now re-check `agent#can_run` via `conversation_agent_checks.require_agent_run` (route service), and at fire time `trigger_scope.require_trigger_actor` asks `runtime_authz()` for the owner's `can_run` (legacy no-op); a denial is an `AppError` the claim logs and skips before any durable run (I18 prepared entry follows the claim, unchanged). Predicate baseline: stale rows for `agent_api/service.py` (3) and `routers/agent_runtime_api.py` (1) dropped (57 predicates on main, 0 additions). Deferred: deployments/keys on agents the caller does not own (execution and `private_deployment_predicate` stay owner-scoped until F-02/F-08), key revoke by agent `can_govern`, trigger `permission_revoked` failed runs + owner notification (F-08), `AgentTriggerRun.user_id`/`AgentApiThread.user_id` owner filters kept (query-identical legacy). Tests `tests/authz/test_d06_agent_api_authz.py` (7) + `test_d06_trigger_authz.py` (4) + `test_d06_runtime_key_routes.py` (13: enforce deny over all 10 KEY routes incl. streams and Dify/OpenAI compat with zero new AgentApiRun/AgentApiThread/Conversation rows; prepared path fails closed before the locks and decides once). committed `1b1a7624`.
- [x] D-05 marketplace (9.6, 10.5) — all 21 `routers/marketplace.py` routes off LEGACY: `CANDIDATES(marketplace_item, can_view)` for the 2 lists, `RES(marketplace_item, can_view|can_install|can_manage, min_visible=can_view)` for detail/versions/version detail (`param=version_id`)/install/version publish/PATCH/ACL/disable/enable, `PRIVATE(marketplace_installations)` for installation update/delete, `CAP(cap_publish_marketplace)` (+ source `can_share` via `gate_unchecked`) for the 3 first-publish routes, `SUPER` for the 2 admin routes. Routes inject an `ItemAuthorizer`/`ListGate` (protocols in `app/marketplace/access.py`, implementations `app/authz/marketplace_access.py`) into the marketplace services; legacy passes `None`, so the existing loaders and `can_*_item` predicates (with the operator bypass) answer with the same queries and errors. Shadow: legacy loader row + legacy predicate give the verdict, `require` samples and raises the legacy error. Enforce: own candidate (caller org, platform, or same-company `tenant_publication=approved`), OpenFGA 404 `MARKETPLACE_ITEM_NOT_FOUND`/403 `permission_required`/503; draft/disabled stay product rules after the permission; list SQL = `candidate_predicate(platform=True)` ∪ company-public with the legacy status/unlisted/`is_listed` rules, paginated through the post-load scan and confirmed by `gate_list`. Operator bypass (10.5): enforced decisions never read `is_super_user`; the only operator path is `app/authz/marketplace_moderation.super_moderation` (SUPER_MODERATION): disable/enable (enable = restore) of platform items only, plus the SUPER listing route's read-back; each disable/enable records a `marketplace.super_moderation` audit event (`reason_code=super_moderation.<action>`). Open question for the spec owner: should operator takedown (disable) also reach user-published org items (10.5 "심사"), or stay FGA-only as now? 10.5 dual-write: `app/authz/marketplace_grants.py` `expected_grants` (C-01 table incl. the public/unlisted `manage` → manager deviation; system fanout computed but never route-written — platform items have no org, catalog/bootstrap owns it) and `sync_item_grants` (diff on (relation, principal) regardless of grant_kind, revoke/grant through `GrantService` → outbox; ACL users outside the org are skipped with a warning; legacy and other-org items no-op) after publish/version publish/PATCH/ACL replace/ACL delete; applied after commit. `marketplace_item_acl` stays (I-04). Lifecycle (`app/authz/marketplace_lifecycle.py`): item tuples on first publish (idempotent on slug reuse), installed skill/MCP server(+tools)/agent tuples on install and `install_new_copy` update, their deletes before `delete_resource=True`; install needs the created type's cap (`cap_create_skill`/`cap_register_mcp`/`cap_create_agent`), checked through `ItemGate.then` only after the item is authorized (hidden and missing items are the same 404 without the cap). Items have no delete/owner-transfer route, so `on_resource_deleted`/`on_owner_changed` are not wired. legacy_routes 21→0 after integration on top of D-06/D-07/D-08 (both copies empty; ratchet cap 0 — no LEGACY routes remain), OpenAPI snapshot regenerated (additions only: +20 paths, +18 schemas), predicate baseline −1 (router installation lookup → `load_private_installation`). Review fixes: `catalog_scope.load_catalog_management_candidate` eager-loads `acl_entries` (+`populate_existing`) — accepted legacy deviation: a member's manage call on a platform item is now 403 `MARKETPLACE_MANAGE_FORBIDDEN` instead of a MissingGreenlet 500; enforced gates call `require_private_actor` first; `active_grants` keeps every row per key and a stale key revokes all of them (the active-key unique index makes duplicates impossible today); sync skips an org item with `visibility=system` (PATCH schema already 422s it). Tests `tests/authz/test_d05_review_fixes.py` (52: operator + member matrix over all 21 routes, viewer 403 on manage routes, cap oracle, install cap 403, publish without cap / without source `can_share`, enforced install applies the copy's tuples and the copy reads 200, E-09 approved visible / unapproved hidden, moderation audit, legacy 403-not-500, system-visibility 422), `tests/authz/test_d05_marketplace_routes.py` (28: per-route operator/member 404 matrix, allow/403/other-org/disabled/503, moderation, SUPER, shadow samples) and `test_d05_grant_mapping.py` (18: 10.5 table, route dual-write exact sets + rerun identity, outbox, legacy no-op; revoke mutation caught). Gaps: publish services keep their private owner lookups of the source and the slug-reuse item in every mode (non-owner sharers/managers cannot first-publish until owner-free publishing); `update_installation` keeps the legacy `can_install_item` (operator bypass on a disabled source of one's own installation); grants drift while in legacy mode (re-run the m85 mapping or reconcile before leaving legacy); committed `49fd75eb`.
- [x] D-10 existing permission gaps (9.3, 9.5, 9.6; legacy behaviour changes are the intended fixes) — feedback upsert (`routers/feedback.py`) resolves the body's `conversation_id` with the private loader after `verify_csrf` (`owned_conversation` reads a path param, so inline; foreign → 404 `CONVERSATION_NOT_FOUND`) and requires `message_id` in that conversation via new `services/chat/message_membership.py` (active-branch checkpoint walk matching raw LangGraph id or `parse_msg_id` public id; purged conversations through the retained reader; else 404 `MESSAGE_NOT_FOUND`); health history for models now runs the platform-row loader through `gate_row` (unknown model → 404 `MODEL_NOT_FOUND` in legacy, shadow samples `ALLOW`, enforce = OpenFGA), shared with `check_now` as `load_model_target` (`gate_unchecked` kept for D-05 marketplace source `can_share`); `GET /api/models/{id}` applies the list's `is_visible` filter for non-super users in every mode (hidden → 404 `MODEL_NOT_FOUND`, a grant never reveals it); `POST /api/models/{id}/test` resolves an explicit `credential_id` through the owner-scoped `gate_row` loader (`credential#can_use` when consulted, same 404 `CREDENTIAL_NOT_FOUND`), default credential stays the owner-checked resolver tier; `GET /api/agents/{agent_id}/usage` (`authz/agent_usage_access.py`) requires a session and organization (`get_org_context`, 401) and uses the owner-scoped agent row as the legacy verdict through `require_agent` (foreign → 404 `AGENT_NOT_FOUND`; one added owner query in legacy); `allow_public_share_links` (org `settings` JSON, default on) enforced in `share_service`: create → 403 `PUBLIC_SHARE_LINKS_DISABLED`, every public read → 404 `SHARE_NOT_FOUND` via `load_share_with_conversation_and_agent` before any snapshot cache (one settings query per read, cache keying unnecessary). New error codes `MESSAGE_NOT_FOUND`, `PUBLIC_SHARE_LINKS_DISABLED`. No route/policy/OpenAPI changes (snapshot identical), predicates 57 / 0 additions. Tests `tests/authz/test_d10_health_models_usage.py` (11) + `test_d10_feedback_shares.py` (4); `test_d07_route_authz.py` legacy-usage-without-session cases removed (superseded), shadow case uses an owned agent; `test_feedback_router.py` stubs the checkpoint tree. Model route checks moved to new `app/authz/model_access.py` (`require_visible_model`, `load_model_target` shared with health, `load_owned_credential`, `model_test_credential`) and the probe audit row to `services/model_test_audit.py`, bringing `routers/models.py` from 285 to 247 pure LOC; stale runtime-dependency baseline row `app.routers.models → app.models.credential` dropped. Hidden models are 404 `MODEL_NOT_FOUND` for non-super users on detail, health history/check (`load_model_target`) and `POST /api/models/{id}/test` alike. Frontend `lib/chat/feedback-adapter.ts`: `resolveFeedbackMessageId` returns undefined for an id not yet in the messages query and the adapter then skips persisting (no request, no client error) instead of sending the raw assistant-ui id, which the server now 404s (vitest +1). Deviations: feedback DELETE/list stay `MessageFeedback.user_id`-scoped; purged conversations match only the retained reader's public ids (no raw LangGraph id); agent usage legacy verdict is owner-only (no org-visibility widening outside OpenFGA). committed `d6adff7b`.
- [x] D-09 permission matrix integration tests (8.4, ch. 9) — `tests/authz/matrix_support.py` (table-driven: actor owner/granted/visible/stranger/foreign × mode legacy/enforce/down(OpenFGA unavailable) → 8.4 status+code; `Probe.known` pins documented deviations with their F-xx reference, `Probe.gate` models in-route re-checks) and `test_matrix_{agents,conversations,catalog,marketplace,agent_api}.py` (RES/CANDIDATES/CAP/PRIVATE/KEY/SUPER/CATALOG/GOVERNANCE representative routes, ~660 cases) plus `test_matrix_policy_model.py` (every route's policy kind is allowed; RES/CANDIDATES type+permission+min_visible, CAP, KEY exist in `model.json`, PRIVATE names a real table, params exist). Fix: enforced marketplace `ItemGate` status rule — a non-owner manager's `POST /items/{id}/disable` committed then answered 404 on the `can_view` read-back; a `can_manage` holder now views hidden states like the legacy owner (`marketplace_access._status_ok`). Pinned known gaps: non-owner editors refused by owner-scoped services (agent PUT F-02, memory settings F-09, skill/credential writes F-13/F-05), shared-agent conversation create/list and deployments 404 for every non-owner (F-06/F-08), skill rows pre-filtered by DB candidates (stranger 404 even when OpenFGA is down). D-10 routes (feedback, health, models, usage, shares) not in the matrix. committed `97eb70ac`.

## P4 checklist

- [x] E-09 company publication policy and approval queue (10.2, 9.7, S05) — `tenants.settings.marketplace_tenant_publish` (`disabled`/`open`/`approval`, absent or unreadable → `approval`; no migration, m81 already added `tenant_publication*`). Routes: `GET /api/tenants/{tenant_id}/policies` (`SELF`, company member) / `PUT` (`GOVERNANCE`, `can_manage_tenant`, tenant `updated_at` `If-Match` → 409 `stale_version`, audit `policy.marketplace_publish_update` before/after); `POST|DELETE /api/marketplace/items/{item_id}/tenant-publication` (`RES(marketplace_item, can_manage)` via the D-05 item gate, legacy owner/ACL `manage`; the request also needs `cap_publish_marketplace` after the item is authorized, withdrawal does not — a publisher can always narrow); `GET /api/tenants/{tenant_id}/publication-requests` (cursor list of pending items: name, description, resource type, organization, publisher, required credential types only — D8) and `POST .../{item_id}/approve|reject` (`GOVERNANCE`, `can_manage_tenant_catalog` re-read under the tenant lock; reject note 1..1000 required; queue and decision only see pending items that are still published, `public` and org-owned — anything else, or foreign, → 404 `not_found`). Lock order tenant → item on both request and decision; the request reads the policy under the tenant lock. Request: `disabled` → 403 `policy_disabled`, `open` → `approved`, `approval` → `pending`; pending/approved re-request returns the same state; only published `public` org items (else 409 `tenant_publication_invalid`); withdraw → `none` from any state. Audit `marketplace.tenant_publish_request|approve|reject` + `marketplace.tenant_publish_cancel` (not in 14.1; withdrawal). Visibility: enforce reaches only `approved` company rows that are still `public` (`marketplace_access._company_public`, also in `item_org` and the list SQL, so a later PATCH to another visibility closes other orgs at once); legacy `catalog_scope.catalog_view_candidate_predicate` now keeps `pending`/`rejected` public items inside the publishing organization (detail, list, install) — this also applies to a legacy `super_user` listing, which no longer sees other orgs' pending/rejected items; `none`/`approved` keep the legacy reach. Installation updates (`install_service.update_installation`) now take the D-05 item gate `can_install` outside legacy via `app/marketplace/install/source_gate.installable_source` (legacy unchanged), so another org's installation cannot pull new versions after withdrawal or narrowing (closes the D-05 `update_installation` gap). `tuple_loaders.marketplace_item_row` now loads `tenant_publication` (it projected `none` before, stale comment fixed). Tuples: `app/authz/marketplace_tenant_projection.py` queues the `tenant:T#member installer` write/delete on approve/open/withdraw (no marker: the DB row closes other orgs first). New errors `policy_disabled`, `tenant_publication_invalid`; cursor kind `tenant_publication_requests`; company route inventory +5 (E09_ROUTES), deferred paths −3; OpenAPI snapshot additions only (+5 paths, +8 schemas); `test_company_administration_routes.py` count 23→28 and its deferred-path sample swapped to `/domains`. Tests `tests/authz/test_e09_policies.py` (5), `test_e09_publication_legacy.py` (8), `test_e09_publication_enforce.py` (5), `test_e09_review_fixes.py` (5: enforced installation update 404 after withdrawal, queue/approve skip private/disabled/draft, withdraw without cap; mutations caught); legacy-visibility mutation caught. Deviations: switching the policy to `disabled` leaves in-flight items as they are (pending can still be decided, approved stays approved); narrowing visibility/unpublishing/disabling a pending or approved item does not reset `tenant_publication` (no narrow hook point outside `publish_service.py`, which is far over the LOC cap) — the queue/approve filter and `_company_public` make it inert. Not done: requester/admin notifications (`marketplace.publish_requested|decided`, no notification system yet), "포함 자원 목록" in the queue, `MarketplaceItemOut` does not expose the state, narrowing `visibility` while `approved` keeps the state and the company tuple (inert behind the DB rule until withdraw/reconcile), other `/policies` fields (audit retention, org default limits). committed `d71e7b81`.
- [x] E-01 share, access-list and ownership-transfer API (9.7 "공유" rows, 10.2, 10.7) — `app/routers/resource_shares.py` registers `GET/POST /api/resources/{type}/{resource_id}/grants`, `GET …/access`, `POST …/transfer` once per shareable type (agent, skill, tool, mcp_server, credential) so each declares its exact policy: `RES(type, can_revoke|can_share|can_govern, param=resource_id, min_visible=can_run (agent) / can_use)`; `DELETE /api/grants/{grant_id}` `RES("{resource_type}", "can_revoke", param=grant_id, min_visible=can_revoke)` (dynamic type added to `test_matrix_policy_model.DYNAMIC_TYPES`; a grant on a resource the caller cannot revoke on is 404). Logic in `app/authz/share_targets.py` (type table, current-org own rows only — platform/system, owner-less and non-personal credentials are 404; legacy verdict = owner in its org), `share_rules.py` (10.1 levels per type, credential consumer/manager only, `manager` ceiling, owner never a principal, `expires_at` only for agent `consumer`, `cap_share_org_wide` via `require_cap` with the DB capability as legacy answer, D17 `409 confirm_required` with `details.delegations` for active delegation grants of credentials or credential-carrying tools/MCP servers), `sharing.py` (same principal+level+expiry → existing grant, 200, nothing written; level change → revoke old + create new, both audited; writes only through `GrantService`, outbox-ordered), `share_access.py` (owner / direct / group (+member_count) / organization / governance rows (org admins + company admins), display names, `my_permissions` over can_share/can_revoke/can_govern), `share_transfer.py` (owner or `can_govern`; non-owner governor cannot name self (`grant_invalid self_transfer`) and must give a reason; new owner must be an active member; skill slug clash → `409 transfer_name_conflict`; compare-and-set owner update (concurrent transfer → `409 stale_version`) after the GrantService org-row locks; new owner's redundant direct grants revoked; optional `keep_manager` grant for the previous owner; `on_owner_changed` ownership marker + owner tuple swap; audit `ownership.transfer` with reason/governance). Schemas `app/schemas/grant.py`; new error codes `confirm_required`, `transfer_name_conflict`; audit action `ownership.transfer`. OpenAPI snapshot regenerated (additions only: +16 paths, +9 schemas). Deviations/deferred: marketplace items excluded (their ACL routes stay the grant writer until I-04, a generic share would be revoked by the next ACL sync); share routes write grants + outbox rows in every mode (no legacy behaviour to preserve; legacy only lets the owner act); `ownership.transferred` notification (P7), delegation validator and readiness invalidation after revoke (F-02/P6), 10.4 personal-account blocking (E-05) not implemented; grant list is unpaginated. Review fixes (R1): credentials are shared by their owner only (a manager grantee or company admin with `can_share` gets 403 `permission_required` when it can use the credential, else 404); a non-owner may only lower their own direct grant (`grant_invalid self_grant`); a grantee may revoke their own grant without `can_revoke`; revocation audits carry `self_revoke`/`governance` (new optional `audit=` metadata on `GrantService.revoke`); after the grant write share/revoke lock the resource row and re-check its owner (`recheck_owner`, order org → grants → resource, same as transfer's compare-and-set; moved owner → 409 `stale_version`); `share_transfer_blocks.py` refuses transfers up front with `409 transfer_blocked` + details — personal account credentials (10.4 list pinned until E-05: google_workspace_oauth2, srt_account, ktx_account, foresttrip_account, mcp_oauth2), agent active triggers / active deployments / unrevoked API keys, and the resource's own credential column (agent `llm_credential_id`, tool/MCP `credential_id`) owned by the previous owner; denied transfers write no audit. Still not handled on transfer: an agent's links to the previous owner's private skills/tools/MCP servers, conversations and memories, per-user credential bindings. Tests `tests/authz/test_e01_share_rules.py` (16 functions, 20 cases), `test_e01_access_revoke.py` (9), `test_e01_transfer.py` (8), `test_e01_review_fixes.py` (11 cases). committed `b6d6b3c9`.
- [ ] E-03 catalog policy fanout (7.2 rows "시스템 자원 × 조직"/"표시 모델 × 회사 허용 × 조직 허용", 7.2.1 "카탈로그 항목 켜기·끄기", 9.7, 10.3, S09) — `app/authz/catalog_fanout.py`: effective items = visible inventory ∩ company policy ∩ organization policy (`enabled_entries`); each item holds `system_fanout` grants with principal = the organization (tool/skill/MCP server: `allowed_org` + `consumer`; model: `consumer`); `sync_fanout` diffs the org's active fanout grants against that set and grants/revokes through `GrantService` (outbox + 8.6 marker path), so turning an item off removes every member's `can_use` once the deletes land. `PUT /api/orgs/{id}/catalog-policy` syncs in the same transaction outside legacy mode and applies after commit (fanout runs before `bump_version`; the org row is refreshed because a revocation's epoch UPDATE also stamps `updated_at`, keeping the returned ETag current); org creation (`tenants/organizations.create_organization`) and SaaS company creation (first org) fan out the allowed catalog to the new org; legacy mode writes nothing (8.4, D-05 precedent). New `GET /api/orgs/{id}/catalog-policy/impact?type=&id=` (SELF + `can_manage_policy` like its siblings): live agents of the org linking the item and agents that cannot run without it (primary model only until F-01 `required`); unknown/non-catalog items 404 `not_found`. Backfill: `python -m app.authz.cli catalog-fanout --actor <operator> [--tenant|--org]` (one transaction per org, org then company lock, outbox only) — **must be re-run immediately before switching AUTHZ_MODE to shadow/enforce**: catalog policy edits made in legacy mode are stored but never projected; also re-run after seeding new platform resources. Review R1 fixes: `sync_fanout` locks every touched object in one global sorted order before a single sorted grant/revoke pass; `system_fanout` grants with an organization principal (not marketplace items) lock and recompute only their organization's rows (`grant_projection.org_scope`: their keys name `organization:O`, and every other row projecting such a key is an organization-principal grant whose `org_id` is O by `check_principal`; marketplace fanouts keep the object-wide lock for the shared `tenant` key); platform changes re-derive one item in every organization (`catalog_item_fanout.sync_item_fanout`, org rows locked in id order, company settings read unlocked) — wired into model create/PATCH/DELETE via `commit_item_change` (hide/delete revoke everywhere, show re-grants per policy; the helper reads the service through `get_authz(request)`), disabled system tools / MCP servers count as unavailable in the inventory, system tool/skill/MCP deletion already retires grants via `on_resource_deleted`; system MCP rows unreadable to the caller mask URL userinfo/query values and stdio args and export as `name (system)`; the impact GET takes no organization/company row lock and no `If-Match` dependency. Tests `test_e03_review_fixes.py` (5). D-04 deferral resolved: `resource_gate.MCP_SERVER` and MCP list candidates treat system servers as platform rows decided by the fanout (legacy loaders unchanged); marketplace publish sources keep the non-platform spec (system servers stay 404 as publish sources). Inventory fix: system skills/MCP servers keep the seeding operator in `user_id` (NOT NULL), so the catalog now selects them by `org_id`/`tenant_id` NULL (B-04 filtered `user_id IS NULL` and never listed them). OpenAPI snapshot regenerated (additions only: impact path + `CatalogImpactView`). Tests `tests/authz/test_e03_catalog_fanout.py` (4), `test_e03_catalog_policy_routes.py` (3), `test_d04_review_fixes.py` system-MCP case rewritten (404 without fanout, 200 + listed with it). Not done here: runtime exclusion of already-linked items (F-06 consumes `can_use`), connection-summary invalidation (G-01), company catalog-policy editing and per-org company-model designation / `allow_system_models` (B-11/E-07; company models currently fan out to every org of the company unless the company policy turns them off), native `expected_tuples()` == OpenFGA Read run (Docker blocked). committed `744be11f`, `82f33aeb`. — native verification pending
- [x] E-05 personal account types and sharing limits (10.4, Q4) — `CredentialDefinition.personal` (True for google_workspace_oauth2, srt_account, ktx_account, foresttrip_account, mcp_oauth2 — the last personal by default because the MCP "조직 공용 계정" marking does not exist yet); `app/credentials/personal.is_personal_definition` replaces the E-01 pinned list in `share_transfer_blocks.py`; `personal` exposed on `/api/credential-types` (field) and every `CredentialResponse` (computed from the definition). `app/authz/personal_accounts.py`: share API refuses `409 personal_account_shared` (`details.personal_accounts`: resource_type/resource_id/credential_id/definition_key) for a personal credential, a tool/MCP server whose `credential_id` is personal, and an agent lending one through its tool links, MCP tool links or `agent_skills.config.credential_bindings`; `PATCH /api/tools/{id}` refuses a personal credential on a tool with active share grants (one extra query only when `credential_id` is set). New error code `personal_account_shared`. Frontend `PersonalAccountBadge` ("개인 계정"/"Personal account", i18n ko+en) on the credential list card, type picker and detail dialog; there is no resource share dialog yet, so nothing to hide. OpenAPI snapshot regenerated (additions only: 2 `personal` properties). Deferred: per-user mode switch (F-04/F-05; the error tells the user to switch), MCP server credential change on an already-shared server and agent tool/skill attach after the agent is shared (writers over the LOC budget; F-02 delegation validator), MCP "조직 공용 계정" marking. Tests `tests/authz/test_e05_personal_definitions.py` (3), `test_e05_personal_sharing.py` (12 cases), vitest `personal-account-badge.test.tsx` (2) + `tests/pages/credentials.test.tsx` (+1). committed `4a70dbda`, `1d2e5867`. Review fixes (R1): agent carriers walk `agent_subagents` transitively (cycle-safe, one query per depth level, then one IN query per carrier kind — `app/authz/personal_account_carriers.py`) and include `Agent.llm_credential_id` of the agent and every subagent; the tool credential PATCH guard also refuses when an agent using the tool, or any agent calling such an agent as a subagent, has active share grants; the personal-account check runs before the D17 delegation confirmation; message "개인 계정 자격 증명은 다른 사람과 공유할 수 없습니다"; pre-E-05 grants are not revoked — `python -m app.authz.cli personal-account-shares [--json]` (read-only, `app/authz/personal_account_report.py`) lists active share grants that now violate 10.4, exit 1 when any; badge carries an sr-only description. Tests `tests/authz/test_e05_review_fixes.py` (7 cases).
- [x] E-04 frontend sharing (13.3, 13.4) — `src/components/sharing/`: `ResourceShareDialog` (agent/skill/tool/mcp_server/credential over the E-01 API; DialogShell title+description, picker + 10.1 level radios (credential consumer/manager only), agent-run expiry date, `/access` list with owner/group/organization/governance rows, revoke behind a name-confirming dialog, ownership transfer section (reason required for governors, keep-manager for owners, `transfer_blocked` dependents listed), D17 `confirm_required` step listing `details.delegations` then resubmitting with `confirm_delegation`, 10.4 notices: shared-account tool/MCP needs an acknowledgement checkbox, credential secret notice), `PrincipalPicker` (ARIA combobox over `GET /api/orgs/{org}/principals`, 250 ms debounce for the 30/min limit, arrows/Enter/Escape (Escape does not close the dialog), already-granted principals hidden, `org` → `organization`, whole-org option only with `cap_share_org_wide`), `ShareBackend` adapter interface + `useMarketplaceShareBackend` (marketplace detail ACL list replaced by `MarketplaceShareDialog`; revoke-only over `DELETE /acl/{user}` until I-04 — the ACL replace route rewrites every row with one permission, so no add form there), error mapping (`share-error.ts`, grant_invalid reasons), `ResourceShareButton`/`SharedWithMeBadge`. API `src/lib/api/grants.ts`, types `lib/types/grant.ts`, keys `['grants', type, id]`, hooks `use-resource-sharing.ts`; `ApiError.details` now parsed from the AppError envelope. Placement: AgentCard (share with `can_share`, settings hidden without `can_view_config`, "공유받음" when `my_permissions` lacks `can_govern`), dashboard dialog, MCP server card, tool detail dialog + tool card badge, credential detail dialog + card badge, no skill entry point (SkillResponse has no owner id; hidden rather than a dead-end button); personal-account credentials (`personal`, E-05) never show the button; `personal_account_shared` lists `details.personal_accounts`; share/revoke/transfer invalidate the resource list root, and self-revoke or a transfer without keep-manager closes the dialog. i18n `sharing` namespace (root scope) ko+en; orphan marketplace ACL keys and `useRemoveItemACL` removed. Tests: `tests/components/sharing/*` (18), `tests/unit/sharing/share-helpers.test.ts`, `tests/unit/api/grants.test.ts`, AgentCard +3; every sharing component test fails on any console error/warning. Not done: no-frontend-signal items — "공유함" (resources I share; lists carry no grant summary), "내 에이전트 / 공유받은 에이전트" filter, owner column for skills (SkillResponse has no owner id), legacy-mode notice (no API exposes AUTHZ_MODE), tools-skills binding dialog (S29/G-04); non-owner sharers with a manager grant don't see the button on tool/MCP/credential rows (no `my_permissions` on those lists). committed `1dfd7507`.
- [ ] E-02 member removal flow and forced transfer before account deletion (10.7 "구성원 제거"/"사용자 삭제", S15) — `GET /api/orgs/{org_id}/members/{user_id}/exit-preview` (`app/authz/member_exit_preview.py`: owned resources of the org with the E-01 blockers that remain after the leaver's own entry points stop, archived flag, personal-account `transferable=false`, owned marketplace items (no transfer path until I-04); active grants naming the leaver; groups, roles; active triggers, Agent API deployments, unrevoked keys; `is_last_admin`; read only) and `POST …/{user_id}/removal` (`MemberRemoval{reason, resolutions[{resource_type, resource_id, action=transfer|delete, new_owner_id}]}`, both SELF + `org_members_context` = `can_manage_members`). `app/authz/member_exit_settlement.py` runs inside `preflight_member_exit(settle=…)` after the member/last-admin checks and before the resource locks (order organization → grants → resource, as E-01): resolutions credentials → tools → MCP servers → skills → agents; `transfer` reuses E-01 `transfer_row` as governance (new owner active member, no self-transfer for a non-owner admin, removal reason as audit reason, no manager grant for the leaver; dependents owned by the leaver no longer block: `check_transferable(exiting=)`), `delete` for credentials only (personal accounts, 10.4; credential audit log + `on_resource_deleted`), anything else `422 grant_invalid delete_unsupported`; a resolution on a resource the leaver does not own → `422 not_owned`; then every active grant naming the leaver in the org is revoked via `GrantService.revoke` (audit `member_exit`). `DELETE …/members/{user_id}` runs the same path with no resolutions (grants revoked, still `409 resource_transfer_required` while anything is owned; the 409 now lists `details.resources` by type). Exit effects added to `apply_member_exit` (also used by company exit): owner's active Agent API deployments → `disabled` (locked with the entry points); org exit clears the member's `last_active_org_id`; `org.member_remove` audit carries `transferred`/`deleted`/`revoked_grants`; routes `apply_lifecycle` after commit. Archived agents no longer block an org exit (10.7 "보관"; they stay with their owner in the trash). `user_service.delete_user` (no route caller yet) refuses first with the owned list (archived agents included, m83 RESTRICT), then revokes the account's grants per org (`account_erasure.revoke_account_grants`, audit `account_erasure`) before the User lock, re-checks under the lock (late share → `409 conflict`) and returns the `LifecycleChange` for the caller to apply. Tests `tests/authz/test_e02_member_removal.py` (10), `test_e02_user_deletion.py` (4); `test_tenant_member_exit.py` preflight stub passes the new keyword. OpenAPI snapshot regenerated (additions only). Deviations/open: agent archive and skill/tool/MCP deletion are not part of the removal transaction (existing routes first); another user's active trigger/deployment on the leaver's agent still blocks its transfer (`409 transfer_blocked`); restore of an archived agent whose owner left is unguarded; company exit does not yet revoke grants; delegation validator (10.7 item 5), `delegation.broken`/`ownership.transferred` notifications and the `removed_member_days` retention (E-08) not implemented; real lock contention not covered (PostgreSQL). Review R1 fixes: `check_principal` locks the user principal's `organization_members` row FOR SHARE (shares/transfers serialize with removal's FOR UPDATE); `transfer_row` locks the organization row before it (order org → member row → grants → resource, same as removal: org → member rows → grants → resource); account erasure locks company → organization → the account's member rows (`lock_account_scopes`) before the revoke pass; `delete_user(..., operator_id=)` uses the operator as revoker/audit actor (defaults to the account itself) — `delete_user` still has no route caller (follow-up); re-running removal of an already removed member revokes left-over active grants (pre-E-02 removals); a credential held by a `skill_credential_bindings` row (RESTRICT) is refused for delete with `409 transfer_blocked {skill_bindings}` and shown in the preview's `blocked_by`. Legacy mode: `GrantService.revoke` writes revocation markers and outbox rows in every mode; `apply_lifecycle` is a no-op there (`tuple_writer` is `None`) and `on_resource_deleted`/`on_owner_changed` queue nothing. `sharing.share` also takes `lock_org_row` (moved to `sharing.py`, reused by transfer) before any grant write: a level change revokes and an in-place expiry change records a marker, both bump the org epoch after `check_principal` would have locked the member row; `DELETE /api/grants/{id}` is org (epoch) → grants → resource and takes no member lock. Tests `tests/authz/test_e02_review_fixes.py` (7; lock order via ORM statement capture, PostgreSQL not available). committed `fcdcda1c`. — native verification pending
- [x] E-07 settings tier: company models, shared credentials, service LLM, company catalog (6.2 설정 3단, 7.2 rows "표시 모델 × 회사 허용 × 조직 허용"/"회사 공용 자격증명 × 허용 조직", 7.2.1 "회사 공용 자격증명 허용 조직 변경", 8.9 시스템 LLM·모델 카탈로그 rows, 9.7, S07) — no migration: m81 already owns `models.tenant_id`, `system_llm_settings.tenant_id` (+ `uq_system_llm_settings_platform_role`/`_tenant_role`) and `credentials.scope`; the ORM now maps `Model.tenant_id` (FK only; the m81 default partial indexes are not redeclared because unit fixtures seed several platform defaults, the company model service keeps one default per company) and `SystemLlmSetting.tenant_id` with both partial unique indexes replacing `unique(role)`; the interim `column("tenant_id")` projection and the tests' `ALTER TABLE models ADD COLUMN` fixtures are gone. Narrowing (`app/authz/company_catalog.py` `CompanyTier`): platform (visibility/availability) → company (`tenants.settings.catalog_policy` off switches; tenant limit `allow_system_models` explicitly false removes every platform model, absent = allowed so the migrated default company keeps them; `tenants.settings.model_orgs` designation — a platform model without one reaches every org, a company model only designated orgs, resolving the E-03 open point) → organization (E-03 policy, cannot re-enable anything a higher tier removed: `catalog_policy_locked`). Company shared credentials (`scope='tenant'`, `is_system`, no owner/org per the m83 checks) are designated through `tenants.settings.credential_orgs` and fan out as `allowed_org` + `consumer` system_fanout grants in the same per-org set as the catalog (`catalog_fanout.fanout_for`, so the org catalog PUT and the backfill keep them); `sync_item_fanout` gained `tenant_id` and `kind=None` (whole-company resync). Routes (all `GOVERNANCE` + fresh `can_manage_tenant_catalog`; member 403 `permission_required`, foreign company 404; writes lock the company's org rows then the company row outside legacy, fanout + commit + outbox apply before responding, nothing projected in legacy): `GET·POST /api/tenants/{id}/models`, `PATCH·DELETE …/models/{model_id}` (company models fully managed, platform models accept only `allowed_org_ids`, else 403; default credential must be this company's shared credential or a platform one while allowed), `GET·POST …/credentials`, `PATCH·DELETE …/credentials/{id}` (values never returned; unknown definition 422; definitions flagged `personal` (E-05) refused; create/delete queue the credential's own tuples via `on_resource_created`/`on_resource_deleted`), `GET·PUT …/catalog-policy` (ETag = tenant `updated_at`, platform-hidden items cannot be enabled), `GET·PUT …/llm-settings` (per role: company override naming a company or (if allowed) platform credential, or both null = inherit). `resolve_system_model(db, role, tenant_id=)`: company row → platform default only while `allow_system_models`; platform paths (`get_setting`, `/api/system-llm-settings`, skill-evaluation pricing, e2e seeds) read `tenant_id IS NULL` rows only. Isolation: `credential_service.list_system/get_system/find_system_by_definition` and the env bootstrap ignore company rows, `get_provider_keys` never uses a company key as a global fallback; `/api/models` lists platform + the caller company's models (legacy and operator page: platform only), `get_model`/`resolve_model` are platform-only; `resource_scope("llm_model")` returns the model's company so a company model cannot be granted across companies. Audit `tenant.catalog_update` (models + catalog policy), `tenant.credential_create|update|delete`, `tenant.llm_settings_update`. OpenAPI snapshot regenerated (additions only: 6 paths, 13 schemas). Deferred: runtime callers still call `resolve_system_model` without `tenant_id` (builder/assistant/image/followup/skill builder — F-phase wiring); company credentials stay on the instance key (`tenant_encryption`/`rotation` treat `is_system` rows as platform; company-key sealing for them is follow-up); `allow_system_models` + company key for a platform model ("회사 키를 연결해야 켬") is shown as `requires_company_key` but no per-platform-model company key binding exists; the platform tenant summary still renders an absent `allow_system_models` as false. Review R1: pre-existing model lookups scoped with `company_catalog.model_scope` — blueprint requested/preferred/descriptor/fallback resolution and fallback validation (caller company + platform), agent create/update `model_id` (another company's model → 400 `Unknown model_id`) and `model_fallback_ids`, assistant `list_available_models` (visible, caller company + platform), `/api/health/models` and `load_model_target` / `load_model` (`require_company_model` in every mode, foreign → 404 `MODEL_NOT_FOUND`); builder default model, hidden skill-builder seed model and skill-evaluation pricing are platform-only; run-time fallback loaders (trigger, invocation, stream, history secrets, marketplace export) stay unscoped because every write path now validates; `TenantLimitSummary` reads an absent `allow_system_models` as allowed. Tests `test_e07_cross_company_models.py` (6). Tests `tests/authz/test_e07_narrowing.py` (4), `test_e07_company_models.py` (6), `test_e07_company_credentials.py` (5), `test_e07_catalog_and_llm.py` (4). committed `ad4ec71a`.
- [x] E-06 delegated-account disclosure and the D17 attach rule (D17, Q13, 10.6, 10.4, S16) — `app/authz/attach_rules.py`: `require_attachable` (single attach gate: a non-owned, non-platform credential or tool/MCP server holding a credential needs `can_share` via `runtime_authz().require(..., min_visible=can_use)`, legacy verdict `not_found`; owned/`is_system` rows and credential-less resources make no decision, so legacy is unchanged — every attach path still loads candidates owner-scoped) wired into agent create (`agent_creation_service`, also blueprint create-agent), agent update (added ids only), builder confirm (`builder_agent_finalization`), blueprint credential bindings (`agent_blueprint_graph.plan_graph`, model/tool/MCP credentials), Assistant `add_tool_to_agent`/`add_mcp_tool_to_agent`/sub-agent add (`write_tools/attach_guard.commit_links`); marketplace install only creates a blueprint (agent creation goes through create-agent). `require_shared_attach` + `attach_baseline`: when the links of an agent that is shared (directly or as a subagent, any depth, of a shared agent) gain account carriers, a personal account → `409 personal_account_shared`, otherwise `409 confirm_required {delegations}` unless `AgentUpdate.confirm_delegation` (new field, default false); carriers the agent already had are not re-asked; the Assistant never confirms (tool returns a message, rolls back) — closes the E-05 gap "attachments after the agent is shared". `app/authz/delegation_accounts.py`: D17 list = current link carriers (`personal_account_carriers.agent_carriers`, incl. model credential, skill bindings, subagents) ∪ active delegation grants, one item per carrier+credential `{resource_type, resource_id, name, credential_id, credential_name, owner_name}`; `share_rules.require_delegation_confirmed` now uses it (sharing an agent whose links carry accounts needs `confirm_delegation` before F-02 writes delegation grants); `GET …/access` returns `delegations` for agents (`AccessList.delegations`, schema `DelegatedAccount`). Frontend: the share dialog shows "이 에이전트가 쓰는 계정 자원" (resource, type, "{credential} · {owner}의 계정"/회사 계정) from `/access` and opens the `DelegationConfirm` step before sending (then `confirm_delegation: true`); the 409 path stays as a fallback (`DelegationList` shared by both; parser keeps credential/owner). i18n `sharing.delegation.{account,companyAccount,previewTitle,previewDescription}` ko+en. OpenAPI snapshot additions only (`DelegatedAccount`, `AccessList.delegations`). Tests `tests/authz/test_e06_attach_rules.py` (5), `test_e06_shared_attach.py` (6), `test_e06_share_preview.py` (3); `test_e01_share_rules.py` delegation assertion widened to the new item shape; vitest `tests/components/sharing/delegation-preview.test.tsx` (3), `share-helpers.test.ts` (+1); mutations (drop the shared re-check, skip the foreign check) caught. Deviations/open: subagent attach follows 10.6 `can_run` (subagents run as themselves), only its lent accounts trigger the shared-agent re-check; `skill_credential_bindings` (skill-level, resolved in the runner's scope) are not agent carriers and not gated; `agent_skills.config.credential_bindings` has no writer today (carriers report it); PUT audit `changed_fields` lists `confirm_delegation` when sent (`routers/agents.py` over the LOC budget); the Assistant cannot attach accounts to a shared agent at all (confirm in settings); widening attach candidates to non-owned `can_use` resources and writing delegation grants remain F-02. Review R1 fixes: agent settings saves (`useUpdateAgent`, both the settings page and the visual flow) answer `confirm_required` through `lib/sharing/delegation-confirm-request.updateAgentConfirmingDelegation` + `DelegationConfirmHost` (root layout; `DialogShell` with `DelegationConfirm`, "확인하고 저장") and resend with `confirm_delegation: true` — declining rejects with `DelegationDeclinedError` (the pages still show their generic save-failed toast; both pages are over the LOC budget); `delegationsToConfirm` moved to `lib/sharing/delegations.ts` (re-exported from `share-error`); `/access` `delegations` only for callers holding `can_share` (governors/managers without it get `[]`); `require_delegation_confirmed` skips no-op (same level and expiry) and lowering shares (`share_rules.widens`; there is no access-list row level-change UI — the picker hides granted principals, so level changes only come through the API); a confirmed save of a shared agent records `agent.delegation_confirm` (new `audit_actions.AGENT_DELEGATION_CONFIRM`, not in 14.1) with `{delegations: [{resource_type, resource_id, credential_id}]}` in the same transaction — a separate row instead of `agent.update` metadata because `routers/agents.py` is over the LOC budget; Assistant refusal text points at saving in the agent settings. Accepted: the confirmation is a blanket flag — accounts attached between the shown list and the confirmed request are confirmed too (F-02 may bind a hash of the confirmed set). Tests +3 backend (access gating, no-op/lower share, audit), vitest `delegation-confirm-host.test.tsx` (3). Known costs (accepted): an agent PUT that sends link lists reads `agent_carriers` twice plus `with_parents`; `delegation_carriers` does one scalar per tool/MCP-server delegation grant (batch it when F-02 starts writing grants); `GET …/access` walks the agent tree on every call. Not re-confirmed: a credential change on a tool or MCP server already linked to a shared agent (tools only get the E-05 personal-account refusal, MCP servers nothing) — F-02. Lint: E-04's `lint:frontend-architecture` failures fixed (`share-error.ts` imports `ApiError` via `@/lib/api/client`; `use-resource-sharing.ts` invalidates list roots through the domain `*QueryKeys.all` factories); `lint:a11y` chat-image.tsx drift is pre-existing. committed `c97bf67f`.
- [x] E-08 retention policies (6.2 보존 정책·대화 열, 10.7, 14.1) — committed `64b28cc2`. `organizations.settings.retention` (`conversation_days` NULL=unlimited/30–3650, `agent_trash_days` 30/7–90, `removed_member_days` 30/7–3650; unreadable → defaults) via new `GET|PUT /api/orgs/{org_id}/retention` (SELF / GOVERNANCE, `can_manage_policy`, If-Match; full replacement; a shortening returns `saved=false` + `preview` counts until `confirm=true`); `tenants.settings.audit_retention_days` (365/90–3650) on the existing E-09 `/policies` (optional field + `confirm`, same preview). Audit `policy.retention_update`, `retention.purge` (system actor, counts). `AuditEvent.tenant_id` now mapped (m81 column) and filled from the session scope or explicitly; no migration. Leader job `retention_sweep` (`app/services/retention/`, wrapper/registration in `app.scheduler`, dispatch in `scheduler_runtime`), daily cron `retention_sweep_cron`, **off by default** (`RETENTION_SWEEP_ENABLED`): per company (active/suspended) tenant_session, per active/suspended org: expired conversations (checkpoints, offloads, attachment blobs, artifact tree), removed members' conversations + memories past the period (re-add clears `removed_at` → restored), trashed agents past the period purged through the B-08 `purge_agent` acting as the org's first effective admin (conversations stay read-only `purged`), then company audit rows. `policy_delete_at` shown only when the sweep is on. Review fixes: idle = `last_activity_at`, `updated_at`, conversation/trigger/Agent API runs and Agent API threads all older than the cutoff (`retention/idle.py`); each conversation is re-selected under its row lock (removed-member path also under the member row locks); memories/audit deleted in ≤1000-id chunks with the predicate re-applied; DB delete commits before best-effort checkpoint/offload/file cleanup; sweep purges carry `retention_sweep`/`actor_kind: system` in `agent.purge` metadata (context var in `agent_lifecycle_scope`, audit only), the borrowed admin never carries super-user, an org without admin gets a skipped `retention.purge` (`no_org_administrator`); leadership re-checked per organization. Known limits: audit rows are not exempt, unlinked uploads and non-local artifact objects are left to their GC, HITL-parked conversations count as idle. Deferred: organization purge (needs the resource disposition contract), legal hold. Tests `tests/authz/test_e08_*.py`.

## P5 checklist

- [x] F-04 m87 credential modes and the per-user connection API (D15, D18, 6.1, 9.x `CRUD /api/credential-bindings`, 10.4, 11.3, 14.1, S28) — committed `bc8ae9da` (review R1 applied). Alembic `f04_credential_modes` (down `d02_agent_user_preferences`; independent of F-01's revision, re-chain at integration): `tools`/`mcp_servers.credential_mode` (`none`/`shared`/`per_user`, CHECK; backfill `shared` where a credential is attached; server default `none` so raw inserts never yield `shared` without one) and `credential_definition_key` (backfilled from the attached credential); `credentials.allowed_hosts` JSON (filled from hosts of MCP servers using the credential; definitions declare no hosts — **handed to F-11**: definition-host backfill and enforcement); new `resource_credential_bindings` (unique `(org_id, user_id, resource_type, resource_id)`, org/tenant FK CASCADE, user FK CASCADE, credential FK RESTRICT, polymorphic resource). ORM `app/models/credential_mode.py`: before-insert/update listener keeps `none`/`shared` in sync with `credential_id` (attach on `none` → `shared`, detach on `shared` → `none`; `per_user` only changes through the mode API). API (`app/routers/credential_bindings.py`): `GET /api/credential-bindings[?resource_type=]`, `PUT|DELETE /api/credential-bindings/{tool|mcp_server}/{id}` (SELF; PUT = target `can_use` + credential `can_use` + same org + the resource's recorded `credential_definition_key` (none recorded: nothing accepted); not `per_user` → `409 credential_mode_not_per_user`, wrong type → `422 credential_type_mismatch` + `details.accepted`; DELETE touches only the caller's row); `PUT /api/tools/{tool_id}/credential-mode`, `PUT /api/mcp-servers/{server_id}/credential-mode` (`RES can_edit`; `none` needs no credential and `shared` needs one, `per_user` needs a registered definition the resource accepts — tools: the tool definition's keys; MCP: required, any registered — all `422 credential_mode_invalid`; leaving `per_user` with a credential attached needs resource + credential `can_share`, someone else's credential → `409 credential_reshare_blocked`; leaving `per_user` re-runs the 10.4 guard, generalized to MCP servers). Audit `credential_binding.create|delete` (14.1), `credential_mode.update` (not in 14.1). **Runtime fails closed now** (`app/credentials/per_user_guard.py`): a `per_user` tool/MCP server never runs on its own `credential_id` — `build_tools_config` (chat/trigger/API, tools and MCP) raises `422 CREDENTIAL_BINDING_REQUIRED`, tool test runs return that error, MCP Apps calls `409 credential_binding_required`; MCP discover/health probes stay owner-side management. **Carriers (10.4/D17)**: a `per_user` resource shared directly lends nothing; one linked by a fixed-identity agent (owner subject, the default) still lends the owner's connection (`app/authz/per_user_carriers.py`: owner's binding credential, else the resource's `credential_id`, else a placeholder judged by `credential_definition_key`), so a fixed agent with a personal-type `per_user` resource is not shared; caller-identity agents are. **F-05 must resolve bindings by the caller only for caller-identity runs; fixed runs use the agent owner's binding.** `personal_account_shared` message hints the per-user switch (backend + ko/en). Tool, MCP server and credential deletes drop their connections in `on_resource_deleted` (every mode). OpenAPI snapshot regenerated (additions only). Tests `tests/authz/test_f04_credential_bindings.py` (8), `test_f04_credential_modes.py` (12 functions, 13 cases), `test_f04_runtime_guard.py` (5), `test_f04_migration.py` (3); adjusted `test_private_scope_models.py` (m87 columns allowlisted), `test_e05_review_fixes.py` (message prefix, carrier query count +2), vitest `resource-share-dialog.test.tsx`. Deviations: unique key includes `org_id`; mode switch is a sub-resource instead of a `PATCH` field (routers over size budget); no S28 UI yet (with F-05).
- [x] F-15 MCP authorization spec (2026-07-28) conformance (11.4.1) — committed `21dca60a`. `mcp_oauth_client.py` split into a facade + `mcp_oauth_discovery.py` / `mcp_oauth_tokens.py` (re-exported) and new `mcp_oauth_spec.py`, `mcp_oauth_step_up.py`, `mcp_oauth_prepare.py` (moved out of `oauth_service`). RFC 8707 `resource` (canonical URI: lowercase scheme/host, default :443/:80 dropped, no query or fragment, no trailing slash; stored on the credential at OAuth start, derived from `server_url` for token calls) on the authorization URL, code exchange, refresh and client-credentials requests. RFC 8414 §3.3 (security review H): discovery accepts AS metadata only when `issuer` is present and exactly equals the AS URL asked about, on every candidate path including origin-root fallbacks; otherwise `AuthorizationServerIssuerError`, and OAuth start fails closed with `400 authorization server issuer could not be verified` (`McpOAuthIssuerUnverified`) — no state row, the `iss` check is never skipped once metadata is fetched (existing `tests/test_mcp_oauth_client.py` fixtures gained `issuer`). With DCR off, metadata is fetched for the issuer only when the new optional field `authorization_server_url` is set; a fully manual config without it records no issuer, so `iss` cannot be checked for it (L1, documented). RFC 9207: AS `issuer` + `authorization_response_iss_parameter_supported` stored in `credential_oauth_states.metadata_json` with state/PKCE; callback takes optional `iss`, exact string compare, mismatch (or missing when advertised) → state row marked consumed (one-shot) and generic `400 invalid authorization response` before the token endpoint. Client registration order: manually entered client_id → CIMD → DCR (**deviation**: the plan puts CIMD first; a manual client_id now wins; a DCR-obtained client, tagged `client_registration=dcr`, yields to CIMD; pre-F-15 DCR clients carry no tag and count as manual); CIMD served at `GET /.well-known/oauth-client/{deployment}` (PUBLIC) only when `MCP_OAUTH_CLIENT_METADATA_BASE_URL` is set (default empty = off) and used only if the AS advertises `client_id_metadata_document_supported` + S256. Step-up helpers: `WWW-Authenticate` Bearer parse, 401/403 `insufficient_scope` detection, scope merge, `auth_required`/`scope_upgrade` interrupt payload (11.5 shape), budget 2 retries per (resource, action). The widened scope is not persisted up front (L3): `POST /api/oauth2-credential/auth/{id}` takes an optional body `{scope}`, the merged scope rides on the state row (`metadata_json.requested_scope`) and replaces the credential scope only when the callback succeeds (token-response `scope` wins); refresh keeps the granted scope. `token_destination_allowed` helper (own canonical origin, no token in query). OAuth routes moved to `app/routers/credential_oauth.py` (mounted from `credentials.py`, D04 module list updated, 53→54 routes). OpenAPI snapshot additions only (`iss` param, CIMD route). Open (review L2, wiring owned by F-02/F-05/11.5 runtime): raising the step-up interrupt from `MCPToolWithRetry`, enforcing `token_destination_allowed` at MCP call time. Open (review L5, handed to F-11): SSRF hardening of discovery/registration/token fetches (metadata-supplied endpoints are fetched without host/IP allowlisting). `authorization_server_url` is not in `OAUTH_IDENTITY_FIELDS` (service.py over budget), so changing it does not drop the OAuth session. Tests `tests/authz/test_f15_resource_parameter.py`, `test_f15_issuer_validation.py`, `test_f15_dcr_only.py`, `test_f15_preregistered_only.py`, `test_f15_insufficient_scope.py` (helper `f15_mock_as.py`).
- [x] F-01 link delegation columns and backfill (8.x m86 row, 10.6 "m86 이전") — committed `ad41d327` (review R1 applied). Alembic `f01_connection_delegation` (down `f04_credential_modes`): `agent_tools`/`agent_mcp_tools`/`agent_skills`/`agent_subagents` get `required` (false), `delegated_by` (FK users SET NULL), `link_status` (`active`/`broken`, `ck_<table>_link_status`), `broken_reason`; `agents.llm_credential_delegated_by` (FK users SET NULL). ORM mixin `app/models/agent_link_delegation.AgentLinkDelegation`. Backfill: `delegated_by` = agent owner on every link and the model credential; one active `delegation` grant `agent:A consumer R` per delegated object (tool, skill, subagent, model credential; MCP server once per agent with `allowed_run_source` over all five run sources), `granted_by` = owner, org/tenant = the agent's, `source_link={"link": <table>, "migration": "m86"}`. Objects of another org/company and archived subagents get `delegated_by` but no grant (same rule as F-02 `_grantable`, D21; F-03 reports them). Set-based: one scope read per type, one read of active keys (skipped, rerun-safe), one batched insert, deterministic uuid5 ids; no tuples (bootstrap projects them). Downgrade deletes agent delegations and their `authz_outbox` rows (tuples already in OpenFGA need `authz rebuild`) and drops the columns. Done-when: grantable delegated (agent, object) pairs = delegation grant count. Tests `test_f01_connection_delegation.py` (3) + `test_f01_backfill_rules.py` (2); `test_private_scope_models` allowlists the agents column/FK.
- [x] F-02 delegation grants follow link changes (10.6, D14, 7.2.1 "위임 연결·해제", 9.1 PUT/POST rows) — committed `ad41d327` (review R1 applied). Single writer `app/authz/agent_delegations.sync_agent_delegations(db, ctx, agent_id, added=...)`: takes the org row `FOR UPDATE` before any grant write (`lock_delegations`, the `sharing.lock_org_row` lock with a Task 10 tracker claim so live execution proofs/prefixes are not poisoned; create/update/builder/Assistant callers take it before their agent/link writes, E-02 order org → grants → resource; sync re-locks only when it writes; no share→exclusive upgrade: link writers set writer intent (`organization_execution_prefix.mark_org_writer`, session-scoped) so the canonical execution prefix takes the Organization row `FOR UPDATE` instead of `FOR SHARE` — agent create, update with link lists, builder confirm (router + `phase8_build`), blueprint create-agent, Assistant write-tool sessions (`write_tools.org_writer_sessions`), archive (`authorize_agent_lifecycle(action=archive)`) and the `delete_agent` facade; readers keep `FOR SHARE`; `test_f02_org_lock_order.py` (2) pins that no `FOR SHARE` on the org row precedes its `FOR UPDATE`/UPDATE within a save), grants `delegation` rows only for objects of links ADDED in this call (`added_keys`, MCP tools → server; Assistant: `claim_new_links` from pending rows) that are still linked and inside the actor's org/company and not archived (D21), and revokes link-derived grants (`source_link.link`, m86 rows included) whose object no link uses (a server stays while any of its tools is linked); grants of another org are skipped, not raised. Kept links without a grant are left for F-03. `delegated_by` is stamped only on rows created in the call (builders/`relink` make(), `claim_new_links`); `llm_credential_delegated_by` is set where the model credential changes (blueprint create-agent). Writes go through `GrantService` in every mode, legacy included (E-01 precedent: rows, outbox, `delegation.*` audit); in legacy there is no tuple writer, so the outbox accumulates and drains when the mode is switched (C-05 worker/bootstrap). Queued outbox ids ride on `session.info`; `commit_and_apply` commits then applies them; `pending_change` merges them into an existing change. Wired: agent create/update (`agent_creation_service`, `agent_update_service` — links diffed with `agent_binding_service.relink`, kept rows keep `delegated_by`/`required`/`link_status`, subagent positions renumbered), `routers/agents.py` POST/PUT/DELETE (`commit_and_apply`, LOC unchanged at 409), blueprint create-agent (`routers/agent_blueprints.py`), builder confirm (`builder_agent_finalization`, `routers/builder.py`, `builder_v3/nodes/phase8_build.py`), Assistant (`attach_guard.commit_links`/`commit_relinked`), agent archive (`agent_deletion._revoke_subagent_delegations`: the archived agent's and its parents' subagent grants, `kinds={agent}` so no private Agent column is read; the org row is locked only when something is revoked, after the lifecycle lock — the pinned B08 order is unchanged otherwise), marketplace MCP snapshot refresh (revoke only; `installation_created` merges the queued rows). Attach candidates widened (`app/authz/attach_candidates.shared_attachable`): once a type is enforced the binding validators also accept non-owned candidates confirmed with `can_use` (tool, skill), `can_invoke` (mcp_tool), `can_run` (agent); legacy unchanged (400). E-06 note 6 closed (batched `delegation_carriers`; a granted model credential is listed once). Tests `test_f02_delegation_sync.py` (6, incl. statement-order and archive), `test_f02_delegation_paths.py` (9), `test_f02_shared_attach.py` (4); mutations (no Assistant/builder sync, no widening, no lock, no archive revoke) caught. Open: E-06 note 7 (credential change on a tool/MCP server already linked to a shared agent → D17 re-confirm) left for F-05; builder/Assistant candidate lookups stay owner-scoped; MCP discovery cascade deletes and the e2e fixture writer do not sync (F-03 cleans); the `delete_agent` facade (no app caller, integration tests only) does not commit, so its queued revokes stay on the session for the caller's `commit_and_apply` or the outbox worker.
- [x] F-03 delegation_validator and broken links (10.6, 8.6 "위임 검증", 10.2 "공유 해제 시 연결된 위임 검증", 10.7 item 5, D14, D17, 12.2) — committed `429e3b8f`, `f1bcd854` (review R1 applied). `app/authz/delegation_links.py` collects the active links (and the model credential via `llm_credential_delegated_by`; platform credentials skipped) of an organization's live agents, optionally narrowed by object or delegator, with the delegator's 10.6 checks (`can_use` tool/skill/credential, `can_invoke` per MCP tool, `can_run` subagent; account carriers also `can_share` unless owned, D17) and whether the object can carry a delegation (same org/company, not archived — F-02 `_grantable`). `app/authz/delegation_validator.validate_delegations(db, scope, authz=, trigger=, dry_run=)`: lost (check fails, delegator account deleted, or object out of scope) → link `broken` (`delegator_lost_access` / `resource_unavailable`, conditional UPDATE), audit `delegation.broken` (system actor, agent target, link/object/delegator/reason/trigger), delegation grant revoked through new `GrantService.revoke_as_system` unless another active link of the agent still uses the object (MCP server); model credential: grant revoked only (no status column; F-05 tier-1 check refuses the run). Kept links without a grant (F-02 leftovers, m86 out-of-scope rows) are repaired (grant as their delegator) when the delegator still passes, else broken. No auto-heal. Links whose delegator/object a pending revocation marker covers are deferred (judged only on settled state). Legacy: no-op; shadow / types not enforced: report-only (`unenforced`). Lock order: org row (`lock_delegations`) before writes. Triggers (`app/authz/delegation_sweep.py`, post-commit + apply, best effort, own commit/apply): `DELETE /api/grants/{id}` (`revalidate_grant`), share stage change and ownership transfer (`revalidate_object`), member removal routes (delegator scope), catalog policy PUT when fanout changed (whole org), grant expiry in `jobs._expire_one` (TODO closed); daily leader job `authz_delegation_validate` (`AUTHZ_DELEGATION_CRON`, default `30 3 * * *`, registered with the C-05 authz jobs, so only outside legacy with a pinned model; per-org company session). Runtime (`app/authz/broken_links.py`, `app/services/runtime_links.runnable_agent`): run prep (`resolve_agent_context`, `build_agent_config_for_loaded_agent` → chat/API/prepared paths, trigger claim) sets a session view that adds `with_loader_criteria(link_status='active')` to every later ORM SELECT (eager loads included) so broken links are never bound (an agent loaded before is reloaded); a `required` broken link of the agent or of a child reached through an active link → `409 agent_not_ready` (`details={overall: blocked, items:[{kind,id,agent_id,required,result: delegation_broken,reason}]}`, new error code); triggers skip. Re-attach is explicit: kept broken links stay broken and are not re-validated on save; `PUT /api/agents/{id}` `reattach_ids` re-delegates the named ones under the editor (same attach checks, D17, grant, audit `delegation.reattach`; OpenAPI snapshot +1 field). Split: `grants.py` audit helpers → `grant_audit.py`. Tests `test_f03_delegation_validator.py` (6), `test_f03_revocation_cascade.py` (3, cascade: share revoke via route → tuple deleted → validator → subagent's required tool link broken + grant revoked + audit → parent run refused, runtime load excludes it; daily sweep; legacy unchanged), `test_f03_runtime_links.py` (4); mutations (no route hook, no loader criteria, no heal) caught. R1: every run uses its own company session (never the caller's); org row locked before links/grants are read; break guarded by `delegated_by IS NOT DISTINCT FROM` the judged delegator; per-link savepoints (`failed`, logged); undelivered outbox rows defer a would-be break; checks `HIGHER_CONSISTENCY` (`AuthzDecisions.batch_check(consistency=)`); model credential never re-granted by repair; catalog PUT scoped to the deleted fanout objects (`revalidate_outbox`); retry = `delegation_retry.revalidate_finalized` on the C-05 60 s maintenance tick (markers finalized since an in-process watermark, held up to 1 h for failed/deferred orgs; replaces the expiry-time hook); link writers (`update_agent`, `sync_agent_delegations`) refuse a session with the runtime view; a trigger with a required broken link records a failed run (`delegation_broken: required link unavailable (…)`). Tests also `test_f03_review_fixes.py` (6), `test_f03_trigger_blocked.py` (1), F-02 explicit reattach test. Open: suspension does not trigger (temporary); Assistant/builder saves do not heal broken links; `delegation.broken` notification is P7; `agent_not_ready` body is a minimal 12.2 subset until the readiness API (F-06/F-12) owns it; `app/scheduler.py` untouched (job rides `register_authz_jobs`).
- [x] F-05 run-time credential resolution (11.1, 11.3) — committed `09785425`. `app/credentials/run_resolution.py` (`RunCredentialResolver`) + `run_subject.py` replace F-04's fail-closed `per_user_guard` (removed with `test_f04_runtime_guard.py`): `shared`/`none`-with-credential uses the resource's credential, which must be the resource owner's (platform rows: the run account's, as before); the 11.1 subject (`agent:A` for fixed, `user:caller` for caller-identity) needs resource `can_use` unless the run account owns it (legacy verdict = owner only, so legacy is unchanged). `per_user` uses the run account's `resource_credential_bindings` row — caller for caller-identity runs, agent owner for fixed runs/triggers/API (identity None = owner); every run re-checks resource `can_use`, the account's active org membership (when not the requester), credential type/org and credential `can_use` (when the account does not own it), fail closed. Missing/unusable connections collected → `409 credential_binding_required`, `details.resources=[{resource_type, resource_id, reason: missing|unusable}]`. `build_tools_config` accepts someone else's same-org agent only for its authenticated caller with `agent#can_run` (`app/authz/run_agent_scope.py`; legacy 404); loading a shared agent tree is still F-06. `read_only=True` (response masking in `chat/secrets.py`, now with a session) resolves bindings without decisions/errors/refresh. MCP: `resolve_mcp_auth` filtered by the chosen credential's owner. LLM tier 1 adds a delegation check (`credential#can_use` for the 11.1 subject). Tool test runs and MCP Apps calls use the caller's/binding subject's own connection (`credential_binding_required`). Tests `tests/authz/test_f05_credential_resolution.py` (11), `test_f05_direct_use.py` (3).
- [x] F-10 `ResolvedCredential` and decryption boundary (11.3 table) — committed `09785425`. `app/credentials/boundary.py`: `ResolvedCredential` (plan shape), `SecretValues` (redacted repr), `open_values(credential, purpose)` with purposes run/redaction/refresh/probe/system/install; all 16 call sites outside the storage layer (runtime, LLM, MCP auth/OAuth, oauth_service, health check, model test/discovery, credential test, system LLM/system resolver, history secrets, marketplace requirements) go through it. Static check `tests/authz/test_f10_decrypt_boundary.py` (AST: `decrypt_with_external`/`open_payload`/`decrypt_data`/`decrypt_tenant`/`cipher.decrypt` only in boundary, `credentials/service.py`, `tenant_encryption.py`, `security/`) + negative fixture. Deviation: decrypted tool values still travel in `tools_config` (AgentConfig) as before; moving them to run_secrets only is left for F-06/F-11.
- [x] F-11 credential destinations and `outbound_http_client()` (11.9, D18, Q14; F-15 review L2/L5) — committed `c62d2e4b`. `app/outbound/`: `hosts` (label-boundary one-label wildcards, S24 pattern validation, `CREDENTIAL_HTTP_ALLOWED_HOSTS` parse), `destination` (`CredentialDestination{allowed_hosts, empty_ok}`, https-only unless the operator lists the host, empty hosts only in the owner's own chat/subagent run — trigger/API/shared/delegated runs get `OutboundBlockedError`; with empty hosts the first host pins the invocation so a redirect never carries the credential elsewhere; bound per tool invocation on a context variable), `transport.GuardTransport` (checks the bound destination on every request and redirect hop, https-only, SSRF guard: every resolved address must be public and the connection is pinned to the checked IP with Host/SNI kept — closes the url_guard DNS-rebinding gap; operator-listed hosts skip the private-address block), `client.outbound_http_client()` (shared trust store, timeouts, optional same-host-only redirects) + `mcp_http_client_factory` + sync twin. Wired: shared runtime tool client (SSRF guard, kept as the one shared client) and tool test runs; `tool_factory`/`runner` bind the destination around the runner; `build_tools_config` emits `credential_destination` for tools and MCP (`owner_own_run` from identity source/caller/subject); generic HTTP tool refuses user auth headers when a credential is attached; MCP runtime blocks a credentialed config before connecting (allowed hosts, https, F-15 `token_destination_allowed` against the stored OAuth `resource`) as a reason stub, credentialed MCP transports (runtime, probe, MCP Apps) use the https-only same-host client factory; MCP registration/update 422s a credentialed http URL and adds the server host to the editor's own credential (never someone else's); MCP OAuth discovery/DCR/token client is https-only + SSRF-pinned + no redirects (L5); Google refresh and credential tester https-only; model SSL clients built by the factory. `CredentialDefinition.hosts` declared for 12 types; new credentials (personal and company) start with them; Alembic `f11_credential_definition_hosts` (down `f01_connection_delegation`) unions them into existing rows (generic HTTP/MCP types stay empty, Q14). Ratchet `test_f11_migration_and_ratchet.py`: `verify=False` banned, direct `httpx.AsyncClient/Client` only in `app/outbound` plus a shrinking allowlist (image gen, langfuse, model discovery/catalog). Tests `tests/authz/test_f11_outbound_transport.py` (24), `test_f11_http_tool_destination.py` (7), `test_f11_mcp_destination.py` (10), `test_f11_migration_and_ratchet.py` (3); adjusted `test_tools.py`, `test_tester.py`, `test_chat_integration.py` (fakes accept the factory kwarg), `agent_runtime/test_runtime_only_params.py`, `authz/test_f05_direct_use.py`. Deviations: a cross-host redirect of a credentialed request is refused instead of re-sent without auth headers; model calls are not https-enforced (local OpenAI-compatible endpoints) and the zero-retry model lease transports keep their own trust store; MCP probe/MCP Apps calls check https but not allowed_hosts (the owner's own direct use); configs without `credential_destination` (none produced by `build_tools_config`) are unchecked; no allowed-hosts edit API/S24 UI yet; skill subprocess credentials are not covered (no in-process HTTP).
  - Review R1 applied (committed `c62d2e4b`): (1) environment proxies (`HTTP(S)_PROXY`/`ALL_PROXY`/`NO_PROXY`, httpx `get_environment_proxies`) are rebuilt as mounts for the async and sync clients (an explicit transport disables httpx's own lookup); proxied routes keep destination/allowed-hosts/https checks and block IP-literal private targets but skip IP pinning (the proxy resolves the name), `NO_PROXY` hosts go direct and pinned. (2) No URL rewrite: SSRF resolve→check→pin moved into `PinnedBackend.connect_tcp` (installed on the httpcore pool), so URL, pool key, Host, SNI and certificate checks stay hostname-based. (3/4) Settings split: `CREDENTIAL_HTTP_ALLOWED_HOSTS` (http allowed for credentials) vs `OUTBOUND_PRIVATE_ALLOWED_HOSTS` (SSRF-exempt); with `APP_ENV=dev` and the setting empty both default to `localhost,127.0.0.1,host.docker.internal` (so the openai_compatible tester reaches local http in dev); MCP PATCH re-checks https only when `url`/`credential_id` changes. (5) `CREDENTIAL_EMPTY_HOSTS_POLICY` = `warn` (default: send + `credential_empty_hosts` log warning; no audit row — the transport has no session) | `enforce` (refuse); the first-host pin applies in both; migration also fills `http_bearer`/`http_api_key`/`http_basic` from the stored `url` of `http_request` tools they are attached to (IP literals/templated URLs skipped); host patterns accept an exact IP when configured explicitly (never in a wildcard; auto-fill never adds IPs). (6) Fail closed: credentials without `credential_destination` raise in the runtime tool path and block the MCP config. (7) All public addresses are tried in resolver order; private ones in a mixed answer are never dialed. (8) NAT64 `64:ff9b::/96`, 6to4 `2002::/16` and IPv4-mapped addresses follow the embedded IPv4 rule. New test file `tests/authz/test_f11_ssrf_pinning.py` (real httpcore pool on a recording network backend); `test_runtime_only_params.py` fixture gained a destination.
- [x] F-06 `prepare_authorized_runtime` and `RuntimeAccessPlanner` (11.0, 11.1, 11.2; done-when 16.2 런타임 경로 테스트) — committed `b4253ee9` (security review R1 applied: M1, M2, L1–L5). **Boundary** `app/services/authorized_runtime.prepare_authorized_runtime(agent, *, path, context, db, identity, conversation_id, authz, root)` → `AuthorizedRuntime(plan, tools_config, agent_skills, sub_agent_links, excluded_capabilities)`, re-exported by `chat_service` (agent_runtime callers keep their baselined facade edge; builders are looked up on the facade at call time so existing monkeypatch seams hold). Wired into every run path that binds tools/credentials/models: `resolve_agent_context` (chat send, edit/regenerate, REST + protocol resume, protocol run.start/fork, side chat, queue launch, skill-builder branch), `build_agent_config_for_loaded_agent` (Agent API), `build_subagents_config` + `resolve_prepared_children` (children, `root=False`, from the parent plan's `sub_agent_links`), trigger claim + `trigger_config_resolver` (identity `None` = owner, fixed). The plan rides on `AgentConfig.access_plan` (last field, no secret values; runtime facade contract fixture +1 param). **Planner** `app/authz/runtime_access_planner.py`: nothing enforced (`legacy`/`shadow`/run types not in `AUTHZ_ENFORCE_TYPES`) = the existing `require_runtime_capability_tree`/`require_agent_capabilities` and every link, no query (`test_f06_shared_agent_runs` pins identical SQL statements and tool configs vs. the old builders). Enforced: root of someone else's agent → caller `agent#can_run` (`require_runnable_agent`); broken links (F-01 `link_status`) skipped; rows the run account owns need nothing (owner fast path); platform rows decided as the run account user (catalog fanout `organization#member` consumer); other same-org rows as the 11.1 subject (`agent:A` fixed / `user:caller` caller-identity) with `can_use` (tool, skill), `can_invoke` (mcp_tool), `can_run` (subagent); the model `llm_model#can_use` as the run account; another org's row is never asked (denied); one BatchCheck per principal; a type not enforced keeps the legacy owner answer per row. Denied required link or model → `409 agent_not_ready` (`details.blocked=[{type,id}]`, new error code); denied optional → excluded (`excluded_capabilities=[{type,id,reason: broken|denied}]`); OpenFGA down → 503. `RunCredentialResolver(decided=...)` skips objects the planner confirmed. **Shared agent load (D-09 gap closed)**: `app/authz/run_agent_loader.py` — `AgentExecutionTarget(run=True)` (chat create/executable conversation, acceptance, run worker, queue launch, transitions, prepared runtime claim/adapter, subagents) admits a tree whose root is someone else's same-org agent after the caller's `agent#can_run` once `agent` is enforced (`authorize_shared_tree`; mutation callers keep `run=False` = owner-only); `get_owned_conversation_with_agent` hydrates my conversation's shared agent (`attach_shared_conversation_agent`), `chat_service.get_run_agent_with_tools` serves `POST /api/agents/{id}/conversations[/draft]` and `GET …/conversations`, the runtime-policy snapshot reads the conversation's agent in org scope when enforced. Legacy unchanged (owner-only 404, no decision). D-09 matrix: `SHARED_AGENT_404` known gap removed (granted actor 201/200, OpenFGA down 503 for non-owners). Tests `tests/authz/test_f06_runtime_access_planner.py` (5), `test_f06_shared_agent_runs.py` (4), `test_f06_runtime_paths.py` (23: the 13 rows of 11.0 parametrized — boundary carriers or classified separate paths with owning task; AST guard: `build_tools_config`/`build_agent`/`_build_mcp_tools`/`build_agent_skills`/`run_model_test` only from listed sites, negative fixture, E2E router only under the e2e flag; mutation caught); adjusted `test_matrix_conversations.py`, `test_d06_runtime_key_routes.py` (model `can_use` tuples), `tests/agent_runtime/test_subagents_runtime.py` (fake planner for transient rows, `plan` kwarg), `fixtures/runtime_facade_v1.json`. Deviations/open: `excluded_capabilities` is on the plan/config but not yet emitted as a run-start event (`langgraph_streaming.py` at the 250-LOC budget) — UI banner with G-01/F-07; trigger runs build children without the parent plan (owner-only, as before); `GET /api/agents/{id}/conversations/page` stays owner-scoped (`is_agent_owned_by_user`); Assistant, skill evaluation, MCP Apps, model test and the E2E MCP Apps path are classified separate paths (F-13/D-08/F-05); decrypted values still travel in `tools_config` (F-10 deviation); prepared children now resolve tools before the middleware/provider unsupported checks (error precedence only). Enforce now needs model catalog fanout (E-03 backfill) or runs are `agent_not_ready`. **Review R1**: (M1) `app/authz/run_decisions.py` run decision memo — keys (principal, run source, permission, object); `require_decided`/`batch_decide` back the planner, `require_runnable_agent`, `require_agent_permission` (so the tree loader, conversation hydration, policy read and planner share one `can_run` decision per scope), `RunCredentialResolver._require` and the LLM tier-1 `llm_credential_delegated`; `bind_request_authz` opens a request memo; `app/services/prepared_run_decisions.predecided_run` (I17 pattern) opens a fresh memo before the chat (`prepared_conversation_start`), API (`prepared_execution`, inside `fresh_decision_scope`) and trigger (`prepared_trigger_entry`) leases, loads the tree unlocked in its own session, replays the root/child plans and LLM delegation, then seals — under the materialization locks a missing answer is a denial (OpenFGA down while deciding stays 503), so no OpenFGA call runs under those locks (`test_f06_prepared_decisions` lock-aware client; `test_d06` now pins every query before the lock; mutation caught). Remaining fail-closed case: a `per_user` binding to a credential the run account does not own is not pre-decided (prepared run → `credential_binding_required`). (M2) a required broken link → `agent_not_ready`; only optional broken links are excluded. (L1) trigger claim/prepared trigger put `runtime.plan` on `AgentConfig.access_plan` (`TriggerConfigSource`, `TriggerMaterialized`), children follow it; `trigger_blocked_tools_for_agent_tree` moved to `services/chat/trigger_blocked_tools.py` and probes only plan-allowed links (denied optional excluded). (L2) `Exclusion.as_dict`/`excluded_capabilities` carry no resource ids (`excluded_counts` for summaries; ids stay internal on `plan.excluded`); `agent_not_ready` uses F-03's `{overall: blocked, items: [{kind, agent_id, required, result, reason}]}` without `id` (same message text as F-03's code). (L3) a non-owned row of a type not yet enforced → `agent_not_ready` (was the type's 404). (L4) `prepared_run_children` keeps the legacy order via the boundary's two steps (`plan_authorized_runtime` → unsupported checks → `bind_authorized_runtime`); pinned by an L4 test (no binding query before the middleware refusal). (L5) the runtime-policy snapshot read re-checks the caller's `can_run` on a shared agent (memoized), and shared loads admit only `runtime_profile=standard`. **Shared-agent memory is silently off** for non-owner runs (memory policy/context reads stay owner-scoped) — F-09 decides the shared memory policy. **Integrated with F-03/F-11 (main f1bcd854)**: `runtime_links.runnable_agent` uses `require_legacy_runtime_tree` and reloads through `get_run_agent_with_tools` (shared agents run under enforce; F-03 link view and required-broken 409 kept); trigger claim keeps F-03 `blocked_links` and F-06 plan propagation/order; one `agent_not_ready` shape `{overall, items:[{kind, agent_id, required, result, reason}]}` — runner-visible items carry no `id` (F-03 `broken_required` items drop `id`; trigger failed-run messages keep it internally).
- [x] F-12 MCP auth errors typed, cache keys and invalidation (11.4, 11.4.1, 11.5; done-when 401·403과 전송 오류 구분 테스트) — committed `52efcf4c`. `app/agent_runtime/mcp_auth_errors.py`: `classify_mcp_error` walks the httpx/anyio ExceptionGroup/cause chain for `HTTPStatusError` → `McpAuthError{kind=auth_required (401; reason auth_needed|expired for invalid_token) | insufficient_scope (401/403 Bearer insufficient_scope) | permission_denied (other 403)}`; everything else stays a transport error. `MCPToolWithRetry` (`mcp_cache.py`) never re-sends a 401/403 and returns an error ToolMessage (`status=error`, tool_call_id kept) with artifact `mcp_auth_error{kind,status_code,reason,server_id,credential_id,step_up_exhausted,interrupt}`; `interrupt` is the 11.5 `auth_required` payload (F-07/G-03 lift it into the real interrupt; not raised from inside ToolNode). `mcp_auth_context.py`: secret-free `McpAuthContext` from the tool config; 401 marks the credential `active→auth_needed` (own tenant session) and evicts its cache; insufficient_scope uses F-15 `plan_scope_step_up` (merged scope, `reason=scope_upgrade`) with a per-conversation `ScopeStepUpBudget` (2 per server+tool, survives the runtime rebuild of a retry resume); discovery-time 401/403 yields a typed stub instead of "temporarily unavailable". `build_tools_config` MCP entries add `mcp_server_name`, `credential_id`, `credential_mode`, `credential_fixable_by`. Cache: `mcp_tool_cache.py` (moved out of `mcp_cache.py`, re-exported) keys = server key + digest(tenant, org, server id, credential id, mode, run account, injected auth_config) with `credential:/server:/account:` tags; the legacy loader now uses per-server interceptors (a cached tool never carries another server's/user's injected args); `mcp_cache_invalidation.py` ORM hooks evict on credential identity-column update/delete (values, status, owner, sharing, allowed hosts, key rotation) and per-user binding insert/update/delete. Tests `tests/authz/test_f12_mcp_auth_errors.py`, `test_f12_mcp_cache_identity.py` (real MCP SDK client over httpx.MockTransport).
  - Security review follow-ups applied (committed `52efcf4c`): (1) a 401 flips the credential to `auth_needed` only when the run actor owns it (`fixable_by=me`), the credential is the one bound to that server and the server said `invalid_token`; otherwise only the typed error is returned (no write to a shared row). **For F-07:** the ConnectionGuard must not assume the row status changed — re-read the credential and treat `fixable_by=owner` 401s as owner notifications. (2) Step-up budget: conversation runs keyed by conversation, trigger runs (`run_source=trigger`, now on MCP configs) and conversation-less runs keyed by run id (never agent-wide); merged scope over 1024 chars / 64 scopes is refused; the cap is per process. (3) Tool cache: LRU-bounded (`MAX_ENTRIES=256`), expired entries swept at insert, idle locks of evicted keys pruned. Auth errors are re-raised `from None` so the URL-bearing httpx error never reaches a traceback/exc_info log.
- [x] F-09 shared agent memory policy (11.8, 9.7 memo routes; done-when "비편집자 저장은 제안으로") — committed `ed69b0ce`. **User x agent memory** stays the caller's own: `app/authz/memory_agent_access.py` (`memory_agent` = my agent, else — `agent` enforced — someone else's same-org standard active agent after the caller's `agent#can_run` via `load_shared_run_agent`; `shared_memory_agent_ids`/`row_shared_memory_agent_ids` answer without a query outside enforce) feeds `memory_scope` predicates an optional `shared` agent-id set (empty = the owner-only SQL unchanged), so a `can_run` runner reads/writes/deletes only their own `scope=agent` records and the agent's `AgentMemorySettings` still bound their policy; the owner never sees them (closes F-06's "shared-agent memory silently off"). **Shared operating memo** (`/agents/{id}/AGENTS.md`): runs where `cfg.agent_owner_user_id != cfg.user_id` get it read-only (`build_filesystem_permissions(agent_memory_writable=False)`; stored policy was already read-only) and a `propose_agent_memo` tool (not in trigger mode) that files `memory_proposals(scope='agent_memo')`; result event `agent_memo_proposed` + tool name registered in `memory_event_projection` (`MEMORY_EVENT_NAMES`/`MEMORY_TOOL_NAMES` → `protocol_redaction` custom-event/args masking, egress, persistence); private `/api/memory-proposals/{id}` never exposes or decides `agent_memo` rows. New routes `app/routers/agent_memo.py`: `GET /api/agents/{id}/memo` + `POST …/memo/proposals` `RES(agent, can_run)`; `PUT …/memo`, `GET …/memo/proposals`, `POST …/memo/proposals/{proposal_id}/decision` (approve appends `- text`, optional edited content; reject) `RES(agent, can_edit)`; `app/services/agent_memo_service.py` (atomic tmp+replace under an flock, audit `agent_memo.update` {length} / `agent_memo.proposal_decide` {proposal_id, decision}, never text). **Memory settings**: `agent_edit_context` decides `can_edit` then serves a non-owner editor in the owner's scope (D-01 TODO removed; D-09 matrix `OWNER_ONLY` pin removed for memory settings, memo probes added). Legacy unchanged (owner-only 404; owner runs keep a writable memo and no new tool). OpenAPI snapshot regenerated (additions only). Tests `tests/authz/test_f09_shared_memory.py` (4), `test_f09_runtime_memo.py` (6), `test_f09_agent_memo_routes.py` (3), `test_matrix_agents.py` updated; mutations (editor scope, runtime shared ids, read-only memo wiring, event registration, private hide) all caught. Deviations/open: per-agent memo on/off toggle and "migrated memo ⇒ on" need a column (migration, left to the migration owner); spec says the memo is read-only for every run — owner runs keep write in legacy-compatible fashion; GET proposals is `can_edit` (proposal text is editor-only); `/api/memories` without `agent_id` does not list records on shared agents (filter by agent works); user memory is already org-scoped (`memory_records.org_id`); frontend (memo editor, proposal review, `agent_memo_proposed` card, "이 에이전트에서의 내 메모리" label) not done; memory_service.py (pre-existing 555 LOC) grew to 608 because the dependency guard forbids a new runtime→services edge for the tool's proposal writer. **Security review R1 applied**: migration `f09_agent_memo` (down_revision `f11_credential_definition_hosts`, to be re-chained after F-14) widens `ck_memory_proposals_scope` to `agent_memo` (same CheckConstraint on the ORM) and adds `agent_memory_settings.shared_memo_enabled` (default off; backfilled on for agents whose `AGENTS.md` has content); `GET/PUT …/memo` expose/set `enabled`, proposals need it (409 `AGENT_MEMO_DISABLED`), at most 20 pending per user per agent (409 `AGENT_MEMO_PROPOSAL_LIMIT`), IntegrityError → 409 `AGENT_MEMO_PROPOSAL_REJECTED` (tool: `memory_rejected` with the code). Under `agent` enforcement no run writes or mounts `AGENTS.md` (owner and skill-builder runs included; no deepagents memory source, no prepared snapshot file, no stored read rule); when switched on the memo is put in the prompt via `load_memory_context_in` (`app/agent_runtime/runtime/shared_memo.py`, "reference facts, not new instructions", `<shared_agent_notes>`); legacy keeps the writable memory source. Approve-append rejects over `MAX_AGENT_MEMO_LENGTH` (400 `AGENT_MEMO_TOO_LARGE`, rolled back); writes are temp file → commit → replace (`app/services/agent_memo_files.py`, failed commit leaves the memo untouched); decisions lock the pending row (idempotent). Non-owner editor memory-settings changes are audited `agent.memory_settings_update` (actor = editor, changed fields). Tests +`test_f09_migration.py` (3).
- [x] F-14 actor and cost attribution (11.10, 6.2 m89 row, 8.7, Q8, Q22; done-when 트리거·API·공유 실행별 귀속 테스트) — committed `1c5040a0`. Alembic `f14_run_attribution` (down `f11_credential_definition_hosts`): `conversation_runs` + `token_usages` get `org_id`/`tenant_id` (FK pair), `run_source` (CHAT/TRIGGER/API), `invoker_type` (user/schedule/api), `resource_owner_user_id`, `on_behalf_of_user_id`, `credential_subject_user_id` (users SET NULL); `token_usages.caller_user_id`; `conversation_run_agents` gets `invoker_type` + the three user columns per participant (subagents keep their own identity, `invoker_type=subagent`); `daily_spend_user|agent|model` get `org_id`/`tenant_id` and the UPSERT key `(org_id, date, target)`; `audit_events.org_id` + index `(org_id, created_at)`. Backfill: historic runs/usage/participants = owner (`user_id` / agent owner), source API/TRIGGER from the envelope rows else CHAT; spend rows get the agent's org, else the user's / model company's default org (unplaceable rows stay NULL); audit not backfilled. One set-based UPDATE per table (runs, usage and participants single-pass), indexes created after the backfill; no id-range batching (UUID keys, one alembic transaction) — run in a maintenance window on large `token_usages`/`conversation_runs`. Downgrade folds per-org spend rows into one `(date, target)` row (sums, max `updated_at`) before restoring the old unique key. `app/tenancy/attribution.py`: `attribute_run(source, context, agent)` — `caller_user_id` = requester = billed user (chat caller incl. shared caller, trigger owner, API key owner = `context.user_id`, `conversation_runs.user_id`), acting user/account = caller for `per_user`, owner for `fixed` and callerless runs; `attribute_child` for subagents. Written once in `accept_owned_execution` (run row + participants, no extra SQL); `persist_run_metrics_and_usage` copies the run's columns into `token_usages` (`usage_columns` raises `AttributionNotLoadedError` if `user_id`/`org_id` are not loaded; audit metadata reads only loaded attributes). The recovery (`conversation_run_cancellation_recovery`) and queue-worker (`RUN_IDENTITY_OPTIONS`/`RUN_TERMINAL_OPTIONS`) `load_only` lists include `RUN_ATTRIBUTION_COLUMNS` (`models/conversation_run.run_attribution_attrs()`). Spend: `HookContext.org_id`/`billing_user_id` from `cfg.org_context`; `SpendHook` books the requester (trigger/API configs carry the agent owner as `user_id`, so a non-owner trigger/key owner is now billed correctly; identical in legacy), `SpendEntry.org_id/tenant_id`, buckets keyed by org; upsert moved to `services/spend_upsert.py` (spend_writer was over the LOC budget). Assistant: `book_assistant_usage` books the caller's tokens (cost 0, no agent/model axis) to the user axis in a `finally` (disconnects still book) — new data, see deviations. Usage (D-07 deviation closed): `agent_usage_gate` returns the caller when `can_view_config` is denied after `can_run` (403 `permission_required` → own usage), `GET /api/agents/{id}/usage` filters `TokenUsage.caller_user_id` (gate moved from decorator to parameter dependency; GET, no CSRF); legacy owner unchanged. Per-user breakdowns (`/usage/summary` `by_agent`, `/usage/daily` agent/model axes) now come from the user's own billed `token_usages` (`services/usage_caller_breakdown.py`, `caller_user_id`, standard agents only) so they add up to the user total and owners never see runners' cost there; owner-wide agent totals only via the agent usage route (`can_view_config`). Run audit (`record_conversation_run_audit`) writes `org_id`/`tenant_id` and the Q22 split (`actor_agent_id`, `on_behalf_of_user_id`, `invoker_type`, `credential_subject_user_id`, `resource_owner_user_id`, `run_source`) into metadata; `audit_service.record_event(org_id=)`. Tests `tests/authz/test_f14_run_attribution.py` (5: chat/trigger/API owner runs + participants + usage copy; shared caller fixed/per_user under enforce), `test_f14_spend_attribution.py` (6), `test_f14_usage_access.py` (3), `test_f14_migration.py` (3), `test_f14_audit_attribution.py` (2); adjusted `test_d07_route_authz.py` (runner 200 own usage), `tests/test_spend_writer.py` (entries carry org), `tests/test_usage_aggregate.py`, `test_usage_service.py`, `test_usage_router.py`, `test_agent_runtime_profile.py` (breakdowns seeded as own `token_usages`). Review R1 applied (downgrade merge, own-usage breakdowns, migration ordering/single pass, load_only columns, required-column check, Assistant finally). Deviations/open: subagent model calls stay inside the root run's usage/spend (billed to the root caller and root agent axis; per-child spend not split); Assistant tokens now appear in `/usage/summary` totals (previously unrecorded — not legacy-identical; no price for the system model); `/usage/summary` and `/usage/daily` are not org-filtered (sum across the user's orgs); breakdown numbers now come from `token_usages` (run metrics) instead of the hook-fed `daily_spend_agent|model`, so historic rows without `caller_user_id` backfill and Assistant tokens (user total only) can make breakdown sums differ slightly from the user total; envelope projection / ownership cancellation / supervision `load_only` lists do not write audit and were left unchanged; `agent_trigger_runs.reason_code` (same m89 row) left to the 11.6 trigger-blocking work; denial/exclusion/auth-request audit events of 11.10 belong to F-07/F-08.
- [x] F-13 separately authorized run paths (11.0 table: Assistant, skill builder hidden agent, skill evaluation, MCP Apps, model test; done-when 경로별 권한 회수 후 거부) — committed `12f77460`. **Skill builder**: `services/skill_builder_run_access.load_skill_builder_run_session` (the locked private session lookup moved out of `conversation_agent_context`) re-decides every run's target in enforce mode only (legacy and shadow keep the private lookup's answer; review L2) with the confirm rule `authorize_builder_target` (improve → `skill#can_edit`, new → `cap_create_skill`; 404/403/503 per 8.4) before workspace/system model; legacy unchanged (no query). The target is read and decided before the session row lock, then the locked re-read re-decides only a changed target (review L1). Under a sealed run decision memo (prepared lease locks) it raises `unsupported("runtime_profile:skill_builder")` before deciding — the same reason `require_lease_supported` gives later — so no OpenFGA call runs under the locks and the lease falls back (`run_decisions.decisions_sealed()`). **Skill evaluation**: `services/skill_evaluation_access.SkillEvaluationAccessPlanner` (private skill load + runtime context moved from `skill_evaluation_worker_state`, now 201 LOC) adds the creator's `skill#can_edit` via `AuthzService.require` (legacy verdict ALLOW = the owner-scoped load; shadow samples) when the worker executes; denial → `SkillEvaluationExecutionError(code)` → run failed before the evaluator; OpenFGA down or `runtime_authz()` not running (RuntimeError wrapped) → `SkillEvaluationAuthzUnavailable`: failed with the retryable `authz_unavailable` message and audit `reason_code=AUTHZ_UNAVAILABLE` (the in-memory queue has no requeue, so it is not left queued; review L3/L4). Credentials stay the creator's own private bindings (owner fast path; no `credential#can_use` asked). **Model test**: `services/model_test_run.probe_registered_model`/`probe_preview_model` own the decisions + `run_model_test` (router no longer calls it; AST allowlist row moved from `routers/models.py` to this module); the model's *default* credential now also goes through the `credential#can_use` gate outside legacy (`model_access.model_test_credential`; explicit ids already did, D-10). **Assistant / MCP Apps**: no code change — already decided per build/tool call (`can_edit`, D-08) and per command (`can_invoke` in `authorize_binding`, D-08) with per-call connection re-resolution (F-05); now pinned by revoke tests. `test_f06_runtime_paths.py`: the five rows name their decision entry (`get_agent_with_eager_load`, `load_skill_builder_run_session`, `SkillEvaluationAccessPlanner`, `authorize_binding`, `probe_registered_model`) instead of `None`, plus `SEPARATE` (each entry's body calls its decision); only the E2E row stays classified. Tests `tests/authz/test_f13_{model_test,assistant,skill_builder,skill_evaluation,mcp_apps}.py` (14, incl. review fixes: shadow answer, decide-before-lock order, changed target re-decided, outage/stopped-runtime retryable failure; allowed → revoked → refused, nothing sent/built after revoke; mutations of the three new decisions caught); `test_d10_health_models_usage.py` patch target moved to `app.services.model_test_run.run_model_test`. F-10 decrypt scan: the `probe` purpose site moved from `routers/models.py` to `services/model_test_run.py`. Open: the Assistant's system model (`text_primary`) and the skill builder's are operator-chosen roles, no `llm_model#can_use` asked; skill-eval credential bindings are not re-asked `credential#can_use` (owner rows).
- [x] F-08 trigger, queue, API key and subagent dispatch decisions (11.1, 11.6; done-when "BLOCKED 기록") — committed `5acdf2c4`. **Reasons** `app/authz/dispatch_block.py`: `block_reason(exc)` walks the exception chain → `permission_revoked` (404/403 denials, `AGENT_NOT_FOUND`/`TRIGGER_NOT_FOUND`), `owner_left` / `org_archived` / `tenant_suspended` (only from owner resolution — a generic `forbidden` is not `owner_left`; `require_trigger_actor` wraps the owner's account/membership resolution in `DispatchBlockedError`, same code/status/body), `agent_archived`, `required_unavailable` / `delegation_broken` (`agent_not_ready`, by item `result`; a detail-less wrapper looks deeper), `auth_required` (`credential_binding_required`), transient `authz_unavailable`; other errors → `None` (unchanged handling). **Triggers**: Alembic `f08_trigger_run_reason_code` (down `f14_run_attribution`) adds `agent_trigger_runs.reason_code` (String 40, nullable; `TriggerRunResponse.reason_code`, OpenAPI snapshot +1 field). `app/services/trigger_dispatch_block.py` (re-exported by `trigger_service`, so the agent_runtime edges stay baselined): a claim-time denial outside legacy rolls the claim back, re-selects the trigger `FOR UPDATE`, then the in-flight check, and records a run (`failed`, `reason_code`, message `<reason>: <code>`; `authz_unavailable` → `skipped`, never counts, no audit). `agent_not_ready` → `required_unavailable` is a `failed` run, so it counts toward the auto pause like any failure) through `finish_trigger_run`, so the consecutive-failure auto pause works unchanged; in-flight/inactive triggers still skip; `run_now` returns the BLOCKED run. Same record for the F-03 required-broken path (`delegation_broken`), the invoke `AppError` branch (denied before the lease) and prepared pre-activation/lease denials (`PreparedTriggerFailedError`/dispatch denial codes); `prepared_trigger_entry._prepare` now settles (fails the queued `ConversationRun`) when `predecided_run` denies before the locks instead of leaving it queued. A BLOCKED run writes audit `run.blocked` only when the trigger's previous run did not end with the same reason (one audit + notification per streak; every run is still recorded and counted, no new pause rule) (new `audit_actions.RUN_BLOCKED`; system actor, agent target, `owner_user_id` = run account, `target_owner_user_id` = agent owner, `reason_code`, run id, metadata `invoker_type/code/source_id`) and calls `run_block_service.notify_owner_blocked(kind="trigger.blocked")` — the P7 (14.2) notification call site, log-only. Legacy: byte-identical outcomes (silent skip, F-03 message, no reason/audit). **Subagents (11.1)**: the trigger claim and the prepared trigger envelope load the tree with `AgentExecutionTarget(run=True)`, so a child owned by another member is decided by the planner as `agent:A` `can_run` (denied required child → `required_unavailable`, permitted child bound) instead of the owner-only loader's `AGENT_NOT_FOUND`; legacy unchanged (`authorize_shared_tree` answers False). **Queue**: `conversation_agent_checks.queue_dispatch_denial` replaces `agent_run_permitted`; `claim_next_input` → `conversation_run_queue_blocked.pause_blocked_queue`: a definite denial pauses the conversation queue (inputs stay `pending`, no run exists), audits `run.blocked` (`invoker_type=queue`) and commits, so the periodic recovery (skips paused queues) does not re-decide/re-audit each tick; resume re-checks `can_run` (D-01). `authz_unavailable` writes nothing. **Agent API**: unchanged from D-06/F-06 — per-request `can_deploy` → opaque 404 before any run row, required links → 409 `agent_not_ready` before the run row, prepared path fails the queued row (`AGENT_DEPLOYMENT_NOT_FOUND`). Tests `tests/authz/test_f08_trigger_blocked.py` (7: revoked owner → permission_revoked + auto pause + audit; owner left; OpenFGA down skipped; denied required subagent; required broken link; legacy silent skip; permitted foreign child bound), `test_f08_queue_blocked.py` (2), `test_f08_dispatch_block.py` (14); adjusted `test_d06_trigger_authz.py` (fire-time denial is a BLOCKED run) and `test_conversation_queue_authz.py` (denial pauses). Open: no `run.blocked` audit for synchronous Agent API denials (no run row; a second session mid-request is unsafe) — rely on the opaque 404; owner notifications are P7; queue pause carries no persisted reason column (audit only); UI reason display is G-phase; fixed pre-existing production bug: `build_subagents_config` read children after `load_agent_tree_for_execution`, whose `populate_existing` reset their lazy relationships (MissingGreenlet on `child.model`); it now re-hydrates the tree with `chat_service.get_run_agent_with_tools` (runtime eager loads) first — unpatched legacy trigger-with-subagent test added.
- [x] F-07 `ConnectionGuardMiddleware` (11.4, 11.5; done-when 허용 밖 호출 차단, 묶음 interrupt) — committed `6c9eab96`. `app/agent_runtime/runtime/connection_guard.py`: `aafter_model` (before the ToolNode) answers calls whose plan link was excluded (`permission_required`) with an error ToolMessage keeping the `tool_call_id` (siblings run), re-reads the credential rows of the remaining credentialed calls (`connection_guard_policy.read_credential_states`, tenant session; per_user rows also re-check the account's binding) and turns visible-but-unusable ones (any status but `active`/`auth_needed` — `auth_needed` keeps F-05's refresh path) and vanished per_user bindings into ONE `auth_required` interrupt; an unknown state (no tenant, row not visible) skips the pre-check (the leaf fails closed) (11.5 shape, items merged per connection, `tool_names` unioned) before anything runs; `abefore_model` (after the ToolNode, all siblings done) lifts the step's F-12 `mcp_auth_error.interrupt` artifacts (and `credential_binding_required` tool errors) into one bundled interrupt — nothing interrupts inside the ToolNode, finished siblings never re-run; `awrap_tool_call` refuses excluded names, tools unknown to the graph (`tool_not_allowed`) and re-decides FGA (`batch_check`, no memo) before every credentialed call on a row the plan decided via FGA (`permission_required`/`authz_unavailable`). Resume (`respond` decision, bare or in `decisions`): pre-call `retry` re-reads (still broken → same interrupt again), `skip` answers the calls as skipped, `cancel` answers every pending call and `jump_to=end`; post-call `retry` marks the error ToolMessages `reconnected` so the model calls again on the re-resolved run (resume rebuilds the runtime), `skip`/`cancel` likewise; answered artifacts drop their `interrupt`. `fixable_by=owner` (credential row not the actor's) never interrupts the runner: `owner_reconnect_required` refusal + owner notification (structured log until H-02), once per (run, credential). Each interrupt carries `attempt` (LangGraph derives the id from the task namespace, so a still-broken `retry` re-asks under the same id); the frontend keys card identity and resolved-interrupt bookkeeping on id+attempt. Policy modes: `interrupt` (user-facing root run), `raise` (root trigger/invoke-API runs: `is_trigger_mode`), `refuse` (subagent graphs, `include_agent_memory_file=False`: pre-call problems are `auth_required` refusals, post-call auth errors stay error ToolMessages, never an interrupt or raise). `raise` runs throw `ConnectionBlockedError(code=auth_required)` → trigger run `failed` with `error_message=auth_required` (`run_error_codes.DENIAL_RUN_ERROR_CODES` +1). Order (`connection_guard_order.py`): the interactive guard takes Deep Agents' `HumanInTheLoopMiddleware` slot by name (tail, so its `after_model` runs first) and approval moves to `GuardedApprovalMiddleware` (same merged `interrupt_on` + fs-permission policy; never offers a call the guard already answered); separate nodes keep each node's interrupt sequence deterministic. Installed in `prepare_runtime_components_impl` only while run types are enforced (`runtime_access_enforced`; legacy/shadow graphs unchanged); ordered in `build_agent_impl`. Frontend: native `auth_required` interrupt (payload `native: 'auth_required'`, set only by the adapter) → synthetic `moldy_auth_required` tool call (id `<interrupt>:<i>#<attempt>`) → minimal `AuthRequiredCard` (items, owner-notified note, retry/skip/cancel; i18n `chat.authRequired` ko+en). Tests `tests/authz/test_f07_connection_guard.py` (9, real Deep Agents graph + MemorySaver; ordering test fails when the order step is disabled), `test_f07_connection_guard_wiring.py` (5); vitest `auth-required-interrupt.test.ts`, `auth-required-card.test.tsx`, partial-args case. Deviations: the general-purpose subagent gets no guard; prepared-lease runs (`prepared_run_lease_enabled`, off) get no ConnectionGuard and rely on `LeaseBoundToolMiddleware` and the lease census (the guard class is not in the lease allow-list); `middleware_registry.py` not touched (over the 250-LOC cap; the guard is not a user-configurable type); prepare-time `credential_binding_required` stays the 409 (G-02); authz_epoch-based decision reuse not implemented (only credentialed calls on non-owned rows are re-decided); connect buttons/OAuth popup and server-side decision allow-list validation are G-03..G-06.
- [x] G-01 readiness service and API, summary cache and invalidation (ch. 12; done-when 12.1 판정표 테스트) — committed `69ef8fff` (review fixes applied). `app/authz/readiness_verdicts.py` (`ReadinessPlanner.verdicts`: the F-06 planner's classification + BatchCheck without raising, incl. the planner's effective `required` (partial enforcement forces it) so readiness and the run agree; `RuntimeAccessPlanner._assess` extracted from `plan`, behaviour unchanged), `app/services/readiness_items.py` (per-link 12.1 result: no_permission for denied/broken → unavailable for disabled tool/MCP tool, MCP `unreachable`/`disabled`, missing/archived rows, hidden model, and an inactive system (operator) credential → credential state without decryption: shared = owner's credential, per_user = run account's binding (F-05 `RunCredentialResolver.bound` re-check), missing/auth_needed/expired → connect_by_me when it is the caller's, else owner_must_connect; MCP tool links reported per server), `app/services/readiness_service.py` (run loader 404/403, chat identity 11.1, subagents one hop with child items required only when both are, 12.2 overall ready/needs_connection/limited/blocked, 12.3 visibility per item's own agent (one BatchCheck of `can_view_config` over root + children): only connect_by_me (id+name) and owner_must_connect (name) items are itemized for agents the caller cannot view, the rest counted per kind/required/result), `app/services/readiness_cache.py` (60 s per tenant/org/caller/agent summary cache incl. negative 403/404 entries; cleared after commit of inserts/deletes or updates of meaningful columns only (grants, outbox `processed_at`, memberships, bindings, credential status/owner, MCP server status/credential columns, MCP tool/tool `enabled`, model `is_visible`, link `required`/`link_status`, agent identity/model/archive, organization/tenant `settings`/`status` = catalog policy); `health_status`, `last_pinged_at`, `last_seen_at`, `updated_at` never flush). Staleness/race limits: per-process cache, other processes rely on the TTL; a summary computed concurrently with a commit may be stored right after the clear and live up to 60 s; OpenFGA changes not written via the outbox wait for the TTL; bulk updates whose values cannot be inspected flush conservatively. `app/schemas/readiness.py`, `app/routers/agent_readiness.py`: `GET /api/agents/{agent_id}/readiness?source=CHAT` `RES(agent, can_run, min_visible=can_run)` (never cached), `POST /api/agents/readiness-summary` `CANDIDATES(agent, can_run)` (≤50 ids, CSRF, 60/min per caller via the slowapi limiter, non-runnable ids omitted, no `can_view_config` checks). OpenAPI snapshot additions only. Tests `tests/authz/test_g01_readiness_table.py`, `test_g01_readiness_api.py`, `test_g01_readiness_review.py`. Open: only `source=CHAT`; G-02 reuses `present()` for the 409 body.
- [x] H-07 scheduler, evaluation worker and spend writer per company (8.8 큐 작업, 8.9 스케줄러 row; done-when 큐 항목 tenant_id 필수, 회사 혼합 배치 0) — spend: `SpendEntry.tenant_id` required (kw-only, `TypeError` otherwise); flush moved to `app/services/spend_flush.py` (`group_by_tenant` → one `tenant_session` + transaction per company, a failing company is logged and re-raised after the others commit). Evaluation: queue moved to `app/services/skill_evaluation_queue.py` (`SkillEvaluationQueueItem(run_id, tenant_id, user_id)` validated; `enqueue(run_id, *, tenant_id, user_id, reserved)`, both keyword-required). **Company rule (shared by worker and reconcile, recorded for I-06):** an evaluation run's company is its skill's company (`Skill.tenant_id`); the router enqueues `skill.tenant_id` and the requester `user.id`; a system skill (`tenant_id` NULL) runs in platform scope (reason `skill_evaluation.system_skill`). `company_session(tenant_id, user_id, maker=)` (`skill_evaluation_worker_tenants.py`, a scoped factory in the B-13 ratchet) opens `tenant_session(SessionTenant(tenant, requester))` or that platform session; the worker uses it per item, and so does the post-commit usage ledger (moved to `skill_evaluation_usage_ledger.py`). Restart reconcile: platform session `skill_evaluation.reconcile` only lists the companies of interrupted runs, then each company (system skills included) is failed in its own `company_session`, isolated (try/log, continue). Ratchet: reasons `skill_evaluation.worker`/`spend_writer.flush` removed, `skill_evaluation.reconcile`/`skill_evaluation.system_skill` added, `company_session` in `SCOPED`. Trigger dispatch was already per company (B-13 `_trigger_tenant`); now pinned by a test. Tests `tests/authz/test_h07_spend_tenant_batches.py` (4), `test_h07_skill_evaluation_tenants.py` (5: item validation, company kept, worker session = skill company + requester / platform for system skills, reconcile uses the same rule per company, failing company isolated), `test_h07_trigger_dispatch_tenant.py` (1); router test asserts `(skill.tenant_id, user)` on enqueue; mutations (single mixed flush, item tenant ignored) caught. Deviations/open: health-check and MCP health polls stay interim platform (I-06); conversation run queue untouched; evaluation runs have no `tenant_id` column (company derived from the skill); `app/scheduler.py` unchanged. committed `bd39f4fe`.
- [x] H-01 m88 access requests API and screens (10.9, 8.4, 9.7 "접근 요청", 9.7.1, 14.1, S18; done-when E2E #7) — committed `6ce83305`. Alembic `h01_access_requests` (down `f08_trigger_run_reason_code`): `access_requests` only (plan's m88 `notifications` is H-02), partial unique pending (org, resource, requester, level), `expires_at` = +30d, expiry is applied in write paths only (reads list a stale pending row as expired without updating it; no sweep job, H-07 owns schedulers). `app/authz/access_requests.py` (create/cancel), `access_request_decisions.py` (approve/deny), `access_request_queries.py` (box=inbox|outbox|handled, keyset cursor, `filter_visible(can_share)` per page), `access_request_rules.py` (levels ≤ editor, not already held, 20 pending/10 per hour → 429 `quota_exceeded` rate/user), `access_request_notify.py` (H-02 call sites, no-ops). Routes `app/routers/access_requests.py`: POST/GET `SELF`, approve/deny `RES({resource_type}, can_share, min_visible=can_share)` (404 for anyone without can_share), cancel `PRIVATE(access_requests)`. Create answers 202 with no body for missing/hidden/foreign resources and OpenFGA outages (8.4, `can_request_access`), 201 new, 200 same pending. Approve runs E-01 `share()` for the requester (same rules; approver may lower the level; an already-held ≥ level writes nothing), applies lifecycle + `revalidate_object`. New error `access_request_closed` (409); audit `access_request.create|approve|deny|cancel`. Credentials are not requestable (owner-only sharing). Legacy: only owners see resources, so requests are effectively enforce-mode. Frontend: `RequestAccessDialog` + `PermissionRequiredState` (`src/components/access-requests/`), wired into the skill studio 403 state; inbox `/settings/organization/requests` (`org-access-requests-content.tsx`) + sidebar entry; `apiFetch` reads a body-less 202 as `undefined`. E2E #7 encoded as `e2e/access-request-approval.spec.ts` (route-mocked, opt-in `E2E_ORG_AUTHZ=1`, skip-manifest rule added) — not run (no E2E/Docker here). Review R1 applied: inbox/handled scan candidate batches and derive `has_more`/cursor from `can_share`-confirmed rows only (no hidden counts/ids); create/cancel take the org row lock before dedupe/quota (org → request); E-02 member exit cancels the leaver's pending requests (audit `access_request.cancel`, `member_exit`). Tests `tests/authz/test_h01_*.py` (20), vitest 4 new files (13 tests). — opt-in E2E passed 2026-10-18 (E2E_ORG_AUTHZ=1, scripted-full 5/5)
- [x] G-03 `auth_required` interrupt and resume (11.5; done-when retry/skip/cancel) — committed `012fd56a`. `connection_guard_resume.py`: a resumed guard node counts the answers it was resumed with before asking, replays all of them and the latest wins, so `skip`/`cancel` hold even when the connection was repaired between ask and answer (and `cancel` after a still-broken `retry` is not lost); `retry` re-reads, and the resume path re-resolves the agent context (F-05 credentials). `input.respond` records `metadata_json.resume_decisions` (`interrupt_id`, `kind=auth_required`, `decision`, `attempt`) on the resume run; the state API returns the pending interrupt with its latest `attempt` (reload). Queue claims already wait while a run is interrupted. Frontend AuthRequiredCard sends `attempt`. The private LangGraph scratchpad read is guarded (missing key / unreadable shape → first-answer-only fallback, logged) with a canary `tests/authz/test_g03_scratchpad_canary.py`. Tests `tests/authz/test_g03_auth_resume.py`.
- [x] G-06 server-side resume decision check (11.5; done-when 허용 목록 밖 결정 거부 테스트) — committed `012fd56a`. `app/routers/conversation_resume_decisions.py`, called from `validate_resume_payload` (protocol `input.respond`) and `require_allowed_rest_resume` (`POST /messages/resume`, both the interrupted-run and the legacy-trace branch; the latter reads the checkpoint; REST `Decision` gained optional `attempt`, schemas moved to `schemas/conversation_resume.py` and re-exported) before anything touches the graph: `auth_required` = one `respond` with `retry|skip|cancel`, optional `attempt` must equal the pending one (409 `STALE_INTERRUPT`); HITL = one decision per action, type inside that action's `allowed_decisions`, `edit` needs `edited_action.args` (name optional, edit-by-index fill unchanged), `scope` only `session` on approve, no unknown keys; unreadable review config fails closed; other kinds (`ask_user`) unchecked. Refusals are 422 `INVALID_RESUME_DECISION`, no resume run. Frontend: an action without a readable review config gets an empty allow-list and the approval card shows no buttons, only `chat.approval.noReviewConfig`. Applies in every AUTHZ_MODE (valid answers unchanged). Tests `tests/authz/test_g06_resume_decisions.py`.
- [x] H-02 notifications API and bell (14.2, 13.8, S25; m88 `notifications`) — committed `42c3c595`. Alembic `h02_notifications` (down `h03_audit_tenant_index`): `notifications` (org_id NULL = company-level row, tenant_id, user_id, kind CHECK of the 14 kinds of 14.2, target_type/target_id/actor_id columns, payload = target name at write time + enum fields only, link, read_at; indexes (user_id, read_at, created_at) and (user_id, kind, target_id)). Writer `app/authz/notifications.py`: `notify` in the caller's session, same kind+target+recipient+org merged within 1 h (24 h for `connection.owner_needed`; refreshed, unread again), per-recipient 90-day prune on write, the actor never notifies themselves; `resource_managers` = owner + active `manager` grants (users, group members) of active org members (legacy: owner). Reads `app/authz/notification_queries.py`: own rows of the current tenant in the current org (+ company-level rows for tenant admins), 90-day window, keyset cursor; every target re-checked with `filter_visible(min_visible)` (legacy: owned in org) — inaccessible targets lose id, name and link; an OpenFGA outage masks instead of failing. Routes `app/routers/notifications.py` (all `SELF`): GET list (limit ≤ 50, default 20, `unread`), GET `/unread-count` `{total, by_org}` (by_org: the caller's active orgs of the current tenant), POST `/{id}/read` 204, POST `/read-all` (current bell scope), DELETE `/{id}` 204; foreign rows 404. Producers: `access_request.created` (managers, payload level; link to the requests inbox) / `.decided` (requester; the approval's share sends no `grant.received`, `share(notify=False)`), `grant.received` (E-01 share to a user principal only), `ownership.transferred` (new owner; previous owner too when a governor moved it, not for member exit), `delegation.broken` (F-03 `_break`, agent managers; payload reason + linked resource type only), `trigger.blocked` (F-08, once per streak, now written before `finish_trigger_run` commits; payload reason + trigger name), `connection.owner_needed` (F-07 `notify_owner` now async: per (run, credential) memo kept, credential owner, own session, failures logged never raised). Not produced (no source event yet): org.invitation (the invitee is not an org member and the token is only shown to the inviter), tenant.*, marketplace.*, usage.limit_warning, agent_memo.proposal — kinds are in the CHECK. Frontend: `NotificationsBell` in `sidebar-utility-footer.tsx` (badge 99+, accessible name with count, 60 s polling + focus refetch, list fetched on open, click → read + link, mark all read, empty/loading/error states), keys `['notifications', orgId, ...]` purged by the 13.11 switch (not retained), i18n `notifications` (root namespace) ko+en. Review R1 applied: fixed merge window (merges keep `created_at` and `read_at`; tenant filter on company-row merges), PostgreSQL `pg_advisory_xact_lock` per merge key (SQLite serializes writers), per-recipient prune at most once a day per process, managers cached per validator run, org-less credential → owner's only active org (else skipped, logged), actor hidden with a masked target, F-07 memo marked only after a successful write with a 2 s cap, no `grant.received` on a no-op re-share. Tests `tests/authz/test_h02_*.py` (5 files, 25), F-07 owner test updated to async; vitest 2 new files (5 tests).
- [x] G-02 chat start/send/resume `409 agent_not_ready` (12.2, 12.3; G-01) — committed `9e864e64`. `app/services/readiness_gate.require_agent_ready(db, context, agent_id)`: while any run type is enforced, runs `readiness_service.assess_agent` (request decision memo) and raises `agent_not_ready` with the 12.3 `present()` body (`overall`, `identity_mode`, `items` with per-agent `can_view_config` visibility: runners get counts, no hidden ids/names) only when overall is `blocked`; `needs_connection`/`limited` start (F-07 guard asks in-run, optional links excluded by F-06); legacy/shadow assess nothing. Wired in `chat_execution_preparation.prepare_executable_conversation` right after the `can_run` decision (covers REST send/resume, protocol `run.start` incl. queued enqueue, `input.respond`, fork, edit/regenerate) and in `chat_run_entries.start_conversation_with_message` before `create_conversation` (`agent_checked=True` skips both). One body helper `error_codes.agent_not_ready(items, identity_mode=None)` now also builds the F-03 (`broken_links.require_runnable_links`) and F-06 (`RuntimeAccessPlanner.plan`) refusals (their item shapes unchanged). Resume exit: `chat_service.prepare_resumed_conversation` (protocol `input.respond`, REST `/messages/resume`) skips the gate when every answer ends/abandons the run (`readiness_gate.abandons_run`: `auth_required` `cancel`, or HITL decisions all `reject`), so a user can always leave a pending interrupt; retry/skip/approve/mixed keep the 409 (F-06's in-run 409 for a denied required link still applies on resume). Tests `tests/authz/test_g02_agent_not_ready.py` (8; gate-disabled mutation fails 4), `test_g02_resume_exit.py` (17; exemption-disabled mutation fails). Not gated: `POST /api/agents/{id}/conversations[/draft]` (no run), triggers/API (F-08 dispatch). Resume files `conversation_agent_protocol_resume*.py` untouched.
- [x] G-04 frontend readiness badge, check panel, auth card connect, per-user connection dialog, required checkbox (13.4–13.7, S21/S22/S23/S29; done-when E2E 4, 5번) — committed `0266149e`. Frontend: `src/lib/{types/readiness.ts,api/readiness.ts,api/credential-bindings.ts,api/readiness-summary-batcher.ts,query-keys/readiness.ts,hooks/use-readiness.ts}` (`['readiness', agentId]`, summaries `['readiness','summary',id]`; the batcher sends one `POST /api/agents/readiness-summary` per tick and per 50 ids, so AgentCard fetches its own badge without touching the dashboard). `src/components/readiness/`: `ReadinessBadge` (12.2 rules: ready none, `연결 필요 N`, `N개 제외`, `사용 불가 · 사유`) in `AgentCard`; `ReadinessPanel`/`ReadinessDialog` (S21 items with StatusChip, required/optional, actions via `readiness-actions.ts`: per-user `connect_by_me` → `CredentialBindingDialog`, other `connect_by_me` → /settings/credentials, `owner_must_connect` → notify owner, `no_permission` → H-01 `RequestAccessDialog` on the item or, when counted only, the agent; "확인" (plan "연결하고 시작"; closes the panel) active on ready/limited); `CredentialBindingDialog` (S23: my credentials of the accepted `definition_key`, or create one with `CredentialCreateModal` and bind; `credential_type_mismatch`/`credential_mode_not_per_user` toasts); `AgentReadinessGate` on the chat screen (folded card when not ready, panel opens itself on `409 agent_not_ready`, announced by `lib/chat/agent-not-ready-signal.ts` from the protocol transport fetch wrapper and the SSE `onopen`, since the SDK only throws a plain Error). `AuthRequiredCard`: "연결하기" (`readiness/auth-card-connect.tsx`) only when the agent's S21 check has a per-user bind action for one of the `fixable_by=me` items, then answers `retry` after a connection; otherwise a note + link to /settings/credentials and "다시 시도" stays; a re-ask (`attempt` > 1) says it is still not connected. The 409 signal carries the transport's agent/conversation (or the SSE path's) and the body's `agent_id` (sent only to config viewers, also on `GET readiness`); the gate opens only for its own agent (`signalMatches`). S29: `AgentLinkRequiredProvider`/`LinkRequiredToggle` (`components/agent/link-required-context.tsx`) put a "필수" checkbox on selected tools/MCP tools/skills (`tools-skills-current-column.tsx`) and subagents (`sub-agents-dialog.tsx`) in the settings form mode (draft `requiredLinkIds` → `required_link_ids`); creation and the visual canvas show none. StatusChip gained `needs_connection`/`limited`/`blocked`/`no_permission`/`archived`/`suspended`. `readiness` is a root i18n namespace; the agents layout also loads `credentials`/`shared` for the in-place credential create. H-01 a11y fix: `request-access-dialog.tsx` radios get a sibling `<label htmlFor>` + `aria-label`/`aria-describedby` (baseline change = chat-image.tsx line shift 49→66 only). Backend (minimal support): `AgentUpdate.required_link_ids` (replaces the `required` flag of every link after the relinks; omitted = unchanged) and `AgentResponse.required_link_ids` (`agent_update_service.linked_targets`); readiness `credential.definition_key` on `connect_by_me` items only; `POST /api/agents/{agent_id}/readiness/notify-owner` `RES(agent, can_run, min_visible=can_run)` + CSRF + 10/min → `connection.owner_needed` for the owner credentials behind `owner_must_connect` items (`ReadinessItem.owner_credential` = (id, company), never presented; `notification_events.owner_needed_count` reads/writes in a tenant session of the owner's company like the runtime path, 24 h merge), 202 `{notified}` = notices written (0 → the panel says it couldn't notify and keeps the button). `agent_not_ready` details gain `agent_id` for config viewers. Also fixed H-02 `notifications-bell.tsx` `text-[10px]` → `moldy-ui-micro`. OpenAPI snapshot additions only. Tests: backend `tests/authz/test_g04_readiness_actions.py` (5; G-01 tests updated for `definition_key`); vitest `tests/components/readiness/*` (4 files), `tests/components/agent/link-required-toggle.test.tsx`, `src/lib/api/__tests__/readiness-summary-batcher.test.ts`, `src/lib/chat/__tests__/agent-not-ready-signal.test.ts`, auth card/agent card/draft tests extended. E2E #4/#5 encoded as `e2e/readiness-connect-start.spec.ts` (panel connect → start; 409 opens the panel) and `e2e/readiness-auth-card-connect.spec.ts` (restored auth card → connect → `input.respond` retry), route-mocked readiness/credential/binding answers over real scripted agents, opt-in `E2E_ORG_AUTHZ=1`, skip-manifest rules added — not run. Open: OAuth credential creation completes through the existing credentials flow (G-05 `use-oauth-completion` not wired); per-user `owner_must_connect` items with no owner credential cannot be notified (no credential to target). — opt-in E2E passed 2026-10-18 (E2E_ORG_AUTHZ=1, scripted-full 5/5)
- [x] H-05 screen permissions (13.9 table) and shared error display (13.1; done-when 표의 모든 화면에 권한별 Vitest) — committed `bd63b9b8`. Backend (minimal): `GET /api/resources/{tool|mcp_server|credential|skill}/permissions?ids=` (`app/authz/resource_permissions.py`, `app/routers/resource_permissions.py`; `CANDIDATES(type, can_use)`, ≤100 ids; only current-org rows passing the list's `can_use` confirmation are answered, hidden/foreign/missing ids omitted; legacy = owner all / platform use+view; enforce BatchCheck, outage 503; not enforced: the legacy rule answers and shadow samples only the first 3 rows) instead of editing the over-cap tools/MCP/credential/skill routers; `OrganizationSummary.capabilities_enforced` (`enforces('organization')`) so create buttons stay visible while capabilities are not enforced. OpenAPI snapshot additions only; predicates 0 additions. Tests `tests/authz/test_h05_resource_permissions.py` (7). Frontend core: `lib/api/error-ui.ts` `mapApiErrorToUiState`/`isNotFoundError` (code-based), `components/shared/api-error-state.tsx` `ApiErrorState` (401/403 permission_required → H-01 PermissionRequiredState with request access/capability_required/org_access_denied/tenant_suspended/404/409 agent_not_ready → ReadinessDialog/429/503 retry) + `useApiErrorMessage` for toasts, all 8 route `error.tsx` boundaries use it, `lib/authz/{permissions,use-permission,use-resource-access}.ts`, `lib/hooks/use-resource-permissions.ts` (`useResourcePermissions` under `['grants', type, 'permissions', …]`, `useCapability`), `components/agent/agent-access-context.tsx`; fail closed in the UI: until the permissions answer arrives or when it fails, rows not owned by the caller keep only use/view (edits/deletes hidden) and `PermissionsLoadError` shows the shared state (503); i18n `common.apiError`, `common.readOnly`. Screens: agent settings + visual settings route gate (`agent-settings-gate.tsx` via new layouts; runner/no can_view_config → permission notice with request access; read-only fieldsets, Save/Delete/Share/Fix/API tabs by can_edit/can_delete/can_share/can_deploy; page split to ≤250), chat header/agent context popover settings entry hidden for runners, triggers create by can_run + stop reason text (`trigger-stop-reason.tsx`), skills list/studio/builder (can_view/can_edit/can_share/can_delete, cap_create_skill, read-only tab shell), MCP cards/detail (server can_edit/can_delete/can_share, cap_register_mcp, 403 request access), tools (test can_edit, cap_create_tool, 403 request access), credentials (shared = metadata only), models (add/edit operator only — previously shown to all), marketplace (can_install/can_manage via `marketplace-access.ts`, publish buttons by cap_publish_marketplace everywhere, detail split), artifacts (load errors/other-org ids → not found), agent-api (candidates error state, page split), conversation share link disabled when `allow_public_share_links` is off (`share.publicLinksDisabled`). Tests: `tests/pages/authz/*` (16 files, one per screen, owner/editor/viewer/runner/none as the table defines), `tests/unit/authz/*`, `tests/components/shared/api-error-state.test.tsx`; frontend suites 362 files / 2035 tests green, tsc/eslint/prettier clean. Follow-up (committed `a9750810`): `GET /api/resources/marketplace_item/permissions?ids=` (`app/authz/marketplace_permissions.py`, `CANDIDATES(marketplace_item, can_view)`; legacy/shadow = catalog candidates + `can_view_item`/`can_install_item`/`can_manage_item`, shadow samples first 3 rows; enforce = `CatalogGate` candidates confirmed by `can_view` + one BatchCheck, disabled never `can_install`, outage 503; hidden/foreign/missing omitted; tests `tests/authz/test_h05_marketplace_permissions.py` (8)); marketplace list/detail use `useMarketplaceItemAccess` (`useResourcePermissions('marketplace_item')`), management hidden until answered/on error, install hidden only when the answer lacks `can_install`. Open: MCP per-tool `can_invoke` not shown (no mcp_tool answer); `/settings/schedules` (442 LOC) row link to agent settings not permission-gated; usage/audit unchanged (already personal); viewer read-only fieldset also disables dialog-opening buttons.
- [x] G-05 frontend interrupt paths and shared OAuth receiver (13.6 five locations, 11.5; done-when 새로고침·재연결 후 카드 복원 테스트) — committed `217fbdf4`. (1) `lib/types/index.ts`: discriminated `AuthRequiredInterruptPayload` (`type: 'auth_required'`, reason/items/attempt) in `InterruptPayload`/SSEEvent. (2) `lib/ag-ui/chat-run-consumer.ts`: `moldy.interrupt` already passes the payload through, so the kind is kept (test only, no code change). (3) `use-chat-runtime.ts` (legacy runtime: assistant panel, builder, legacy main chat, active-run attach = reconnect): `case 'interrupt'` normalizes with `toStandardInterruptPayload` (`auth-required-interrupt.ts`) into the native standard payload → `moldy_auth_required` card + coordinator. (4) `auth-required-interrupt.ts`/standard-interrupt path: items carry `credentialId`/`connectKind`; `authRequiredDecision(response, attempt)` = `{type:'respond', message, attempt}`; `hitl-interrupts.upsertPayload` keeps the higher `attempt` when reload state and a replayed stream disagree on one interrupt id. (5) `lib/hooks/use-oauth-completion.ts`: `useOAuthCompletion` (window `message` from the callback page, origin = app or API origin, credentialId match, invalidates credentials + detail + readiness) and `useOAuthAuthorize`/`openOAuthPopup` (popup opened inside the click, then `POST /api/oauth2-credential/auth/{id}`); used by the MCP wizard (listener moved out, `isOAuthCompletedMessage` re-exported from form-state), `CredentialCreateModal` (`authorizeOAuth` opt-in: OAuth-type credential → popup after create, `onCreated(id, {oauthStarted})`), `CredentialBindingDialog` (new or non-active OAuth credential → bind then popup, waits for completion before `onConnected`) and `AuthRequiredCard` (`fixable_by=me` + `connect.kind=oauth` + `credential_id` → `AuthCardOAuthConnect` "{name} 연결하기" → completion → `retry` with attempt; other items keep G-04 `AuthCardConnect`). `HiTLContextValue.agentId` (v3 section) so side chat binds on its own agent. `BUILDER_TOOLKIT` registers `moldy_auth_required` (the shared legacy runtime normalizes there too). Backend (state API gap): `streaming._interrupt_to_standard_chunk` passes `auth_required` through as `{interrupt_id, **value}` — it used to drop it, so the legacy SSE event, reconnect synthesis (`pending_input_requested_events`), the state API checkpoint fallback and the legacy REST resume check (G-06 fail-open) never saw it. i18n `chat.authRequired.{connectOAuth,oauthWaiting,oauthPopupBlocked,oauthStartFailed}`, `credentials.create.toast.{oauthPopupBlocked,oauthStartFailed}`, `readiness.binding.oauthWaiting` (ko+en). Tests: backend `tests/authz/test_g05_auth_interrupt_wire.py` (5); vitest `lib/chat/__tests__/use-chat-runtime-auth-interrupt.test.tsx` (legacy live, resume, attach-replay reconnect), `lib/chat/langgraph-runtime/__tests__/auth-required-restore.test.tsx` (v3 reload from thread state with attempt 2; reconnect re-synthesis → one card; stale replay keeps the re-ask), `lib/hooks/__tests__/use-oauth-completion.test.tsx`, `components/chat/tool-ui/__tests__/auth-required-card-oauth.test.tsx`, `tests/components/readiness/credential-binding-dialog-oauth.test.tsx`, extended `auth-required-interrupt.test.ts`, `chat-run-consumer.test.ts`. E2E `e2e/org-authz-auth-card-oauth.spec.ts` (reload restores the card, OAuth popup, completion → `input.respond` retry attempt 2), opt-in `E2E_ORG_AUTHZ=1`, skip-manifest rule added — not run. Review fixes (applied): legacy reload — `services/chat/auth_interrupt_cards.py` (hooked in `services/chat/interrupts.py`) rebuilds the `moldy_auth_required` card on the latest assistant message from the newest record's auth payload (latest attempt) until the run moved on (a newer assistant message) or the asked calls carry a resolved guard answer (`skipped`/`cancelled`/`reconnected`); the legacy runtime answers a hydrated single-action card without a coordinator (`lib/chat/legacy-pending-interrupt.ts`; other interrupt ids still `InvalidStateError`); legacy SSE interrupt id is now the LangGraph `Interrupt.id` (falls back to `ns`; it was always ""); `CredentialBindingDialog` listens before binding (bound + authorized in either order), a failed binding closes the popup (`CredentialCreateModal.onCreated` passes the popup); `_oauth_callback_html` never posts to `"*"` — the start request's origin, else `settings.cors_origins_list`, JSON escaped for `<script>`. Tests added: `tests/authz/test_g05_legacy_auth_card.py` (3), `test_g05_auth_interrupt_wire.py` (+3: interrupt id, callback origins, script escape), `tests/test_hitl_wire.py` mock interrupts set `id=None` (ns fallback). Deviations/open: excluded_capabilities banner and permission_required group rendering (13.6 last two bullets) not done (backend does not emit excluded_capabilities on run start yet); pre-existing >250-LOC files touched minimally (`use-chat-runtime.ts` +2, `hitl-interrupts.ts` +3, `chat-runtime-section.tsx` ±0, `types/index.ts` +14). — opt-in E2E passed 2026-10-18 (E2E_ORG_AUTHZ=1, scripted-full 5/5)
- [x] F-06/G-05 follow-up — `excluded_capabilities` run-start event + banner (13.6 bullet 7; F-06 and G-05 open items) — committed `17d75508`, `27f53b6c`. Backend: `event_names.EXCLUDED_CAPABILITIES = "moldy.excluded_capabilities"`; new `app/agent_runtime/run_start_events.py` (`head_event_specs` now builds every v3 stream-head event — subagent_names, memory_recalled, skill_draft, excluded_capabilities — so `langgraph_streaming.py` shrank 250 → 241 LOC; `excluded_capabilities_payload` rebuilds `{items: [{type, reason, count}], total}` from `type`/`reason` only, so no resource id or other key can reach the wire; `excluded_capabilities_of(cfg)` reads `cfg.access_plan`). v3: emitted once after `running` with stable id `<run_id>:excluded_capabilities` (persisted → replayed on reload/reconnect, same contract as `moldy.subagent_names`); legacy SSE (`streaming.py`, pre-existing >250 LOC, +3): named event right after `message_start` (persisted, replayed by the active-run attach). Both only when non-empty; root plan only (children's exclusions are not surfaced). Not a memory event (no redaction matcher). Both stream bodies (`langgraph_stream_execution`, `agent_stream_execution`) pass `excluded_capabilities_of(cfg)`, so chat, prepared leases and the Agent API stream carry it (Agent API SSE clients see one extra ignorable event, counts only). Frontend: `lib/stores/chat-excluded-capabilities.ts` (per-conversation `{runs, notice}`: every run start — v3 root `lifecycle: running`, legacy `message_start` — clears the notice so a later clean run hides the banner; runs are kept in arrival order so a replay of an older run never resurrects its notice, and a replay of the latest run keeps a dismissal; no-conversation legacy panels share one slot; the banner subscribes to its own conversation via `selectAtom`), `lib/chat/excluded-capabilities.ts` (parser), `langgraph-runtime/excluded-capabilities-events.ts` (`useChannelEffect` on lifecycle+custom with replay, run ids from the stable event-id prefix, wired in `use-stream-runtime-messages.ts`), legacy `use-chat-runtime.ts` case (+7), `SSEEvent` type (+2), `components/chat/excluded-capabilities-banner.tsx` (one dismissible `role=status` line under the memory chip in `assistant-thread.tsx`, status-warn tokens, per-type count chips with reason broken|denied, request-access hint when any denied), i18n `chat.excludedCapabilities.*` (ko+en). Tests: backend `tests/agent_runtime/test_excluded_capabilities_event.py` (10: v3 emitted once/stable id/persisted, absent for None/[], legacy after message_start, absent for None/[], no ids/extra keys, both runners pass the plan's exclusions); vitest `excluded-capabilities.test.ts`, `excluded-capabilities-banner.test.tsx`, `langgraph-runtime/__tests__/excluded-capabilities-events.test.tsx` (replay restore, dismissal kept, run N excluded → run N+1 clean clears → replay of N stays cleared, subgraph lifecycle ignored), `use-chat-runtime-excluded-capabilities.test.tsx` (live, attach replay, clean run clears + older replay ignored). Open: 13.6 bullet 8 (`permission_required` ToolMessage group rendering + request-access link) not done — the Connection Guard refusal carries no resource id (by design, L2), so the link target needs a product decision.


## P7 checklist

- [x] H-03 m89 organization audit and usage (14.1, 9.6 audit/usage rows, 9.7 `/api/tenants/{tenant_id}/audit-events`, S26; done-when `scope=org` 테스트) — committed `f046ac86`, `e592e549` (review R1 applied). F-14 `f14_run_attribution` already owns the m89 columns; Alembic `h03_audit_tenant_index` (down `h01_access_requests`) adds `ix_audit_events_tenant_created (tenant_id, created_at, id)` and rebuilds `ix_audit_events_org_created` as `(org_id, created_at, id)` (full cursor key). **Write side** (`app/services/audit_org_scope.event_org_scope`, called by `record_event`): explicit `org_id` → for org-scoped targets (agent, skill, tool, mcp_server, credential, conversation, trigger, group, org_invitation, marketplace item/installation, agent_deployment, agent_api_key, skill_builder_session) the target row's own `org_id` (identity map, else one `(tenant_id, org_id)` column select — never private payload columns, no autoflush; active org A acting on an org B resource → B; vanished/system rows never fall back to the session org, only to `metadata["org_id"]`) → the organization the event is about (`target_type="organization"` or `metadata["org_id"]`, same company only; fills an empty `tenant_id`) → for other targets only, the organization of the session's context (`app.database` remembers `org_id` from `OrgContext` in `tenant_session(s)`/`bind_tenant` under its own `session.info` key, never the RLS scope; two orgs → none). **Read side**: `app/authz/org_oversight.load_org_oversight` (`can_view_audit`/`can_view_usage` = company admin/auditor, or org admin/auditor role held directly or via a group, membership not required; neither + non-member 404, member 403 `permission_required`; builder is not oversight). `GET /api/audit-events?scope=org` (current org via `get_org_context`) and `GET /api/orgs/{org_id}/audit-events` (addressed org, no cookie dependency; shared `routers/audit_filters` filters/cursor) — both mask `ip_address`/`user_agent`; `GET /api/tenants/{tenant_id}/audit-events` (`can_view_tenant_audit`, optional `org_id`, full client details). All pinned through `audit_listing.tenant_scope_conditions` (tenant and org, fails closed without a checked filter). `GET /api/orgs/{org_id}/usage`, `/usage/members`, `/usage/agents` (`can_view_usage`; F-14 org-keyed `daily_spend_user`/`daily_spend_agent`, window 30 days default / 365 clamp; standard agents only). `agent_memo.*` audit metadata verified: ids, length, enabled flag and decision only, no memo text. OpenAPI snapshot: +5 paths, `scope` enum gains `org`; company route inventory gains `GET /api/tenants/{tenant_id}/audit-events` (SELF), no longer deferred. Tests `tests/authz/test_h03_org_audit.py`, `test_h03_audit_attribution.py`, `test_h03_org_usage.py`. **Gaps:** `scope=all` still returns every event to super users — the 14.1 rule that SaaS operators see only company-operation events (`tenant.*`, authz sync) and not in-company resource/share events is not enforced; daily CSV/JSON audit export (14.1, Q23); tenant usage/limits views (H-06); no backfill of `org_id` on historic `audit_events` rows. Retention: E-08 `audit_retention_days` purges by `tenant_id`; events whose org is resolved from an unbound session now also get `tenant_id`, so they fall under company retention.
- [x] H-06 usage limits (8.7, 8.4 quota row, 9.7 `/api/usage-limits`, `/api/tenants/{tenant_id}/usage|limits`, S08·S10·S26; done-when 단계별 차단과 `scope` 표시, 위 한도 초과 저장 422, 80%·100% 알림, 진행 중 실행 정지) — committed `9b4da3b8` (review R1 applied). No migration (m78/m81 columns: `tenants.limits.monthly_spend_limit|member_limit|concurrent_run_limit` + new key `request_rate_limit`, `organizations.monthly_spend_limit`, `organization_members.monthly_spend_limit`, `agents.monthly_spend_limit`; org policy `settings.default_member_limit`). **Period:** `app/tenancy/limit_usage.py` counts whole UTC days (the F-14 aggregates are UTC-day rows) from the first UTC midnight at or after local month start (`tenants.settings.timezone`, default Asia/Seoul) up to the same point of the next month; `resets_at` is exactly that instant (Seoul 09:00 KST on the 1st, New York the 2nd 00:00 UTC), so window and `resets_at` always agree (reset up to a day after local midnight, no partial boundary day). Used from `daily_spend_user` (tenant/org/user-in-org) and `daily_spend_agent`. **Checks:** `limits.py` `UsageLimits.check/blocking/states` (per-subject 10 s cache only while every tier is < 80%; ≥ 80% reads fresh; cleared on save); `admit_run` in `create_run_from_locked_proof` before the run row: company concurrent runs → request rate → spend, company first; 429 `quota_exceeded` details `kind`, `scope`, `resets_at`, plus `limit`/`used` only when the caller may see that scope's amounts (`usage_limit_queries.can_view_amounts`: own user limit; company scope with company admin/auditor; org/agent with company or org admin/auditor); resumes are not refused at start. **Overshoot bound:** spend is booked when a run's usage is reported and flushed by the spend writer within 5 s, so a subject may pass a limit by the in-flight runs' usage plus ≤ 5 s of booked spend (and, below 80%, what is booked within the 10 s cache). **In-run:** runtime preparation (`with_usage_guard(middleware, cfg)`, every entry incl. Agent API, prepared leases, resumes, after restart — no process registry) asks `UsageLimits.states` for the subject (company, org, billed `org_context.user_id`, root agent) and installs `UsageLimitGuardMiddleware` on root and child runtimes whenever any tier has a limit; children carry `AgentConfig.usage_root_agent_id` (facade contract fixture +1 field) so a subagent uses its root's agent tier; `abefore_model` ends the graph with "사용 한도에 도달해 멈췄어요." (`additional_kwargs.moldy_stop`); failed lookups/checks log and let the run through. **Capacity:** `limit_capacity.py` — members (non-removed) counted under the company row lock each caller already holds (company-first: add_member, company/org invitation acceptance, registration; no re-lock — the lock-order ratchet forbids a parent `FOR UPDATE` after a child) on add/invite/accept/open registration; active runs = queued/running/canceling; rate = run starts per minute per company, per process, only while `rate_limit_enabled`. Triggers refused by a quota → F-08 BLOCKED reason `quota_exceeded`, TRANSIENT → recorded `skipped` (never auto-pauses; legacy: skipped without reason code). **Alerts:** `limit_alerts.py` `usage.limit_warning` at 80/100% (company+org → company admins, company-level rows; user → org admins + the user; agent → org admins), once per scope/month/threshold via a derived target id, own company session, 2 s cap. **Writes:** `limit_writes.py` locks company → org (→ agent), `If-Match` on the versioning row (company / org (also for user limits and the member default) / agent `updated_at`; ETag on GET and PUT; missing/stale → 409 `stale_version`), narrowing org ≤ company and user/agent/default ≤ min(org, company) → 422 `limit_exceeds_parent` {scope, parent_scope, parent_limit}, bumps the row, audit `policy.limits_update` {scope, before, after}; views `limit_settings.py` (`allocated` = sum one tier below; self reads without `can_view_usage` get no `parent_limit`). Fairness `fair_share.py`: the evaluation queue pops the oldest item of the company running the fewest; released by a task done-callback (also when cancelled before running). Access `app/authz/usage_limit_access.py` (tenant: read `can_view_tenant_usage`, write `can_manage_tenant` in single_tenant only — SaaS operators keep `/api/admin/tenants`; org: read `can_view_usage`, write company admin; user: self or `can_view_usage`, write `can_manage_members`; agent: read `can_view_usage`, write `can_manage_policy`; hidden agents 404). Routes: GET `/api/usage-limits` SELF, PUT GOVERNANCE + If-Match (`?scope=&id=&org_id=`), GET `/api/me/usage-limits` SELF, GET `/api/tenants/{tenant_id}/usage` + `/limits` SELF, GET/PUT `/api/orgs/{org_id}/limits` (member default, `can_manage_policy`, If-Match). Inventory: H06_ROUTES (7, count 43→50), out of DEFERRED_PATHS; OpenAPI snapshot additions only; error `limit_exceeds_parent` 422; bell shows `usage_limit` targets as accessible. Legacy: with no limit set nothing is refused and no guard is installed. Tests `tests/authz/test_h06_*.py` (8 files). Open: concurrent-run/rate checks are not serialized and rate is per process; the spend of in-flight runs is only seen once booked; fairness covers the skill-evaluation worker only (triggers are individual APScheduler jobs, the conversation queue is per conversation).
- [ ] H-08 company SSO — OIDC + LDAP/AD (10.10, 9.7 domains/sso-connections/auth sso rows, S30; done-when 모의 IdP·테스트 LDAP) — committed `aebe2589`. Alembic `h08_sso_hardening` (verified-only domain uniqueness, `sso_connections.trust_email_claim`, `refresh_tokens.auth_method`; B-15 tables otherwise reused; policy in `tenants.settings.sso` {mode off|optional|required, break_glass_user_ids ≤2}). `app/auth/sso/`: `domains.py` (IDNA normalization, `TxtResolver` interface, default dnspython — already locked transitively, not a direct dep), `oidc.py` (discovery issuer must equal the configured issuer, HTTPS endpoints, code+PKCE S256 exchange with client_secret_basic default, ID token: asymmetric alg from the issuer JWKS, iss/aud/exp/iat/sub required, nonce, azp for multi-aud), `http.py` (`outbound_http_client(https_only=True)`, no SSRF public guard because company IdPs sit on private networks; `TRANSPORT` test seam), `state.py` (state/nonce/verifier in a signed HttpOnly cookie on `/auth/callback`, SameSite=Lax, 10 min), `ldap.py` (`LdapDirectory` interface, RFC 4515 escaping, ldaps:// or StartTLS required; **ldap3 is not a dependency → default directory answers 503 `ldap_unavailable`; plan names authlib/ldap3 as new deps — not added**), `ldap_login.py` (10 attempts / 5 min per connection+username in-process, empty password never binds, uniform `invalid_credentials`), `secret_box.py` (client secret / bind password sealed with the company data key when `TENANT_DATA_KEYS_ENABLED`, else instance key; AAD sso_connections/row/column), `provisioning.py` (identity by (issuer, subject); email auto-link and JIT only for a domain verified by the connection's own company, `email_verified=false` refused, platform operators never auto-linked), `membership.py` (JIT company + org membership; `group_role_mapping` {idp group: {org_id, role, group_id?}} additive on every login, default org + `default_role` when nothing maps; suspended/removed never re-activated), `policy.py` (password-login gate: any SSO-required company without break-glass → 403 `sso_required`, break-glass logins audited `auth.sso_break_glass_login`). Routes (inventory H08_ROUTES, DEFERRED_PATHS cleared for these): `GOVERNANCE` + `can_manage_tenant` (reads too; member 403, foreign 404) `GET|POST /api/tenants/{id}/domains`, `POST /domains/{id}/verify`, `DELETE /domains/{id}`, `GET|PUT /api/tenants/{id}/sso-policy` (**new path, not in 9.7**: required needs an active connection, a verified domain and 1–2 active company admins as break-glass; returns `password_members` for the S30 notice), `/sso-connections` CRUD + `/{id}/test` (OIDC: authorization URL + test transaction cookie, callback records pass/fail and redirects to `/settings/company/sso?test=…&result=…` without signing in; LDAP: immediate directory check returning the profile) + `/{id}/activate` (only `tested`); auth-relevant edits send tested/active back to `draft`; last active connection / last verified domain cannot be removed while required. `PUBLIC`: `POST /api/auth/sso/discover` (verified domains of active companies only; issues the login CSRF cookie+token), `GET /api/auth/sso/{id}/start` (302), `GET /auth/callback/{id}` (302, state instead of CSRF), `POST /api/auth/ldap/{id}/login` (`X-Login-CSRF` double submit). Success mints the existing JWT cookies; audit `auth.sso_login` success/failure, `tenant.sso_*`. Inventory: 302 routes need no response model, PUBLIC routes skip the session-CSRF check. Frontend: email-first discovery on email blur in the login page (password form kept for break-glass), `SsoLoginOptions` (continue with IdP / LDAP form), `sso_error` notice, `sso_required` message; `/settings/company/sso` (domains, connections + test/activate, policy) + sidebar entry for company admins; i18n `auth.sso`, `auth.errors.ssoRequired`, `orgAdmin.sso`, `appSettings.nav.companySso` ko+en. Tests `tests/authz/test_h08_*.py` (10 files, 73), vitest 3 files (12). **Open:** break-glass MFA (no MFA subsystem) and the break-glass notification (needs a new notification kind + CHECK migration) not done; multi-company users are refused a password login if any company requires SSO (no per-company re-login on switch); OIDC test claims table not persisted (only pass/fail); `/t/{slug}/login`, SSO_ENABLED flag, group mapping UI and a real ldap3 directory are later work; `app/routers/auth.py` (already >250 LOC) got a one-line gate call. **Security review R1 applied:** attaching an IdP identity to an existing account needs a trusted email claim (`email_verified=true`, or none on a `trust_email_claim` connection; LDAP default untrusted) and, for an account with a password but no `email_verified_at`, a link-pending confirmation by one password login (signed cookie on `/api/auth/login`); every attach revokes the account's refresh tokens; password self-registration is refused (`sso_signup_blocked`) for a verified domain with an active connection. Sessions carry `auth_method` (`password`/`sso:<tenant>`, NULL = password): refresh is refused (401 `sso_required`) when an SSO-required company of the user did not issue the session (break-glass: listed *and* still an active company admin); SSO via company A is refused while company B requires SSO (fail closed); switching to required revokes members' password-origin refresh tokens (access tokens run out on their TTL); Agent API keys are untouched. IdP and LDAP destinations use the SSRF guard outside `single_tenant` (OUTBOUND_PRIVATE_ALLOWED_HOSTS exempt; LDAP gets the checked addresses to dial). Domains: pending claims may compete (one per company), only verified rows are unique, verification removes competing pending claims, other companies' claims are never revealed. Emails normalized to `local@idna`; discovery/JWKS cached 5 min with a refetch on an unknown `kid`; `SSO_PUBLIC_BASE_URL` builds redirect/start URLs. — native verification pending
- [x] H-04 operator sync screen and metrics (14.3, S27, 9.7 `/api/admin/authz` row; depends C-05) — committed `7be6fee8`. API `app/routers/admin_authz.py` (`SUPER` via `require_super_user`, both deployment modes per S27 — not the SaaS-only operator gate; no org/company context): `GET /api/admin/authz/status` (`app/authz/ops_status.py`: mode, `sync_enabled` = C-05 `jobs_enabled`, OpenFGA health, outbox pending/retrying/failed(≥10 attempts)/lag via the new log-free `jobs.read_backlog`, revocation markers pending/stuck(>10 min)/oldest, live + last reconcile run, shadow disagreements by object type split legacy-only/fga-only from `authz_shadow_diffs`, broken delegation links across the 4 link tables + `delegation.broken` audits in 24 h, retention sweep enabled/cron/last `retention.purge`/24 h count, which C-05 jobs + `retention_sweep` this config schedules), `GET /failures?limit=` (`ops_failures.py`: pending rows with ≥1 failed attempt; company read from the source row's `tenant_id` when its table has one, resource type/id from the tuple object, exception class only — no error text, subject or names), `POST /failures/{outbox_id}/retry` (makes the row due now, attempts kept, the drain applies it in `seq` order; 404 if processed), `POST /reconcile` → 202 `{job_id}`, `GET /reconcile/{job_id}`. Actions: `verify_csrf` decorator dependency first, then the super-user parameter dependency; refused with 409 `authz_sync_disabled` unless C-05 jobs are enabled; audited `authz.reconcile_run` / `authz.outbox_retry` (platform events, no tenant) in the same transaction. Revision `h04_authz_reconcile_runs` (down `h08_sso_hardening`, re-chained at integration): `authz_reconcile_runs` (status/trigger checks, `active` TRUE-or-NULL with a unique constraint = one live run across processes, counts-only `report`, per-company `partitions_done/total`). `app/authz/reconcile_runs.py` (`claim_run` closes rows idle > 2 h as `abandoned` first, 409 `authz_reconcile_running` while one is live; `execute_run` → `reconcile_store(mode=repair, progress=)` — new optional `progress` callback in `reconcile.py` — records succeeded/skipped(advisory lock held)/failed, slot released on cancellation via shield; operator runs drain and report `repaired`), `reconcile_launch.py` (in-process background task, `get_reconcile_launcher` dependency); the C-05 daily job now records a `scheduled` run through `scheduled_reconcile` (skips while another run is live; still no drain). Metrics (14.3, log-based): `authz_check_errors` warning per failed SDK call (operation + exception class), `authz_check_latency_ms` per health probe, `authz_openfga_down` error once per transition, `readiness_blocked_runs` per `agent_not_ready` body, `authz_reconcile_failed` error; existing `authz_backlog`/`authz_revocation_stuck`/`authz_reconcile_diff`/`authz_shadow_summary` unchanged. `/api/health`: unchanged `{status: ok}` in legacy (no probe); otherwise adds `authz: ok|degraded|down` (`ops_health.py`: `FgaClient.ping` = `get_store`, 15 s cache per process, degraded above half `OPENFGA_TIMEOUT_MS`). Inventory: `/api/admin/authz` removed from `DEFERRED_PATHS`, five reviewed `H04_ROUTES` (`CompanyRoute.saas_only=False` checks `require_super_user` instead of `require_saas_platform_operator`), count 43→48; OpenAPI snapshot additions only; predicates 0 additions. Frontend: `/settings/admin/authz` (server wrapper + `authz-sync-page-client.tsx` guard), `features/platform-authz/components/authz-sync-{content,sections}.tsx` (S27: run button with confirm dialog, disabled while a run is live or sync is off, per-company progress, OpenFGA down banner with last success time, backlog stats, failures table with retry, shadow/delegation/retention/jobs sections; polls 3 s while a run is live, 30 s otherwise), `lib/{api,types,query-keys}/admin-authz.ts`, `lib/hooks/use-admin-authz.ts`, sidebar admin entry + breadcrumb label, i18n `appSettings.authzOps` + `nav.authzSync` (ko/en). Tests: `tests/authz/test_h04_admin_authz_routes.py` (7), `test_h04_reconcile_runs.py` (10), `test_h04_health_metrics.py` (5); vitest `tests/components/platform-authz/authz-sync-content.test.tsx` (5), `tests/pages/admin-authz-settings.test.tsx` (2). Deviations: plan 14.3 names `/settings/admin/organizations` for the screen, S27/13 table name `/settings/admin/authz` — used the latter; job status is configuration-derived (what this config schedules), not APScheduler runtime state (the serving process may not be the leader); shadow counts come from the persisted diff table, not the per-process recorder counters; delegation stats are broken-link and audit counts (the daily sweep keeps no run record). Review R1 (APPROVE with lows) applied: live runs heartbeat `updated_at` every 60 s and are abandoned only after 15 min of silence (`close_stale` at lifespan start-up outside legacy and on the leader's 60 s `authz_backlog_alert` tick; a heartbeating run is never closed); reports carry `repaired` and `deferred` (rows still pending) separately; every route reads/writes through `platform_session("authz.ops")` (reason pinned in the B-13 ratchet) via the `get_ops_db` dependency; refused actions (409 `authz_sync_disabled`/`authz_reconcile_running`, 404) are audited with `outcome=denied` + `reason_code` (own commit). Open: CLI `check`/`explain`/`outbox --retry-failed` (14.3) not part of this task; reconcile dry-run option on the screen (operator runs are repair-only); bulk retry not offered; process-local OpenFGA client counters (`ClientMetrics`) are not exported.

## P8 checklist

- [x] I-06 m92 company RLS policies and app/migration DB role split (8.8, D24, m92 row, 16 `DB_RLS_ENABLED`; done-when 8.8 테스트 4종, 체크포인트 메타데이터 대조) — Revision `i06_company_rls` (down `i07_tenant_exports`; PostgreSQL only, SQLite no-op): classifies every physical table (`app/migrations/org_authz_v1/rls_manifest.py` + `rls_children.py`, adapted from the `schema_drafts` m92 draft and extended with `agent_purge_receipts`, `tenant_invitations`, `agent_purge_cleanup_items`, `conversation_history_*`, `conversation_run_agents`, `authz_reconcile_runs`) and aborts before any DDL on an unreviewed table or broken shape; installs one `tenant_boundary` policy per protected table (`rls_policies.py`: `nullif(current_setting(...), '')::uuid` casts, `app.scope=platform|identity`, same `WITH CHECK`, children re-read through protected parents); `RUNTIME_EXEMPT` = `alembic_version`, `apscheduler_jobs`, the four LangGraph checkpoint tables (plan 8.8: checkpointer outside RLS). Role split: `alembic/env.py` uses `MIGRATION_DATABASE_URL` when set; when set and its user differs, the `DATABASE_URL` role gets DML-only grants + default privileges. `ENABLE`/`FORCE ROW LEVEL SECURITY` only when `DB_RLS_ENABLED=true` at migration time (requires a superuser/`BYPASSRLS` migration role, `RlsRoleError` otherwise), else later via `python -m app.migrations.org_authz_v1.rls_activation enable|disable` — default installs stay behaviour-identical (policies inert). Start-up `app/security/rls_role_check.py` (lifespan, after `enforce_production_safety`): with `DB_RLS_ENABLED` on PostgreSQL, the app role must not be superuser/`BYPASSRLS`/owner of a protected table and every protected table must be enabled+forced; production refuses to start, other envs warn (platform reason `startup.rls_role_check`). Checkpoint cross-check: both run builders put `moldy_tenant_id` in `configurable` (LangGraph copies it into checkpoint metadata; `_prepare_agent` from `cfg.org_context`, prepared leases via `run_configurable` from the binding); `app/tenancy/checkpoint_audit.py` (`python -m app.tenancy.checkpoint_audit [--json]`, platform reason `checkpoint.tenant_audit`) pages threads, joins conversations and reports mismatched/unattributed(pre-I-06)/orphaned, exit 1 + `checkpoint_tenant_mismatch` log on mismatch. Tests: `tests/authz/test_i06_rls_manifest.py` (metadata-derived ratchet: every table classified, no stale names, tenant_id tables == DIRECT_TENANT, every protected table policy+ENABLE+FORCE, nullif-only casts, parent column/type checks, single head), `test_i06_rls_roles.py`, `test_i06_checkpoint_audit.py`; lane `test_i06_rls_postgres_integration.py` (8.8 tests (1)–(4) as a non-owner NOBYPASSRLS login role on a disposable DB migrated to head, forced-policy inventory, startup check app vs owner, disable/enable) and `test_i06_checkpoint_audit_integration.py` (real PostgresSaver), both registered in the company lane — collected, not run (no PostgreSQL/Docker). Open: app-wide `DB_RLS_ENABLED=true` is not yet safe — the request session reads users/memberships before `get_org_context` binds it (needs an identity scope), two `UNBOUND_TENANT_SITES` remain, health/MCP polls and conversation-run lifecycle/queue stay interim platform (per-row company split not done), `conversation_checkpointer(thread_id, ctx)` pre-read gate not built (metadata cross-check only); the checkpointer's `setup()` needs schema CREATE, so `DATABASE_URL_SYNC` should not be the restricted app role; new tables after m92 need their own policy (the ratchet fails until classified). Review R1 applied: identity gateway scope `app.scope=identity` (`database.bind_identity`/`identity_scope`, `dependencies.get_identity_db` as router dependency of `routers/auth.py` + `auth_sso.py`; token-user lookup and `resolve_org_context` run in `identity_scope` before `bind_tenant`; call sites pinned by `test_i06_identity_scope.py`); policies open the account directory (`IDENTITY_DIRECTORY`: tenants/orgs/memberships/role grants/groups/invitations/SSO connections+domains/audit events, plus users/refresh tokens/identities) to that scope and nothing else; `rls_activation enable` refreshes every policy before ENABLE/FORCE, sets `lock_timeout` (`--lock-timeout`, default 5s), refuses while `DB_RLS_ENABLED` is off, and with role separation (m92 too) refuses when the app role owns tables, printing `REASSIGN OWNED BY ... TO ...`; m92 pre-creates the LangGraph checkpoint and APScheduler tables as the migration role when roles are separated and `init_checkpointer` skips `setup()` once all LangGraph migrations are recorded (app role needs no CREATE); startup check covers ownership/superuser/BYPASSRLS through role membership and a missing `tenant_boundary` policy; `skill_usage_events` accepts platform catalog skills bound through a company conversation/agent, `health_check_history` lets companies read catalog-target history (writes platform only); builder and assistant runs also stamp `moldy_tenant_id` (fork/update-state already inherits `_prepare_agent`'s config). Runbook `docs/operator-setup.md` section 8. Lane test `test_i06_identity_postgres_integration.py` (login, `/me`, refresh, org-context route, wrong-password audit under forced RLS) registered, not run. Still open: Agent API key auth, public share links and routes using only `get_current_user` without org context run unbound (0 rows under RLS); Langfuse/tracer metadata `tenant_id` is I-08. committed `e7c47fe7`. — native PostgreSQL lane passed 2026-10-18 (371/371 at fca4c9d1)
- [x] I-06 follow-up — open items closed (plan 8.8 "요청에서 파생된 작업"/"체크포인터", 8.9): request paths without an org context now bind a scope through shared helpers — `tenancy/session_binding.bind_company_once` (binds an unbound root session to one company without org attribution; a bound session keeps its scope, so RLS-off behaviour is unchanged), `database.platform_scope(session, reason)` (one temporary pre-company lookup) and `database.bind_platform(session, reason)`. Agent API keys: the key id is looked up in `platform_scope("agent_api.key_lookup")`, the session is bound to the key's company only (no owner identity, so a 401 lends none), then the secret/actor is verified there. Public share links: `public_share_rate_scope` looks up the token in `platform_scope("share.public_token")` and binds the link's company as the conversation owner — the identity the public reads resolve (unknown token stays unbound). `get_company_db` (active company, tolerant like `get_org_context` but never failing) on feedback clear/list, templates, usage summary/daily, `/api/audit-events`, `/api/me/memory-settings`, archived agents/restore/purge, model list/get (after `verify_csrf` on mutations); `get_identity_db` on `/api/me/organizations`, `/api/me/active-org`, `/api/invitations/accept`; `resolve_tenant_context` reads the membership in `identity_scope` and binds the path company, and `load_org_control`/`load_org_oversight` read the path organization in `identity_scope` (covers every `/api/tenants/{id}`/`/api/orgs/{id}` admin route); `require_platform_operator` (models/system-credentials/system-llm/k-skill sync) and `require_saas_platform_operator` bind platform (`operator.system_catalog`, `platform.tenant_admin`). Background: MCP health poll moved to `services/mcp_health_poll.py` (catalog rows in platform, each company's servers in its own `tenant_sessions`; catalog and company failures isolated); health-check sweep stays platform by policy (health_check_history is platform-write); run heartbeat runs in the run's company (`cfg.org_context`), shutdown intent stays platform (documented); queue launch/fail resolve the input's company with one platform read (`_company_of`) and run in a tenant session, recovery sweep stays platform (documented); triggers already per company. Checkpointer: `agent_runtime/conversation_checkpointer.conversation_checkpointer(thread_id, ctx, *, db, saver)` 404s before the pool unless the conversation's company/org/owner match; wired into `chat/messages.list_messages_from_checkpointer` (also the public share read) and `GET .../messages`. Ratchets: B-13 reasons `agent_api.key_lookup`/`share.public_token`/`operator.system_catalog`/`platform.tenant_admin` added, health/queue/lifecycle anchors re-documented, reason index checked for session binders; identity sites extended; new `test_i06_conversation_checkpointer.py` pins the 23 remaining raw `get_checkpointer()` sites. Tests `test_i06_request_scopes.py` (5), `test_i06_background_scopes.py` (3), `test_i06_conversation_checkpointer.py` (9); mutations (gate tenant check removed, company MCP poll in platform) caught. Open: the 23 pinned raw checkpointer sites (branch entries, fork, state snapshot, pinned summaries, run worker/delivery/prep, purge, builder/assistant threads) are not yet behind the gate; `mcp/apps_projection_store` unbound read and `oauth2-credential/callback` remain; no PostgreSQL lane test added (not runnable here). committed `1eba9024`. — native PostgreSQL lane passed 2026-10-18 (371/371 at fca4c9d1)
- [x] I-06 follow-up 2 — checkpointer gate on every conversation-scoped site, MCP App projection and OAuth callback scope (plan 8.8 "체크포인터", "요청에서 파생된 작업"): `conversation_checkpointer` gains a call-time default saver (`default_saver` reads `checkpointer.get_checkpointer` when called, the patch surface of local-import sites; module-bound sites still pass their own binding) and a sibling `loaded_conversation_checkpointer(conversation, ctx, *, db)` (same DB gate, or the held row's company/org/owner when there is no session). Gated: branch entries (edit/regenerate/switch, `resolve_branch_checkpoint(..., db=, context=)`), protocol fork (`_fork_overwrite_input(*, db, context, ...)`), state snapshot (`load_thread_state_snapshot(conversation, context, db=None, ...)`), live thread history (factory default), pinned summaries, resume redaction (`restore_redacted_resume_payload(..., db=, context=)`, gate outside the restore try so a foreign thread is a 404), request-path pending interrupts (`interrupt_payloads_from_checkpointer(config, saver)`), feedback membership, quote/transcript resource context. Non-conversation threads: `authz.builder_scope.builder_checkpointer(db, session_id, user_id)` (session row must be the user's in the DB session's bound company; unbound fails closed) and `agent_runtime.thread_checkpointers.assistant_checkpointer(db, thread_id, agent_id, ctx)` (thread must be `assistant_<agent>[_<session>]`, agent in the caller's company; edit access stays the caller's check). Pin `tests/authz/test_i06_conversation_checkpointer.py` shrinks 23 → 10 direct reads, each with its reason (pool module lease, gate default saver, run-stream fallback, run preparation x2, e2e helper, agent purge x2 after their own authorization, worker finalize/baseline after `load_worker_conversation`; delivery's call-time `worker_attr` lookup documented the same way) and asserts the gated/loaded/builder/assistant call sites. `mcp/apps_projection_store.load_verified_artifact` looks up the held conversation's company and owner once in platform scope (`mcp_apps.conversation_company`) and reads the binding in that company's `tenant_session` (unbound site removed from the B-13 ratchet). `/api/oauth2-credential/callback`: `credentials/oauth_callback_scope.live_callback_state` reads the live state row and its credential's company in `platform_scope("oauth.callback_state")` and binds the session (`bind_company_once`, as the flow's user) only for a known, unconsumed, unexpired state, before the iss check so the one-shot consume write is in that company. Split: `routers/conversation_protocol_messages.py` (protocol input message helpers, re-exported by the commands facade) to keep the fork module ≤250. Tests `tests/authz/test_i06_checkpointer_sites.py` (fork, state snapshot with/without session, branch resolution, pinned sources × other company/org/owner — pool never read; builder other owner/unknown/other company/unbound; assistant other company/agent/prefix/conversation thread/unknown agent), `test_i06_callback_and_projection_scope.py` (valid state binds credential company+user; unknown/expired stays unbound; projection binding read in the conversation company, unknown conversation opens no company session); updated seams in assistant/state-snapshot/HITL/pinned/g06 tests. Deviations/open: pre-existing oversized files `conversation_agent_protocol.py` (391, unchanged) and `conversation_agent_protocol_state_snapshot.py` (271 → 267) and large test files grew by ≤6 lines; assistant threads without a session id (`assistant_<agent>`) remain shared by every editor of the agent (pre-existing, not a company leak); the run-path reads (lease seed, compiled agent, stream fallback, worker) rely on the run's own context rather than this gate; no PostgreSQL lane run. committed `4a54c7e6`. — native PostgreSQL lane passed 2026-10-18 (371/371 at fca4c9d1)
- [x] Native PostgreSQL `all` lane repair (first native run since P1: 371 executed, 29 failed → 371/371 passed, manifest `.omo/evidence/project-restart-consolidated-roadmap/pg-all-final-20261018.json`). Product fixes: `authz/org_context` reports `403 tenant_suspended` (plan 8.1) when the cookie-less fallback finds only a suspended company (was `409 no_organization`); `authz/attach_rules` MCP server lookup uses an IN-subquery (PostgreSQL has no `=` for JSON, `SELECT DISTINCT mcp_servers.*` failed); `conversation_run_queue_worker.dispatch_next_for_conversation` treats the claim's opaque 404 (e.g. organization archived while the previous run finished) as nothing to dispatch instead of raising out of run teardown. Stale/buggy tests: live-app labs upgraded to `head` instead of the old `b11_company_administration`/`m80` pins (shared `alembic_head()`), head pins compare with the script head, I-06 tests (reserved `.test` login email, production `expire_on_commit=False`, ids read before commit, current role-check text), workload run `source="start"` (ck constraint), member-exit cleanup deletes company audit rows (RESTRICT FKs), MCP prep failure code `mcp_preparation_failed` (shared pre-marker table), archive before revocation in Task 10 authority race, one execution proof per transaction in the chat race, E-02 refuse-first ordering in the run-first user delete race, shared-DB leftovers parked after the Task 10 restore/queue tests. Tests `tests/authz/test_org_context.py` (+1), `tests/test_conversation_run_queue_dispatch_refusal.py` (2). committed `fca4c9d1`. Re-verified at `49a2c918` (2026-10-18): backend suite 9,435 passed (remaining failures/errors only the known sandbox files), frontend vitest 2,503 passed (2 port-binding files passed outside the sandbox), native `all` lane 372/372 (one earlier run failed `test_agent_lifecycle_trigger_races::test_native_launch_first_cancels_local_owner_and_preserves_remote_owner` once, passed on rerun — flaky).
- [x] Unit-suite `no such table` cascades on CI (PR #314) — implemented, uncommitted. Cause: the unit engine was a plain `sqlite+aiosqlite://` StaticPool; a task cancelled mid-statement makes SQLAlchemy invalidate the pooled connection, the pool reconnects to a new empty in-memory database, and every later test of the worker fails (`_schema_ready` stays true). Cancelled mid-statement: the H-04 reconcile heartbeat (`_stop`), the conversation run heartbeat (`_heartbeat_until_terminal`, cancelled in the run's finally) and the supervision poller (`agent_execution_supervision.poll_cancel`, cancelled by the owner's task group). Fixes: new `app/services/cancel_deferred.finish_then_cancel` (a cancel waits for the DB step in flight, then propagates) used by those three loops; `tests/sqlite_test_engine.py` (shared-cache in-memory DB held by an anchor connection, so a replaced connection keeps tables and rows; replacement logged with the test id); `test_f12_mcp_cache_identity` no longer patches the global `time.monotonic` (froze the event loop clock). Tests `tests/test_cancel_deferred.py` (3).
- [x] I-09 internal-hop TLS and production boot check (15.1, 15.2.1, 16.6 "운영 모드에서 평문 내부 URL로 기동 거부"; done-when 평문 내부 URL로 운영 기동 거부, TLS 구성에서 통합 레인 통과) — `app/security/internal_tls_check.py` added to `collect_production_warnings`: with `APP_ENV=production` boot is refused when `DATABASE_URL`/`MIGRATION_DATABASE_URL` (asyncpg `ssl`) or `DATABASE_URL_SYNC` (libpq `sslmode`) is not `verify-full` (missing, `disable`/`prefer`/`require`/`verify-ca` all refused), `OPENFGA_API_URL`/`VAULT_URL` (when set) is not https, or `OPENFGA_CA_CERT`/`VAULT_CA_CERT` points to a missing file; only `INTERNAL_PLAINTEXT_HOSTS` hosts (case-insensitive, default empty) are exempt; only sqlite URLs and unix-socket URLs (absolute `host=` path, no network host) are skipped (`postgres://` and every other scheme checked); a `host`/`hostaddr` query override voids the exemption; messages name the setting + host:port, never credentials; dev only warns (dev boots now log these hints). `VAULT_CA_CERT` wired into `hvac.Client(verify=…)` (OpenFGA CA was already passed as `ssl_ca_cert`). Compose overlay `docker-compose.tls.yml` (certs at `MOLDY_TLS_CERT_DIR` → `/certs:ro`; both Postgres servers `ssl=on` + `hostnossl reject`; OpenFGA `OPENFGA_HTTP_TLS_*` + datastore `sslmode=verify-full`; backend verify-full URLs, `PGSSLROOTCERT`, https OpenFGA + CA), `scripts/internal-tls-certs.sh` (private CA + SAN server cert; `ca.key` in unmounted `certs/internal-ca` 0600, cert dir 0750, `server.key` 0640 read by OpenFGA via `group_add: MOLDY_TLS_KEY_GID`; `certs/` git/docker-ignored), lane `scripts/run-internal-tls-lane.sh` + `docker-compose.tls-lane.yml` (loopback ports) running `tests/authz/test_i09_tls_lane.py` (asyncpg/psycopg `pg_stat_ssl`, plaintext rejected by pg_hba, OpenFGA https with private CA, lane settings pass the check; self-skips without the lane env, no `integration` marker so the company-lane registry is untouched). Install guide `docs/operator-setup.md` section 7 (Korean). Tests `tests/authz/test_i09_internal_tls_check.py` (23), `test_i09_vault_ca.py` (2); `tests/test_production_check.py` baseline now uses TLS URLs, `test_external_secrets.py` expects `verify=True`. Not run: the TLS lane (Docker unavailable) — plan 20.3 "TLS 설정 실행" stays open until it passes. Not covered: OpenFGA gRPC TLS / mTLS (plan names server TLS + CA only), MCP/other internal services (F-11 owns outbound), Redis (not used). committed `f4c0c680`. — TLS lane passed 2026-10-18 (5 passed)
- [ ] I-07 company export, deletion schedule and permanent purge (S03/S04, 9.7 `/api/admin/tenants/{id}/export|exports|schedule-deletion|cancel-deletion`, 7.2 "회사 영구 삭제", 8.1, 14.1; done-when 삭제 후 회사 행 0, 다른 회사 영향 0; depends B-12) — committed `0d10d7eb`, `58c171d0` (review R1 applied). Alembic `i07_tenant_exports` (down `h04_authz_reconcile_runs`): `tenant_exports` (one queued/running per company), `tenant_purges` receipts (no FK, outlive the company), `marketplace_installations.item_id`/`version_id` nullable + `install_status='source_removed'`, `agent_trigger_runs.tenant_id`/`agent_api_runs.tenant_id` (FK CASCADE, backfilled from trigger/agent/conversation and deployment/conversation; writers `trigger_schedule_service.start_trigger_run` and `agent_api.run_acceptance` set it). Routes (`app/routers/platform_tenant_data.py`, `SUPER` + SaaS-only, moved from `DEFERRED_PATHS` to `I07_ROUTES`): `POST …/export` 202 `{job_id}` (409 `tenant_export_in_progress`), `GET …/exports`, `GET …/exports/{export_id}/download` (new; inventory `FILE_ROUTES` exempts it from `response_model`), `POST …/schedule-deletion` (`confirm_slug`, else 409 `tenant_deletion_slug_mismatch`; due = now + 30 days), `POST …/cancel-deletion` (→ `suspended`; allowed after the due time until the purge ran — the purge holds the company row lock for its whole transaction and re-checks the status, so cancel either wins or finds the company gone); If-Match/CSRF/idempotent repeats, platform audit `tenant.export|deletion_schedule|deletion_cancel`. Scope (`tenant_data_scope.py`, from ORM metadata): `tenant_id = :company` tables, NULL-tenant rows reached through an owning key, tenant-less children through any FK; `inbound_references` lists every reference from rows outside the company (users, platform rows, other companies). Purge (`tenant_purge.py` + `tenant_purge_rows.py` + `tenant_purge_detach.py`; leader job `tenant_purge`, `tenant_purge_enabled` default off): one transaction under the company row lock — re-check status/due (L9) → refuse while an export is queued/running → preflight: any non-nullable outside reference aborts before any change (logged `outside_reference table.column=n`, nothing deleted, keys intact) → record cleanup targets and projected tuples → other companies' installations of the company's items become `source_removed` with their local copies kept (audit `marketplace.installation_source_removed` per installing organization, in the installer's company) and every other nullable outside reference is cleared → delete company tables children-first → `destroy_tenant_keys` → platform events about the company replaced by `tenant.deleted` → receipt → company row → OpenFGA deletes queued in `authz_outbox` (non-legacy) → commit. After commit, resumed from `rows_deleted` receipts by every run: revoke sessions of members left without a company (8.1), delete files and `tenant_exports/<tenant>/`, clear cached keys, receipt `completed`. CLI and leader serialize on the row lock. Export (`tenant_export_archive.py`, `tenant_export_redaction.py`): rows streamed (`yield_per` 500) into each zip entry; secrets excluded (named secret columns, `*_encrypted`, `users` allowlist id/email/name/is_active/created_at so `last_login_ip`/password fields are out, whole-word secret keys inside JSON configs incl. `{name,value}` header pairs, URL userinfo and query strings dropped), marketplace ACL rows of non-members excluded. Leader job `tenant_export_gc` (hourly, first run at leader start): stale queued/running jobs (> `tenant_export_stale_minutes`, 120) → `failed`/`export_interrupted`; expired archives deleted. CLI `python -m app.services.platform.tenant_data_cli export|schedule-deletion|cancel-deletion|purge`. Error codes `tenant_export_in_progress`, `tenant_deletion_slug_mismatch`; audit actions `tenant.export`, `tenant.deletion_schedule`, `tenant.deletion_cancel`, `tenant.deleted`, `marketplace.installation_source_removed`; session reasons `tenant_lifecycle.purge|export|export_gc|cli`. Tests `tests/authz/test_i07_tenant_purge.py` (4), `test_i07_purge_cross_company.py` (2: installer keeps copy + `source_removed` installation; preflight abort leaves every table and the keys unchanged), `test_i07_tenant_data_routes.py` (8), `test_i07_export_hygiene.py` (15), `test_i07_cli_and_job.py` (7), `test_i07_migration.py` (3). Deviations: small guarded edits in the oversized pre-existing `app/marketplace/install_service.py`/`origin_service.py`/`models/marketplace.py` (source_removed rows skip version/item lookups); the export still runs as a request background task (streamed; no durable queue — the GC fails interrupted jobs). Open: no completion notification / company-admin download (S04 알림); external secret store namespace and non-local artifact objects not purged; skill builder draft workspaces stay; drifted stored tuples left to reconcile; a scheduled company's members get the generic `tenant_unavailable`; the whole row delete is one transaction (large companies → long lock); native PostgreSQL run not executed. — native verification pending
- [x] I-08 company file paths, rate-limit keys, public share company check, Langfuse metadata (8.9 rows 파일 저장 경로 / 요청 속도 제한 / 공개 공유 링크 / Langfuse 추적; done-when 다른 회사 경로·링크 접근 404). Paths (`app/storage/paths.py`): new company files go under `tenants/<tenant_id>/` of their root (`tenant_relative`, `skill_storage_dir`); reads go through `check_company_path` / `resolve_company_data_path` / `upload_file_path` — a `tenants/<X>/` prefix must equal the row's `tenant_id` (platform rows: no prefix), else `ForeignTenantPathError` (a `ValueError`) → 404; legacy unprefixed paths are not moved and count as the default company's (single-tenant: the only company; multi-tenant: m82 `DEFAULT_TENANT_ID` and platform rows). Applied: chat uploads (`upload_dir/tenants/<id>/`, 3 upload routes + resource-context attachment read → `FILE_NOT_FOUND`/not found), artifacts (`LocalArtifactStorageBackend.put_file/local_path(tenant_id=)`, keys `tenants/<id>/conversations/...`, content/download/share/resource-context reads → `ArtifactNotFoundError` 404), skills (text/package create and marketplace install write `tenants/<id>/skills/<skill_id>`; install update and skill-builder replace keep the skill's current directory so legacy skills stay put; skill file read/list/text content and resource-context skill read are company-checked — foreign path reads as missing/empty, file mutations get the existing 400 `invalid_file_path`; upload route checks the expected company dir). Rate limit (`app/rate_limit.py`): `caller_key(scope)` = `(tenant_id, user_id)` from `request.state.org_context`, `platform` bucket without an org context (readiness summary/notify, principal search switched); public share routes `share_key` = `(token company, ip)` via `public_share_rate_scope` dependency (cheap token→tenant lookup before the limiter, never raises; unknown token → `unknown` bucket); pre-auth routes stay per-IP. Public shares (`share_service.public_share_open`): the link's company and organization must be active, the organization must belong to the link's company and allow public links, checked before any cache on all 6 public routes (the owner re-resolution already 404'd suspended companies; this makes it explicit). Langfuse: metadata `moldy_tenant_id`/`moldy_org_id` + tag `tenant:<id>` from the run's org context; multi-tenant forces input/output capture off (`capture_io_enabled`); with capture off the mask (which the SDK also applies to metadata) redacts body keys and free strings but keeps `moldy_*`/`langfuse_*` scope keys. Review R1: prefix check rejects case variants of `tenants` (fail closed); company check added to the remaining readers/mutators — runtime skill freeze (`prepared_run_input_builder`) and `marketplace/skill_runtime` descriptors (`run_data_path_or_none`: a prefix must be the run's company, unprefixed platform/legacy paths pass; mismatch = skill source missing / skipped), marketplace install snapshot copy (installing company), publish snapshot copy, skill draft seeding (skill + attachment), skill evaluation case generator / embedded evals payload, revision rollback read + package replace (`company_storage_value`), `package_metadata`, skill text write and delete (checked before the row is deleted), install-service rmtree, builder replace — all fail closed (`ForeignTenantPathError`); new company files now also prefixed for skill revision zips (`tenants/<id>/skill-revisions/...`), company-owned marketplace version snapshots (`tenants/<id>/skills/_marketplace_versions/<vid>`; platform items unprefixed) and the builder's no-storage fallback (`skill_storage_dir`). File-size splits (moved verbatim, re-exported): `artifacts/recorder.py` → `snapshots.py`, `run_links.py`; `routers/shares.py` → `services/share_public_view.py`, `services/share_rate_scope.py` (router re-exports `chat_service`/`trace_storage`/`chat_secrets` for test patch targets); `observability/langfuse.py` → `langfuse_context.py`, `langfuse_metadata.py`, `langfuse_redaction.py`; `skills/service.py` → `skills/runtime_dict.py` + `read_text_content` into `file_service.py`. Runtime dependency baseline: stale `app.routers.shares → app.models.message_event` edge removed. Tests `tests/authz/test_i08_company_paths.py` (8), `test_i08_public_share_company.py` (5), `test_i08_rate_keys_and_traces.py` (4), `test_i08_company_readers.py` (6); layout assertions updated in `test_storage_path_relative_invariant.py`, `test_skill_builder_confirmation.py` (pre-existing 289 LOC, one line), `authz/test_private_skill_writer_membership.py`. Explicit exceptions (unprefixed, with reason): `data/workspaces` (OpenWiki vendored, hash-preserved scripts derive the location from `OUTPUTS_DIR`), runtime `conversations/<thread_id>` outputs and `runtime/<thread_id>` dirs (same derivation; per-thread, rebuilt from authorized rows), skill drafts `skill-drafts/<session_id>` (transient per-session workspace of a company-scoped session row, GC'd; prefixing needs the session company threaded through `skill_draft_workspace.py`, 505 LOC), revision list/content reads (`SkillRevision` has no company column; the router authorizes the skill first — rollback, which mutates, is checked). Pre-I-08 files: never moved; a single-tenant install keeps reading them (one company); in a multi-tenant deployment, files of the default company and platform rows keep working, but unprefixed files written before I-08 for any other company become unreadable (404 / fail closed) until they are moved under `tenants/<id>/` and the row paths updated (no CLI yet — nothing is deployed multi-tenant today); no move CLI (spec: existing files are not moved); rate limits stay per process. committed `d0f75c6c`.
- [ ] U-04 member removal, ownership transfer and organization archive screens (13.14 S15/S17/S19, 13.12.2, 13.16; depends E-02) — built before the U-05 user study by user decision (U-05 findings to be folded back afterwards). S15 `MemberRemovalWizard` (`src/features/organizations/components/member-removal/`): opened from the members row "제거" (replaces the plain DELETE confirm), reads `GET …/members/{id}/exit-preview` (`src/lib/{types,api,hooks,query-keys}/member-exit.ts`, key under `['org-admin', org]`), 3 steps (owned resources → shares/triggers/deployments/keys revoked or stopped → reason + summary), submits `POST …/removal` with `If-Match` and per-resource `transfer`(new owner via the E-04 `PrincipalPicker`, "apply to all")/`delete`(credentials only; personal-account credentials delete-only). Fail closed: archived agents stay with the owner, marketplace items (no transfer until I-04), `blocked_by` dependents (other users' triggers, deployments, keys; skill bindings block a credential delete) and `is_last_admin` are listed and keep Next disabled; preview errors render the shared `ApiErrorState`; stale_version/resource_transfer_required/transfer_blocked/grant_invalid map to localized copy and the preview is re-read. S17 `src/components/sharing/transfer-ownership-dialog.tsx` (DialogShell over the E-01 transfer via `useResourceShareBackend`): reachable from a wizard row "지금 바로 이전"; submit only with `can_govern`, governance reason required, keep-manager for owners, delegation note, `transfer_blocked` dependents listed, name-confirming confirm step. Organization archive `organization-archive-dialog.tsx` (`OrganizationArchiveAction`, company organizations list row): Policy A — nothing offered for a default org (or a non-active one); impact (members, running runs stopped, schedules paused and kept paused on restore, 30-day restore) + exact-name confirmation over the existing B-11 archive route (no backend change). i18n `orgAdmin.memberRemoval`, `orgAdmin.archiveDialog`, `sharing.transferDialog` (ko+en); orphaned `orgAdmin.members.remove` removed. Tests: `tests/components/organizations/member-removal-wizard.test.tsx` (5), `organization-archive-dialog.test.tsx` (4), `tests/components/sharing/transfer-ownership-dialog.test.tsx` (3), `tests/unit/organization-removal-plan.test.ts` (3), members content +1. Not done: S19 agent archive/trash screens (B-08 lifecycle UI; not in the removal transaction per E-02), agent/schedule counts in the archive impact (no API), mobile full-screen wizard, E2E removal flow, `delete_user` route. committed `8122c038`. — native verification pending
- [ ] U-02 company settings screens (13.14 S05–S07, 13.16 S05~S07 row, 9.7 company rows; deps B-11, E-07, E-09) — **built before the U-05 user study by user decision** (U-05 normally precedes U-01~U-04; findings from U-05 may still change these screens). Frontend only, no backend change: every field already had an API. Routes stay under the existing `/settings/company` prefix (plan names `/settings/tenant/*`): `/settings/company` (S05 overview: new `CompanyProfileContent` — status, organization/member/admin counts for admins/auditors, admin rename via `PATCH /api/tenants/{id}` — above the existing organization list), `/policies` (S05 정책: publication policy 불가·자유·승인 + audit retention 90–3650 via `GET|PUT /policies`, a shortened retention shows the preview count and resends with `confirm`; company members read-only; 공개 승인 queue only for admins while the policy is `approval`: name/description/type/organization/publisher/credential types, approve, reject with a required reason), `/members` (S06: members with company admin/auditor toggles via `PUT /roles/{user}`, suspend/reactivate, remove with confirmation; invitations list/create (none/admin/auditor)/revoke/recreate, the one-time token goes to the clipboard only), `/models` (S07 tabs: models — platform rows only narrow `allowed_org_ids` incl. 모든 조직=null, company rows create/edit/delete with default credential and base URL; shared credentials — create (personal types excluded), rename, enable/disable, replace values, allowed orgs, delete, values never shown; service LLM — per role company override (company credential + model id) or inherit; company catalog — per-item on/off sent as `enabled=false` overrides only, platform-hidden items locked). Gate `features/company-settings/components/company-admin-gate.tsx` fails closed: nothing renders until `GET /api/tenants/{id}` `access` loads, load errors use the shared `ApiErrorState`, S06/S07 show an admin-only notice to members and auditors without calling their APIs; sidebar entries come from `components/layout/company-nav-items.ts` (none while access is unknown; member → policies; auditor → overview + policies; admin → all + SSO). Company writes send `If-Match` as `tenantVersionHeader(updated_at)` (body `…Z` → backend ETag `…+00:00`, exact string compare); S06 writes use the overview `updated_at` because member/invitation pages carry no version, and every write invalidates the overview and all `tenant-admin` caches. New API/hook/type files `lib/{api/tenant-admin,api/tenant-catalog,hooks/use-tenant-admin,hooks/use-tenant-catalog,types/tenant-admin,query-keys/tenant-admin}.ts`; i18n `orgAdmin.companySettings`, 4 `orgAdmin.errors` codes, 3 `appSettings.nav` keys (ko+en). Tests: vitest `tests/components/company-settings/*` (4 files, 22: admin/member|auditor/loading/error per screen, token never rendered, Escape + focus return), `tests/unit/api/tenant-admin.test.ts`, sidebar org-nav +3. Not done: org row menu (rename/archive/admin designation) — `company-organizations-content.tsx` is already over the LOC cap; S05 cost/limit columns (H-06 data, separate screen); credential 시험 (no company test route); service LLM offers company credentials only (backend also accepts platform ones while allowed); axe checks (no axe tooling in the repo); E2E. committed `d2a79dd4`. — native verification pending

- [ ] U-01 (U-05 user study pending) platform operator screens: company list and detail (13.14 S03/S04, 13.16 `/settings/admin/tenants`, 13.12.2 회사 정지·삭제 예약; depends B-12, I-07) — **Built before the U-05 user study by user decision** (U-05 stays open; its findings may still change S03/S04). Routes `/settings/admin/tenants` (S03) and `/settings/admin/tenants/[tenantId]` (S04): Server Component pages wrapping client islands in `src/features/platform-tenants/`; gated by `GET /api/me/installation` `platform_tenant_management_enabled` (fail closed: loading/error/false hide the screen; settings sidebar lists 회사 목록 only for the operator; backend re-gates every route). S03: status filter (전체/이용 중/정지/삭제 예정), name/slug search (debounced 250ms), keyset pager, metadata rows (organizations, members/limit, monthly spend limit, status chip with text), row menu (상세, 정지/재개, 데이터 내보내기, 삭제 예약/예약 취소 — no entry into company content), 회사 만들기 dialog (slug/email validation, limits, system models; one-time invitation code copy only, never rendered; no mail, Q19). S04: overview (status, created, organizations, members/limit, this month spend/limit and reset, active runs/concurrent limit), limits/name edit (PATCH with If-Match), data (export history with progress/size/expiry, download, one live export at a time; deletion schedule with D-n and 예약 취소). Risky confirmation (13.12.2): suspend and schedule-deletion open `DeleteConfirmDialog`'s new "이름 입력" variant (`confirmText`/`confirmTextLabel`/`error` props; typed text reset per open and handed to `onConfirm(typed)`, sent as `confirm_slug` so the backend check stays authoritative; suspend has no server confirm field) with member count, active run count and effect text (30-day grace then permanent purge, only `tenant.deleted` remains); success/failure toasts. Permanent purge itself has no HTTP route (I-07: leader job `tenant_purge` after the due date / CLI) — the UI schedules it and explains it; no immediate-purge button was added. Minimal backend (`app/routers/platform_tenant_usage.py`, SUPER + SaaS only via `platform_context`, inventory `U01_ROUTES`, OpenAPI snapshot additions only): `GET /api/admin/tenants/{id}/limits` (company `UsageLimitView`: used, limit, members, active runs — S04 이번 달 사용량 and the 13.12.2 counts) and `GET /api/admin/tenants/{id}/deletion` (I-07 `TenantDeletionState` + ETag, so the D-30 badge survives reloads; directory/detail stay metadata-only per the B-12 query boundary test). If-Match helper `tenantVersionHeader` turns JSON `Z` into the backend `isoformat()` `+00:00` ETag (note: `organizationVersionHeader` in `lib/api/organizations.ts` sends the `Z` form unchanged — likely `stale_version` on org rename/archive/restore, not touched here). i18n `appSettings.platformTenants.*`, `appSettings.nav.platformTenants`, `nav.platformTenants` (ko+en). Tests: backend `tests/authz/test_u01_platform_tenant_summary.py` (3); frontend `tests/pages/platform-tenants-settings.test.tsx` (operator/loading/non-operator/fail-closed), `tests/components/platform-tenants/tenant-list-content.test.tsx` (7: loading, rows, filter/search, error+retry, empty, suspend and deletion slug confirmation incl. failure), `tenant-detail-content.test.tsx` (6), `tenant-create-dialog.test.tsx` (3), `tests/components/layout/settings-sidebar-platform-nav.test.tsx` (2), `tests/unit/api/platform-tenants.test.ts` (1). Not built (no API): S04 회사 관리자 tab (invite/resend/revoke for the operator) and 보안 tab (secret namespace status), list column "이번 달 사용" per row (list shows limits; usage is on the detail), completion notifications, mobile row-to-card layout, breadcrumb shows the tenant UUID. committed `b14ed9a2`.
- [ ] U-03 (U-05 user study pending) organization settings screens S08–S14 (13.14, 13.16, ch.17; dep B-04) — built before the U-05 user study by user decision (U-05 findings to be folded back into these screens afterwards). Frontend only, existing APIs (no backend change): S08 `/settings/organization` adds join/sharing policy (`PATCH /api/orgs/{id}` settings: default role, registration incl. company-inherit, public links), retention (`GET|PUT .../retention`, bounds checked, shortening shows the deletion preview and resends `confirm=true`), usage limits (H-06 `GET|PUT .../limits`: month use vs company-set org limit read-only, member default ≤ cap; shown only to org admins/auditors/company admins); S09 new `/settings/organization/catalog` (per-kind tabs, company-disabled items locked, turning off shows `.../catalog-policy/impact` affected/required agent counts before the full-replacement PUT; nav entry gated by `can_manage_policy`, query not issued without it); S10/S11 members: invite dialog (comma/newline emails, role, optional manual group, per-email result, one-time link copy only, never rendered), pending/expired invitations with resend (new-link dialog) and revoke, per-member monthly limit dialog (`PUT /api/usage-limits?scope=user`), link to capability grants; S14 roles: fixed role-defaults matrix (mirrors `org_permissions.py`) above the existing grant editors; S12 `/invite/[token]` (public in `src/proxy.ts`, bare layout): logged out → sign up with the token (`/register?invitation=`, passed as `invitation_token`) or log in and return (`callbackUrl`); signed in → one-click `POST /api/invitations/accept` then full reload into the joined org (an existing member also succeeds); wrong account (logout), expired, used, revoked/invalid, membership blocked and org/company unavailable states, other errors via `ApiErrorState`; review fixes: `canViewUsage` = org admin/auditor role only (backend `can_view_usage`), catalog off-confirm disabled until the impact preview loads (fail closed), override list documented (view = whole inventory, unlisted ids 404, so nothing storable is dropped), no archive control at all for a default org (Policy A; U-04 `OrganizationArchiveAction` needs a tenant summary with member counts, so the settings card keeps its own dialog for non-default orgs); shared `ApiErrorState` for load failures on settings/members/groups/requests/catalog/invitations. Modules: `lib/{types,api,hooks}/org-governance.ts`, `features/organizations/components/org-{policy,retention,limits}-section.tsx`, `org-catalog-content.tsx`, `org-catalog-disable-dialog.tsx`, `org-invite-dialog.tsx`, `org-invite-results.tsx`, `org-invitations-section.tsx`, `org-member-limit-dialog.tsx`, `org-role-defaults.tsx`, `org-select-field.tsx`, `lib/{amount,catalog-overrides,invite-emails}.ts`; size splits (moved verbatim): `org-roles-content.tsx` → `org-grant-section.tsx`, `org-groups-content.tsx` → `org-group-panels.tsx`. Not built (U-04 or other tasks): member removal/ownership transfer/archive flows untouched (existing entry points kept); S13 group detail tabs (group roles, shared resources) and member/resource counts, S10 role/group/usage columns and filters (no list API fields); S26 org activity log/usage screens. Tests: Vitest `tests/components/organizations/org-settings-governance.test.tsx` (6), `org-catalog-content.test.tsx` (6), `org-members-invitations.test.tsx` (9), `tests/unit/api/org-governance.test.ts` (5), `tests/components/invite/invitation-accept.test.tsx` (10), catalog impact-failure, default-org archive and `canViewUsage` cases, roles matrix + sidebar catalog gate cases; tsc, eslint, lint:i18n, lint:a11y, lint:design-system, lint:frontend-architecture --strict pass. committed `85d094c6`.
- [ ] U-05 internal user study (17장 U-05, 1.3 "과업 성공률 80%"; S16, S21/S22, S15, plus S18 and S07) — study kit prepared, study not run. Kit: [org-authz-u05-study.md](org-authz-u05-study.md) (recruitment of 3–5 internal participants across org admin/builder/member, throwaway stack seeded by the capture-tour helpers then cloned per session into an `AUTHZ_MODE=enforce` stack with a fresh OpenFGA, 6 tasks T1–T6, SEQ + UMUX-Lite, recording sheet and the 80% success-rate rule). S21/S22 run on the route-played opt-in specs until the 16.5 enforce fixtures exist. Results, screen decisions and U-01–U-04 follow-up fixes are to be recorded here.
- [ ] U-01~U-04 capture fixes (13.9, 13.14, 14.2; from `frontend/e2e/captures/captures-org-authz.spec.ts` shots 04-47) — implemented, uncommitted. Breadcrumbs: ko/en labels for every company/organization/admin settings segment (parent-scoped, e.g. company vs organization `members`), tenant/skill/marketplace/agent id segments resolve to names with a generic fallback (never a raw UUID), `/agents/{id}/conversations/new` = 새 대화, `/invite/{token}` hides the token, `traces` labelled. Marketplace management visibility select shows the label, not the raw value; access-request fallback copy without "누군가 님이". Names instead of ids: role/capability grant views carry `principal_name` (`app/authz/principal_names.py`); the S16 access list names users by profile `display_name` (was registration `name` → stale "E2E User"); new `GET /api/marketplace/items/{id}/acl` (`can_manage`, legacy owner/ACL manage; OpenAPI snapshot additions only) names marketplace recipients. `grant.received` keeps target/actor names (not the link) while the recipient still holds an active share (legacy hid them because only the owner opens the item); fallback copy rewritten (no "누군가 님이"/"을(를)"). Member removal: exit preview `owned[].can_transfer_now` (caller `can_govern` via one BatchCheck for all owned rows, OpenFGA outage → false without failing the preview; legacy admin false) gates "지금 바로 이전" with an explanation. Share dialog `fixed` height + sticky submit row. Viewer agent settings: tools/skills/middleware "+ 추가" and row remove hidden, test chat tab hidden (assistant endpoint needs `can_edit`), default tab 오프너. Tests: backend `tests/authz/test_{principal_display_names,marketplace_acl_names,h02_shared_notification_names,e02_transfer_now}.py`; vitest breadcrumb-nav, notification-text, share-dialog-layout, roles/marketplace share/removal wizard/agent-settings-permissions additions.
- [ ] U-01~U-04 re-capture fixes (13.9, 13.14) — implemented, uncommitted. Viewer agent settings also hide the opener add/edit/delete controls and the model/sub-agent gear buttons (`useAgentAccess().canEdit`); capture shot 30 runs only when "지금 바로 이전" is offered (hidden in legacy by design); new `DIALOG_HEIGHT.compact` token (content-sized, viewport-capped) used by the org archive, company invite (+ link), catalog disable and member monthly limit dialogs. Invite accept page still shows no org/inviter name: no endpoint returns them before acceptance (token preview = product decision). Tests: vitest agent-settings-permissions (viewer opener/gears), organization-archive-dialog (compact token).
- [ ] Plan 1.3 "axe 심각·치명 0" — implemented, uncommitted. `frontend/e2e/a11y/org-authz-axe.spec.ts` (gate `E2E_ORG_AUTHZ=1`, scripted-full; skip-manifest rule `org-authz-axe`) runs axe (wcag2a/aa, 21a/aa, 22aa) on 49 screens/dialogs of the shared org-authz walk (`e2e/helpers/org-authz-tour*.ts`, `org-authz-seed*.ts`, `org-authz-api.ts`, `org-authz-platform-stub.ts`; the capture tour now uses the same walk; U-01 is route-played when `DEPLOYMENT_MODE` is not multi_tenant). First run: 29 findings, all serious/critical (button-name ×4, color-contrast ×24, scrollable-region-focusable ×1). Fixed: light tokens `--muted-foreground` 0.556→0.51, `--destructive` 0.577→0.53, `--status-success` 0.596→0.51, `.moldy-status-surface` text mix 72%→60%, new `--primary-strong-text` (line-tabs active, button/badge link, agent card hover title); `aria-label` on the dashboard favorites toggle (`dashboard.favoritesOnly` ko/en) and the marketplace visibility select; `SettingsShell` scroll container is a tab stop. Final run: 0 findings of any impact (manifest `.omo/evidence/project-restart-consolidated-roadmap/org-authz-axe-run3.json`). Light theme only.
- [ ] Plan 1.3 upgrade regression ("업그레이드 후 기본 조직에서 기존 자원 사용") — implemented, uncommitted. `backend/tests/authz/test_legacy_upgrade_http_integration.py` + `legacy_upgrade_fixture.py` (native PostgreSQL lane, `integration`): m77 (origin/main 9657ca60 head `m77_side_chat_link`) DB seeded by SQL with a user, scripted model, OpenAI credential, agent, conversation and a pre-upgrade LangGraph checkpoint → `alembic upgrade head` → current app as a real uvicorn process: login, single default org, rows scoped to it, credential decrypts and lists, conversation history readable, v3 `run.start` completes and appends to the old conversation. Passed against an owned PostgreSQL 16 container. Found (pre-existing, also at the baseline): REST `POST /api/conversations/{id}/messages` always fails with `execute_agent_stream() got an unexpected keyword argument 'run_metrics'` (legacy executor vs worker kwargs).
- [ ] I-05 load/security checks and operator documentation (16.6, 8.10, 15.1–15.4; deps D-09, I-09) — docs implemented, load/security test pending staging. Operator guide `docs/org-authz-operations.md` (Korean: legacy → shadow → per-type enforce → RLS rollout, new settings, migration chain m78 → `i06_company_rls` with rollback notes, OpenFGA CLI/ops, company export/deletion/purge, TLS overlay, monitoring of shadow diffs, reconcile runs and outbox); RLS role split and `REASSIGN OWNED` stay in `docs/operator-setup.md` section 8 (linked). PR 1 body `docs/exec-plans/org-authz-pr.md`. Not done: the 16.6 load test (org 1, users 2,000, agents 5,000, grants 50,000; Check p95 ≤20ms, BatchCheck 50 p95 ≤60ms) and the 16.6 security checklist — they need a staging stack with `AUTHZ_MODE=enforce` and the I-09 TLS configuration; checklist in the guide section 9. I-05 scripts prepared, staging run pending (implemented, uncommitted): `scripts/i05_load_test.py` (16.6/8.10 load scenarios over real API/OpenFGA routes, p50/p95/p99 + error rate vs targets, JSON+MD, exit 1 on a miss) and `scripts/i05_security_check.py` (route-inventory-driven cross-company IDOR 404 parity, CSRF, forged org cookie, per-company rate-limit keys, public share link checks, transport/cookie/TLS, RLS on/off parity), with `--i-know-this-is-staging`/`--dry-run` guards and env-only secrets; how to run in guide section 9.0.
  - I-05 local rehearsal (2026-10-18, implemented, uncommitted; numbers do NOT replace staging): both scripts ran end to end against a throwaway local stack (postgres:16 + in-memory OpenFGA v1.22.0, `AUTHZ_MODE=enforce`, `DEPLOYMENT_MODE=multi_tenant`, two companies seeded over the API). Harness fixes: IDOR verdict `parity_verdict` (same 422 = `warn` gate not reached, same 403 `org_access_denied` = pass per 13.12.1), CSRF on SUPER routes uses `security.operator` (else `skipped`), `resource_type` placeholder, `--label`, cookie detail. Product fixes found by the probes: `GET /api/conversations/{id}/feedback` now 404s foreign/unknown conversations (`owned_conversation`), pinned-summary PUT/DELETE check CSRF before ownership. Observed, not fixed: default-org signup/membership writes no OpenFGA membership tuples until `reconcile --apply` (C-04), so a new member sees no catalog models in enforce mode until the nightly reconcile. Tests `tests/test_i05_probe_route_contracts.py`, harness tests extended.

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

## B-06 prerequisite: immutable legacy fixtures (2026-10-11)

The historical fixture previously copied 62 tables from the live application
metadata, with only `users` already frozen. A failing case demonstrated that a
live `conversations.title` nullability change also changed the alleged m77
fixture. That coupling would invalidate P0/B-02 migration and rollback evidence
as B-06 adds live scope fields.

`schema_drafts/org_authz/m77/` now holds explicit SQLAlchemy definitions for those
62 tables, with the existing frozen user definition completing all **63**.
`baseline_metadata()` constructs a fresh independent instance from that package.
No current model/table reflection is used. Python UUID/list/dict/scalar/default
factories and naive/aware UTC timestamp behavior are retained. Literal SQL server
defaults remain literals; dialect-aware `func.now()` remains a function instead
of being flattened into a text expression. Index/constraint/FK actions and
nullable/SQL type definitions are preserved.

The pre-promotion PostgreSQL/SQLite DDL was captured once as `ddl_contract.json`.
All **282** compiled table/index statements match its definitions, allowing only
constraint ordering inside CREATE TABLE. Three regression cases guard live-ORM
independence, independent caller copies and both-dialect DDL fidelity. Do not
regenerate this package from later live models to make tests green.

Focused schema/migration tests: **12 passed**; immutable DDL contract cases:
**3 passed**. Actual owned PostgreSQL draft/full-chain/scope/rollback/writer-lock/
live organization migration selection: **11 passed, 46.84s**. Changed type/format
gates select **66** Python files and pass; Ruff/Pyright pass. Logs are
`output/org-authz/p1/b06-{frozen-*,freeze-*}`. The whole-backend regression is
recorded in `b06-freeze-full-backend.log`: **4,870 passed, 3 existing warnings,
215.17s**, official isolated runner child exit 0 and cleanup `removed`.

B-06 itself remains incomplete: private ORM/query/writer scoping and newly seeded
dev account scope still need implementation. This fixture preparation does not
establish organization isolation for any existing private endpoint, and no
shared/main database or runtime data was changed. The full goal remains active.

## B-06 query foundation (2026-10-11, endpoint cutover remains open)

Added `app/authz/scoping.py`: `org_scoped` limits queries to the exact current
company/organization pair; `private_scoped` additionally requires the current
owner, including for platform operators. `private_parent_scoped` joins an
explicit private parent for child tables without scope columns. Nullable shared
resource columns and required private columns have separate typed model
contracts; query row types are preserved without casts or suppression comments.
Platform rows with null scope are excluded from these organization queries;
catalog policy and resource permissions remain separate checks.

The helper follows failing-test-first implementation. Seven real SQLite cases
cover same-user organization switching, another owner, an inconsistent company
pair, platform rows, missing organizations, operators, non-null model columns
and inherited child visibility. The selected context/frozen-schema regression
passes **25 tests**. A real owned PostgreSQL child/parent query also passes
**1 integration test** and its disposable database is removed by the lab.
Whole-backend Pyright/Ruff and changed-file type/format gates pass;
the ownership predicate ratchet remains **173**, with zero additions.

This is a reusable query boundary, not a product endpoint isolation claim.
Existing private ORM/writers and service callers still need the B-06 cutover.
The historical full-backend baseline remains 4,870 tests from the preceding
checkpoint; it was not rerun for this isolated helper addition. No shared
database, environment, runtime data, deployment or remote branch was modified.

## B-06 development-account scope prerequisite (2026-10-11)

Development E2E account seeding now provisions a dedicated explicit company and
organization using reserved UUIDv5 IDs (`moldy/e2e-tenant` and
`moldy/e2e-organization`). It enrolls the configured account and gives that
fixture account explicit company/organization admin roles; the global operator
flag alone is not used as an organization role. The saved organization is set
only when absent. Production and disabled-feature entrypoints return before
creating any fixture scope.

Repeated seeding is idempotent. Existing suspended company/organization scope
and inactive/removed memberships are rejected with `e2e_scope_unavailable`
rather than reactivated. PostgreSQL startup transactions acquire a dedicated
transaction advisory lock before account lookup so simultaneous development
backends cannot duplicate the account, membership or role grants. Account and
scope writes remain in the caller's transaction.

Failing tests first demonstrated missing enrollment. The selected seed,
organization-context and signup regression passes **25 tests** (1.04s); actual
m83 PostgreSQL concurrent bootstrap passes **1 test** (4.00s), including normal
organization resolution and explicit company/org membership. Logs are
`output/org-authz/p1/b06-e2e-scope-{focused,native}.log`. Pyright and changed-file
type/format checks pass for **4** Python files, Ruff passes, and the ownership
predicate ratchet remains **173**, additions **0**.

No application startup, shared/main DB migration, runtime-data mutation,
deployment or push was performed. This closes development-account preparation,
not B-06 private endpoint/service/writer scoping or full P1 acceptance.

### B-06 helper contract for existing Skill/MCP owners

Fresh source verification found that actual m81–m83 retain required `user_id`
on Skill/MCP rows while their organization/company columns are nullable for
platform resources. The scoping helper now accepts this mixed column shape
explicitly, without changing frozen migrations or owner nullability. A typed
failing case first proved the missing shape; **8** real SQLite helper cases now
pass (0.27s). The new case also verifies that matching the owner of a platform
row does not expose it through a private organization query. These type-only
adapter additions do not change the SQL predicate or claim resource endpoint
cutover.

## B-06 live cutover in progress (2026-10-11, uncommitted)

The coherent Tasks 4–14 cutover has started. Live `Credential` and `Tool` models
now declare the actual m81/m83 organization/company columns, named composite
scope FKs, system checks and restricted owner FKs. Credential creation takes a
typed payload plus a mandatory `OrgContext` or an explicit `PlatformScope`.
Personal writes persist the exact caller/company/organization; platform writers
deliberately use null scope. Personal credential CRUD, OAuth start, model probes
and private tool CRUD now receive the request's validated context. Existing
platform bootstrap and E2E LLM seed callers were converted to explicit scopes.

Failing real HTTP cases reproduced same-owner cross-company credential/tool
reads and mutations before the change. Those **9** focused cases now pass.
The expanded credential/OAuth/model/tool selection passes **78 tests** (2.43s).
An actual m83 owned PostgreSQL HTTP case proves persisted credential scope and
same-owner company-switch denial: **1 passed** (4.02s). These results are scoped
evidence, not full B-06/P1 acceptance.

Public OAuth completion derives the actor from the persisted credential's
company/organization and reloads account/membership state before token exchange.
Three failing revocation cases first demonstrated the missing boundary; company
suspension, membership removal and inactive accounts are now denied without
exchanging tokens or consuming state. A separate failing identity-map case
demonstrated cached company status surviving an SQL suspension; organization
resolution now refreshes already-loaded ORM rows. That regression passes.

The existing mocked-auth unit app now declares a real test company/organization
and membership graph. The unmodified `raw_client` still exercises actual
JWT/cookie/CSRF and request membership checks. Exactly **85** existing credential
test writer calls were converted using Python AST import/call resolution, with
dry-run diffs before application. The ast-grep executable was unavailable; the
stdlib AST fallback was used. No production default, before-flush scope fill or
optional-context compatibility branch was added. Unrelated formatter-only
changes were identified by identical ASTs and removed.

Current whole-backend Ruff passes; the ownership predicate ratchet decreases
from **173** to **168**, additions **0**. Whole-backend Pyright currently reports
**1 remaining error**: the runtime model-default credential lookup still passes
a user UUID instead of `OrgContext`. It must be converted through the runtime
scope spine; no implicit owner/default/session fallback will be added.

This cutover remains **uncommitted**. Required remaining work includes runtime
context propagation, other shared/private ORM writers and their direct test
constructors, MCP organization filtering/persistence, all B-06 private consumers,
native cross-surface acceptance and the isolated full backend gate. The current
MCP binding/import paths have context-aware credential validation, but their
server ORM/read/write organization cutover is not complete. The frozen m77
fixture, shared/main DB, environment and runtime data remain untouched.
Logs are `output/org-authz/p1/b06-{credential-*,cutover-*,oauth-*}`.

The additional legacy Tool/MCP transition selection currently has **45 passed,
8 failed** (2.79s). Failures identify direct test ORM constructors still omitting
the new required scope (credential/tool `org_required` checks), including runner
fixtures. They must receive explicit declared test scope; the production checks
will not be weakened to preserve those constructors. The log is
`output/org-authz/p1/b06-tools-mcp-transition.log`. Changed-file format checks
pass for **59** Python files. No cutover commit or full-suite success is claimed.

### B-06 MCP persistence/child queries and required runtime credential scope

The eight legacy Tool/MCP failures above were resolved with explicit test scope;
the original selection then passed **53 tests** (1.20s). Test fixtures also seed
the declared company/organization graph instead of relying on SQLite's disabled
FK enforcement.

`McpServer` now maps the exact m81/m83 scope columns/checks/FKs while preserving
its required owner column. Creation, registry creation and import persist the
request context. Private server lists/details/updates/deletes/exports and global
child-tool lists require context; child queries join the scoped parent through
`private_parent_scoped`. Import deduplication is partitioned by owner and current
organization. Update/delete re-load their scoped target before mutation.
Seven new real HTTP cases cover persistence and cross-company list/detail/
child-tool/update/delete/export denial. The combined Tool/MCP and new private
selection passes **65 tests** (1.79s).

The owned actual-m83 PostgreSQL HTTP matrix now covers **Credential, Tool and
McpServer**, including physical scope persistence and same-owner company-switch
denial: **3 passed** (12.68s). It does not cover the remaining scoped tables or
prove full B-06 acceptance. Log: `b06-native-resource-scope-matrix.log`.

Runtime credential resolution now requires the frozen `AgentCredentialAccess`
input containing an explicit `OrgContext` and `AgentRunIdentity`. The implicit
agent-owner identity input fallback is removed. Direct bindings, model defaults
and provider matching all require the credential subject plus the exact company/
organization. Three initial failing cases demonstrated the missing mandatory
scope interface; the positive/negative matrix now passes **6 tests** (0.43s).
The credential subject is explicitly derived from the supplied identity while
preserving the supplied company/org, not selected from owner membership or
saved preferences.

Whole-backend Ruff passes; the selected changed implementation/tests have zero
Pyright errors; changed-file format passes for **64** Python files; ownership
predicates decrease to **163**, additions **0**. **Whole-backend Pyright is still
red (20 errors)** because five production runtime entrypoints and older runtime
tests still call the old credential resolver interface. The required next step
is to propagate explicit scope through those entrypoints and persisted parents,
without adding an optional context or compatibility fallback. Other scoped ORM
and writer conversions remain open. All cutover edits remain uncommitted;
no full-backend green, startup, shared DB migration or deployment is claimed.

### B-06 request/runtime scope spine (2026-10-11, still uncommitted)

`AgentConfig` now has a required keyword-only `org_context` with no default.
Ordinary chat and skill-builder config producers preserve the explicit request
context. Agent invocation config producers require the same context, and child
agent configs/credential access inherit their parent's declared organization.
Chat start/send/resume, message edit/regeneration and LangGraph state/command
routes inject the validated context and pass it through their helper calls.

The selected conversation/router/protocol/runtime-credential regression passes
**77 tests** (3.04s), recorded in `b06-runtime-http-spine-green.log`. Initial
failures identified old mock resolver signatures and a platform Tool fixture
that omitted `is_system`; those fixtures now declare the new inputs/platform
identity explicitly. The selected request/config/subagent implementation has
zero Pyright errors, and whole-backend Ruff passes.

**Whole-backend Pyright remains red: 57 errors.** The required field/interface
exposes remaining production consumers (queued execution, triggers, Agent API,
secret collection, evaluation config) and older test constructors/callers.
They need persisted-row scope propagation and explicit fixture context, not a
default value. Conversation/trigger/deployment/private ORM and writer alignment
remains necessary before those detached paths can derive context correctly.
No full B-06/P1/goal completion, full-suite green or cutover commit is claimed.
Logs: `b06-runtime-spine-{ruff,pyright}.log` and the HTTP spine test log above.

### B-06 persisted conversation scope and detached queue actor (2026-10-11)

This is further work inside the same **uncommitted, coherent cutover**. Live
`Agent` and `Conversation` now map their actual m81/m83 scope fields; five of
the twenty scoped root models are mapped (Agent, Conversation, Credential,
Tool, MCP). Conversation ownership is independent of Agent ownership. Creation
requires an explicit `OrgContext`, validates the agent with `private_scoped`,
and persists owner, org/company, agent-name snapshot and last-activity time.
Ordinary, draft, skill-builder and side-chat conversation writers pass explicit
request or freshly resolved persisted-parent context. Trigger and Agent API
writers still need their own mapped persisted roots and cutover work.

`Agent.conversations` no longer ORM-cascade-deletes histories, including loaded
collections. The physical FK preserves history via SET NULL. Agent's restricted
owner FK and archive/budget fields now match m81/m83, with explicit User FK
relationships. The common private-conversation dependency and detail/runtime
loaders use conversation owner + current org/company. Detail responses can
represent an absent agent and expose its stored name and last activity; frontend
history rendering remains pending. Runtime callers reject agentless histories.

The new `authz.conversation_scope` helper restores a detached actor from the
persisted Conversation owner/org/company and checks fresh account/membership/
company state. Queue launching uses this context before runtime assembly, and
queue dispatch obtains its owner from Conversation, not Agent. Revocation or
company suspension terminalizes the claimed input/run before any runtime config
is built. No saved-preference fallback is introduced.

Existing runtime unit constructors now explicitly declare the unit fixture
context. The reviewed runtime-facade JSON changes only by the six-line required
`org_context` parameter record; it is not a regenerated migration fixture.
Legacy LLM resolver tests pass explicit `AgentCredentialAccess` with declared
identities and scope.

Evidence in `output/org-authz/p1/`:

- `b06-conversation-runtime-focused-final.log`: **94 passed**, 3.46s; includes
  own/foreign-company retained-history HTTP detail, persisted scope after company
  switching, revoked membership, loaded-relationship deletion preservation,
  queue revocation/suspension, ordinary chat/protocol, runtime facade and all
  credential resolver tiers.
- `b06-conversation-native-m83.log`: **1 passed**, 4.19s. Owned temporary
  PostgreSQL database upgraded through the actual m83 chain; real mapped Agent
  and Conversation creation, loaded-agent deletion, then history re-read in a
  new session with nullable agent FK, preserved name and timezone-aware activity.
- `b06-required-runtime-context-unit.log`: **23 passed**, 0.54s, focused runtime
  stream/offload/resume/HiTL/facade contracts after the required-context change.
- `b06-conversation-source-pyright.log`: selected conversation/queue/context/ORM
  implementation has **0 errors**. Whole-backend type checking remains red;
  required trigger/API/evaluation/secret inputs and remaining legacy test calls
  are open, not suppressed with defaults.
- Whole Ruff and `git diff --check` pass. Changed Python format: **122 selected,
  passed**. Changed Python type ratchet: **122 selected, 7 errors**, still red.
  Ownership guard: **161 predicates, 0 additions**; its baseline is unchanged.

No full-backend regression green, all-root scope closure, frontend acceptance,
B-06/P1/goal completion, cutover commit, shared database migration, app startup
or deployment is claimed. Next: finish required Agent writers and other root
fixture conversions, then map persisted Trigger/Deployment/API-key/Skill roots
and propagate their scope to detached runtime/secret/evaluation paths. Close all
remaining private readers/writers and run the full isolated gates before the
planned single cutover commit.

### B-06 Agent writers, Builder authority and persisted Trigger scope (2026-10-11)

Further **uncommitted cutover** work now maps seven of the twenty scoped roots:
Agent, Conversation, Credential, Tool, MCP, BuilderSession and AgentTrigger.
The source m81/m83 migrations and ownership-ratchet baseline are unchanged.

Agent list/summary/detail queries and conversation aggregates use the shared
private owner/org/company predicate. Main CRUD/image and Assistant entrypoints
receive explicit request context. Agent creation persists it; update/favorite/
delete reload the target under independently supplied context before mutation.
Tool, MCP and subagent references use exact scoped candidates. Skill reference
and template-install scope closure remains dependent on their root conversions.

All three direct Agent writers now receive explicit scope: ordinary creation,
builder confirmation and hidden skill-builder creation. Blueprint materialization
passes its required context to the ordinary writer; blueprint/dependency root
scope checks remain part of the unfinished wider cutover. Hidden runtime rows
are looked up per private org/company instead of across all the user's orgs.

BuilderSession uses the actual m81/m83 required scope/FK contract. Session
creation/get/confirm require explicit context. Confirm reloads the matching
session, checks fresh persisted actor authority and then materializes the Agent
in that same scope. LangGraph phase-8 resolves authority from the persisted
session before claiming confirmation; it does not select an organization from
saved preferences or assume graph-state identity alone proves authority.
`langgraph-fundamentals` was read for this node change.

AgentTrigger maps required scope with no default. Trigger CRUD/list/summary and
selected-conversation validation receive exact context; updates/deletes reload
the scoped root. The executor restores a fresh private actor from the persisted
trigger, passes its context to Agent/credential/config loading, and denies
revoked membership, company suspension and inactive accounts even under
`force=True`. Schedule-thread creation/reuse validates private conversation
scope and uses the explicit-context Conversation writer. Assistant cron writes
restore the persisted target Agent's actor before invoking the scoped service.

Evidence in `output/org-authz/p1/`:

- `b06-agent-builder-regression.log`: **83 passed**, 2.68s (Agent API/service,
  builder API/service and new private Agent/Builder scenarios). Includes
  same-owner foreign-company list/summary/ID denials and foreign-company builder
  confirm rejection before Agent creation. Updated fixture inputs explicitly
  declare unit scope; platform catalog tools declare `is_system`.
- `b06-trigger-chat-regression.log`: **108 passed**, 4.09s (Trigger API/executor,
  private Trigger actor, ordinary conversation and protocol regressions).
- `b06-private-runtime-boundaries.log`: **47 passed**, 1.72s (private roots,
  detached actors, runtime credential tiers/facade and shared scope composition).
- `b06-agent-trigger-native-final.log`: **2 passed**, 8.49s, each in an owned
  temporary PostgreSQL database upgraded through actual m83. Real HTTP writes
  persist Agent/Builder/Trigger ownership/scope, company suspension denies a
  forced trigger; loaded Agent deletion preserves scoped conversation history.
- `b06-agent-trigger-source-pyright.log`: **0 errors** for the selected changed
  Agent/Builder/Trigger/model/route/graph/cron implementation. Whole Ruff and
  `git diff --check` pass. Ownership guard: **144 predicates, 0 additions**.
- Whole-backend Pyright remains **red: 73 errors**. Production entrypoints still
  open are Agent API, secret collection and skill evaluation; remaining legacy
  test/helper calls must supply the new context. This is not a full-suite pass.

Required next work: map Deployment/API-key and Skill/evaluation parents, carry
persisted target scope into those remaining runtime/secret paths, finish all
other root writers/readers and their explicit fixtures, then run the planned
full isolated static/native/regression gates. B-06/P1/full-goal completion,
cutover commit, frontend acceptance, shared DB migration and deployment remain
unclaimed.

### B-06 persisted Agent API scope (2026-10-11, still uncommitted)

Deployment and API-key live models now match actual m81/m83 required org/company
scope and FK contracts: **nine of twenty** scoped roots are mapped. Control
plane candidates/lists/create/update/revoke services and routes require explicit
`OrgContext`; key deployment references must be private candidates in that exact
scope. Both writers persist owner/org/company without defaults.

Authenticated Agent API principals now contain required key-derived context.
Authentication verifies the secret and current key state, then restores the
persisted key actor with fresh account/company/membership checks before writing
usage counters. The browser's active-org cookie never selects a key's scope.
`allow_all_deployments` remains limited to the key's own org/company and owner.

Runtime deployment selection, public lists and thread reads enforce that scope.
Thread reads also preserve direct child ownership and return the same 404/code
for unavailable and inaccessible thread IDs. The old regression's 403 assertion
was deliberately updated to the repository's enumeration-masking requirement.
Missing operation scopes still return their existing 403 scope-required error.

Private API conversation creation uses the common explicit-context Conversation
writer. Before run rows, policy snapshots or runtime config are built, the key
and deployment are reloaded: revocation, expiry, grants, actor state and exact
private scope are checked again. Runtime config producers explicitly provide
`invoke` or `stream`; there is no operation/context default. Configs use the
persisted deployment/key organization for Agent and credential resolution.

Evidence in `output/org-authz/p1/`:

- `b06-api-regression.log`: **40 passed**, 1.97s, including the final explicit
  fixture membership graph. Coverage includes control plane/key/runtime/Dify/OpenAI adapters, UI-source
  exclusion and seven new private boundary scenarios. These prove same-owner
  foreign-company denial, key scope surviving UI org switch, no usage-counter
  write after revoked membership/company/account authority, stale-key revocation
  and removal of `invoke` scope before policy or credential preparation.
- `b06-api-native-m83.log`: **1 passed**, 4.05s, in an owned temporary PostgreSQL
  database upgraded through actual m83. Real HTTP Agent/deployment/key/thread
  writes preserve matching scope, and company suspension denies the key.
- `b06-api-source-pyright.log`: selected Agent API implementation and new native/
  private boundary tests have **0 errors**. Whole Ruff and `git diff --check`
  pass; ownership guard is **133 predicates, 0 additions**, baseline unchanged.
- Changed Python ratchets: **164 selected**, format passed, types remain red
  with **11 errors**. Whole-backend Pyright is still **red: 71 errors**; remaining
  production consumers are secret collection and skill evaluation, together
  with old context/signature calls in broader regression fixtures.

The full private cutover, other eleven scoped roots, full isolated backend
regression, frontend acceptance, B-06/P1/P0-P8 goal completion and cutover commit
remain open. No shared database migration, app startup or deployment occurred.
Next: Skill/evaluation persisted scope and explicit secret-collector context,
then the remaining private root readers/writers and their strict fixtures.

### B-06 Skill, evaluation actor and remaining Skill-writer prerequisites (2026-10-11)

The same **uncommitted coherent cutover** now maps **13/20** scoped roots: the
previous nine plus Skill, SkillBuilderSession, MarketplaceInstallation and
AgentBlueprint. These use exact m81/m83 scope/FK contracts. Skill's owner FK is
RESTRICT and slug uniqueness is `(org_id, user_id, slug)`. The immutable source
migrations, frozen historical package and ownership baseline are unchanged.

Text/package Skill creation takes an immutable input DTO plus required
`OrgContext` (three arguments). Private list/get and text/metadata/delete
mutations require explicit context, reload matching owner/org/company candidates
and reject foreign-company targets before storage changes. Skill/file/revision/
credential/evaluation request routes pass validated context. Agent/Builder Skill
references also filter exact private scope.

Evaluation preparation restores a fresh private actor from the persisted target
Skill, verifies evaluation-set/run parent identity, and places its organization
in required `AgentConfig.org_context`. Membership/company/account failures occur
before runtime mount. The worker now catches preparation failures through its
existing failure outcome path, so a permission-denied job is terminalized rather
than left grading. Runtime requirement resolution requires explicit context and
selects an active non-system credential by subject + org/company before decrypting;
foreign credentials become missing requirements without decryption.

SkillBuilderSession writers/readers and slug resolution now require context.
Confirmation validates matching session scope and fresh membership, and passes
that scope to package creation/improvement. Detached finalization restores the
persisted session actor; request controllers pass their explicit selected context.
Marketplace install/update/delete dispatch receives recipient context; new Skill,
MCP, blueprint and installation rows preserve it. Reuse queries use private scoped
installation candidates. Wider marketplace catalog/permission/binding/read closure
remains part of the unfinished cutover; these prerequisites do not claim it complete.

The test-call migration uses Python AST import resolution, preserving legacy data
arguments in immutable DTOs and declaring unit contexts explicitly. Test-only Skill
helpers prepare real unit user/company/org/member graphs before calling actual
strict writers; no production defaults or first-org fallback were added. Filesystem
regressions run through `scripts/run-isolated-command.sh`, which reports owned
mirror cleanup. Newly added private scenarios patch storage to pytest temp paths.

Evidence in `output/org-authz/p1/`:

- `b06-skill-evaluation-regression.log`: **46 passed**, 2.07s, with isolated mirror
  cleanup **removed**. Covers text/package CRUD, storage/mount/subprocess/evaluation
  workers and new private Skill/evaluation scenarios.
- New boundaries include identical slugs in different companies, private HTTP
  list/ID denial, rejection before modifying/deleting foreign-company storage,
  all three actor revocations before evaluator execution, persisted target scope
  after a saved-company switch, and no decryption of a foreign-company credential.
- `b06-skill-native-m83.log`: **1 passed**, 4.03s. Owned temporary PostgreSQL
  database upgraded through actual m83; real Skill HTTP write and scoped builder
  session write/re-read preserve owner/org/company, with temporary storage only.
- `b06-skill-source-final-pyright.log`: selected changed Skill/evaluation/builder/
  installer/model/controller implementation and new private/native tests have
  **0 errors**. Whole Ruff and `git diff --check` pass. Ownership guard is
  **123 predicates, 0 additions**, baseline unchanged.
- Changed Python: **245 selected**, formatting passes; type ratchet remains red
  with **92 errors** in broader context/signature fixture conversions. Whole-backend
  Pyright is still **red: 159 errors**. The only reported remaining production
  missing-context call is secret collection; old test/builder/marketplace callers
  and strict parent fixtures must still be reconciled, not hidden by defaults.

No all-root scope closure, full isolated backend regression green, B-06/P1/full
P0-P8 goal completion or cutover commit is claimed. Seven scoped roots, explicit
secret-collector context, broader private child/read/write checks, fixture closure,
full gates and frontend acceptance remain open. No shared DB migration, main data
write, app startup or deployment occurred.

### B-06 continuation — secret, share, file, memory and catalog roots (2026-10-11)

The live ORM now maps organization/company columns on all **20** tables in
the frozen `M81_SCOPED` contract. This is metadata coverage, not proof that every
service, background consumer, or product surface has completed scope cutover.
No Tasks 4–14 cutover commit or B-06/P1/full-goal completion is claimed.

- Secret collection now requires an explicit context. Conversation, credential,
  runtime-tool and replay paths reject foreign organization tuples before
  credential decryption. Public shares rehydrate their authorized persisted
  conversation scope before collecting secrets or reading content.
- Share links retain physical `created_by`; the authorization `user_id` synonym
  creates no extra SQL column. Private share operations use exact owner/org/company.
  Public token reads check the link/conversation scope chain before cache use.
- Uploads persist owner/org/company. File lookup, conversation/message linking,
  message hydration, resource-context loading and draft input copying are scoped.
  An already linked upload cannot be reassigned to another conversation.
- Artifact readers, counters, favorites, soft deletion and content access require
  context. Library predicates also check the conversation parent; name hydration
  checks the parent tuple. Runtime recording carries `ArtifactRuntimeContext.org_context`
  and rechecks the persisted conversation actor before filesystem snapshots and ingest.
  Finalization derives its explicit maintenance scope from the persisted conversation.
- Memory records/proposals persist exact context, including approval-created records.
  Runtime hydration/tools use required context; writes and effective-policy loading
  recheck the actor. Source conversations use direct Conversation ownership. User
  memory settings remain global to the user. Credential-default uniqueness includes
  organization, matching immutable m81.
- Catalog items retain physical `owner_user_id`, with a non-column synonym for
  common query guards. Private publishing validates source context and scopes reuse.
  Management candidates are private scoped rows or explicit null-scoped platform rows;
  existing catalog management authority checks still apply. Tenant-publication
  columns are mapped to m81; publication approval/FGA policy is not implemented here.
- Test-only ORM fixtures declare the unit organization/company and conversation
  owner/activity fields explicitly. Authentication fixtures prepare an actual
  membership graph; no production scope defaults or preference fallbacks were added.

Evidence (ignored `output/org-authz/p1/`):

- `b06-private-roots-focused.log`: **44 passed**, 1.83s (new private boundaries,
  memory CRUD/router/tools, agent memory-settings recovery).
- `b06-artifact-attachment-regression.log`: **37 passed**, 1.46s (recording, file
  routes, message attachment backfill, library and public-share artifact behavior).
- Actual m83 PostgreSQL/ASGI checks: share **1 passed/4.69s**, upload **1 passed/4.10s**,
  artifact ingestion + HTTP content **1 passed/4.20s**, memory + proposal HTTP writes
  **1 passed/4.12s**, catalog publishing + credential-default ORM write
  **1 passed/4.28s**, in the corresponding `b06-*-native-m83.log` files. These use
  owned temporary databases, no application lifespan or external LLM execution.
- `b06-all-fixture-types.json`: whole-backend Pyright **0 errors**, 1,410 files.
  Changed-Python type/format ratchets: **381 selected, passed**. Whole Ruff and
  `git diff --check` pass. Ownership guard: **98 predicates, 0 additions**;
  its reviewed baseline is unchanged.
- `b06-full-cutover-inventory.log`: the prior full isolated snapshot was **red**:
  **548 failed, 4,404 passed**, 239.96s. Its owned mirror was removed. A second
  full isolated inventory in `b06-full-cutover-inventory-r2.log` after explicit
  fixture updates is still **red: 117 failed, 4,852 passed**, 235.24s. Its owned
  mirror was removed (`cleanup=removed`, child exit 1). Snapshot hash:
  `9f7c20a7f8ce8603053efdda05a8283d2344bddb128ca0d804468267ffc4bd86`.

Outstanding scope closure includes Assistant read/write tools, builder catalog
consumers, run/queue mutation services, marketplace origin/installed-state and
materialization readers, residual private child lookups, and remaining fixture
regressions. The frontend, real E2E acceptance, B-13 session/RLS work, D/F shared
authorization, and later P0–P8 operational gates remain open. No shared database
migration, main data write, startup, push or deployment occurred in this continuation.

### B-06 continuation — detached Assistant, indexes and materialization (2026-10-11)

Assistant read/write builders now require `OrgContext`. Their frozen write context
carries the exact selected organization; fresh target loads recheck user and
membership state. Tool/MCP/Skill/subagent/cron candidates are scoped before use.
The shared Assistant/Builder catalog also requires context. Builder stream/resume
rehydrates its persisted session actor, independently of the saved company preference.

Conversation pagination, private lookup, cancel entry points and deletion require
explicit context. Global history filters direct Conversation owner/org/company.
Deletion scopes its locked reload before checkpoint/file cleanup and uses the
Conversation owner when the associated Agent no longer exists. Agentless history
navigation and frontend presentation remain B-08 work.

Blueprint materialization now passes context to credential validation and all
dependency resolvers. Tool/MCP creation copies the parent's exact org/company;
same-owner resources in another organization are excluded from reuse pools.
Credential candidates must be active, non-system and private to the same tuple.
Test-only builder sessions prepare real membership fixtures; runtime-env fixtures
pass explicit context, and retained public catalog fixtures declare their scope.

Evidence (ignored `output/org-authz/p1/`):

- `b06-assistant-builder-regression.log`: **127 passed**, 4.21s, including scoped
  targets/catalogs and persisted Builder scope after a saved-preference switch.
- `b06-assistant-native-m83.log`: **1 passed**, 4.59s. A real Assistant tool changes
  the prompt; after the user is disabled, the same captured tool refuses further
  writes. This invokes the tool directly, not an LLM/HiTL/browser journey.
- `b06-conversation-index-regression.log`: **71 passed**, 2.46s. New private tests
  reject foreign history/cleanup and preserve correct agentless cleanup ownership.
- `b06-conversation-index-native-m83.log`: **1 passed**, 4.09s. Real HTTP history
  pagination excludes another company's rows after an authenticated cookie switch;
  direct foreign Agent pagination returns 404.
- `b06-blueprint-builder-regression.log`: **87 passed**, 3.04s, in the isolated
  mirror with successful cleanup. Includes the same-owner foreign-tool collision
  regression, builder finalization, credential env injection and install preparation.
- `b06-blueprint-native-m83.log`: **1 passed**, 3.97s. Real m83 Agent/Tool/MCP
  materialization preserves exact owner/org/company; no provider execution occurs.
- `b06-full-cutover-inventory-r3.log`: full isolated snapshot remains **red**:
  **43 failed, 4,933 passed**, 225.74s, cleanup removed. Snapshot hash
  `5291213d896e7544a820319e0478f67e9b17978379c92ce39510e866d9cd9e45`.
  This snapshot predates the materialization/finalization fixture corrections above.
- Whole Ruff and `git diff --check` pass. Ownership guard: **77 predicates,
  0 additions**, unchanged reviewed baseline. Latest changed-Python formatting:
  **402 selected, passed**. `b06-blueprint-types-final.json` reports whole-backend
  **0 errors**, 1,417 files. The updated type ratchet is also **402 selected,
  0 errors, passed**. Full regression remains red pending a new complete snapshot.

Run/queue service closure, marketplace origin/installed-state/catalog readers,
residual private child access, remaining regressions and full acceptance are open.
No cutover commit, B-06/P1/full P0–P8 completion, shared database migration, main
data write, lifespan/startup, push or deployment is claimed.

The subsequent full isolated snapshot (`b06-full-cutover-inventory-r4.log`) is
still **red: 20 failed, 4,957 passed**, 225.35s, with owned-mirror cleanup removed.
Snapshot hash: `e3ca83b026f779dbd9630884c0f9f3927e8320132311e986d221ff8706693fef`.
`b06-full-failure-domains-r4.txt` records the remaining domain inventory. The
post-snapshot runtime-profile membership fixture correction passes together with
catalog-policy and blueprint scope checks: `b06-blueprint-caller-regression.log`,
**17 passed**, 1.27s. Other remaining failures have not yet been cleared by a full
snapshot; run/queue and catalog/origin scope closure remain separate required work.

### B-06 continuation — account erasure and queue context (2026-10-11)

The handoff explicitly requires releasing protected resource ownership before
account deletion. `delete_user` now locks the User, checks all six restricted
owner roots, and rejects with `resource_transfer_required` (409) before external
cleanup. MarketplaceItem's live owner FK matches m83 RESTRICT. Account history
cleanup reads direct Conversation ownership, including agentless histories, so
Agent ownership cannot select another user's transcript. No new erasure/retention
policy, automatic ownership transfer or archive completion is claimed.

Queue enqueue/claim, direct-input persistence, edit/delete/reorder/list/pause/resume
and promotion require explicit `OrgContext`. The locked conversation must match
owner/org/company and fresh actor authority. Background dispatch derives scope
from its persisted conversation before claiming. Workerless cancellation uses a
separate explicit PlatformScope maintenance target: stopping already-revoked work
remains possible, while private queue execution continues to fail closed. Recovery
also verifies run/input parent identity before touching a bound input.

Fixture repairs preserve actual identities: raw authenticated histories copy
their persisted Agent tuple, foreign side-chat histories use the foreign owner,
and private builder/client fixtures prepare real memberships. Removed dependency
baseline entries are pruned only when the corresponding import disappears; the
baseline was not widened to permit new dependency violations.

Evidence in ignored `output/org-authz/p1/`:

- `b06-all-remaining-regression.log`: **110 passed**, 7.91s, owned mirror removed.
- `b06-erasure-native-m83.log`: **1 passed**, 3.91s. Actual m83 account deletion
  rejects outstanding Agent ownership before checkpoint cleanup and retains rows.
- `b06-queue-context-regression.log`: **43 passed**, 2.57s, owned mirror removed.
- `b06-queue-native-m83.log`: **1 passed**, 4.33s. Actual enqueue/claim writes plus
  system cancellation after user deactivation, without provider execution/lifespan.
- `b06-queue-types-final.json`: whole-backend **0 errors**, 1,422 files. Whole Ruff
  and `git diff --check` pass. Runtime dependency guard passes with **89 reviewed
  violations, 6 reviewed cyclic edges** after pruning one obsolete violation.
- `b06-full-cutover-inventory-r5.log`: full snapshot **red: 1 failed, 4,976 passed**,
  225.09s, mirror removed; hash
  `8ba2afb41c52e6a8b1a9ea7fbca6681f3cab8cd3cb3bfc4969cf9d032f2a38a3`.
  The remaining offload fixture now explicitly releases its protected Agent before
  exercising failed cleanup of independent account history; its focused regression
  passes. This snapshot predates the new queue context interfaces.
- The complete updated snapshot is running in `b06-full-cutover-inventory-r6.log`;
  full-green acceptance remains unproven until it finishes and is inspected.

Catalog/origin/installed-state private scope, remaining run-service closure,
worker relationship checks, all-surface native acceptance and review remain open.
No cutover commit or B-06/P1/full P0–P8 completion is claimed.

### B-06 continuation — catalog projections and conversation runs (2026-10-11)

Catalog readers now require explicit `OrgContext`. Private owner candidates,
installed filters, installation annotations, installation child prefetches and
publication management summaries respect owner/org/company. Operators cannot see
another organization's ungranted private catalog entries by default. Public and
explicit platform catalog candidates retain their existing visibility checks.
Skill serializers carry a frozen `ResourceViewContext`; publication annotations
validate the persisted Skill and MarketplaceItem roots before exposing metadata.
`mark_installation_dirty` also requires an explicit private installation context.
These changes do not implement the later FGA publication workflow.

Conversation run create, active/interrupted/detail/latest queries and bulk page
projections now require explicit context. Read predicates inherit the direct
Conversation scope, including its owner independently of Agent ownership.
Creation requires the matching private Agent and Conversation plus fresh actor
membership; it retains User-before-Conversation locking for account-erasure races.
Protocol, queue, history and disposable E2E fixture callers propagate that context.
Native positive run creation and negative same-user cross-company metadata reads
are covered against actual m83.

Claimed queue workers now require queue input, run and conversation identities to
agree on conversation, owner and Agent before rehydration or executor launch. A
regression first reproduced failure recovery changing an unrelated run to failed.
Recovery now fails only the malformed input and leaves that unrelated run intact.
This closes the claimed-input binding case; direct run-worker reauthorization and
remaining private Skill binding/quality readers are still separate open work.

Evidence in ignored `output/org-authz/p1/`:

- `b06-full-cutover-inventory-r6.log`: **4,980 passed**, 3 warnings, 263.18s,
  owned mirror removed; hash
  `7cd4694ae7267ea115f1b7924f0414e26b369f104c7d150a2f4961df4c807796`.
  This successful snapshot predates this catalog/run/publication continuation.
- `b06-catalog-origin-regression.log`: **124 passed**, 8.93s, mirror removed.
- `b06-catalog-origin-native-m83.log`: **2 passed**, 7.97s; actual m83 catalog
  publish/default writes and private Skill/builder scope, without lifespan/LLM.
- `b06-catalog-origin-types-final.json`: **0 errors**, 1,424 files. Both changed
  Python ratchets passed for **420 selected files** at that prior snapshot.
- `b06-worker-binding-red.log`: reproduces an unrelated run incorrectly becoming
  failed; the corrected recovery/launch regression passes both cases.
- `b06-run-scope-red.log`: required context contract was missing from run readers;
  the new same-owner foreign-org query/create rejection regression passes.
- `b06-run-worker-focused.log`: **18 passed**, 1.10s.
- `b06-run-context-regression-r2.log`: **104 passed**, 4.23s, mirror removed; hash
  `1b8b2c3dee2617d240127141e9960f72683b06d73dc252aa29472a1d58023409`.
  Fixtures prepare real memberships rather than bypassing new creation authority.
- `b06-run-context-native-m83.log`: **2 passed**, 8.71s. Actual m83 run creation,
  HTTP active/detail boundaries after company switch and revoked-user system queue
  cancellation; owner-role app sessions, no RLS/lifespan/provider/E2E claim.
- `b06-publication-regression.log`: **125 passed**, 10.88s, mirror removed; hash
  `210b20c62021f7b5dac5ea533d90b587df6f7df83ef65e335d7a8c278b0e2995`.
- `b06-run-publication-types-final.json`: whole-backend **0 errors**, 1,427 files.
  Ownership guard after removing redundant child owner predicates passes with
  **67 predicates, 0 additions**. Whole latest formatting/lint gates follow below.
- `b06-full-cutover-inventory-r7.log`: complete regression snapshot is running.
  It predates the subsequent publication change and dormant PostgreSQL lane seed
  repairs; its terminal result must be recorded separately when inspected.

B-06/P1 and full P0–P8 remain open. No cutover commit, shared database migration,
main data write, push, deployment, full native acceptance or product completion is
claimed. 14-day shadow, stabilization and human acceptance gates remain unchanged.

The r7 full isolated snapshot finished **red: 7 failed, 4,980 passed**, 226.99s,
3 warnings, mirror removed; hash
`2112a2ba57ff4b6d864680d609b7ca57844fced84d191976bc50bddefda69a82`.
The failures were three run-fixture helpers lacking actual organization membership.
Those helpers now seed the declared test organization/company memberships. Focused
reverification (`b06-run-caller-regression.log`) passes **34 tests**, 1.01s, mirror
removed; hash `bdb7f48772f38f8bb9b47353ee2805fb24523d1d1ceedab56e0a8d69c9b520a5`.
Dormant PostgreSQL lane fixtures also receive explicit matching scope and membership;
those standalone lane concurrency scenarios have not been executed in this pass.

Durable message-to-run link queries now carry explicit `OrgContext` and inherit
`private_run_predicate`; the previous owner-only query DTO is removed. The native
index scenario verifies actual PostgreSQL JSON projection through the HTTP link
endpoint in the correct organization and absence from the same owner's other
company context (`b06-run-links-native-m83.log`: **1 passed**, 4.88s).
`b06-run-links-regression.log` passes **12 tests**, 0.83s.

An additional red regression showed that a resume child belonging to another
conversation could hide a genuine interrupted parent. Both interrupted lookup
and bulk current-run projection now require a coherent parent/conversation/owner
relationship through a shared authorization predicate. The combined run/router/
historical-links regression (`b06-run-final-regression.log`) passes **57 tests**,
2.41s. These focused results remain distinct from the r8 full snapshot, which was
started before the final message-link and resume relationship changes.

The r8 isolated complete snapshot finished **green: 4,988 passed**, 3 warnings,
227.77s. Its final cleanup and snapshot identity are recorded below. This snapshot
includes the corrected run-membership fixtures and publication scope change, but
predates the final message-link/resume relationship additions. Those additions
have the separately recorded 57-test combined regression, real m83 link projection
and latest private regression (`b06-run-final-private.log`: **5 passed**, 0.30s).
`b06-run-final-types.json` reports **0 errors**, 1,427 files. The latest changed
format ratchet passes for **428 selected files**. Whole Ruff, `git diff --check`,
ownership guard (**66 predicates, 0 additions**) and the unchanged runtime
dependency baseline (**89 reviewed violations, 6 reviewed cyclic edges**) pass.
Full source closure remains pending, especially direct worker reauthorization,
Skill binding/quality readers and invalid installed-child reference handling.

R8 owned-mirror cleanup: **removed**, child exit **0**; snapshot hash
`9b665c0a8c6f25c232d25561d7075befa5909aec91351998a2bf93b73d8e8ef8`.
Latest changed type ratchet (`b06-run-final-type-ratchet.log`):
**428 selected, 0 errors, 0 warnings, passed**.

### B-06 continuation — direct worker and private installed-child closure (2026-10-11)

Direct conversation execution validates its explicit AgentConfig identity and
private execution target before creating a broker or registering a task. The
actual `queued -> running` transition independently reloads and locks the matching
User/Conversation/Agent/run graph and rechecks membership. Missing/mismatched scope
or revocation aborts before execution; failed preauthorization cannot finalize or
mutate an unrelated run. A frozen `RunExecutionTarget` carries persisted run and
conversation identity. Worker fixtures now build real typed AgentConfig from an
explicit disposable session factory instead of identity-free namespaces.

Skill credential binding read/validate/upsert/delete, installation binding
validation and runtime env inputs require explicit context. Private Skill roots
and fresh actor membership are checked before binding writes or secret resolution.
Credential references require matching owner/org/company; eligibility also checks
active status, system separation and definition keys. Malformed foreign bindings
cannot satisfy required setup. System credentials on this private binding surface
are uniformly `CREDENTIAL_NOT_FOUND` (404); the previous system-existence hint is
removed. Existing system test fixtures now use the physical null/null platform
scope. Obsolete `_LightUser` adapters and unused runtime-env `user` input are gone.

Skill quality/evaluation/set/binding readers and Agent usage counts inherit private
Skill/Agent scope. Foreign Skill quality entries are omitted; inactive, foreign or
wrong-definition credentials stay missing in bulk readiness. The bulk path still
batches the binding query and does not call the per-Skill missing-key service.

Installation candidates now validate every non-null Skill/MCP/Agent/Blueprint
reference against the installation context. Invalid child references are absent
from single/bulk catalog annotations and installed filters. Reuse rejects them
before materialization; update, overwrite and uninstall reload a private authorized
installation and scoped child roots before filesystem/DB effects. Install entry
rechecks active actor authority before copying snapshots. Removal requires explicit
context; its unused keep-installation mode is removed (no callers used that mode).

Evidence in ignored `output/org-authz/p1/`:

- `b06-direct-worker-red.log` reproduces task registration without organization
  validation. `b06-direct-worker-regression-r3.log`: **35 passed**, 2.20s,
  mirror removed; hash `69c85046d4f054139196217db99f4cefe1727b7283afc32eaeac13302965ed7b`.
- `b06-direct-worker-native-m83.log`: **2 passed**, 8.15s. Actual direct running
  transition succeeds for active membership and stays queued after suspension.
  No executor/provider/lifespan is invoked by these native transition cases.
- `b06-skill-binding-red.log` proves missing context interfaces. The added malformed
  foreign binding readiness and credential-selection cases pass with real fixtures.
- `b06-skill-binding-regression-r2.log`: **134 passed**, 6.07s, mirror removed;
  hash `2036703764ac8f1f73b64631ab3b66586ae262f66e54ee05f5f0a5c78918beee`.
- `b06-skill-quality-regression.log`: **136 passed**, 6.09s, mirror removed;
  hash `1ff7230011b92969194ed03069ea658d8ef093640e6a8141b33ac806960b657e`.
- `b06-skill-binding-native-m83.log`: **1 passed**, 4.51s. Real JWT/CSRF HTTP PUT
  rejects same-user foreign-company credentials without creating a binding and
  accepts the correctly scoped credential; actual cipher writes use dummy data.
- `b06-installed-child-red.log` reproduces exposing a foreign private child ID as
  a normal active install. `b06-installed-child-reuse-red.log` reproduces reuse
  accepting that malformed link. The corrected scope cases pass.
- `b06-installed-child-regression-r1.log`: **123 passed**, 8.86s, mirror removed;
  hash `33a5c8a88bb8cbbab92903578a4f3edc8bcd84f4775d5dd531859df5206d555d`.
- `b06-installed-child-native-m83.log`: **1 passed**, 4.30s. On actual m83 the valid
  install is projected normally; a persisted cross-company child reference is
  absent, reuse and resource deletion are denied, and the foreign Skill and own
  installation remain unchanged. No filesystem/provider action is exercised.
- `b06-full-cutover-inventory-r9.log`: complete snapshot **red: 2 failed, 4,994
  passed**, 217.68s, 3 warnings, mirror removed; hash
  `89a7a7fdc997f9ccd769e1b3d5696f0f856fca1a92ada724d36494badae33348`.
  Both failures were template auto-install fixtures without actual organization
  membership. All four template fixtures now seed their declared membership;
  their corrected regressions are included in the 123-test result above.
  This snapshot predates installed-child closure and is not latest full acceptance.
- The updated complete isolated snapshot runs in `b06-full-cutover-inventory-r10.log`.

This is bounded application/owner-role native evidence, not RLS, actual app
lifespan/provider/browser, all-root matrix/review or full P0–P8 completion. B-06
cutover and Task 15 remain uncommitted pending the complete source audit and gates.

The r10 complete isolated snapshot finished **green: 4,998 passed**, 3 warnings,
223.69s, child exit 0, owned mirror cleanup **removed**; hash
`cfc1a066f83d1311def7fa0e1c39bf7eee368f1c7cbaafe600fe550c24b1a9a6`.
This snapshot includes direct worker, private binding/quality, installed-child
read/reuse/update/overwrite/removal checks and the corrected template memberships.
The subsequently added exact-20 structural gate is verified separately:
`b06-exact20-frozen-gates.log`: **4 passed**, 1.66s. It independently executes the
frozen m80→m83 DDL, compares all twenty live tables' complete column names and
nullability, all FK targets/delete policies, scope CHECK names and SQL bodies,
and rejects implicit org/company defaults. Frozen m77 source diff remains empty.
This is SQLite DDL/live-ORM parity and frozen-source evidence, not the remaining
native twenty-root cross-surface acceptance matrix.

`b06-worker-binding-installed-types-final.json`: **0 errors**, 1,434 files.
The preceding changed-Python gates (`b06-final-scope-{type,format}-ratchet.log`)
pass for **437 selected files** with 0 errors/warnings. Ownership guard remains
**59 predicates, 0 additions**; runtime dependency baseline remains **89 reviewed
violations, 6 reviewed cyclic edges**. The newly added model gate's final type and
format results are recorded separately after inspection.

No B-06 commit/completion is claimed from these green gates alone. Task 15's native
same-user/two-company twenty-root read/write/non-enumeration/operator/platform
matrix and the final implementation/source/fidelity reviews remain required.

Final inspected static results for this continuation:
`b06-exact20-types-final.json`: **0 errors**, 1,435 files.
`b06-exact20-type-ratchet.log`: **438 selected, 0 errors, 0 warnings, passed**.
`b06-exact20-format-ratchet.log`: **438 selected, passed**.
Whole Ruff and `git diff --check` pass; ownership guard has **59 predicates,
0 additions**, and frozen m77 source remains unchanged. The new exact-20 gate
also reruns green after its SQLAlchemy constraint-name typing correction.
The goal remains active; no cutover commit, P1, deployment or full product
completion is asserted.

### B-06 native acceptance continuation (2026-10-11)

Added the exact-twenty native ORM/query graph plus authenticated HTTP matrix in
`tests/authz/test_private_scope_integration.py`, with typed fixture and HTTP response
helpers. Generic boundary results are deliberately separated from real service/
route writes. `docs/exec-plans/org-authz-native-scope-matrix.md` maps all twenty roots,
actual HTTP read coverage, writer evidence and explicit exclusions.

The native HTTP matrix found a real remaining Blueprint disclosure: the same
owner could read another company's Blueprint through the list/detail router.
Both router paths now require context and scope Blueprint plus joined Installation.
The resulting combined query reproduced SQLAlchemy auto-correlation removing all
FROM clauses in a child check; explicit Installation correlation fixes the predicate.
Native matrix+migration gates: **8 passed**, 34.49s. The focused Blueprint/caller
regression passes **91 tests**, 2.78s, owned mirror removed.

Builder/Skill builder confirming claims now require explicit context, scoped parent
and fresh actor authority before atomic UPDATE RETURNING. Completed Agent lookup is
scoped. Phase 8 reauthorizes before its idempotent completed path. Skill builder chat
attachment uses a frozen DTO and validates persisted Session, Conversation and Agent
scope before linking; session Agent lookup is also scoped. Scope regressions include
same-owner foreign claims and conversation attachment, with no state mutation.
`b06-builder-attachments-regression.log`: **81 passed**, 2.47s, owned mirror removed.

Real writer verification now covers default and a second company selected through
the actual active-org API: six native scenario pairs (**12 passed**, 47.30s), three
file scenario pairs (**6 passed**, 23.19s) and credential/tool/MCP original+switched
writes (**3 passed**, 12.51s). Added actual Agent publication/install proof for
Blueprint and Installation. CredentialDefault has no production creation endpoint
or service writer, so its explicit ORM writer is identified rather than overstated.

`b06-full-cutover-inventory-r11.log`: **5,002 passed**, 3 warnings, 219.39s,
child exit 0, owned mirror removed; hash
`33067e867ef2f5095925ac65772b782e1434e17fed100f655227fd782080ab4d`.
This snapshot includes Builder/attachment, Blueprint scope and correlation changes,
but predates subsequent native-writer parameter expansion and documentation-only
coverage mapping. Latest static/type/frozen checks are inspected separately.
No cutover commit or full B-06/P1/full-goal completion is asserted yet; foreign
mutation coverage, full source audit and independent final reviews remain required.

The inspected native acceptance mapping now includes actual production write flows
for Blueprint and Installation (`b06-native-install-writers.log`: **1 passed**, 5.06s),
not only fixture ORM construction. Default/second-company writer expansion is green:
`b06-native-second-company-writers.log` **12 passed**, 47.30s;
`b06-native-second-company-file-writers.log` **6 passed**, 23.19s;
`b06-native-second-company-credential-tool-mcp.log` **3 passed**, 12.51s.
These tests physically inspect emitted scope in both companies and use real JWT/
CSRF active-org selection. Scheduler syncing alone is explicitly stubbed; provider
execution/lifespan is not performed. All native databases are disposable owned labs.
CredentialDefault remains ORM-only evidence because no production writer exists.

Final static inspection for this native matrix continuation:
`b06-native-writer-matrix-final-types.json`: **0 errors**, 1,441 files.
`b06-native-writer-matrix-format-ratchet.log`: **444 selected, passed**.
`b06-native-matrix-frozen-gates.log`: **4 passed**, 1.88s, exact-20 ORM/frozen checks.
Ruff and `git diff --check` pass; ownership guard is **55 predicates, 0 additions**;
reviewed runtime dependencies remain **89 violations, 6 cyclic edges**; frozen
m77 source diff is empty. Full goal remains active and no cutover commit, P1,
RLS/OpenFGA/production/browser/human or full product completion is claimed.

### B-06 foreign HTTP mutations and conversation service writes (2026-10-11)

The exact-20 native matrix now compares raw physical snapshots before/after 28
schema-valid foreign mutation requests as the switched same owner and again as
an ungranted authenticated super-user. Every request returns 404, including
materialization/confirmation and installation update/delete paths. Native result:
`b06-native-foreign-mutations-r1.log` **1 passed**, 5.24s. This remains m83 owner-role
ASGI/database evidence, without lifespan, provider execution or RLS certification.

Direct conversation mutation services previously accepted a cached Conversation
or bare UUID without explicit organization context. Five regression cases first
failed with `DID NOT RAISE` (`b06-conversation-mutations-red.log`). Required typed
targets/context now guard title/pin changes, read state, activity touch, automatic
title and branch clear/switch using the exact persisted owner/org/company tuple,
row lock and freshly checked account/membership before mutation. Foreign and
revoked actors receive the uniform conversation 404. All real route callers pass
context, including branch selection and protocol state updates. No context default
or current-preference fallback is added.

`b06-conversation-mutations-regression.log`: **166 passed**, 6.14s, child exit 0,
cleanup `removed`, run-root SHA256
`6fbcc7a91ae9012e5d70b1381d9995ae173a4c3d93035208928e056ab1a61010`.
This includes 18 direct mutation cases (six operations across own/foreign/revoked
membership), plus chat, branch and protocol regressions. Native recheck
`b06-conversation-mutations-native.log`: **2 passed**, 9.03s. Full Pyright snapshot
`b06-conversation-mutations-full-types.json`: **0 errors**, 1,445 files.

The subsequent source inventory explicitly identifies more service seams to close
before B-06 completion: draft/side-chat promotion, runtime-policy initialization,
pinned-summary child read/write and the legacy bare-ID conversation reader.
Existing request dependencies authorize these routes, but they do not satisfy the
required private service context contract by themselves. These findings are not
waived by the green HTTP matrix. The final source audit and F1–F4 wave remain open.

Source audit also flags joined Agent projections for malformed parent/child scope
and retained history: `list_global_conversations_page` currently requires a live
Agent join, while `get_owned_ui_conversation_with_agent` and runtime eager joins
need private Agent predicates in the join itself. Orphan-history detail evidence
must not imply global-list or every joined child projection acceptance. Activity
touch also needs explicit inspection of the m83 `last_activity_at` contract.

Current mutation-stage static gates: changed-file types and format **449 selected,
passed**; whole Ruff and `git diff --check` pass; frozen/exact-20 checks **4 passed**,
2.07s; ownership ratchet **55 predicates, 0 additions**; runtime dependency guard
unchanged at **89 reviewed violations, 6 cyclic edges**. Frozen m77 source diff
remains empty. The broader B-06 cutover remains uncommitted and incomplete.

The complete isolated mutation-stage snapshot is green:
`b06-full-cutover-inventory-r12.log` **5,020 passed**, 3 warnings, 188.80s;
child exit 0, cleanup `removed`, run-root SHA256
`409e38dea7094456b5abe66b5dad4100b68516248133997e2f6c595ab6e139a7`.
This snapshot predates the following retained-history/Agent-projection corrections;
it must not be presented as coverage of later changes or full B-06 acceptance.

### B-06 retained history and joined Agent projections (2026-10-11)

Focused reproduction `b06-conversation-projections-red.log`: **4 failed, 2 passed**.
Global history omitted directly owned conversations after Agent deletion and when
the linked Agent had a foreign scope. UI/runtime eager reads also hydrated the
foreign Agent's private fields. All three query paths now left-join Agent with its
own exact user/org/company predicate in the join condition. Global history keeps
directly owned retained rows, still excludes live own hidden runtime agents, and
uses the persisted Agent name snapshot when no authorized live Agent is present.
`populate_existing` prevents an earlier cached relationship from surviving this
new scoped projection. Runtime consumers receive no live Agent for a mismatched
reference and continue to reject execution through their existing required-Agent
boundary. No provider execution is exercised here.

`b06-conversation-projections-regression-r2.log`: **67 passed**, 2.89s, child exit 0,
cleanup `removed`, run-root SHA256
`3d19f633ab37420dfc7518a2de7f074c811cf2e19fbe3e4fd0d413d7c08caf0d`.
Native HTTP/root/index recheck `b06-conversation-projections-native.log`:
**3 passed**, 12.90s. The six new projection cases use real SQLite ORM graphs;
the native recheck covers existing actual HTTP/root/index scenarios, not every
malformed-reference fixture on PostgreSQL. Targeted Pyright: **3 files, 0 errors**.

Remaining B-06 source findings are not waived: draft/side-chat promotion,
runtime-policy initialization, pinned-summary services, legacy bare-ID reader and
activity timestamp contract still need closure before the final review wave.
The 5,020-test r12 snapshot predates these projection edits; latest full regression
will be captured after the remaining service contracts are closed.

### B-06 conversation policy, promotion and pinned-summary services (2026-10-11)

Required `ConversationPromotion` and `ConversationPolicyTarget` now carry explicit
context into draft promotion and policy initialization. A shared conversation
authorization helper reads the exact owner/org/company root and freshly checks
account/membership, returning the uniform 404 before writes. Side-chat creation,
reuse and promotion require context; reuse refuses an existing unique child whose
scope does not match its parent. Run, queue, Agent API and detached trigger callers
pass their already-authorized persisted target context. No saved preference or
first-membership inference is added.

Red reproductions: `b06-conversation-draft-red.log` **2 failed, 1 passed**;
`b06-conversation-side-promotion-red.log` **2 failed, 1 passed**. Policy reproduction
`b06-conversation-policy-red.log` shows three unauthorized initialization cases
failing with `DID NOT RAISE`; the own case initially had a test-only missing flush,
corrected before reading the persisted tuple. The first regression uncovered 13
trigger failures because refreshing the whole Agent invalidated already-loaded
async relationships (`agent.model`, MissingGreenlet). The policy query now selects
current policy/profile scalar columns alongside the locked authorized Agent,
preserving the caller's eager runtime relationships.

Activity touch now writes m83 `last_activity_at` as well as the existing update
timestamp; `b06-conversation-activity-red.log` **1 failed, 2 passed** reproduced
the stale activity field. The legacy conversation reader now requires context and
uses the private root predicate; `b06-conversation-reader-red.log` reproduced its
foreign-company disclosure.

`b06-conversation-policy-side-activity-regression.log`: **113 passed**, 3.79s;
child exit 0, cleanup `removed`, run-root SHA256
`226a8ad9333f1b88301cedb668aaa9f827fa12237c3a41bd4175a7f673b2147b`.
`b06-conversation-reader-regression.log`: **77 passed**, 3.09s; cleanup `removed`,
run-root SHA256 `f9ed817278bbb787ea0a94851ab770d31f718413fed70973a61835697d211503`.
`b06-conversation-policy-side-native.log`: **5 passed**, 21.07s, covering actual
default/selected-company side-chat HTTP policy creation/reuse/promotion, Agent API
writers and the authenticated root matrix.

Pinned-summary read/pin/unpin services now require the private conversation
context; reads recheck membership without a row lock, while mutations lock the
private root before child lookup/source validation. `PinSummaryTarget` replaces
the unscoped four-parameter writer. `b06-pinned-summary-scope-red.log` **6 failed,
3 passed** reproduced foreign/revoked child reads and mutations. All router/test
callers pass context, and retained agentless history remains supported.

`b06-pinned-summary-scope-regression.log`: **51 passed**, 1.77s; child exit 0,
cleanup `removed`, run-root SHA256
`f9f2acaf691574754436010edd31f1c1035f903c8cc6161cd41d6596e6957faa`.
`b06-pinned-summary-native.log`: **2 passed**, 8.98s. The concurrency scenario now
uses an independently migrated, created/dropped m83 database, real private actor
and simultaneous service transactions; both first pins commit and one valid child
remains. Source snapshots in this focused test are explicit fixtures, not actual
LLM/checkpointer messages. The other case runs **34** foreign HTTP mutation paths
per owner/operator actor and checks twenty-root physical snapshots unchanged.

Whole Pyright snapshot `b06-private-conversation-services-types.json`: **0 errors**,
1,449 files. Ruff passes. Full isolated r13 is running after these changes;
no final B-06 acceptance/commit, RLS, startup/provider/browser/human or full P0–P8
completion is asserted. Deletion/maintenance child traversal and the remaining
cross-domain source audit still precede the independent final review wave.

The dependency gate caught a new router-to-ORM import introduced by the pinned
route annotation. `PinnedConversationRef` now exposes only the checkpoint property
the route needs, preserving the existing layer boundary and allowing agentless
retained histories without the legacy `conversation_ref` non-null-Agent check.
Dependency guard is again **89 reviewed violations, 6 cyclic edges** with no new
violation. The HTTP round-trip regression explicitly covers both live-Agent and
retained agentless conversations. The full r13 mirror predates this final router
protocol/retained-test adjustment; its result remains a bounded snapshot.

Full isolated r13 finished: `b06-full-cutover-inventory-r13.log` **5,047 passed**,
3 warnings, 190.44s; child exit 0, cleanup `removed`, run-root SHA256
`7e74e7c57840eb01caff6243d0cb5eb9438891f76e74a9671378f15830a15bd2`.
Final protocol/retained-history adjustment:
`b06-pinned-summary-router-protocol-r2.log` **17 passed**, 0.97s; child exit 0,
cleanup `removed`, run-root SHA256
`7498ebfd59fee9f925f74f091e904e75d62f86f95fba07e0071d622958270c62`;
targeted Pyright **2 files, 0 errors**. Changed-file types/format stage is
**457 selected, passed**; ownership guard remains **55 predicates, 0 additions**.
The full B-06 cutover is still uncommitted; remaining deletion/maintenance and
cross-domain parent/child audit plus independent review are required.

### B-06 deletion, explicit maintenance and same-org peer secret boundary (2026-10-11)

Direct deletion now uses the locked private conversation helper and fresh actor
check. It refuses a foreign-scope side child before checkpoint/offload effects;
the owned side-history update additionally includes the exact private predicate.
`b06-conversation-deletion-red.log`: **2 failed, 2 passed**, reproducing revoked
membership deletion and foreign child mutation. New cases keep filesystem effects
in owned temporary storage and stub only the external checkpoint deletion seam.
`b06-conversation-deletion-regression.log`: **55 passed**, 2.46s; isolated cleanup
`removed`, run-root SHA256
`ec3fb9b52c79b96b3b88ebdcf27e66779ebcdec824f508e31a39b18be8af67ee`.

Draft GC requires `DraftConversationCleanup` with a non-default `PlatformScope`;
the named scheduler job supplies that explicit maintenance context. This remains
an internal typed maintenance contract, not B-13 platform-session/RLS enforcement.
The collector now preserves any draft with a side child, durable run or queued
input, even before the first message event. `b06-conversation-gc-red-r2.log`:
**3 failed**, each incorrectly deleted a parent containing child work. Fixture
input uses the actual pending/position/Agent contract and the Agent/conversation
owner tuple is aligned. Retention-positive validation, new/normal history and
message-event protection remain in their existing regressions; no B-08 retention
policy or archive lifecycle is introduced.

Combined `b06-conversation-deletion-gc-regression.log`: **68 passed**, 2.59s;
child exit 0, cleanup `removed`, run-root SHA256
`adb35ba913ea07abcdd2d63dfff2ee6d7aa92276d08730d45d7b16902664c8b2`.
`b06-conversation-deletion-native.log`: **4 passed**, 16.65s: actual m83 FK-backed
own/revoked/foreign-child deletion plus the 34-path JWT matrix. Rejected cases
leave the physical parent/child and SET NULL reference unchanged; native checkpoint
deletion is explicitly stubbed, filesystem root is temporary, no live provider or
production checkpoint cleanup is claimed.

The cross-domain audit reproduced another private read gap: the read-time secret
collector used organization scope without owner scope for its linked Agent.
`b06-same-org-peer-secret-red.log` **1 failed**, showing a same-org peer's actual
dummy credential in the returned secret set through a malformed owned conversation
link. Agent loading now uses the full private predicate before identity/credential
resolution. `b06-same-org-peer-secret-regression.log`: **23 passed**, 0.91s; child
exit 0, cleanup `removed`, run-root SHA256
`5542938b858d23b04182304bb05baf8043cbf76893f2c439caa80f9b5423134e`.

Current phase static gate: changed-file types/format **460 selected, passed**;
ownership **55 predicates, 0 additions**; dependency guard unchanged at **89
reviewed violations, 6 cyclic edges**. Whole source type snapshot before the added
native deletion test: **1,450 files, 0 errors**. Full isolated r14 is running.

Audit frontier remains explicit: SkillBuilder failed/canceled claim recovery still
uses a user-only update and needs exact persisted context. The original HTML task
appendix assigns Skill feedback/evaluation authorization to D-03 and organization
usage/counter schema and permissions to H-03/m89; those remain full-goal work,
without promoting a 21st m81-scoped table or claiming those phases here. This
distinction is supported by the plan itself (task B-06, H-03 and appendix), not by
a narrower substitute objective. No B-06 commit/final review/full-goal completion
is asserted yet.

Full isolated r14 completed: `b06-full-cutover-inventory-r14.log` **5,056 passed**,
3 warnings, 243.46s; child exit 0, cleanup `removed`, run-root SHA256
`30b56742ac28af98d0ca667a180aab5b9a9ee75d243f153015fcb1ee54827d55`.
Latest whole type snapshot `b06-deletion-maintenance-secret-final-types.json`:
**1,451 files, 0 errors**. Ruff and `git diff --check` pass. The original frozen
m77 fixture/source is unchanged. This closes the current deletion/GC/collector
regression unit, while SkillBuilder recovery scope and the final source audit/
review gates remain open. Full P0–P8 goal stays active.

### B-06 recovery checkpoint and independent review (2026-10-11)

The previously identified SkillBuilder claim-recovery writer now retains the
original required OrgContext across the separate recovery transaction, including
cancellation paths. The update qualifies session ID, owner, company, organization
and confirming status. Focused recovery: **22 passed**, native recovery/matrix/
migration batch: **13 passed**, frozen model gate: **4 passed**. Full isolated r15
before independent review: **5,059 passed**, cleanup removed, run-root SHA256
`d3b967fbebd6fbc71024d4f9f207b158b181058fb19bcc0e4cbc7f181085db28`.
These are bounded prior snapshots, not acceptance for later corrections.

A dedicated attached review checkout captured immutable local candidate
`9595fd89554040e72282f103b3c5866b4e9a33b6`, based on
`54ce914f87a79e182e5228ddbc84e6dddb66569f`. The primary cutover remains
uncommitted and the candidate has not been adopted, pushed or deployed. All five
independent lanes returned **FAIL**. Stamped reports and the review ledger are
preserved under `output/org-authz/b06-review/`; they are ignored local evidence.

Fresh independent QA on that exact candidate: native m83 **41 passed**, focused
SQLite **206 passed**, official full suite **5,058 passed, 1 failed**, cleanup
removed, run-root SHA256
`b2dc70eb17541e5ae1b775e34b9183419ce22a93322f1ec6dda3ba6e5c567d60`.
The full-suite failure was the newly tracked native-scope matrix missing from the
document inventory. This demonstrates why the earlier uncommitted snapshot's
full-green result cannot be substituted for an exact committed-candidate check.
No owned temporary native databases remained after that QA.

Review findings require B-06 corrections, rather than moving their requirements
to deferred phases: Builder node persistence, SkillBuilder runtime/session/tool
bindings, private runtime child Agent/Tool materialization, trigger joined
projections and effects before authorization, and current artifact version parent
validation. Runtime/security witnesses used malformed persisted references;
ordinary HTTP reference forging was not demonstrated.

### B-06 first review corrections (2026-10-11)

Builder checkpoints now include their original organization/company tuple. A
shared node helper parses that required tuple, restores current actor membership,
and locks the exact privately owned session before phase-7 save, phase-8 confirm
or error writes. Missing old checkpoint scope fails closed. Node regression red:
**8 failed, 2 passed**, covering revoked membership, moved session, wrong user and
missing scope; green with related Builder cases: **49 passed**, targeted types
**0 errors**. This does not resolve all SkillBuilder tool/runtime findings.

Detached triggers now validate their persisted actor and referenced private
Agent/Conversations before claiming a durable run. Rejected scope leaves no new
run or failure-counter/status mutation; the existing trigger row lock and
duplicate-run guard remain. List joins independently qualify Agent and schedule
Conversation projections. The three authorization-denial regressions failed on
the reviewed behavior; initial new projection fixtures required missing model/
conversation fields to be corrected before their green check. Independent F2
provided the actual foreign projection/orphan-claim witness. Trigger regressions:
**39 passed**, targeted types **0 errors**. The old missing-Agent test now asserts
the approved no-private-side-effect behavior, rather than expecting error-state
mutation on a malformed reference.

Artifact current-version reads now require both version ID and the authorized
artifact's parent ID. An invalid non-null pointer is rejected before returning a
foreign object key. Red **1 failed, 2 passed**; related artifact green **19 passed**.

Subagent execution checks the child's exact owner/company/organization before
identity or credential resolution; linked Tools must be exact private resources
or explicit system owner-null/company-null/organization-null rows. Cross-company
Agent/Tool regression red **2 failed** after correcting the fixture to fixed
identity and stubbing only the provider/component boundary; green **11 passed**,
cleanup removed, run-root SHA256
`be9405da5e783983d63cea4c1cb7c13771e381af885714d7f4a2837a5aa915e1`.

The documentation inventory now includes the native matrix. Combined current
affected regressions: **125 passed**, cleanup removed, run-root SHA256
`3da2c054836933975ef5b2a39dc6f7fc4e8782aa23449d8004c443090211ad4e`.
Runtime dependency guard remains **89 reviewed violations / 6 cyclic edges**;
ownership guard **54 predicates / 0 additions**. These changes invalidate reuse
of the reviewed candidate SHA; no replacement candidate is accepted yet.

Remaining mandatory B-06 work includes scoped SkillBuilder runtime/tool bindings,
native concurrent same-item installation deduplication in and across companies,
and concurrent detached run/queue/artifact/memory isolation/recovery. Then fresh
full/static/native gates and all applicable independent review lanes are needed
at a new exact SHA. B-06 and the full P0–P8 goal remain open. RLS/platform sessions,
shared OpenFGA, later D03/H03/m89, providers/browser/humans and timed release gates
are not certified by these backend tests.

Current correction static checks: changed-file types **467 selected, 0 errors**;
changed-file format **467 selected, passed**; selected Ruff and `git diff --check`
passed. Only the new matrix and its inventory entry were staged to make the
tracked-document contract concrete for the affected repository check. No primary
commit, candidate adoption, push, PR or deployment has occurred.

### B-06 SkillBuilder scope and native install concurrency corrections (2026-10-11)

Skill Builder tools now require an explicit OrgContext instead of an optional
user-only identity. The runtime binding protocol forwards the frozen context.
Each validate/evaluation/sandbox/finalize action restores current actor authority,
locks the exact privately owned session, verifies the stored workspace binding,
and checks the attached private Conversation/Agent before file operations. The
finalize service itself receives the caller's required context; it cannot derive
a different company after a session move. Existing direct unit fixtures now seed
real User/membership rows rather than relying on absent account state. Policy-only
configuration fixtures use valid UUIDs; no production authorization fallback was
added for their former arbitrary strings.

The initial four-tool scope regressions were **12 failed**, covering foreign
company, revoked membership and mismatched bound workspace. Runtime reverse lookup
also reproduced an owned Conversation linked to a foreign SkillBuilderSession:
**1 failed**, and the prior path reached workspace creation and system model
resolution. The lookup now requires exact owner/company/org and fresh Conversation
authority before any workspace creation or system credential/model resolution.

An additional persisted-workspace witness was **4 failed / 12 passed**: a private
session whose stored path was replaced by another session's directory still passed
simple path-equality validation. A shared storage helper now supplies the canonical
`skill-drafts/<session_id>` contract to authorization, runtime lookup, workspace
creation and finalization. The canonical-path check blocks such malformed persisted
paths. These are bounded service/tool tests; no ordinary HTTP path-forging exploit
or live provider execution is asserted.

Combined current SkillBuilder regressions **59 passed**, cleanup removed, run-root
SHA256 `ac682985586af3442bf2ca2c4bf006e2aa5e665f12fef126429ebb99e5d60da6`.
Evidence logs reside under `output/org-authz/p1/b06-skill-builder-*`. The original
red cases' filesystem reads, sandbox execution and finalize boundary are observed
with narrow external seams; the asserted authorization and session state are real
SQLite service/ORM operations.

Task 13's missing concurrency scenario now has fresh native evidence:
`b06-install-concurrency-native.log` **1 passed**, 4.45s. The test creates an actual
m83 lab, real JWT/CSRF actor and two companies, publishes one real Agent template,
and launches four HTTP installation transactions (two per company). A real
PostgreSQL holder transaction keeps the item row locked until all four requests
reach the original production lock function. The observer does not substitute a
fake lock, inject rows or bypass the installer. After release, the requests yield
one reused installation/Blueprint within each exact company and distinct IDs
between companies. The persisted installation and Blueprint owner/org/company
tuples are inspected. This is owner-role m83/ASGI evidence, not RLS, browser,
provider, startup or marketplace OpenFGA acceptance. Owned native lab database
inventory after the run: **0**.

Task 14 still requires simultaneous detached queue/run/stream/artifact/memory
execution and recovery. The next scenario will exercise the actual queue worker
and run lifecycle with a bounded provider boundary, rather than substituting
sequential per-company service tests. Full isolated/static/native regression and
all applicable independent reviews must then run at a new exact candidate SHA.
B-06 is not accepted, and full P0–P8 remains active.

Latest static snapshot after these corrections and native concurrency test:
changed-file types **473 selected, 0 errors**; format **473 selected, passed**;
ownership guard **53 predicates / 0 additions**; runtime dependencies unchanged
at **89 reviewed violations / 6 cyclic edges**. Selected Ruff and
`git diff --check` pass. No replacement candidate has yet been committed or
approved, and these focused results are not a full backend or product gate.

### B-06 actual detached concurrency and correction gates (2026-10-11)

Task 14 now has a native two-company execution/recovery experiment, not merely
serial per-company transitions. Two actual `launch_claimed_input` queue workers
restore contexts from their stored input/run/Conversation bindings, register real
run tasks and remain live together at the deterministic provider boundary. A real
CSRF preference-switch request commits while both are paused. Each then invokes
the production artifact recorder/storage and memory writer, persists stream events
and completes the actual run lifecycle with its original organization. Own content
is read through HTTP and foreign content returns 404; runtime memory and persisted
trace/replay payloads contain only their source company's marker. Two concurrent
workerless cancellation recovery sweepers cancel two later claimed runs once;
their cleared queue-run pointers are checked, and the previous outputs remain
unchanged. No recovery invokes the provider boundary.

Initial experiment needed an ordinary-account fixture correction (the default
first-admin flag differed from the constructed context) and the input-pointer
assertion follows the existing recovery contract. It did not reproduce an
authorization defect. With the corrected fixture, native **1 passed**, 4.86s.
The test deliberately excludes initialized LangGraph checkpoint and real provider
execution; optional checkpoint-metrics baseline warnings are not accepted as
checkpoint/token-usage/billing evidence. This is owned temporary m83 ASGI/worker
evidence, not RLS, application startup or full product acceptance.

Final verbose native correction batch: **15 passed**, 81.60s, exact node names in
`b06-review-corrections-native.log`; includes the two mandatory concurrency cases,
the exact-20/34-foreign-write HTTP matrix, original SkillBuilder claim recovery and
actual frozen migration round trips. Owned native lab database inventory: **0**.

All concrete authorization domain files together: **443 passed, 126 deselected**,
184.36s, cleanup `removed`, run-root SHA256
`a0a62d4b9bce47231fa20f0b4b94da06fa67943faec38c5096382132e5bf64d9`.
The explicit Task 14 no-side-effect selection: **26 passed, 2 deselected**, 1.85s,
cleanup `removed`, run-root SHA256
`27206afea4a0b0e12307eaddb55741fc7af0d7cc7ea258a88cf9f228b6262fa1`.
Actual frozen m77/exact-20 ORM gates: **4 passed**, 2.13s. Whole-source Pyright:
**1,462 files, 0 errors**; changed types/format **475 selected, passed**. Whole Ruff
passes after removing an unused fixture import; predicate additions remain zero.

The local B-06 execution plan now explicitly maps its provisional consolidated
test filenames to the concrete domain files. Its acceptance criteria are unchanged;
the mandatory concurrency commands use the actual owned PostgreSQL lane to observe
real locks and detached execution. No missing-path command or deselected native
case is treated as passing evidence. Full isolated r16 and a fresh exact-SHA review
candidate remain pending. The original handoff files are unchanged; B-06 and the
full P0–P8 goal remain active.

Full isolated r16 completed: **5,091 passed**, 3 warnings, 263.39s, child exit 0,
cleanup `removed`, run-root SHA256
`dac75cf8cb29cc834e68c5a6f138a2f35e212e333c9f23f991665e9f05721fa7`.
This is the correction snapshot before the new dedicated review candidate; a
fresh exact-candidate gate remains required. The only later source changes before
preparing that candidate are a formatting-only SSE interpolation split and unused
test import removal, plus evidence documentation. No primary cutover adoption or
full-goal completion is claimed.

### B-06 second independent review and private preflight corrections (2026-10-11)

Dedicated candidate `484932c517425e50f04b1ab4b19bf68588499e9a` was rejected:
F1/F2/F3/F4 FAIL and F5 context PASS. All five stamped terminal reports and
bounded QA logs are preserved in `output/org-authz/b06-review-r2/`; no primary
adoption or partial cutover commit follows from this candidate. Its fresh QA
results were 5,091 full-suite passes, 443 focused passes, and 33 native passes /
2 failures. Both real installation/detached concurrency requirements passed.
The native failures were stale suspended-Trigger expectations: the executor
correctly returned before any durable claim. Native assertions now explicitly
require no run plus unchanged Trigger status/failure count; **2 passed**, 9.03s.

F1 reproduced an owned Artifact projecting a same-organization peer's private
Agent name through a malformed persisted reference. Both batch and single
summaries now match Agent owner, organization and company to the Artifact.
Initial regression: **2 failed**; first focused correction batch: **21 passed**.
The single-row predicate uses the central authorization helper, preserving the
no-new-ownership-predicate ratchet rather than increasing its baseline.

F2 reproduced foreign Tool denial after a committed Trigger claim, leaving a
running row that blocked forced retry. F4 reproduced the Trigger risk scanner
decrypting a same-organization peer subagent's MCP credential before the direct
subagent guard. These require malformed persisted capability edges; ordinary
HTTP edge forging, provider calls and external MCP connections are not claimed.
Capability assembly and one-hop scanning now occur inside preclaim authorization.
The shared tool builder first checks the Agent's complete private tuple and checks
MCP Server private scope or explicit platform null/null scope before auth resolution.
Denied executions return without run/counter/conversation effects.

New regressions failed before correction (foreign Tool: **1 failed**; peer child /
peer Server: **2 failed**). Corrected preflight/scope/runtime-child batch:
**11 passed**, 0.90s; existing Trigger/credential/projection regressions:
**41 passed**, 2.10s. The MCP witness uses actual scoped SQL/Cipher with a dummy
secret and a spy delegating to real decrypt; it asserts no decrypt and no claim
across two forced attempts. Final whole-source/static/isolated gates and a fresh
exact-SHA five-lane review remain required. B-06 and full P0–P8 remain active.

### B-06 third review: complete-tree preflight and Skill/MCP consumers (2026-10-11)

Candidate `51a06237553c0d58418e7c27e320114a59d45f59` is rejected. All five
terminal reports are preserved in `output/org-authz/b06-review-r3/`: F1/F3/F5
PASS; F2/F4 FAIL. Fresh exact-candidate QA passed full **5,096**, authz **448**,
native **35** (including both actual concurrency requirements and corrected
suspended-Trigger assertions), all static/document gates and owned DB cleanup0.
Those passes do not erase the independent reproduced findings or cover the
later primary-only corrections. No primary cutover adoption follows.

F2 reproduced valid parent Tool credential decryption before malformed peer-child
denial. The new pure capability-tree preflight validates all supported one-hop
Agent/Tool/MCP/Credential/Skill references before any node's secret resolution.
Trigger scanning precedes root configuration; ordinary chat and API configuration
use the same preflight before LLM resolution. Owned-parent-secret regressions
failed **2** before correction; an ordinary invocation regression failed **1**.
Corrected focused runs: **29** Trigger cases, **31** runtime cases and **52**
chat/SkillBuilder cases, with isolated cleanup removed. Intermediate full r18:
**5,097 passed**, 3 existing MCP deprecation warnings, cleanup removed, root hash
`2d7de0865ef2eab11a12decf5f4869cb3631af67ebb1420c83259e279e53c2ae`.
That full run predates the following Skill/MCP corrections.

F4's foreign Skill descriptor is now rejected before configuration/materialization
and credential resolution. `build_agent_skills` requires `OrgContext` at every
parent/subagent/Trigger/invocation caller. Its real foreign persisted-edge test
failed before correction; the completed experiment supplies an actual temporary
foreign file and asserts no materializer call or runtime directory creation.
The initial fixture incorrectly used the retired content column; that setup error
is not counted as an authorization reproduction.

MCP health list/history/probe use the current private server scope, and explicit
or persisted probe credentials must resolve in that same context. Initial list
and history cases reproduced exposure; the initial probe stub had a session error
after the unauthorized probe was reached, not a successful probe-result receipt.
The corrected test returns a row in the actual request session and verifies the
owned probe succeeds before all three foreign paths deny. Combined health cases:
**14 passed**, 1.00s; no remote probe or real secret was used.

MCP Apps now require request `OrgContext`, validate binding/run/Conversation/Agent
references and current membership, scope linked Servers and credentials before
proxy operations, and retain the original scope in runtime callbacks. The runtime
factory requires captured org/company IDs instead of reconstructing preferences.
All three real HTTP commands previously returned200 after a company switch;
now they return404 before read/call proxy seams. Existing cache/App tests:
**25 passed**, 1.16s. Callback experiments preserve original scope after a real
preference switch and deny a removed membership without inserting a binding;
the combined App/private-server regression batch: **28 passed**, 1.36s.
Binding reuse also rejects mismatched persisted provenance before returning an ID.

F4's proposed all-null MCP owner representation conflicts with the frozen m83
contract: Skill/MCP retain required user FKs on explicit platform rows. A different
creator on an `is_system=True`, org/company-null row does not make that platform
row private. Both runtime and bound-App candidates use the existing explicit
platform exception; private rows still require the complete owner/org/company
tuple. No migration or owner nullability was changed. Actual HTTP creation cannot
set the system flag (it remains false), and mutation rejects that extra field422.
The required-creator/null-scope runtime and flag-write regression batch:
**29 passed**, 1.46s. This establishes the existing platform representation, not
future shared OpenFGA authorization or tenant-owned system-resource policy.

Final combined Skill/MCP focused isolated batch: **42 passed**, 1.85s, cleanup
removed. Predicate ratchet is **47 / 0 additions**, with its baseline unchanged.
Fresh full/static/native gates and a new immutable five-lane review are still
required for the expanded correction source. B06 and full P0–P8 remain active.

The health router now delegates its MCP root lookup to the scoped service, so
its direct `app.models.mcp_server` dependency disappeared. The dependency baseline
was tightened by removing only that verified obsolete exception; no new exception
or cycle was added. Guard: **88 reviewed violations / 6 cyclic edges**; dependency
guard regressions **47 passed**, 2.32s. Whole types: **1,470 files / 0 errors**;
changed types/format **489 passed**, whole Ruff and documentation inventory pass.

Full r19: **5,107 passed / 1 failed**, cleanup removed. The failure is the existing
subagent test double still accepting one argument after `build_agent_skills`
became context-required. The typed replacement accepts and asserts the supplied
context; focused **4 passed** (not a production fallback or weakened boundary).
Full r20 and the current native35 batch remain pending before a replacement
immutable candidate can be reviewed. No r19 success or B06 adoption is claimed.

The corrected-source native batch completed: **35 passed**, 172.36s, including
the real installation and detached execution/recovery concurrency scenarios,
exact20 authenticated read/write matrix, migration roundtrips and representative
private writers. Owned disposable database inventory is **0**. This remains
owner-role m83/ASGI evidence without startup/RLS/live-provider/browser acceptance.
Full r20 and new exact-SHA independent review remain required.

Full r20 completed: **5,108 passed**, 3 existing MCP SDK deprecation warnings,
259.06s, child exit0 and cleanup removed. Run-root SHA256:
`91ff05f17dbcd8d4ceece7594a749a674d5acd68384e76a77f410b90272a6460`.
The only later source edits were the already-verified test-double formatting and
evidence documentation, not a runtime change. Current changed types/format:
**489 selected, passed**; whole Ruff passes; whole types **1,470 / 0 errors**.
Native35/cleanup0 and the unchanged exact20/shared6/m83 contract remain bounded
as described above. A new immutable candidate with five fresh stamped review
lanes is required; no primary adoption, deployment or full-program completion.


### B06 R4 independent review corrections (2026-10-11)

Immutable candidate `29740be5202f13a2a1ea272a442d836c8c744aca` is rejected:
F1/F5 passed; F2/F4 each returned a concrete HIGH blocker. F3 is still running
and its terminal exact-SHA result must be preserved before the aggregate closes.
Primary has not adopted this candidate.

F2 reproduced an enabled foreign private stdio MCP link being omitted before
server scope validation because its URL is absent. Both the pure capability
preflight and runtime projection now check scope before URL eligibility. The
projection also validates a linked credential before skipping URL-less servers.
The new regression tests cover foreign owner, same-user other-company server, and foreign credential variants,
assert no decryption and no TriggerRun claim across two forced attempts. The
corrected fixture first reproduced DID NOT RAISE; green evidence is recorded in
`output/org-authz/p1/b06-r4-stdio-red.log` and subsequent focused logs.

F4 identified the persistent sequential/concurrent health sweep and lightweight
MCP poll resolving same-user credentials across organization boundaries. Shared
probe validation now restores the persisted server's exact private actor, checks
fresh membership, and loads the linked credential under a lock before any auth
resolution or connection. Scheduled mismatches are skipped without a probe or
health-history write; direct discovery/probe calls deny. Platform rows retain
the frozen m83 explicit system/null-scope representation; platform credential
metadata must also be explicitly platform-scoped. Model health/company-scoping
remains the separately deferred TENANT_ONLY3/D10/H work.

Tests reproduced all three scheduled-path failures before correction. Added
direct discovery denial, positive owned-credential probe with preferences in the
other company, and membership-removal denial with no preference fallback. Legacy
health fixtures now seed real memberships instead of bypassing the new guard.
Final bounded focused evidence: **49 passed**, 1.88s, no warnings, in
`output/org-authz/p1/b06-r4-fixes-focused-final.log`. Dependency guard remains
**88 reviewed violations / 6 cycles**, without baseline changes. Full r21,
changed-source/static gates and a new immutable five-lane review remain required.
These are B06 corrections, not full P0–P8 completion or deployment evidence.

R4 corrected-source focused follow-up: **50 passed**, 2.63s, including the exact same-user/different-company stdio-server witness. Whole types **1,473 files / 0 errors**; changed types/format **494 passed** before this final test-only extension. Full r21 was already collected before that extension; its terminal receipt must be paired with this focused run, and the next immutable candidate gets its own fresh exact-SHA F3 verification.

R4 review is now fully terminal: **F1/F3/F5 PASS, F2/F4 FAIL; aggregate FAIL**.
Fresh F3 reproduced 5,108 full, 460 authz and 35 native passes, static guards,
empty frozen-m77 diff, source immutability and owned database cleanup0 at exact
29740 SHA. Reports and regular raw QA receipts were copied to primary
`output/org-authz/b06-review-r4/` immediately. Passing F3 does not override the
concrete F2/F4 security findings or cover the subsequently corrected source.

Full r21 completed: **5,115 passed**, 3 existing MCP SDK deprecation warnings,
258.78s, child exit0 / cleanup removed. Receipt SHA256:
`513ccc1b8faae4df7c25dca78bb437c68fecd24991abb28fbc3573e3cdfc1248`.
The later same-user stdio variant and test-only exhaustive-match/fixture cleanup
are verified separately (8 negative/positive tests); production code did not
change after full-suite collection. Ruff and selected format pass. Fresh R5 F3
must run the complete fixed immutable candidate (now 5,116 expected cases); no
prior exact-SHA coverage is reused. The owned PostgreSQL inventory remains0.


### B06 R5 independent review corrections (2026-10-11)

All five lanes are terminal at `7a27a8f46d707724445fa1b1fe5f403c7d3c773b`:
**F1/F2/F4 FAIL, F3/F5 PASS; aggregate FAIL/REQUEST_CHANGES**. Candidate not
adopted. Reports, witnesses and regular raw QA receipts preserved primary
`output/org-authz/b06-review-r5/`. Fresh F3 reproduced **5,116 full**,
**468 authz**, **35 native**, all static/contract gates, clean source and labs0;
those passes do not override concrete review failures or cover new corrections.

F1's direct-service witness shows credentials.update accepting another company
row without mandatory context while HTTP PATCH is already filtered upstream.
Update, test recording and audit retrieval now require explicit context plus
typed immutable patch/test/query input. The service reloads a locked credential
using exact owner/org/tenant and fresh private membership, before data decryption
or changes. Platform mutations require explicit PlatformScope and an exact
system/platform/null-owner/null-org/null-tenant row. Actor identity cannot differ
from private context. All actual callers, including OAuth regression/audit tests,
pass explicit context. Context never defaults to the user's selected preference.
The new tests deny foreign update/test/audit before effects, preserve valid
original scope despite preference change, reject platform scope on private rows,
and deny after membership removal. R5 witness is the actual behavioral red;
new API tests additionally failed against the old signature before correction.

F4's real JWT/CSRF wizard-preview witness shows same-user foreign and revoked
credential secrets sent through /api/mcp-servers/probe. The route now requires
OrgContext and validates the non-system credential under it before auth
resolution. Local HTTP regression reproduced foreign200 before fix, then
owned200/foreign404/revoked404 with no denied-path decrypt/connector calls.

F2 reproduced public share metadata from a same-user foreign-company Agent
through a malformed persisted Conversation→Agent link. Public resolution now
validates the linked Agent exact tuple against the share's authorized private
Conversation before returning its name/description; mismatch returns no share.
Existing valid share control remains successful. No checkpoint/provider work is
needed for a denied chain.

F2 also found the new platform credential guard succeeded but probes incorrectly
used the required MCP creator FK as credential owner. Discovery and persistent
health now pass the validated credential's subject to auth resolution. System
platform credential NULL owner is distinct from required MCP creator FK. Both
real encrypted platform credential regression cases failed before correction
and pass afterward; private scheduled-scope negative tests still pass.

Bounded corrected-source evidence: credential/OAuth/LLM-sync **40 passed**,
MCP/share/OAuth/health combination **60 passed**, final credential/MCP/share
selection **62 passed**, 2.43s. Seven targeted files typecheck0; selected Ruff
passes after import/format cleanup. Full r22 and all source gates are running.
No schema/hand-off source changes, shared/main DB migration, deployment, archive
completion, RLS activation, shared-FGA or whole-goal completion is implied.

Original F4 wizard witness rerun against corrected primary source: **3 passed**,
0.55s (owned, switched company, independent membership revocation). Thus the
revocation case is also tested independently of the sequential company-switch
regression. Changed-source types/format **498 passed**, dependency guard88/6,
ownership47/0 unchanged; whole Ruff and documentation inventory pass.

Original F2 public-share/platform-probe witnesses rerun against corrected primary:
**4 passed**, 0.72s, including the owned public HTTP share control. Whole types:
**1,477 files / 0 errors, warnings or information**. Frozen schema/handoff sources
remain unchanged; native R5 35-pass evidence belongs to old immutable source,
so the next independent F3 must rerun the native batch for its own candidate.

Full r22 completed: **5,124 passed**, 3 existing MCP SDK deprecation warnings,
220.16s, child exit0 and cleanup removed. Receipt SHA256:
`5657d3b8c5a0d4c59410b9acd747429f7be74babc96d2f338b3f9d7ef05fdbc0`.
Changed-source types/format **498 passed**, whole types **1,477 / 0 errors**,
whole Ruff/docs and unchanged predicates47/0/dependencies88/6 pass; owned labs0.
The later import ordering and evidence-only changes do not change runtime code.
A new atomic503-file candidate and all five fresh stamped review lanes are
required before primary adoption. All previous rejected candidates remain
rejected; passing receipts alone do not establish B06 acceptance.


### B06 R6 independent review corrections (2026-10-11)

All five lanes terminal at `92f8ea29bab2ac15cf4a7cfb685b8145aa971810`:
**F1/F2 FAIL, F3/F4/F5 PASS; aggregate FAIL/REQUEST_CHANGES**. Reports and
regular raw QA receipts preserved primary output/org-authz/b06-review-r6.
F3 freshly reproduced **5,124 full / 476 authz / 35 native / 5 additional
witnesses**, static gates, cleanup/labs0 and clean immutable source. That bounded
PASS does not override the two rejecting lanes or cover the new primary fixes.

F2's actual JWT/CSRF Tool-run witness reached foreign-company credential decrypt
and bounded runner through an owned Tool with a malformed persisted credential
edge (HTTP200/decrypt1/runner1). Standalone runner and service now take an
explicit private target/options, reload a locked Tool under captured scope and
fresh membership, then reload a locked exact-scope credential before decryption
or opening the client/dispatch. All five direct-run tests and HTTP/service
callers migrated; the last-use test double now accepts/asserts the typed inputs.

F2's read-time collector witness decrypted an owned LLM credential before finding
a malformed foreign Tool credential. The shared lightweight collector now
preflights the complete loaded one-hop capability metadata before any secret
resolution. Malformed bound capabilities raise neutral AppError404, preventing
read egress rather than returning an under-masked payload. Missing historical
agent handling remains separately retained. Local regression reproduced the
missing denial and then verifies no decryption; original foreign-bound collector
test now asserts the stronger before-effect denial contract.

F1 reproduced five SkillBuilder bare-ORM service mutations committing after
membership removal, plus foreign-company Session→Conversation consent projection
that read draft metadata before a scope check and committed foreign consent.
Five mutations now require frozen explicit target/context and typed message/eval
input, reload locked exact-scope session and fresh actor before modifying fields.
All production/test callers migrated with explicit captured or declared unit
context. Consent command carries explicit conversation/context and parsed resume
payload; service query validates Session/Conversation/Agent exact tuple before
any draft read, validates canonical session workspace, and records consent through
the required mutation guard. Tests seed real conversation/agent graphs instead
of dangling SQLite-only session FKs. A trigger-eval legacy fixture now seeds a
real user/membership before directly calling the guarded service.

Dependency guard was tightened by pruning only the verified obsolete consent
router→SkillBuilderSession model exception after query delegation: **87 reviewed
violations / 6 cycles**, no new exception/cycle. Ownership scanner now **46 /
0 additions**, baseline unchanged. Focused isolated combination: **74 passed**,
3.11s, childexit0/cleanupremoved, receipt
`b56cc7205e6ea78e55461ba20279b47a92efd1e05627e2b4e7a104bb781c157a`.
Full r23 and current type/format gates remain pending; no R6 candidate adoption.

Execution correction: two focused FS-containing commands were mistakenly run
without the isolation wrapper. Four newly created dummy-only text-Skill
directories in the shared data symlink were identified by exact fixture hashes
and each command's creation/modification time interval, and recovered into
ignored output/org-authz/p1/recovered-owned-test-files. All four original paths
are absent; no unknown file was removed. Ownership/recovery evidence:
output/org-authz/p1/b06-r6-fs-ownership-proof.json. No main/shared DB migration
or environment modification occurred. Subsequent FS-containing tests use the
official isolated wrapper. The typed consent test's filename ending -red.log
actually recorded a pass because its command ran concurrently with the fix;
only F1's preserved six behavioral failures are counted as consent's red proof.
This correction is recorded rather than claiming shared filesystem was untouched.

R6 corrections type refinement: TriggerEvaluationResult is a TypedDict with
object-valued Mapping compatibility. The producer's structured result is parsed
through the standard Pydantic JsonValue adapter before building the frozen eval
input; the Korean fixture declares its actual JSON value type. This avoids a
cast/Any escape. Type adapter/fixture follow-up happened after r23 collection,
so its final result is paired with the new focused **16 passed**, 1.40s,
childexit0/cleanupremoved, receipt
`af20884f5b5b6fe31d8892879b32347dea5a51d81292f7cba54998ea2c8e325c`.
Changed types **506 / 0 errors**, selected format and whole Ruff pass. No
previous candidate review PASS is reused; a fresh candidate/F3 full run is needed.

Full r23 completed: **5,132 passed**, 3 existing MCP SDK deprecation warnings,
222.78s, childexit0/cleanupremoved, receipt
`a1dd0bbf1aa2dc3c66acf893119b40c01d8810e61057e7710fe943f37849fc18`.
Pair with the post-collection parser/type follow-up focused16 described above.
Whole types **1,483 / 0 errors**, changed types/format **506 passed**, whole
Ruff/docs pass; dependency87/6 and predicates46/0 additions, owned PG labs0.
Next candidate requires all five fresh exact-SHA terminal approvals; previous
R6 aggregate rejection and corrected filesystem execution remain preserved.


### B06 R7 terminal rejection and persisted-edge corrections

R7 exact `1f94e85e8d0cca90109688c0fa9ce871bb4cacda` completed all five
independent lanes: F1 FAIL, F2 FAIL, F3 PASS, F4 FAIL, F5 PASS. Aggregate
FAIL / REQUEST_CHANGES; primary has not adopted it. Preserved reports and raw
receipts are in output/org-authz/b06-review-r7. Fresh F3 full **5,132**,
authz **484** (126 deselected), actual m83 native **35**, and bounded controls
**14** pass; these do not override the three rejecting lanes. F4 and F2
external witnesses explicitly prove imports from the dedicated checkout;
earlier unpinned output-only runs are excluded from exact candidate coverage.

F1 found cached ORM SkillBuilder confirmation authorizing a now-foreign
persisted session, and cached Tool credential relationships hiding a changed
foreign credential before secret preflight. F2 found resource context artifact
hydration validating the artifact tuple without its Conversation parent.
F4 found authenticated builder file routes following an owned session's
stored foreign-session workspace path.

Five typed repository regression cases independently reproduced those failures
before production edits; official wrapper childexit1/cleanupremoved, receipt
`55f91402759608971fa1a2fd5762411a1c0047830b1069f7dcddad465e1863ea`.
Confirmation now reloads the exact locked mutation target and fresh actor before
validation writes; canonical session workspace is checked before disk scans.
Collector eager loading populates existing ORM rows from persisted data before
the complete capability preflight. Artifact source hydration reuses the shared
artifact-and-parent predicate before storage resolution, refreshing existing
rows. Builder session reads refresh persisted fields, and sibling file routes
require the canonical session workspace before listing/content access.

New regression **5 passed**, cleanupremoved receipt
`a39322e0779261baeb62080eac75eb2c9a30336cdfb34da72c5e769da83f7c49`;
related confirmation/finalize/files/resource-context controls **49 passed**,
cleanupremoved receipt
`51ddc82c569807d130241fd7eddbeb7425c3041475b500c194341f98eb786a0e`.
Whole Ruff passes; changed types/format **509 pass / 0 errors**, dependency
**87 / 6**, predicates **46 / 0 additions**, docs **214 / 22**. An initial
root-system-Python predicate command lacked pydantic and is not counted;
the backend uv runtime rerun passes. Full r24 is pending. No previous SHA's
approval transfers to a new candidate. Full P0–P8 goal remains incomplete.

F1's two original witnesses were additionally rerun by root against immutable R7 with explicit dedicated PYTHONPATH and imported-production __file__ assertions; both behavioral failures reproduced (0.66s), cleanupremoved receipt4c60b54f5679508be4c965aaae4ca57eb69a5945de048b3ae1e07f364f7faa8c. Initial supplement omitted explicit pytest config and had fixture setup errors; not behavioral coverage. Whole current types1,486/errors0. Root r24 full is running serial; fresh candidate QA owns authoritative full/authz/native suites before adoption.


### B06 R8 rejection and fresh persisted-parent/membership fixes

R8 exact `c9a2fa5efc1a55be96114b818eea306c52542642` finished allfive:
F1 FAIL, F2 FAIL, F3 PASS, F4 FAIL, F5 PASS; aggregate FAIL/REQUEST_CHANGES.
Reports/receipts preserved output/org-authz/b06-review-r8. Fresh F3 full5137,
authz489/126deselected, native35 across16, correction40/legitimate9/consent5,
plus separately labeled native prerequisite2 pass; these cannot override the
persisted-boundary blockers. Primary has not adopted R8.

F1/F4 reproduced stale cached Conversation root/Agent reference allowing
collector decryption after persisted scope/reference changes. Collector now
uses require_private_conversation to reload the exact persisted root and
fresh captured-scope account/membership before Agent resolution. Agent-less
history still skips key lookup; a foreign Agent is excluded by scoped lookup.
F1 reproduced SkillBuilder own Session→foreign Conversation validation writes:
shared private_skill_builder_predicate checks nullable attachment's exact
Conversation and any surviving Agent before session read/mutation. Detached
agent-less owned history remains representable. F1 also reproduced Builder
and SkillBuilder creation after organization-member removal: both creators
now resolve fresh captured-scope actor before source/filesystem/write.
F2 reproduced cached ArtifactVersion's old artifact_id hiding a persisted
foreign parent: exact version ID AND artifact ID are now SQL predicates before
storage read, with populate_existing/autoflushFalse.

Root added five typed regression cases, verified behavioral red5 BEFORE
production edits (cleanupremoved e3db6fad863a6c7f7d54389784ed38c2bd30cceb6300784f5ac0f0f0d93bf7dd),
then green5 (cleanupremoved88dd27b2e34fdbcb1939d0bff3a4b3ecef73eb8710153d9af9a08cc1cff01928).
Additional cached Conversation root/Agent control2 passes after the shared
reload fix (filename ending -red.log recorded PASS, not red coverage).
Related correction/legitimate controls88 pass, cleanupremoved
b300597a6d001bc84af9a276fa61d48dd5f11cb8aad2ffcd3c761e9fcfe840a5.

F4 additionally reproduced durable MemoryRecord update/delete after suspended
membership. Root reproduced those and sibling proposal rejection/agent-memory
settings lazy creation/update. Shared record/proposal loading and owned agent
settings now refresh captured actor before effects, and refresh persisted ORM
values. Corrected red5 receipt0b6a8bb1e36af86b95c3b4b3478caba1223b2689a91ed4ca59ad775b6c69f33a;
first two sibling red attempts included wrong test-only Pydantic field names,
excluded from behavioral coverage. Original update/delete red2 independently
valid (393ecaa47890bdeff41f78b28656f13613673b9a28f85068a33cb0513b533d54).
Current combined new regressions/redaction24 pass cleanupremoved
9cf137a2e9000eb3bf4d5bf3d95c605788a76b62a3bd3bf0d67382d1ae11b4f5
(b06-r8-complete-regressions-green.log). Existing memory32 controls pass,
cleanupremoved3638ce20efc096c5b8eae16c63a239f7695dd3a4358605630cc94ef2d01f89d3.
A legacy fallback-model secret fixture now creates actual user/member scope
before collector; its initial missing-membership failure was not relaxed in
production. New code retains required explicit OrgContext, no bypass/fallback.

Whole Ruff/types1490errors0, changedtypes/format513 pass, dependencies87/6,
predicates46/0 additions, docs214/22 pass. Root serial r24 interrupted as it
covered superseded R8 source and duplicated fresh F3 suite; owned wrapper
cleanupremoved244d84a1afa62ea255e05ac86104fc4200fa5c1147fec2362183f4da9069bd68,
no full-suite pass inferred. Current four-worker r25 is running, with observed
failures pending diagnosis. New candidate not frozen/adopted; goal active.


Full r25 terminal: **6 failed / 5,143 passed**, 3 existing MCP deprecations,
229.77s, cleanupremoved2165134f63fee925d44a05daab2830ac3191f7494154984882c58e7cf40dbfa2.
All six failures were old unit fixtures lacking a real membership graph:
two protocol replay collectors, three attachment read-path collectors, one
SkillBuilder foreign-owner session creation with nonexistent user. Those
fixtures now seed actual declared unit users/tenant+organization membership;
production fresh-actor guards retained. Exact affected files19 passed,
cleanupremoved207d45cce821563fbd165af2e128f5cdc44638620762bbc9a9aa3446ce40e6e0.
Full four-worker r26 is running on this corrected source; no pass inferred yet.
R8 allfive reports remain terminal and aggregateFAIL. Next candidate approval
requires fresh allfive exactSHA reports; no goal completion or primary adoption.


### B06 R9 aggregate rejection and common private writer authority repair

R9 exact f8eff74b1466a739fe8f83c2bfa51b5190fa7d99 allfive terminal:
F1FAIL/F2FAIL/F3PASS/F4FAIL/F5PASS, aggregateFAIL/REQUEST_CHANGES. Reports/raw
receipts preserved output/org-authz/b06-review-r9. Fresh independent full5149,
authz501/126deselected, native35 across16, prerequisites2/corrections52/
legitimate9/consent5/static pass applies only rejected R9. Root r26 separately
5149pass/273.15s/cleanupremoved1bb2053d907be32f8c954f375beb67413734db51b6f181f31c1e0884e0028ea1.
No primary adoption or full-goal claim.

F1/F2/F4 exposed the common missing fresh-authority boundary in current private
writers: Credential/Tool/MCP/Agent/Skill creation; Tool/MCP mutations; Skill
filesystem editing; Share creation/revocation; Deployment/API-key creation and
mutations. Required captured scope was present but not refreshed before effects.
Core private creator/get-owned boundaries now resolve_private_actor before
private effects; Credential's explicit PlatformScope remains separate and
unchanged. Existing scoped mutation callers reuse those guarded loaders.
MCP registry/import creators and Agent deletion receive the same required guard.
API control writers and Share writers explicitly resolve current captured
membership/root before generation or mutation. No context preference fallback,
private operator bypass or new dependency exception was introduced.

F1's malformed improvement source-Skill edge reached validation/status writes.
private_skill_builder_predicate now includes nullable source/finalized Skill
exact scope in addition to attached Conversation/surviving Agent. F4's real JWT
Attachment with a persisted foreign Conversation parent returned200/read1.
New private_attachment_predicate checks nullable parent scope; shared upload
content/download lookup refreshes row+actor before any filesystem read, while
owned orphan/attached positives remain available.

Root independently reproduced current typed-repository writer/source cases
before production edits: red24, cleanupremoved9407e9c6aace49a88681faeb1e41fa8cc5e26ed25012729513cf72791e993fc1;
Attachment red1, cleanupremovedc6342a2a16642d9397a9ffef29fc3231b5ac4d3379c63abb140865b5c727ebbe.
After repairs all25 pass, final AnyIO filesystem-inventory test follow-up
cleanupremoved2a407a632911449d2d43a521f9f483052d8550226aa33212241d378e491217a1.

Root followed the same proven missing-guard seam into Trigger CRUD and Artifact
metadata writers. Seven real suspended-membership variants reproduced before
edits, cleanupremoved03c4fe3cb1ed2db748ef36157e2fe8c3c929875e91277ab245a44fee8c6ba7ad.
Shared Trigger owned-agent/trigger loaders and Artifact owned-root loading now
refresh authority before any change; all7 pass,
cleanupremoved42e0b9c7166f2715672329a34f406d28743519b8eefe72542d58ef87a06c6876.
Current Ruff/type+format522/dependencies87-6/predicates46-0 pass. Full r27
started before final Trigger/Artifact guard and test-inventory follow-ups;
its result must be paired with focused7+25 and a fresh exact-candidate F3 full.
No previous SHA's PASS transfers. Full P0–P8 remains active/incomplete.


Root r27 terminal: 3failed/5171passed, 3 existing MCP deprecations, 234.83s,
cleanupremovedee959ffcd2049fc42a6d99177b544b42287872307b404216d794fdb2e5446308.
Two Blueprint model-resolution fixtures had a real user but no membership;
ensure_unit_scope now seeds the actual declared membership before legitimate
materialization. The finalize source-deletion fixture left a dangling FK under
SQLite (foreign_keys unenforced), unlike native ON DELETE SET NULL. It now
explicitly models that native nullification; missing-source business error and
claim release/retry remain tested while foreign nonnull source denies before
effects. No production authorization weakening. Affected files plus source
negative and Trigger/Artifact controls25 pass,
cleanupremoved45dd80ec73de0783d0b4942e1d0a78a1e1e413d0bb795efaf2fea4bc6bfc1d48.
Whole currenttypes1499errors0; selected522types/format, Ruff, dependencies87/6,
predicates46/0, docs214/22 pass. Fresh rootr28-n4 running currentcorrectedsource,
expected5181 cases; no currentfullPASS inferred. R9 rejected SHA unadopted;
next exactcandidate requires allfive freshterminalPASS and native35.


### B06 R10 terminal rejection and current corrections

Exact rejected candidate d5f97ee7bd3d2cdf63fdb92a4cb5dfa00bc8c698:
F1 FAIL, F2 FAIL, F3 PASS, F4 FAIL, F5 PASS. All terminal reports, scripts,
raw logs and receipts are preserved in output/org-authz/b06-review-r10;
aggregate FAIL / REQUEST_CHANGES. No primary adoption. F3 independently
passed full5181/authz533/native35 and static gates for that SHA only.
Root r28 also passed5181; neither result approves subsequent corrections.

The actual missing boundaries were catalogue management/publication and upload
writers after captured membership suspension; Blueprint dependent Tool creation
before actor validation; SkillBuilder tool/list queries omitting the complete
source/finalized Skill parent invariant; Attachment link writers using a stale
actor; and API control projections exposing unscoped Deployment/Agent children.
Current shared seams refresh actor before effects, reuse the complete session
predicate, require a fresh private Conversation for linkage, and require exact
API Deployment/Key child closure before projections. No private operator bypass,
implicit context or new dependency exceptions were introduced.

Typed repository witnesses were run before production edits: first20 cases
15failed/5passed (077933a1c789ba950e8d8c1287f1dd43e4e63e3b9cdcd4bb7a294a8e30ad58a0),
Attachment6 cases3failed/3passed
(4f491dfc9f8990454d9275f068b18722e90d6bfee3128966828c778313cbc087).
After correction all26 passed
(2a4bd83d398ca8591861cc51a2cdeb0a9a216b91acf8b819d0e9594ff540a642).
All receipts report cleanup removed. Root followed the proven API child seam
into key-revocation serialization: actual missing-denial red1
(739bb65832412482910d7cb3cc8d0467ba4065b965156d5f793a0b512cc7e61b).
The Skill publication sibling test initially expected404 for suspended authority;
the actual documented fresh membership denial is403, so that harness assertion
was corrected without production weakening. The positive publication followed
by suspended zero-file/zero-row behavior now passes; the test was moved into a
separate publication module, and12 related cases pass after the move
(7edb379d7dc7bb1883f6832b8f49f7560ffdfb2ce66891f26e5dd66fd696a5a2).
That already-fixed sibling test is a green control, not newly claimed red evidence.

Root whole types1505/errors0, changed types/format528, Ruff, dependencies87/6,
predicates46/0 and docs214/22 passed before the final key-revocation follow-up.
Full r29/native35 are running against the earlier current corrections; they
cannot approve that pending production follow-up or newly added tests. Fresh
final source gates and all five exact-SHA review lanes remain mandatory.
Full P0–P8 goal remains active/incomplete.

Root r29 terminal: 1failed/5206passed, 3 existing MCP deprecations,259.12s,
cleanupremoved4cbb4ec47da0dd6399448d65bc2fd936ad283214528f569f6efb8534600d836a.
The legacy source/finalized session-list fixture referenced a random Skill ID
without a Skill row. It now persists an actual owned exact-org Skill before
constructing both sessions; parent authorization is unchanged. Root follow-up
key revocation uses the full key-child predicate and refreshed eager loading
before write/response. Related scope, publication and fixture33 cases pass,
cleanupremoved9edde7c3b479a0daea4d3714185d6931c2b37f909b171a06dfed438c7adeb3a9.
Native35 passed181.90s with cleanupremoved
6ac8139a6e0c4e2d38ed974468ef2f293d33306c681e6f561629af66e3640771;
owned temporary PostgreSQL labs0 afterward. This native run preceded the small
key-revocation follow-up; the next F3 exact-candidate native run remains required.
Current finalRuff/selected529format/docs pass; finaltype and r30full are pending.
No final full-suite or aggregate approval inferred.

Root r30 terminal:5209passed/3 existing MCP deprecations/248.28s,
cleanupremoved1baf39541ccf406c61cde10f9b23ea3a5ff6b39b9c1bca12b2b1177ccf4f5132.
Production source was stable throughout r30. During the run one already-collected
owned upload control was moved to the Attachment writer test module to keep the
new publication module below250pureLOC (243); behavior stayed unchanged, and all
18 affected tests passed afterward
(627c8a7dfc403185111b81c944eb7a01e927e01f49e03663ad5967e741cb7ab3).
Freeze type529/format529 pass. Fresh F3 will run the whole suite after that move
on the exact immutable candidate, together with native35 and all-domain guards.
Current cutover still unadopted; five-lane gate pending, full P0–P8 incomplete.


### B06 R11 terminal rejection and primary repairs

Rejected exact534-path candidate6b1cdb941ab0660594b6af94ce86fe8e40e8adaa:
F1/F2/F3/F4 FAIL, F5 PASS. All five terminal reports and raw witnesses are
preserved under output/org-authz/b06-review-r11; ledger aggregate FAIL /
REQUEST_CHANGES. No adoption. F3 final configured full5209/authz561 plus126
integration deselections/native35 across16/supplemental10/prereq2/static pass,
but independent evaluation preclaim2fail/1pass rejects this SHA. Full P0–P8
remains active/incomplete. Initial F3 inaccurate progress count was corrected;
its actual initial full1fail5208 was a repo-local temporary-parent harness error.
The next same-parent full was interrupted143 with cleanup removed; final full
used an owned outside-repo parent and child TMPDIR/basetemp,5209pass226.71s.
All41 prior raw receipts and final integrity receipt report cleanup removed;
lab inventories0. No old or harness-limited PASS substitutes for final acceptance.

Actual defects: runtime API list and deployment authorization omitted scoped
Agent parent; runtime key authentication accepted foreign Deployment links
before usage commits; SkillBuilder runtime/consent omitted source/finalized Skill
parents before model/workspace reads; hidden Agent creator omitted fresh actor;
evaluation worker committed grading/start/audit before context authorization;
resource resolver read private file/artifact content after membership suspension.

Root adapted reviewer witnesses to four strict repository regression modules.
Before production edits,23 cases12failed/11passed, cleanupremoved
 d7e15347fe18fae9c7598b80ddd4fdeb86cfce3efeac838edfcdd298363e0ff4.
Common runtime Deployment selections now use the complete predicate and refreshed
hydration; runtime key actor checks the complete Key graph before usage writes.
An invalid linked-key graph now receives401 before usage, while an allow-all key's
unavailable deployment is absent from200 listing. Both deny foreign metadata;
this is deliberate key-authentication semantics, not weakened scope filtering.
SkillBuilder runtime/consent use the full session predicate before effects; hidden
creator validates actor before model lookup/insertion. Evaluation context loads
before the start claim; existing failure/cancel cleanup remains possible. Resource
resolve/reauthorize validate captured actor before any content reader.

The first corrected run19passed/4failed exposed SQL auto-correlation when the
complete session predicate was reused inside a Conversation/Agent join. Explicit
correlation to each actual parent now retains inner tables. All23 then passed,
cleanupremovedc46f4a10b19228ff97f55f37bd14c59112d258adfe22a9e811e863edf3dce3eb.
Hidden creator tests were moved into their own focused module; no behavior change.

Root additionally reproduced a cached EvaluationSet whose persisted Skill parent
had changed: evaluator_calls1 plus durable grading/start/audit despite the stale
identity map, actual red1 cleanupremoved
 cdf322a1f7e6c4d496c1ba9f5d2e2d01cd437478b50860c21ce111d7ec7a333d.
Run and EvaluationSet now refresh persisted values; SQL bounds the Set ID and Skill
parent, and the pre-existing owner/Skill tuple check remains before construction.
Related creator/runtime/consent/evaluation55 passed
(983b9e46c9a91eb27ec3c42d857cc20b004db6efac66c2d2b30d23ba148bd016);
final cache/actor9 passed
(a02300a27e7f189fc751bf836a82c8bfc9ac1831b8f98b79755b89b9b2ef0954).
No new ownership-predicate/dependency baseline exception was added.
Ruff, changed534format and predicates46/additions0 pass; final types, r31 full
and current native35 pending. A new exact candidate and five fresh review lanes
are required; current primary source remains unadopted.

Root r31 terminal:12failed/5221passed,3 existing MCP deprecations,261.19s,
cleanupremoved29fedd9d406d9e935410059179929356b5f7212da0d3d542d8f19ff42843bc13.
Eleven failures shared a resource-context test fixture with an actual User but
no declared membership. One hidden-agent Trigger fixture had neither actual
User nor membership before calling the strict creator. Both now explicitly
seed unit scope via ensure_unit_scope; no production guard or expectations were
weakened. Affected quote/resource/Trigger files plus new adversarial modules53
pass, cleanupremoved42365662a6ac46c01f836c0d2e91b21ce1521c3e140a1f3e5c888a2b01818b8f.
Current native35 passed179.87s, cleanupremoved
5bb3f7226c924841c5f19bf46c478ab00ca71661ca77d34448fffd6fcded8b84;
these fixture-only follow-ups do not alter production. Freeze534types/format and
Ruff pass; whole types/errors0. Fresh current-source r32 full running, expected5233;
no current fullPASS inferred before footer/receipt. Dedicated pending R12 branch
contains539 authorized paths, uncommitted until gates complete. No primary adoption.

Root r32 terminal:5233passed/3 existing MCP deprecations/260.59s,
cleanupremoved2abb0d784148b7fc74f006ecbe1dbda3617d784f2c63a2ea4df13d93b129e6c3.
Production source stayed stable. One additional API cached-revocation control was
added after collection and already passed on unchanged production (HTTP401,
usage0), cleanupremoved8384cfa06a9ca3880041e3c34e478496b301d2efcf0ead231076037b0be5e3ad.
Its initial '-red' filename has been renamed '-control'; no new bug or failing
proof is claimed from that run. The exact new candidate F3 full must include
that control (expected5234), authz586 and fresh native/static acceptance.
Current539-path atomic cutover remains unadopted; five fresh lanes required.

### B06 R12 rejection and shared runtime/memory corrections

Exact R12 `4d32dc8c6fbfee313aec465a19b5d6ede0b8e8c8`: all five terminal,
F1/F2/F4 FAIL, F3/F5 PASS; aggregate FAIL. All reports/raw witnesses preserved
in `output/org-authz/b06-review-r12`; broad5234/authz586/native35 PASS cannot
override demonstrated memory parent, runtime preparation and cached-key effects.
No primary adoption or full P0–P8 completion.

- Ordinary chat refreshes the private Conversation actor before eager runtime
  hydration/decryption. Shared Agent runtime loading refreshes persisted root and
  eager capability graph with autoflush disabled. Actual chat and API config
  witnesses now deny stale/foreign graph before dummy decryption.
- Initial API-key authentication refreshes key/deployment graph before verification,
  state and usage. The failure was a bounded preloaded-session case; fresh-session
  revoked-key requests already denied correctly and remain positive controls.
- MemoryRecord/Proposal queries authorize the exact private root and every
  non-null Agent/source Conversation/surviving source Agent before hydration or
  mutation. Common loaders protect update/delete/reject/approve; both lists refresh
  captured actor. Creators share the source graph predicate. Legitimate NULL history
  and user-global settings remain supported. New pure helper is61LOC/typed.

Typed repository controls: `test_private_runtime_fresh_preparation.py`,
`test_private_api_cached_runtime_graph.py`, `test_private_api_cached_authentication.py`,
`test_private_memory_persisted_parents.py`, `test_private_memory_parent_consumers.py`,
`test_private_memory_parent_integration.py` under backend/tests/authz.
Primary replay of original witnesses:8fail/11pass beforefix, cleanupremoved
c9f0e11e08c9776ba9e76c852e75ada79aebf1657e1fc6b2964d08a440040167.
Additional source-Agent closure7fail, list actor2fail/2pass, source creator2fail
are preserved as actual red evidence; no positive control is relabeled red.

Shared corrections:18 original behavioral controls pass; related71pass before
creator extension. Final affected memory/runtime/API suite88pass5.47s, cleanupremoved
2dd191d8b0316bc899f23f109e7354814251b9e5cdd561b228da4c4797a8e762.
Final actual-m83 JWT/CSRF malformed-parent plus legitimate memory HTTP4pass18.52s,
cleanupremoved6cfdc8067c462e304a85a301bd8cc19d30a3db73a371197228bd41894b85ff45.
Initial native invocation deselected2/exit5; it is excluded. Two native test type
errors were corrected to actual current_database() and typed CurrentUser; final
changed541types/format and whole1517types report0errors/0warnings; Ruff passes.
Ownership ratchet46/additions0, dependency87/6, document inventory214/22 pass.

Root r33 broad5291pass/3existingMCP warnings/224.79s, cleanupremoved
14246aa8beb7611b47ec62325bd6e610a89e2d8ab3a29be306490a1acef925bc, covers
production BEFORE the final shared source-creator guard; its production snapshot is
`output/org-authz/p1/b06-r33-production-snapshot.json`. The two creator cases added
after collection are verified separately; no5293 current-source full PASS inferred.
A fresh exact full candidate must pass all five independent lanes and fresh full/
authz/native/static gates before adoption. Full P0–P8 remains ACTIVE/INCOMPLETE.

### B06 R13 all-lane rejection and complete metadata read boundaries

Exact R13 `ca42a4f6a86f01f8275105fa63ab68f558ba7736`: F1/F2/F4 FAIL,
F3/F5 PASS, all terminal and preserved in `output/org-authz/b06-review-r13`.
Fresh full5293/authz645/native37 PASS does not override current Thread parent
and captured-actor metadata findings. Primary remains unadopted.

Thread detail now retains its direct owner filter and adds complete surviving
Deployment/Agent + Conversation/Agent parents before eager hydration, with explicit
SQL correlation and persisted row refresh/autoflushFalse. It shares that loader
with existing Thread execution callers; no nonexistent GET/list-run route claimed.

`require_private_actor(db, context)` reuses the existing current account/member
resolver without autoflush.23 inventoried public read entrypoints now call it before
queries or projections; typed mapping is in ignored output. Sidechat already uses
fresh Conversation policy lock; files delegate to the now-fresh common Conversation
loader. Message reading uses the common fresh private Conversation boundary before
checkpoint source access, including provided-tree callers. No preference fallback,
operator private bypass, new schema or deferred policy promotion.

New typed tests: `test_private_metadata_actor.py`, `test_private_api_thread_parent.py`,
`test_private_metadata_siblings.py`, `test_private_message_reader_actor.py`.
Original+sibling true red13fail17pass, cleanupremovedf583d3dc48482c52d6e275492113e5172167a1b54fc51ff52988b301f396323b;
checkpoint source red3fail1pass, removed91a21301f94633121d010f38d3858c30168e186ecfddd36a8365b532c8e28f07.
Repaired34pass2.31s, removed9447c3e9cfbabab89673644d746333a3fdfa899200bd1a07adddfb8dd940543b.
Related recovery/binding44pass3.09s removed6de2a6c782588703cfc4f51292104bdd45cff93042a7871854c42bf0114e52af.
Actual m83 API/conversation read4pass17.01s removed67005e643a45e753dd19e3e50d705277fd888816b114bb41b19f28d45a01982b; lab0.

Rootr34 full12fail5315pass261.32s removedd89da76b8e5e03a5af6102d19fa9c341e39a5431339eef8051f6cd17a5d5797b:
old missing actor/member/Conversation fixture roots and total-SELECT performance
expectation. Test-only corrections seed real actors/legacy Conversation history
and measure the unchanged one-query Conversation/Agent base-read optimization
separately from authorization refreshes. No production guard relaxed. Affected
fixtures/domain/newcontrols126pass4.43s removed120f8012c7abfebaadd89ca8f80e2990ca526f60066ebd02651f9b071a947a59.
First copied witness parse-overlap and external sibling missing skills.slug setup
failures are retained/excluded; subsequent actual red evidence above is authoritative.

Current changed545types/format0, whole1521types0, Ruff, predicates46/additions0,
dependencies87/6 pass. Rootr35 fresh full pending until terminal footer/receipt;
no5327 fullPASS inferred. Full550-path candidate requires five fresh exact-source
review lanes and appropriate broad/native/static gates before adoption. Full
P0–P8 remains ACTIVE/INCOMPLETE; B08/B13/sharedFGA/frontend and later work outstanding.

Rootr35 terminal:5327pass/3existingMCP deprecations/261.61s, cleanupremoved
507df985a79a065d8c36ed0ebceb5b10b527c9c911b5acaa440b8bc96f3b0c7b.
Code stable before collection; current full gate now confirmed. Pending fresh
R14 entire550-path candidate, all-five exact-source gates still required.


### B06 R14 rejection and current-authority read/preflight corrections

Exact cdee94bf05112476bf9dd80236fe2ccff7dc116c: all five terminal FAIL,
aggregate REQUEST_CHANGES. Reports, source inventories, raw witnesses and cleanup
receipts preserved under output/org-authz/b06-review-r14 before correction.
Fresh full5327/authz679/native37 and static gates passed that rejected SHA; those
results do not override concrete omitted actor refresh and pre-denial SQL flushes.

17 independent read guards now reuse require_private_actor: Artifact lists/by-message/
page/recent/stats, MCP server/all-tools, Tool list, API deployments/keys, SkillBuilder
detail/list, Run interrupted/active/batch/latest/detail. Current-run wrapper delegates
to the batch guard. The earlier38-function source inventory was incomplete; expanded
AST inventory contains477 OrgContext functions, including guarded callers/delegates
and legitimate cleanup, rather than pretending all477 require duplicate guards.
Mapping b06-r14-read-guard-mapping.json is source evidence, not independent acceptance.

Direct resolve_private_actor suppresses autoflush throughout account/member refresh.
Detached Agent/Conversation/Builder/SkillBuilder actor preflights select persisted
owner/org/tenant scalar columns without autoflush; pending content stays staged.
Private Skill, Installation and executable-run preflights disable autoflush before
authority checks; existing root/parent predicates and lock order remain. Revoked
cleanup may close old work, but must not start new private content. No schema, UI,
shared-FGA, ambient/default context or private-operator bypass was introduced.

New typed repository tests: test_private_artifact_mcp_read_actor.py,
test_private_run_tool_read_actor.py, test_private_api_control_read_actor.py,
test_private_skill_builder_read_actor.py, test_private_actor_no_autoflush.py,
test_private_installation_no_autoflush.py. Independent copied witnesses converted
to repository tests: true red38fail35pass, repaired73pass; additional persisted-target
preflights true red10fail4pass, run-lock2fail, installation2fail; corrected preflight
16pass and final affected/legitimate100pass5.58s. Official wrapper receipts all removed,
indexed in b06-r14-correction-receipts.json. Initial sibling fixture incorrectly used
removed Skill.text_content field (14 setup failures) and first Installation test
omitted future annotations (collection error); both retained and excluded from red
evidence. Current Run scope test's random non-member organization now expects actor
403; real same-user two-company empty controls remain covered by new typed matrix.

New production functions remain frozen/typed/no Any/cast/ignore/<=250 pure LOC/<=3
arguments. Predicate ratchet remains46/additions0; current changed551types/format0,
dependencies87/6. Rootr36 full and bounded native are RUNNING, no terminal success
claimed. A fresh whole candidate and five exact-source reviews are mandatory before
adoption; full P0–P8 ACTIVE/INCOMPLETE, B08/B13/sharedFGA/UI/later gates remain open.

R14 correction bounded native actual-m83:7pass35.36s across detached writer/API/install/artifact
files, cleanupremovedac3c6295547fef64a59ec6a9b89a259f15fb0e68b98c8d49519b5fcf88f8a990;
owned lab inventory0. Whole1527types0errors/0warnings, Ruff,551changedformat,docs214/22
pass. Rootr36 full still running; observed failure means no final PASS can be inferred.

Rootr36 terminal5fail5409pass/3existingMCPwarnings/228.99s; cleanupremoved
c8fa76fc499c026c295e36e9425fe8436422c81265baf7bcd530e03d9eeaa594.
Five failures are fixture/performance assumptions: two foreign readers lacked actual
User/membership, one Artifact batching total included fresh account/member queries,
and two Builder fixtures staged changelog/eval without persisting before fresh
authority/session reload. Fixtures now seed actual foreign actors and explicitly
flush intended fixture metadata; Artifact data batching retains<=4 with account/member
SELECTs measured separately. No production guard changed. Caller source inspection
found no production direct changelog_draft/eval_result assignment relying on implicit
flush. Affected five files plus all new regression controls137pass6.27s, cleanupremoved
70ee14f1b64e303bda0a9d8789cc61e6dbebee558fd2903ee5ce3790c9b46595.
Rootr37 full RUNNING on stable current production/tests; no full PASS inferred.

Rootr37 terminal5414pass/3existingMCPwarnings/225.37s, cleanupremoved
965c3b04f18e454cb78a4f0122e38b3db61df90b8dbcf128ebbc41233dca760e.
Current production/tests stable before collection; final correction full gate confirmed.
R15 entire556-path candidate requires five fresh exact-source terminal reviews before
adoption. Root remains unadopted; full P0–P8 ACTIVE/INCOMPLETE.


### B06 R15 all-lane rejection and complete no-effect target preflights

Exact d869a9844b519709a5beaaaf0ae3f111e6338939: allfive terminalFAIL,
aggregateREQUEST_CHANGES. Full reports/raw witnesses/32 QA receipts/hash matrices
preserved output/org-authz/b06-review-r15 before correction. Independent full5414,
authz766/native37 and static/control gates PASS did not override actual pre-denial
SQL effects, revoked Conversation creation, or rollback snapshot/file/row effects.

Shared org_scoped/private_scoped/resource_candidates now disable SELECT autoflush;
private_parent_scoped inherits that option. This protects target lookup, rather than
only the preceding account/member refresh. Queue root locking refreshes without
autoflush; evaluation Skill hydration and SkillBuilder Agent/Conversation child
preflights also suppress it before denial. create_conversation refreshes captured
actor before any Agent/Conversation work. No ambient/default scope/operator bypass.

New strict/frozen RevisionRollbackTarget and load_private_rollback_target refresh
actor, lock exact scoped persisted Skill, and select exact persisted Revision plus
complete private Skill parent before snapshot-pruned checks or byte access. Cached
input rows do not authorize rollback. Existing package/text behavior stays behind
that common boundary. Skill/Revision SQL filters and lock precede all snapshot read,
file replacement and new revision effects; no new raw ownership predicate added.

Typed repository regression files: test_private_conversation_creator_actor.py,
test_private_conversation_preflight_effects.py, test_private_owned_loader_effects.py,
test_private_detached_preflight_effects.py, test_private_revision_rollback_boundary.py,
test_private_pending_authority.py. True red23fail15pass2.28s, cleanupremoved
5809071030f61305563d300c1412cc0b8a8c65f5800f6e3915b84a8587be8f21; repaired
38pass2.02s removed068408f022e1ea1f15f20a642d83ff9667b93bcead72926a2742d0738691a6ba.
Affected revision/queue/SkillBuilder/current controls99pass4.11s removed
0478c31c88f25f9de3cfeef5ff83eec3df0a71a85ca1c22336ea1b9bfd124887.
Additional cached Revision->foreignSkill parent controls (added after sharedfix,
not red claims) plus creator18pass1.27s removed
3f01b7688f8255497d40c3909e2a6bfc5e1864269feac8d15e137024cf5f5f6d.
Initial two new scalar-count Optional type errors corrected with real non-None
assertions; no Any/cast/ignore. New shared production helper remains<=250pureLOC/
<=3args/frozenDTO. Predicate ratchet46/additions0, Ruff+changed558format pass;
changed558types final rerun pending. Rootr38 full and selected actual-m83 native
RUNNING; no full/native terminal PASS claimed. Current563-path whole cutover still
requires fresh five exact-source reviews before adoption. FullP0–P8ACTIVE/incomplete.

R15 correction actual-m83 selected native8pass40.71s, cleanupremoved
33bbc48a838e6c58f1e6457cef459eab1189d582d5a2bedfa5656b29403b57ee.
Changed558types0errors/0warnings,558format,Ruff,predicates46/additions0,deps87/6,
docs214/22 PASS. Rootr38 full stillRUNNING; no terminal/fullcandidate acceptance.

Additional source-led capability preflight siblings: require_mcp_probe_scope credential
lookup, refresh_api_key_principal root/eager lookup, lock_executable_run child lookup
all independently autoflushed staged content before denial. New typed witness
test_private_capability_preflight_effects.py: true red3fail0pass, cleanupremoved
9d9d9ef8ab5388f896d6a524b86cc44f995df85dfde255f9a6d5a0be4d2862f0. Three
lookup execution options now autoflushFalse before authority/child decisions.
Rootr38 collected before these new tests and was running when the three lookup
options changed; even terminal PASS would belong only to the preceding snapshot.
It cannot approve final current source. Fresh final full gate required. No native
claim transferred from the prior8pass across this final source extension.

Rootr38 terminal1fail5453pass/3existingMCPwarnings/189.06s, removed
c196e343da2d307aa448c00245d09718c5d2656e71b4641835a50ef7e5de69d9.
This run predates additional capability extension and is not final acceptance.
One genuine sequential Credential mutation issue: the guarded fresh loader discarded
a previously authorized but unflushed service update. update/record_test now explicitly
flush only their validated Credential row; callers still commit, unrelated pending
content is not flushed by this explicit leaf operation. API auth and principal-refresh
prefixes suppress autoflush around complete eager graph/actor decisions, since parent
SELECT options alone did not cover secondary eager queries.

Additional capability red3 ->initial repair2pass1fail (eagerrefresh) retained honestly.
Final seven-file corrections plus cached API/Credential controls55pass3.29s, official
cleanup receipt pending footer read. Initial final-corrections invocation referenced
a nonexistent guessed test path, ran0tests/exit4/removed8c72ec805f9d2c25ae70998c27f7caa04c179a4fc0dea90cef16b19ab473b340;
excluded and corrected using actual rg-discovered test files. Rootr39 final full and
fresh selected native RUNNING. Current564paths; newtests43cases. No adoption.

Final correction55pass receiptremoved8e9b20305bcad619dc65449e4ebf7541c1bfa105452951a3b419da36d40ca542.
Fresh final actual-m83 native10pass58.06s, removed0998eed79f2c963941f965c1cee177fb1ac6614e7065febb05b4f5c59a21e97e;
whole1535types0errors/0warnings andchanged559types/format/Ruff/ratchet/deps/docs pass.
Rootr39 remainsRUNNING, no terminal/full adoption inferred.

Rootr39 final current production/tests terminal5457pass/3existingMCPwarnings/248.03s,
cleanupremoved3a30733d194447e0ac96c7adc9521a474b9d938fe03bb20318d01de9a90b52b3.
Code/tests stable before collection; authoritative root final full gate confirmed.
R16 whole564path candidate still requires five fresh exact-source terminal reviews
before adoption; primaryunadopted/fullP0–P8ACTIVE/incomplete.


### B06 R16 검토 거절 및 실제 실패 경로 수정

R16 `c32a156b2ff619ae9b2d0167efc5db46454371bd`의 564경로 전체 변경에 대한
독립 검토 5개가 모두 종료됐다. F1/F2/F4/F5는 FAIL, F3 QA는 PASS다.
보고서·원시 로그·정리 영수증은 `output/org-authz/b06-review-r16/ledger.md`에
해시와 함께 보존했다. 전체 5,457개 통과 결과는 이 후보에 한정하며,
다른 검토에서 재현한 실패를 상쇄하지 않는다. 후보는 primary에 채택하지 않았다.

확인된 문제는 공유 링크의 캐시된 persisted scope/Agent 부모, private active-share의
Conversation 부모 누락, Agent 및 runtime/secret collector의 eager secondary SELECT,
Revision/Evaluation child detail, MCP binding/Run 및 Trigger 참조의 판정 전 autoflush다.
각 검토는 실제 SQL 쓰기 관측 후 롤백했으며 durable commit/provider 효과를 주장하지 않는다.

동일한 조건을 실행하는 typed 회귀 테스트 6파일/29사례를 추가했다.
수정 전 **23 failed / 6 passed**, 격리 루트 정리 `removed`였다.
조회 그룹에 한정한 `db.no_autoflush`, child/binding SELECT 옵션,
공유 링크·대화·Agent refresh 및 Conversation 부모 predicate로 수정했다.
첫 수정에서 frozen MCP exception이 generator context manager를 통과하며 TypeError로
변경되는 문제가 5사례에 재현됐다. MCP authorization 전체를 감싸는 구문을 제거하고
이미 옵션이 적용된 각 조회를 사용하도록 고쳤다. exception 자료형은 변경하지 않았다.

최종 회귀 **29 passed**, 기존 정상 동작 및 조직 격리 관련 **108 passed**.
원시 로그는 `output/org-authz/p1/b06-r16-regressions-{red,green,green-r2}.log`와
`b06-r16-related.log`다. 각 실행의 격리 정리는 `removed`로 확인됐다.
전체/실제 PostgreSQL/정적 최종 게이트와 새 후보의 독립 검토는 완료 결과를
별도로 기록한다. B06 및 전체 P0–P8 목표는 아직 완료되지 않았다.


최종 수정 소스에서 rootr40 전체 **5,486 passed / 기존 MCP 경고 3개 / 240.51초**,
정리 `removed` (`d54db0c90921c22751a998db719cf7077007618a3a3c70cf336f13c5b36d47f5`).
관련 실제 m83 PostgreSQL **10 passed / 51.94초**, 소유 테스트 DB 잔여 0.
변경 Python 566개 타입·형식, 전체 1,541개 타입 오류/경고 0, Ruff,
권한 predicate 46/additions 0, dependency 87/6, 문서 214/ADR 22 검사가 통과했다.
제품 코드·테스트는 전체 실행 전에 고정했고 이후 변경하지 않았다.
전체 571경로 후보의 새 독립 검토 5개가 모두 통과해야 B06을 채택한다.
전체 P0–P8 목표는 ACTIVE이며 완료를 주장하지 않는다.


### B06 R17 검토 결과와 공용 판정 경계 수정

`5d8bb3732587004c68710aeaf0a7a4561348eceb` 전체 571경로 검토 5개가 모두 종료됐다.
F1/F2/F4/F5는 FAIL, F3는 PASS이며 모든 보고서·원시 증거·정리 영수증을
`output/org-authz/b06-review-r17/ledger.md`에 해시와 함께 보존했다.
F3의 새 전체 5,486개, authz 838개, native 37+2+12개 통과는 그 SHA에 한정한다.
다른 검토에서 재현한 실패 때문에 후보는 채택하지 않았다.

확인된 실패: file/Skill resource target 조회의 판정 전 autoflush, Deployment 생성·변경과
선택 Deployment의 API Key 생성 및 Agent 게시의 eager target 조회, MCP Tool 메타데이터와
Server Credential FK의 오래된 캐시, 중복 MCP Binding의 캐시된 다른 회사 Conversation 부모
및 충돌 거절 전 autoflush. 모의 wire 1회 진입은 실제 외부 호출·복호화·secret 노출과 구분한다.
별도 4파일/25사례로 **15 failed / 10 passed**를 재현한 뒤 동일 조회 경계를 수정해
**25 passed**를 확인했다. 기존 관련 동작 **76 passed**도 통과했다.

같은 공용 검증 방식의 Agent 생성·변경 연결 항목도 실제로 확인했다.
Skill/MCP/SubAgent 및 복합 입력의 foreign/missing 16사례가 모두 판정 전 SQL 쓰기로 실패했다.
수정은 `_validate_agent_binding_ids`에서 요청한 전체 연결 항목을 no-autoflush로 먼저 검증하고,
그 뒤 Agent 속성·연결을 변경하도록 순서를 옮긴 것이다. 기존 권한 검사와 명시적 쓰기는 유지한다.
새 회귀 41사례와 기존 Agent 서비스 10사례를 합쳐 **51 passed**다.

새 helper는 typed request union을 사용하고 3개 인수·짧은 함수다. 전역 Session 설정,
권한 우회·optional context·스키마·프론트엔드·공유 FGA를 변경하지 않았다.
정적 raw SELECT 후보 74개 목록은 실행 실패 74개라는 의미가 아니다. 권한 판정과 정상 쓰기
이후 조회를 구분해야 하며, 성공한 정상 쓰기의 autoflush는 일괄 금지하지 않는다.
관련 원시 로그는 `output/org-authz/p1/b06-r17-*.log`와 `b06-r41-full-n4.log`다.
rootr41 전체 및 실제 PostgreSQL·정적 최종 검사 결과는 종료 후 별도로 기록한다.
B06 및 전체 P0–P8 목표는 ACTIVE이며 완료되지 않았다.


최종 rootr41은 수정된 코드·테스트에서 **5,527 passed / 기존 MCP 경고 3개 / 227.60초**,
정리 `removed` (`b0aa2d0fe5b80875f4ff1f7774f8e032d42e92789630c3cdf77f2feb38d0cb4f`)다.
추가 정상 Agent/Builder/SubAgent 동작 **117 passed**, 실제 m83 PostgreSQL 관련 **5 passed**, 테스트 DB 잔여 0.
변경 Python 571개 타입·형식, 전체 1,546개 타입 오류/경고 0, Ruff,
predicate 46/additions 0, dependency 87/6, 문서 214/ADR 22 검사가 통과했다.
제품 코드·테스트는 rootr41 수집 전에 고정했으며 이후 변경하지 않았다.
전체 576경로의 새 후보는 정확한 SHA에서 독립 검토 5개가 모두 통과해야 채택한다.
B06 및 전체 P0–P8 목표는 ACTIVE이며 완료를 주장하지 않는다.

### B06 R18 검토 거절과 조회·게시 경계 수정

R18 `1f3ab68351b157b30f5985a38ca11dd4b6229c59`의 독립 검토 다섯 개가
모두 종료됐다. F1/F2/F4/F5는 FAIL, F3는 실행한 범위에서 PASS다.
보고서·실패 원문·격리 정리 기록을 양쪽 작업 폴더에 보존했고 후보는 채택하지 않았다.
F3의 전체 5,527개 및 권한 879개, 실제 m83 37+2+12개 통과는 다른 검토의
구체적인 반례를 상쇄하지 않는다.

이번 수정 전 현재 코드에서도 실제 HTTP 상세·목록의 외부 회사 하위 리소스 노출
8건, 요약 개수 2건, 수정 후 응답과 연결 보존 검증 2건을 재현했다.
같은 경로의 Agent 게시 요청도 이동한 Skill·Tool·MCP·SubAgent 네 경우 모두
201로 받아들였다(정상 대조 네 경우 통과). 게시 서비스의 fallback Model 조회와
암묵적 ArtifactVersion fallback에서는 거절 전 관련 없는 private SQL 변경이
재현됐다. 실제 provider 호출이나 secret 복호화·노출을 주장하지 않는다.
원문과 정리 영수증은 `output/org-authz/p1/b06-r18-*-red.log` 및
`b06-r18-red-receipts.json`에 있다.

조회는 현재 저장된 하위 리소스의 조직 범위로 link 후보를 제한하며 요약 개수에도
같은 조건을 사용한다. 쓰기 경로는 완전한 연결을 다시 로드해 숨겨진 연결도 교체할 수
있다. 상세·생성·수정·favorite·Builder confirm·Blueprint materialization 응답은
공용 조회 경계를 거친다. 게시에서는 비활성 도구를 포함한 모든 직접 metadata
export 대상을 portability 오류·payload 생성·item 변경 전에 검사한다.
ArtifactVersion과 fallback Model 조회에는 자동 flush 방지와 현재 값 재로딩을 적용했다.

첫 수정에서 하위 Agent가 목록의 독립 항목이기도 한 경우 `populate_existing`가
응답에 필요한 relationship을 비우는 실제 async ORM 오류 네 건을 발견했다.
실행 중 `inspect(...).unloaded`로 상태를 확인하고 같은 범위의 직접 조회 옵션을
하위 Agent에도 적용해 수정했다. 첫 실패 기록은 보존했다.
새 회귀 검증 네 파일 44개가 통과했고 격리 루트는 제거됐다.
관련 기존 검사에서 소유자를 빠뜨린 비시스템 Tool 테스트 데이터를 확인해
에이전트 소유자를 명시했다. 전체·native·최종 정적 검사와 새 후보의 독립 검토는
별도로 이어간다. B06 및 전체 P0–P8은 ACTIVE이며 완료되지 않았다.

R18 수정 후 최종 실행 결과: 전체 백엔드 5,571개 통과(기존 MCP 경고 3개,
228.18초), 새 회귀 44개, 관련 기존 동작 163개, 실제 m83 작성·게시 5개 통과.
전체 1,552개 파일 타입 오류·경고 0, 변경 Python 577개 타입·형식 통과,
권한 predicate 46/추가 0, 의존성 87/기존 순환 6, 문서 214/ADR 22 검사 통과.
전체 테스트 정리 영수증은
`33784d73b19f8b6c8ae696753e3cd02bd3f14c578ebd42ce407a098fc2989728`이며
격리 루트는 제거됐다. 현재 전체 변경은 582경로다. 이 결과는 구현 검증이며,
새 후보의 독립 검토 다섯 개가 모두 통과하기 전에는 B06 채택을 주장하지 않는다.

최종 Ruff 재검사에서 unread 집계의 동적 `setattr`가 B010에 걸렸다.
`AgentPresentation` frozen DTO로 값을 반환하도록 바꿨고 응답에서 명시적으로 반영한다.
변경 후 새 회귀·관련 기존 동작을 합친 207개, 전체 1,552개 파일 타입 검사와
Ruff가 통과했다. 위 R42 전체 5,571개 결과는 이 DTO 변경 전 결과로 보존한다.
새 후보의 QA 검토가 최종 고정 소스에서 전체 검사를 직접 실행하며, 그 결과가
나오기 전에는 현재 소스의 전체 검사 통과를 주장하지 않는다.

### B06 R19 검토 거절과 설치·worker 경계 수정

R19 `256df39301f463013a098eae40d6c7ad70797550`의 다섯 검토가 모두 종료됐고,
F1/F2/F4는 FAIL, F3/F5는 실행한 범위에서 PASS다. 보고서·반례·정리 기록을
양쪽 작업 폴더에 보존했으며 후보는 채택하지 않았다. F3은 최종 DTO를 포함한
고정 소스에서 전체 5,571개, 실제 m83 37+2+12개 검사를 통과했다. 이 결과는
설치·Blueprint·실행 완료 경계의 구체적인 실패를 상쇄하지 않는다.

설치에서는 실제 회사 선택 쿠키를 B로 바꾸었을 때 Agent A와 private Item A가
404인데도 설치 요청은 201로 받아들여 B의 Blueprint에 A의 비공개 system prompt를
복사하는 문제가 확인됐다. 일반 사용자와 실제 운영자 모두 재현됐고, A의 정상 설치는
통과했다. 최초 검토의 중복 쿠키로 인한 Item 상세 200 주장은 제외했다. 현재 설치
진입 조회는 기존 catalog 후보 범위를 사용하며 private 회사 경계를 적용한다.
공개·unlisted·플랫폼 카탈로그의 정상 설치 의미는 유지한다.

없거나 이동한 버전 조회는 관련 없는 staged SQL을 flush하지 않고, 현재 item_id와
version_id를 함께 확인한다. 암묵적인 latest version 경로에서 물리적으로 다른 항목으로
이동한 버전을 설치하던 문제를 실제 HTTP로 재현했다. 최종 거절 전에 eager loader가
이동한 버전 payload를 읽는 문제도 실제 SQL 두 사례로 확인해 eager 조회를 제거했다.
현재 설치 관련 회귀와 기존 정상 검사 38개가 통과했다.

worker의 완료된 edit/regenerate branch 처리에서는 소속이 제거된 뒤에도 checkpoint
콜백을 읽고 실제 branch 값을 커밋하는 문제가 재현됐다. metrics와 attachment
backfill에서도 같은 소속 제거 뒤 source 호출 두 사례가 재현됐다. 공용 frozen target이
캡처한 context를 전달하고, 현재 actor와 정확한 대화 부모를 검사한 뒤 private 작업을
수행한다. branch 읽기 도중 소속이 제거된 경우도 변경 직전에 다시 검사한다.
대화의 물리적 회사 이동·소속 제거·정상 대조 및 기존 worker 검사 22개가 통과했다.
checkpoint 콜백은 제한된 대체 구현이며 실제 provider/checkpointer 검증을 주장하지 않는다.

현재 수정 후 실제 m83 PostgreSQL에서 회사별 설치·동시 설치·worker 관련 5개가
통과했고 생성한 테스트 DB 잔여는 0이다. Blueprint의 전체 그래프 사전 검사 수정은
진행 중이며, 이후 전체 고정 후보에서 새 독립 검토 다섯 개가 필요하다.
B06 및 전체 P0–P8 목표는 ACTIVE이며 완료되지 않았다.

최종 실행 경로 감사에서 root의 일부 pytest 명령이 child TMPDIR·basetemp·cache를
명시하지 않아 격리 루트 밖에 임시 폴더 세 개를 남긴 것을 확인했다. 실행 시각·파일시스템
식별값·해당 테스트 이름으로 소유권을 확인한 세 폴더만 no-follow 방식으로 옮긴 뒤
공식 정리 helper로 제거했다. 공유 부모는 보존했으며 실행 전 경로 부재를 기록했다는
주장은 하지 않는다. 원래 로그는 보존하고 최종 정리 증거에서 제외했다.
올바른 child 경로를 명시한 최종 관련 검사 60개, 실제 PostgreSQL 5개가 다시 통과했고
격리 루트는 제거됐다. 테스트 DB 잔여 0, 안정화한 installer/worker 9개 파일 해시는
변하지 않았다. 이 재실행은 확인된 격리 경로 오류를 수정하기 위한 검증이다.

R19 Blueprint 수정은 전체 dependency와 최종 Agent 입력을 no-autoflush 읽기 계획으로
확정한 뒤 한 번 적용하도록 바꿨다. Tool·MCP 생성, 기존 MCP credential 변경,
Skill·SubAgent·모델·fallback·credential·middleware·runtime policy·AgentCreate 검증이
모두 적용 경계 앞에 있다. frozen DTO에 최종 UUID를 예약하고 기존 Agent writer의
추가 검증과 router 감사·커밋 순서는 유지한다.

실제 서비스·JWT/CSRF HTTP의 늦은 거절에서 DML이 없으며 정상 대조는 실제 행과
연결을 생성한다. 캐시된 Blueprint·모델·credential·Tool·MCP 상태 다섯 실패도
재현했고, 현재 SQL projection을 사용해 수정했다. 관련 없는 미저장 행을 덮어쓰지
않는다. 요청 fallback 순서·중복·기존 hidden fallback 허용 의미와 MCP의 세 전략,
동일 요청 재사용·생략값·native construction 변환을 유지한다.

마지막 신규 회귀 27개, 관련 기존·신규 선택 검사 103개(설정상 제외 1개)가
통과했다. 전체 백엔드 타입 오류·경고 0, 소유한 Python Ruff와 새 10개 파일 구조
검사 통과다. 초기 MCP fixture의 key_id 누락, 대조 검사의 async relationship 로딩과
별도 세션의 오래된 count, 기존 credential mismatch 409를 422로 기대한 오류는
행동 증거에서 제외하고 원문을 보존했다. 최초 조합 정적 명령의 exit 0이 Ruff
오류를 가린 결과도 통과 근거로 쓰지 않으며 마지막 fail-fast 정적 검사가 통과했다.
원문·소스 해시는 `output/org-authz/p1/b06-r19-blueprint-*`에 있다.

R19 수정 후 첫 전체 R43 검사는 5,613개 통과·1개 실패(기존 MCP 경고 3개,
218.07초)였다. 실패는 기존 queue-worker metrics 테스트가 조직 context 없는
SimpleNamespace를 AgentConfig로 cast한 데서 재현됐다. production fallback을
추가하지 않고 실제 AgentConfig와 명시적 테스트 조직을 전달했으며 cast를 제거했다.
해당 테스트·worker 관련 31개와 수정 파일 타입·Ruff·형식 검사가 통과했다.
R43 실패 원문과 별도 red 재현은 보존한다. 수정 전 통과한 전체 정적·597경로 해시와
수정 후 테스트 파일 하나의 차이를 기록했으며 다음 전체 검사는 별도로 실행한다.

수정 후 최종 R44 전체 검사는 **5,614 passed / 기존 MCP 경고 3개 / 231.23초**다.
격리 루트 정리 `removed` (`23b285b201c9e8b2362085c9b588ec7be8c5298209ffa8401d67f878cc8d8c3b`).
전체 검사 동안 597경로의 소스 해시가 같았고 종료 후 다시 확인했다.
전체 Ruff·타입 검사, 변경 Python 592개 타입·형식, predicate 46/추가 0,
의존성 87/기존 순환 6, 문서 214/ADR 22 검사는 R43에서 통과했다.
R43에서 발견한 테스트 파일 하나는 별도의 타입·Ruff·형식·관련 31개 검사를 통과했고
R44에 포함됐다. 최종 후보 생성 전 변경 파일·문서 검사를 다시 확인한다.
B06은 새 정확한 SHA의 독립 검토 다섯 개가 모두 통과해야 채택하며,
전체 P0–P8 목표는 ACTIVE다. source/footer 이후 제품 코드·테스트를 바꾸지 않는다.

### B06 R20 검토 거절과 Assistant·설치 업데이트 수정

R20 `b25ba6eec593242f2c2cabda0b7b2f2711cc7952`의 다섯 유효 검토가 종료됐다.
F1/F2/F4/F5는 FAIL, F3는 실행한 범위에서 PASS다. 최초 F2/F4는 자동 내용 필터로
중단되어 INCONCLUSIVE로 보존했고, 각각 한 번의 제한된 재검토가 기존 반례와 현재
소스·정상 대조를 확인해 FAIL을 보고했다. 원문·보고서·해시·정리 기록은 양쪽 작업
폴더의 `output/org-authz/b06-review-r20/`에 있다. 후보는 채택하지 않았다.

Assistant의 공통 조회에서 이동한 하위 Tool metadata를 보여주고, 소속 제거 거절 전에
대기 중인 Tool INSERT를 실행하는 두 실패를 재현했다. 현재 actor와 범위가 적용된
Agent projection을 재사용한다. Tool·Skill·MCP·SubAgent의 현재 metadata와 숨겨진
물리적 연결 보존, 거절 전 target SQL·provider 준비 미호출을 포함한 신규 16개와
기존 관련 143개 검사가 통과했다. 최초 직접 서비스 import는 실제 의존성 래칫에
걸렸으므로 기준선을 늘리지 않고 공통 권한 모듈 경계를 정리한다.

설치 업데이트 overwrite·keep_current가 다른 Item으로 이동한 버전을 받아들이는
두 실패와 정상 대조 네 사례를 실제 HTTP로 재현했다. 초기 설치·업데이트는 같은
현재 catalog source 조회와 item/version 소속 검증을 사용한다. 자격증명만 갱신하는
Agent·MCP 재사용 경로의 retained version도 같은 검증을 거친다. 카탈로그 최신 버전
관계와 단일·일괄 버전 조회는 예상 Item을 함께 확인한다. 잘못 연결된 설치본의 상태
요약은 다른 Item의 requirements를 사용하지 않으며, 캐시된 버전 상세도 현재 저장된
부모와 내용을 재조회한다. 관련 검사 107개 통과, 격리 루트 정리 removed다.
일반 HTTP 상세 대조는 먼저 통과했지만 같은 세션의 캐시된 직접 서비스 호출은
별도로 실패했다. 두 결과를 구분하며, stale cache 거절·갱신 근거를 HTTP 대조만으로
추론하지 않는다.

수정 중 첫 전체 검사는 5,646 passed / 1 failed / 7 warnings / 234.19초였다.
실패는 `test_runtime_dependency_guard`의 Assistant 직접 서비스 import다. 경고 중
네 개는 신규 fixture plugin assertion rewrite 순서, 세 개는 기존 MCP deprecation이다.
정리 루트는 `98d9082825be19ec3142c8448ba2c04ff2a6ac65dce9131241cf75f76ab6c125`
removed다. 이 전체 실행 뒤의 추가 수정에 대한 최종 전체 통과를 주장하지 않는다.
실제 m83 수정 관련 5개 검사와 테스트 DB 잔여 0을 확인했고, 이후 version 상세·요약
변경의 native 검사는 별도로 실행한다. 새 정확한 SHA의 다섯 독립 검토가 모두 통과해야
B06을 채택하며, 전체 P0–P8 목표는 ACTIVE다.

후속 검증: 버전 소속 공통 경계 수정 후 관련 107개 검사와 실제 m83 PostgreSQL
업데이트·회사별 카탈로그 5개가 통과했다. native 정리 루트는
`e136f062034e561d75f703d1b5000aa1281ee0267501045bcea6b644a4eb5108` removed다.
Assistant 조회 구현은 `authz.agent_projection_scope.load_agent_presentation`으로
옮겼으며 서비스와 런타임이 같은 구현을 호출한다. 실제 의존성 래칫은 기존 기준선
87건/순환 6건 그대로 통과했고 관련 기존·래칫 158개가 통과했다. fixture 등록 순서
경고는 공유 fixture 변경이나 경고 무시 없이 수정했다. 최종 후보 QA가 고정된 정확한
소스에서 전체 검사를 실행한다. 수정 전 전체 FAIL은 그대로 보존한다.

R20 수정 후 최종 고정 소스의 전체 검사: **5,651 passed / 기존 MCP 경고 3개 / 227.13초**.
격리 정리 `removed` (`a74a12c1467ec42265ed10756474db49259aa83b16d7d7b3ab1faa43bab6a67d`). 검사 전후 606경로의 해시가
같았으며, 아래 증거 기록만 이후 추가했다. 최종 전체 Ruff·타입 오류/경고 0, 변경
Python 601개 타입·형식, 의존성 87/기존 순환 6, predicate 46/추가 0, 문서
214/ADR 22 및 frozen schema 9개 검사가 통과했다. 고정 후보의 독립 검토와
B06 채택은 별도이며 전체 P0–P8은 완료되지 않았다.


### B06 R21 거절 후 수정 및 R22 검토 준비

R21의 다섯 독립 검토가 모두 끝난 뒤 Blueprint 조회의 선행 INSERT와 멤버십
철회 후 카탈로그 반환 문제로 후보를 거절했다. 보고서와 원본 실행 기록을 두
체크아웃에 보존했다. Blueprint 조회는 현재 DB의 scalar projection으로 공통화하고,
카탈로그는 현재 사용자 상태와 실제 조회 후보를 먼저 확인한다. 거절된 조회의
대기 쓰기를 막으면서 정상 이름 변경을 보존하는 회귀 검사도 추가했다.

최종 고정 617경로 소스의 전체 검사 **5,715 passed / integration 185 제외 /
기존 MCP 경고 3개 / 636.03초**. 검사 전후 해시가 같았다. 격리 정리
`10078c589aeef082a8b1122bb4e425e57fd3e8895479628004556a42426a3325` removed.
관련 카탈로그 220개와 실제 m83 조회·게시·업데이트 9개가 통과했다. 전체 Ruff,
타입 오류·경고 0, 변경 Python 612개 형식 검사가 통과했고, 불필요해진 두 의존성만
기준선에서 제거하여 실제 의존성 85건/기존 순환 6건으로 통과했다.

처음 전체 실행의 임시 디렉터리 설정 오류(10실패/3,302통과 후 중단)와 정상 변경
보존 결함 발견 전 중단한 실행(1,025통과)은 최종 전체 통과 근거에서 제외한다.
각 실행의 원본과 removed 정리 기록을 보존했다. 이 문단만 전체 검사 이후
추가했으며 제품·테스트 소스는 바뀌지 않았다. R22 정확한 SHA의 새 다섯 검토와
B06 채택은 아직 완료되지 않았다. 전체 P0–P8 목표도 ACTIVE다.

### B06 R22 거절 후 수정 및 R23 검토 준비

다섯 검토의 보고서와 원본을 모두 보존한 뒤 R22를 거절했다. 카탈로그 검색의
정상 미저장 이름 변경 보존, 중복 ownership 조건 제거, 대기 입력 재시도에서
현재 사용자·멤버십·실제 대화와 입력 부모 관계를 먼저 검사하도록 수정했다.
정상 재시도의 최초 파일 스냅샷과 대기 쓰기는 유지한다. 관련 카탈로그 98개,
대기 입력 53개, 실제 m83 검색 2개와 재시도 2개 검사가 통과했다.

수정된 제품과 수집된 기본 테스트의 전체 결과는 **5,746 passed / 기존 MCP
경고 3개 / 201.39초**다. 격리 정리
`7d9b0fc722e211f23405b7801b42e4353e85879f46cb2838d0e85dc130af7cb6` removed.
전체 수집 이후 native 전용 재시도 테스트를 추가했으므로 이 결과를 최종 전체
소스 고정 증거로 주장하지 않는다. 정확한 R23 SHA에서 새 QA가 전체와 실제 DB
검사를 실행한다. 잘못된 예외 형식·actor SELECT 금지 등 native 테스트 작성
실패는 원본과 정리 기록을 보존했고 현재 권한 기준을 검증하는 형태로 수정했다.
B06 채택, 후속 P1 및 전체 P0–P8 완료는 아직 대기 중이다.


### B06 R23 거절 후 공통 조회·대기 입력 경계 수정

다섯 검토의 보고서와 원본을 모두 보존한 뒤 R23을 거절했다. 공통 대화 조회와
잠금, 큐 잠금은 현재 사용자를 먼저 확인한다. 대기 입력 작업기는 식별·바인딩
메타데이터와 현재 권한을 확인한 뒤 내용을 읽는다. 권한 회수 후에도 필요한 실패
정리는 내용을 읽지 않고 수행하며, 복구 수집은 ID만 읽는다. 정상 대기 쓰기,
최초 입력 스냅샷, 대화 기록과 취소·실패 동작은 회귀 검사로 보존했다.

최종 소스 628경로를 검사 전후 고정하여 **5,791 passed / 기존 MCP 경고 3개 /
221.23초**, 현재 명시 매핑의 실제 m83 **72 passed / 350.67초**를 확인했다.
전체 Ruff·타입 오류/경고 0, 변경 Python 623개 타입·형식, 소유자 조건 추가 0,
기존 의존성 85/순환 6, 문서 214/ADR 22와 frozen 모델 검사가 통과했다.
전체 정리 `0887dad52f0a57b05685972c5b1629fa74f3e6dee79e83b50212cb89728b805d`,
native 정리 `1e9c7c30d28d989b2e1522bbec60a5af3a7f02dc0b05380eacd865b22ad563ea`,
정적 정리 `a7ee80ace9991457a0dafb583c6f7477bd01385a7ea24c7f8fb902ce57bfb31c`
모두 removed다. 이 기록만 검사 후 추가했으며 제품·테스트 소스는 바뀌지 않았다.

새 영구 검사에서 unit 읽기 순서 20실패와 native 2실패를 먼저 재현한 원본,
작업기 15실패 및 작성·형식·동시 편집 중 검사 실패를 별도 보존했다. 검사 통과는
R24 고정 커밋의 새 다섯 검토와 B06 채택을 대신하지 않는다. 운영 환경·실제
제공자·브라우저·사용자 시험 및 전체 P0–P8 완료도 아직 진행 범위다.

### B06 R24 검토 거절 후 설치본 조회 순서 수정

R24 후보 `d5af2b7c3bab468f14d4dc4a607930bc71460891`의 다섯 최종 보고서와
원본 증거를 양쪽 체크아웃에 보존한 뒤 종합 실패를 기록했다. 계획·코드·실행·맥락
검토는 제한된 범위에서 통과했지만, 보안 검토는 설치본 업데이트·삭제에서 현재
구성원 자격 확인 전에 비공개 설치본 행을 읽는 결함을 재현했다(거부 4건 실패,
정상 4건 통과). 거부 응답은 404였고 쓰기나 HTTP 내용 노출을 입증한 결과는 아니다.
이 후보는 채택하지 않았다.

`load_private_installation`의 현재 권한 확인을 기존 범위·하위 자원 잠금 조회
앞으로 이동했다. 기존 오류, 범위 조건과 정상 저장 동작은 유지했다. 직접 조회·
업데이트·삭제, 조직·회사 구성원 제거·계정 비활성화, 일반 사용자·운영자를 확인하는
새 회귀 24건은 수정 전 실패했고, 수정 후 관련 회귀 49건이 통과했다. 실제 m83의
설치본 업데이트·삭제 시나리오 8건도 통과했다. 정상 요청의 명시적 저장은 별도 세션에서
확인했으며, 거부 요청에서는 비공개 설치본 조회와 쓰기가 없었다.

전체 백엔드 5,815건(기존 MCP 경고 3건), 명시적으로 매핑한 PostgreSQL 80건,
전체 Ruff·Pyright, 변경 Python 625개 타입·포맷, 소유자 판정 45/추가 0,
의존성 85/6, 문서 214/22와 모델·고정 스키마 검사 4건이 통과했다. 검증 전후
변경 파일 630개의 바이트 해시는 같았고 테스트 DB 잔여 수는 0이었다. 이 문서의
증거 기록은 해당 검증 후 추가했다. 새 후보의 다섯 독립 검토와 채택은 아직 남았다.
원본 결과는 `output/org-authz/p1/b06-r24-*`, 거절된 검토는
`output/org-authz/b06-review-r24/`에 있다. 전체 P0–P8 목표는 계속 진행 중이다.

## B06 R25 평가 작업자 조회 순서 보완 (검토 대기)

R25 후보의 다섯 검토 결과와 원본 증거를 양쪽 작업 폴더에 보존한 뒤 채택을
보류했다. 평가 내용과 스킬 본문이 현재 사용자 권한보다 먼저 조회됐고, 커밋의
필수 계획 참조와 첫 검토 명령의 증거 기록도 보완이 필요했다.

평가 작업자는 실행 식별정보와 스킬 소속만 먼저 확인하고, 현재 계정·조직·회사
권한을 검증한 뒤 해당 스킬·평가 세트·실행의 본문을 읽도록 수정했다. 취소 확인과
재시작 복구에는 상태·식별정보만 사용한다. 기존 평가 결과, 사용량, 취소·실패·감사
기록과 정상 저장 동작은 유지했다. 새 조회 순서·정리 회귀 12건은 수정 전 실패했고,
수정 후 관련 테스트 42건이 통과했다. 계획의 옛 통합 테스트 경로는 실제 여섯 모듈과
명시적 PostgreSQL 사례로 대응시켜 기존 동작 기준을 그대로 검증했다.

전체 백엔드 5,827건(기존 MCP 경고 3건), 권한 테스트 1,175건, 명시적 PostgreSQL
88건, 대응한 런타임 회귀 174건이 통과했다. Ruff·Pyright, 변경 Python 628개 타입·
포맷, 소유자 판정 45/추가 0, 의존성 85/6, 문서 214/22와 모델·고정 스키마 검사
4건도 통과했다. 검증 전후 소스 633개의 해시가 같았고 임시 테스트 DB 잔여 수는
0이었다. 이 증거 기록은 해당 검증 후 추가했다. 원본은 `output/org-authz/p1/b06-r25-*`,
거절된 검토는 `output/org-authz/b06-review-r25/`에 있다. 새 후보의 다섯 독립 검토와
채택은 아직 남았다. 전체 P0–P8 목표는 계속 진행 중이다.

## B06 R26 실행 시작 전 취소 조회 보완 (검토 대기)

R26 후보는 검토 절차의 증거 누락과 실행 시작 전 취소 처리의 불필요한 본문 조회로
채택을 보류했다. 다섯 보고서와 원본 증거를 양쪽 작업 폴더에 보존하고 소스 633개의
일치를 확인한 뒤 수정했다. 현재 권한을 잃은 사용자의 작업도 취소·복구할 수 있도록
유지하면서, 대화·실행·입력 조회를 필요한 식별정보와 상태로 좁혔다. 보호된 실행이나
다른 프로세스 소유 실행에는 취소 ACK를 만들지 않고, 잘못 연결된 입력은 변경하지 않는다.

새 조회 회귀 8건은 수정 전 실패했고, 수정 후 관련 회귀 26건이 통과했다. 정상·조직
구성원 제거·회사 구성원 제거·계정 비활성화와 일반 사용자·운영자를 조합한 실제 m83
사례 8건에서도 취소·ACK·입력 수정 횟수·원본 내용·이벤트·감사 기록을 확인했다.
전체 백엔드 5,838건(기존 MCP 경고 3건), 권한 테스트 1,186건, 명시적 PostgreSQL
96건과 정적 검사가 통과했다. 변경 Python 630개 타입·포맷, 소유자 판정 45/추가 0,
의존성 85/6, 문서 214/22, 모델·고정 스키마 4건과 새 코드 규칙도 통과했다. 검증
전후 소스 635개의 해시가 같았고 임시 테스트 DB 잔여 수는 0이었다.

이 기록은 검증 후 추가했다. 원본은 `output/org-authz/p1/b06-r26-*`, 거절된 검토는
`output/org-authz/b06-review-r26/`에 있다. 새 후보의 검토 명령은 첫 실행부터 설정·
문서·원본 로그·정리 기록을 함께 제공한다. 다섯 독립 검토와 채택은 아직 남았으며
전체 P0–P8 목표는 계속 진행 중이다. B08 보관·복원·영구 삭제 계획은 실행 가능성
검토를 통과했고, B06 반영 후 현재 소스를 다시 확인해 개발을 시작한다.

## B-06 R27 adoption record (2026-10-11, Asia/Seoul)

B-06 is adopted at candidate `5b894c6661e4f24603eacb8b29344432af4a93dc`,
parent `54ce914f87a79e182e5228ddbc84e6dddb66569f`, with **635 source paths / 630
Python paths**. The authoritative adoption receipt is
`output/org-authz/b06-review-r27/adoption.json` (`adopted=true`, verdict `PASS`);
the aggregate is `output/org-authz/b06-review-r27/aggregate.md` (`R27 aggregate —
PASS`). All five dual-copy-preserved review lanes passed at the same candidate:
`f1-plan.md`, `f2-code.md`, `f3-qa.md`, `f4-security.md`, and `f5-context.md`
under `output/org-authz/b06-review-r27/`.

Fresh F3 acceptance evidence in `output/org-authz/b06-review-r27/f3-qa.md` records
the whole backend **5,838 passed / 3 existing MCP warnings / 220.25s**, authz
**1,186 passed / 171 integration deselected / 227.69s**, and canonical actual-m83
native **96 passed / 424.94s**; separate native collection was **96 collected,
collection PASSED**, with owned labs **0** and static checks **0 errors / 0
warnings**. Independent B08 Gate0 evidence in
`.omo/evidence/b08/task-1-gate0-report.md` and
`.omo/evidence/b08/task-1-b06-adoption.json` confirms **80 existing source hashes
and 41 planned-absent paths** against the adopted baseline.

The full P0–P8 implementation goal remains **ACTIVE/incomplete**; P1 remains
**in progress**. B08's four foundation tasks remain **in progress**. This record
does not claim B08 feature/UI/deployment/shared-database/provider acceptance or
overall completion; those boundaries remain explicit in the Gate0 and F3
evidence.

## B-08 Task 15 gate-start 기록 (2026-10-12, Asia/Seoul)

이 블록은 2026-10-12 native/full gate 시작 시점에 확인한 **bounded progress snapshot**이다.
이후 실행 결과를 자동 반영하는 evergreen current-status 기록이 아니다.

B08 Tasks 1–14는 현재 승인되었고, Task 15의 단일 cross-surface witness, 전체 회귀,
소스 freeze와 다섯 독립 검토는 아직 **OPEN**이다. Task 14 권위 증거
`.omo/evidence/b08/task-14-canonical49-rerun4-pass-report.md` (SHA-256
`edffb57bc9d37511a3ba8ff0683280f957c17e4de94bf4d8453ae3d72f9ef75a`)의 native
canonical49는 49 selected / 49 executed / 49 passed / 0 failed / 0 skipped / 0
deselected를 기록한다. migration current/head는 `b08_agent_lifecycle`이고, 140개
소스 경로 해시가 전후 동일했으며, owned cleanup은 완료되고 foreign resources는
보존되었다.

Task 14의 authority 및 retained-history native acceptance도 각각 승인되었다
(`.omo/evidence/b08/task-14-authority-native-acceptance.md`, SHA-256
`3bf228b29e258993bbc1577101ff1c9b7028502ba0e98a58b4b21110b95e1a57`;
`.omo/evidence/b08/task-14-retention-native-acceptance.md`, SHA-256
`2c85e607f7e8416dc45a8b28b6243e8730d4360f6d2dc23b0f1094b16080d550`). 현재 증거가
확인하는 범위는 metadata governance와 archive/restore/purge의 exact-name,
retained-history masking, cleanup replay 경계다. Provider invocation은 0이며, 이는
live provider 성공을 뜻하지 않는다.

Gate 시작 전에 확인한 Task 15 bounded native 결과는 다음과 같다. 각 결과는 해당 범위의
PASS일 뿐 canonical 전체 또는 F1–F5 승인을 뜻하지 않는다.

- private authorization cohort: 38 selected / 38 executed / 38 completed / 0 failed / 0 skipped /
  0 deselected. 이는 canonical 269-node 범위가 아니다. 결과 보고서
  `.omo/evidence/b08/task-15-private-cohort-green-r2-pass-report.md` (SHA-256
  `78a8c527daa1bb8f4f6719ea3379ca2d02580be633e463b54b8a3d5a3a8029bb`)에 기록되어 있다.
- queue concurrency: 4 selected / 4 executed / 4 completed / 0 failed / 0 skipped / 0 deselected.
  결과 보고서 `.omo/evidence/b08/task-15-queue-green-r1-pass-report.md` (SHA-256
  `dc5648d8011b3b40589498f9ed355e7da597078de71dbf61f75b8ea60f97638b`)는 canonical
  four-node queue mode로 한정한다.
- delete race: 3 selected / 3 executed / 3 completed / 0 failed / 0 skipped / 0 deselected.
  결과 보고서 `.omo/evidence/b08/task-15-native-delete-green-r1-pass-report.md` (SHA-256
  `55f1bf9ed4e916a97a3f43eec312cb5e269b6da37a7e5c1e2d7d8fe3abce89ba`)는 noncanonical
  exact-three coverage로 한정하며 broad 269-node gate를 주장하지 않는다.

이 gate-start snapshot에서 broad native all-mode, full backend, source freeze 완료, F1–F5
승인은 **PENDING**이었다. 따라서 이 기록은 full B08 또는 P0–P8 완료, full UI, provider/live
성공, production deployment, shared-database migration을 주장하지 않는다. 이후 native/full
결과와 review receipts는 이 문서를 갱신하지 않고 `.omo/evidence/`에 기록해 frozen source
union을 보존한다.

## B-08 Task 15 current gate validation (2026-10-12, Asia/Seoul)

앞의 gate-start snapshot 이후, 같은 B08 source union에서 다음 검증 결과를 확인했다. 이는
현재 candidate의 validation record이며 Task 15 final candidate 또는 F1–F5 승인을 뜻하지
않는다.

- Canonical native all은 269 selected / 269 executed / 269 completed / 0 failed / 0 skipped /
  0 unexpected deselected, 580.608초로 통과했다. 권위 보고서는
  `.omo/evidence/b08/task-15-native-all-structured-r3-native-report.md` (SHA-256
  `a0c125cd2689b545a3cb8b9abe74f87450421b2e01d00a2648c92dd103b44e67`)이다.
- Migration roundtrip은 `b08_agent_lifecycle`에서 한 revision downgrade 후 head 부재를
  확인하고 head와 fingerprint를 복원했으며, `migration_roundtrip=true`로 통과했다. 권위
  보고서는 `.omo/evidence/b08/task-15-migration-roundtrip-current-native-report.md`
  (SHA-256 `23785eb22aa2449b58c146abccc22b405358b40f7c4b75aa591937f9e59f5920`)이다.
- Whole backend는 **6,043 passed / 기존 MCP 경고 3개 / 272.14초**였다. Whole Ruff와
  Pyright는 통과했고, changed Python types/format은 231개, focused guards는 158개,
  lifecycle/history/purge는 102개가 통과했다. 권위 보고서는
  `.omo/evidence/b08/task-15-fullgate-current-report.md` (SHA-256
  `3d4281c6c3b23ae32184856985c34cfb547aae762b44e2d47f0b57300a28512d`)이다.

Native 및 migration 결과가 사용한 complete source seal은 3,478개 경로와
`bd312a411948540bee689b903143fb3a4a806af78072efcabc4930106f691a70`이며, full-gate after
manifest는 `c8b3a0bb5a393e2b573084a1b661c15a2bf4b222317c504f1614861c62f3f65a`이다. file-pin
delta는 0이며 metadata shape 차이만 있다. 다만 `check_plan_history.py verify`는 canonical
precondition baseline 부재로 실행할 수 없어 candidate-bound final check가 남아 있다.

따라서 Task 15 final candidate history/atomicity와 F1–F5는 아직 **OPEN**이고, full P0–P8도
계속 남아 있다. 이 기록은 provider/live 성공, production deployment, full UI 또는
shared-database migration을 주장하지 않는다. 이 append는 current source seal 이후의 문서
기록이므로, 다음 candidate freeze가 필요하면 이 문서 상태를 포함해 새 source union을
고정해야 한다.

## B-08 Task 15 adopted validation (2026-10-12, Asia/Seoul)

Task 15와 F1–F5 검토를 통과한 B08 candidate를 채택했다. candidate는
`c5cea625ee1e1470e386525edf120b02178bdbaf`이며, 3,488개 경로의 complete source
manifest SHA-256은 `df500998aac88c91facfc8f4cf66111beef53794698803c521df8acc222816b3`이다.

- Whole backend는 **6,047 passed / 기존 MCP 경고 3개**였고, canonical native는
  **269 selected / 269 executed / 0 failed / 0 skipped / 0 unexpected deselected**였다.
  Native 범위 안에 current 51개 노드가 포함되었다. completion은 passed receipt와 exact
  inventory로 확인했다.
- `b08_agent_lifecycle` migration roundtrip은 실제 downgrade에서 head 부재를 확인한 뒤
  재upgrade하고 fingerprint를 복원하는 경로로 통과했다.
- 별도 runtime audit은 네 가지 가설을 실제 통과 node assertion으로 반증했으며, global
  provider counter나 live provider 성공을 주장하지 않는다.
- F1–F5는 모두 같은 candidate와 source manifest를 가리키는 PASS 기록이다. 최종
  candidate-bound validator도 `B08_ACCEPTANCE_VERIFIED`로 통과했으며, source-pin 변조와
  review lane 누락 negative control은 각각 `B08_SOURCE_DRIFT`와
  `B08_FIVE_REVIEWS_REQUIRED`로 차단되었다.

권위 증거는 `.omo/evidence/b08/typed-final-gate-bundle.md`,
`.omo/evidence/b08/typed-final-acceptance-report.md`,
`.omo/evidence/b08/typed-final-acceptance.raw`,
`.omo/evidence/b08/typed-final-negative-controls.raw` 및
`.omo/evidence/b08/typed-final-reviews.json`에 보존되어 있다. 이 채택은 B08 backend
lifecycle 및 Conversation-owned retained-history 범위에 한정한다. C02/C04 grant
projection/outbox, E08 retention scheduling/policy, U04 full UI, live provider·human-browser
검증과 나머지 P0–P8은 계속 남아 있다.

## B-11/B-12 candidate - verification pending (2026-10-16, Asia/Seoul)

이 절은 B11 회사 관리 API와 B12 SaaS 운영자 회사 관리 API의 **candidate 상태**만
기록한다. parent Task 16의 candidate 게이트, F1–F6 독립 검토, Task 17의 기록이 아직
없으므로 B11과 B12는 위 checklist에서도 미체크로 남는다. 상태 전환은 Task 16B 이후
Task 17만 별도 절로 덧붙인다. 설계 근거는 ADR-023의 "B11/B12 company administration
(candidate - verification pending)" 절에, route 계약과 9.7 route 단위 crosswalk(57행)는
`org-authz-company-administration-contract.md`에 있다.

### 기준 SHA (따로 기록)

- 채택된 B08 SHA(이력 입력, 변경 없음): `c5cea625ee1e1470e386525edf120b02178bdbaf`.
  근거는 위 "B-08 Task 15 adopted validation" 절이다.
- 계획 baseline: `6d5323eec9ad27f0e5e6451e0f8fb271fc9dfa55`.
- 작성 시점의 candidate HEAD: `da84a76964d4fa91255832846f5b7ff58211ed30`
  (`feature/org-authz`, baseline 이후 48개 커밋). 이 문서 변경은 그 뒤에 커밋되므로,
  Task 16은 문서 커밋을 포함한 새 candidate SHA를 고정하고 검증해야 한다. 이 SHA는 게이트를
  통과한 기준점이 아니다.
- Migration: `b11_company_administration`의 부모는 `b08_agent_lifecycle` 하나다. 최종
  head와 ancestry, roundtrip 검증은 Task 16이 맡는다.

### candidate 범위 (구현, 검증 대기)

- B11 신규 route 17개: overview, 조직 목록/생성/이름 변경, archive/restore, 회사 구성원,
  역할, 회사 초대. 기존 `POST /api/invitations/accept`와 `POST /api/auth/register`에는
  `tv1.` 회사 토큰 분기만 추가했다.
- B12 신규 route 6개: 운영자 회사 목록, 생성, 상세, 메타데이터 수정, 정지, 재개.
- 구현 파일은 `router_registry.py`에 한 번씩 등록되어 있다(da84a769). 신규 route
  OpenAPI snapshot과 company route inventory test도 함께 들어갔다.
- 조직 archive는 실행 진입 직렬화와 함께 들어갔다. 이를 위해 parent Task 10의 I-unit
  계열이 추가되었다: gateway, `PreparedRunLease` registry, lease-bound
  checkpoint/tool/snapshot/provider leaf, dispatch permit, archive terminalizer,
  chat/queue/API/trigger prepared entry, recovery.
- 경계: archive는 Policy A(기본 조직은 모두 archive
  불가)와 30일 metadata-only restore로 제한된다. 조직 영구 purge는 포함하지 않는다(아래
  등록 참조).
- Policy A는 HTML 9.7(`org-authz-plan.html:1217`)과 S05(1989행)가 정한 "마지막 조직만
  보관 불가"를 더 좁힌 것이다. HTML 19장 결정이 아니라 parent plan
  (`.omo/plans/p1-company-administration.md:13`)의 결정이다. 운영자가 만든 회사의 첫 조직은
  기본 조직이므로 archive할 수 없다.

### 진행 방식 (B안)

검증 과잉으로 진척이 멈춘 문제 때문에 2026-10-16에 진행 방식을 B안으로 바꿨다. 근거는
`.omo/evidence/company-administration/b-plan-process-change-20261016.md`다. 각 단위는
실패 테스트 → 구현 → Ruff/Pyright → 필요 시 격리 PostgreSQL → 독립 리뷰 1회 → 커밋
순서로 진행한다. 봉인 체인과 메타 검증 도구는 새로 만들지 않는다. 같은 기록에 따르면, 단위별
전체 일반 스위트에는 HEAD와 대조해 같은 환경 실패(16 failed/36 errors: 샌드박스 소켓,
공유 data 등)만 남았다. Task 14 커밋(da84a769) 시점에는 그중 실제 결함이던 항목(조직 보관
fixture의 `archived_at` 누락, 미검토 OpenAPI snapshot)을 고쳐 8 failed/36 errors(모두 샌드박스
소켓·포트·외부 실행 환경)만 남았다. 이 결과는 Task 16의 unfiltered 전체 스위트 게이트를 대신하지 않는다.

### 아직 실행하지 않은 검증

- **native PostgreSQL lane 미실행.** 개발 샌드박스가 Docker 소켓을 차단한다. Task 14가
  등록한 전용 `company-administration` lane(신규 integration module 26개와 legacy race
  node 13개), canonical native `all`, migration roundtrip 모두 아직 실행하지 않았다.
  integration marker가 붙은 native module은 collect만 되었다. 따라서 실제 PostgreSQL에서의
  lock 경합, deadlock 부재, barrier race 결과(membership add/accept, archive와
  prepared-lease의 다섯 entry family, archive와 trigger claim, B08 agent archive 호환)는
  아직 증명되지 않았다. SQLite 일반 테스트로는 이를 증명할 수 없다.
- Task 16 Stage A/B의 다음 항목도 아직 없다: 전체 backend unfiltered 실행
  (`-m "not integration"`만 허용), full Ruff/Pyright/format, ratchet, assembled HTTP
  witness, F1–F6 receipt.

### 기본값과 켜기 전 선결 조건

`settings.prepared_run_lease_enabled`의 기본값은 `False`다. 기본 상태에서는 모든 실행이
gateway prefix 뒤의 legacy runtime으로 돈다. 다만 gateway 자체는 flag와 무관하게 모든 실행
진입에 적용되므로, legacy run도 정지, owner membership 상실, archive에서 fail-closed한다.
flag를 켜기 전에 다음 제품 결정이 필요하다.

1. OpenAI moderation middleware 처리. 이 middleware는 lease 밖에서 송신한다.
2. fallback model chain.
3. `recursion_limit`.
4. legacy 호환 정책 `agent_memory_file_write`.
5. 기본 memory 정책 `ask`(memory tools).

이 결정 전에는 대부분의 에이전트가 legacy로 fallback한다.

### 기록된 편차와 결정

- **Dispatch permit:** 계획 원문은 "serialized permit under these locks"로, permit을
  leaf 동안 보유하는 해석이 가능했다. 구현은 lock → decide → record → release 후에 I/O를
  수행한다. provider 송신, tool 호출, child launch 동안에는 lock, transaction, connection을
  보유하지 않는다. 승자는 permit이 lock을 잡는 시점에 정해진다. 그 이후에 커밋된 정지,
  회수, archive는 진행 중인 leaf에는 보이지 않고, 다음 dispatch 경계에서 반영된다.
- **정지는 대기 작업을 종료하지 않음:** pending input은 pending으로 남고, 정지 중에는
  claim되지 않는다. 실행 중인 run은 다음 permit에서 `tenant_suspended`로 끝난다. 재개 후에는
  같은 input을 다시 claim할 수 있다. 작업을 terminalize하는 것은 archive와 member exit뿐이다.
- **Agent API의 불투명 401:** 정지된 회사의 API key도 B05 계약대로
  `401 AGENT_API_KEY_INVALID`로 응답하며 `tenant_suspended`를 노출하지 않는다.
  플랫폼 운영 경로 외 모든 요청을 `403 tenant_suspended`로 막는
  `org-authz-plan.html:953`과 다르다.
- **archive 시 진행 중 실행 처리(리드 결정, 사용자 확인 대기):** S05
  (`org-authz-plan.html:1989`)는 "진행 중 실행은 끝까지 두고 새 실행 차단"이다. 구현은
  materialization pair가 있는 run만 닫힌 lease 계약으로 끝까지 두고, pair가 없는 활성 run은
  모두 실패시킨다. 이미 `running`인 legacy run도 포함되며 로컬 task도 취소된다. 준비되지 않은
  실행은 archive 후에도 config, memory, credential, history를 읽을 수 있어 닫힌 lease
  계약으로 경계를 지을 수 없기 때문이다. `prepared_run_lease_enabled`가 꺼져 있으면(기본값)
  실행 중인 legacy run 전부에 적용된다.
- **화면 항목 owner:** S03/S04의 운영자 회사 관리자 관리(초대, 재발송, 회수)는 새 후속 항목
  B12-F1이 맡는다. B12는 회사 생성 때 첫 관리자 링크만 발급한다. S04의
  `allow_system_models`와 시스템 자원 팬아웃은 E03, 이번 달 사용량·한도 열은 H03, 비밀 저장소
  namespace 상태는 B16이 맡는다. 매핑은 contract 문서의 화면 항목 표에 있다.
- **F4 수정과 보류:** 구성원·역할 쓰기는 회사 상태보다 호출자 자신의 membership을 먼저
  확인하고(enumeration-safe 404), pending 초대의 live token을 다시 내줄 때
  `tenant.invitation_reissue`를 남긴다(92781e8b). F4-4(Agent API run error에 원시 예외 문구,
  기존 동작)와 F4-5(`Settings` repr이 `jwt_secret`/`encryption_keys`를 일반 `str`로 노출,
  `SecretStr` 강화)는 후속 hardening으로 보류한다.
- **검증 면제:** `ruff format --check .`가 보고하는 74개 파일은 모두 이 branch diff 밖의 기존
  파일이다. `scripts/check_project_facts.py`는 baseline `6d5323ee`에서 이미 tracked
  `docs/exec-plans/org-authz-*.md` 4개가 `active/`·`completed/` 밖이라 실패하며, 이 branch가
  다섯 번째(`org-authz-company-administration-contract.md`)를 더한다. 봉인된 `.omo` 기록이
  이 경로를 참조하므로 이동은 미룬다.
- **process 도구와 type escape:** `backend/scripts/task10_review_binding.py`(순수 288 LOC)를
  비롯한 `backend/scripts/task10_*`는 B안 전환으로 은퇴한 I1 검토 도구이며 이력용으로만
  남긴다. `Any` 등 type escape 증가는 Task 10 runtime 모듈에 몰려 있다.
- **trigger 출력 비영속:** lease 아래 trigger 모드의 출력 파일은 run root cleanup 때
  삭제된다. legacy와 다른 동작이며, trigger lease 출력의 artifact 영속화는 구현되지 않았다.
- **MCP discovery 상한:** materialization lock을 잡은 채 수행하는 selected-MCP discovery는
  서버당 3초, 전체 5초로 제한된다. marker 이후의 호출은 10초이며 lock을 잡지 않는다.
- **이번 변경과 무관한 기존 버그(main에도 존재, 미수정):** 저장된 자식 에이전트의 legacy
  config에서 `MissingGreenlet`이 난다(`subagents.py:118`). legacy
  `execute_agent_stream`/`resume_agent_stream`은 worker의 `run_metrics` kwarg를 받지 않는다.

### 조직 purge 등록

조직 영구 purge는 **E08/I07**에 등록한다. E08은 restore 기간이 지난 조직을 보존 정책에 따라
예약 purge하고 `retention_sweep`을 맡는다. I07은 회사 영구 삭제 운영 CLI/API와 소속 조직의
연쇄 삭제를 맡는다. purge는 **resource disposition 계약**이 생긴 뒤에만 시작할 수 있다. 이
계약은 에이전트, 대화와 retained history, 자격 증명, skill/MCP, trigger/deployment,
artifact/file, 감사 행 각각의 삭제/보존/읽기 전용 처리와, E02 이전 및 C04 회수의 순서를
정해야 한다. 그 전까지 archive된 조직은 archive 상태로 남는다. B12는 I07(export, 삭제 예약,
삭제 취소, 영구 삭제)을 포함하지 않는다.

### 남은 task (B11/B12 밖)

- C04 — GrantService, outbox, 회수 순서. member exit seam의 outbox 확장 owner다.
- E02 — 구성원 제거 흐름과 자원 이전. `resource_transfer_required` 해소의 owner다.
- E03 — catalog policy와 runtime fanout.
- E07 — 회사 모델, 공용 자격 증명, 서비스 LLM, 회사 카탈로그 route.
- E08 — 보존 정책, `retention_sweep`, 조직 purge(I07과 공동), 감사 보존 field.
- E09 — 회사 공개 게시 정책, 승인 흐름, `/policies` route owner.
- H03 — 회사 usage/audit reader.
- H06 — 한도(회사/조직/사용자/에이전트), `/limits`, `/usage-limits`.
- H08 — 도메인 확인, SSO/LDAP route.
- I07 — 회사 export, 삭제 예약/취소, 영구 삭제.
- I08 — 회사별 파일 경로, rate-limit key, 공유 링크 회사 검증, Langfuse metadata.
- B13 — tenant/platform session, 파생 작업 컨텍스트.
- B16 — 회사별 data key와 ciphertext v2.
- B12-F1 — 운영자 회사 관리자 초대·재발송·회수(후속). B12는 첫 관리자 링크만 발급한다.

이 절은 다음 어떤 것도 주장하지 않는다: 9.7 전체, UI(B-07/B-09), 배포, provider/live
성공, shared-DB migration, purge, fanout, audit reader, session/RLS, 암호화.
