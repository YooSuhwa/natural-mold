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
- [ ] A-02 — OpenFGA Compose and settings; private network startup evidence.
- [ ] A-03 — pinned SDK client; timeout, retry, metrics tests.
- [ ] A-04 — model/tests and CI; 144 checks and six ListObjects cases.
- [ ] A-05 — bootstrap/check/explain CLI; model ID persistence.
- [ ] A-06 — fake authorization and live integration lane.
- [ ] A-07 — route policy declarations and missing-policy ratchet.
- [ ] A-08 — ownership predicate ratchet.
- [ ] A-09 — CSRF and single Alembic head invariants.
- [ ] A-10 — middleware ordering, durable auth interrupt, revocation prototypes.
- [ ] A-11 — RLS 15 scenarios and two counterexamples in disposable PostgreSQL.
- [ ] A-12 — boundary intersection benchmark with the supplied final model.
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

## Next stage start conditions

P1 begins only after every P0 acceptance item and the phase CI-equivalent checks
pass. Each missing prerequisite remains unchecked. Required checks include
backend lint/types/tests/coverage, disposable PG integration and cleanup,
frontend lint/build/tests/coverage and scripted smoke.
