# Execution Plans Index

This index mirrors the tracked execution-plan directories. The wording after
each link records its declared state; Completed also holds closed/superseded
plans, explicitly labeled below. Current product follow-ups live in `../../TASKS.md`.

The active organization authorization program follows the supplied
[HTML specification](active/org-authz-plan.html).

## Active

- [조직·권한·공유 실행 기록](active/org-authz-progress.md) — approved 2026-10-10; P0–P7 and P8 implementation merged in PR #314 (2026-10-19); I-01–I-05 operations and U-05 pending deployment.
- [개인 테넌트·회사 키·공유 공백 실행 계획](active/personal-tenant-sharing-plan.md) — approved 2026-10-21 (Q1–Q8 recommendations accepted); implementation in progress.

## Completed

- [백로그 C — credentials list N+1 복호화 제거](completed/backlog-c-field-keys-cache.md) — implemented; current field_keys cache verified 2026-09-08.
- [백로그 E — Connection 엔티티 통합 리팩토링](completed/backlog-e-connection-refactor.md) — closed/superseded by ADR-009, not pending work.
- [HiTL Phase 2 — Wire Contract](completed/hitl-phase2-contract.md) — APPROVED — 후속 마일스톤(M1·M2) 진입 게이트.

## Deferred programs

The programs below are neither approved active plans nor current commitments.
Each requires separate scope approval, design, implementation, and test/E2E
gates before it can move into `Active`.

<!-- future-program: rubric; status=deferred -->
- Rubric
<!-- future-program: total-technical-debt-cleanup; status=deferred -->
- Total technical-debt cleanup
<!-- future-program: domain-relocation; status=deferred -->
- Domain relocation
<!-- future-program: attachment-video-expansion; status=deferred -->
- Attachment and video expansion
<!-- future-program: async-subagents; status=deferred -->
- Async subagents
<!-- future-program: store-composite-backend-adoption; status=deferred -->
- General StoreBackend adoption (CompositeBackend already powers scoped offloads)
<!-- future-program: observation-window-removal; status=deferred -->
- Observation-window removal
