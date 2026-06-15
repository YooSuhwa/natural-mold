# Skill Evaluation Roadmap

This file is an index for the split skill-evaluation plans. Do not implement from this
file directly. Pick one implementation plan below and follow it task-by-task.

## Active Plan Documents

1. Foundation plan:
   `/Users/chester/.codex/worktrees/0d2b/natural-mold/docs/superpowers/plans/2026-06-16-skill-evaluation-v2-foundation.md`

   Use this for the current development cycle.

   Scope:
   - JSON v2 result schema.
   - Backward-compatible summary, benchmark, and case result fields.
   - Portable `evals/evals.json` import.
   - Upload and marketplace install evaluation-set preparation.
   - Optional LLM-generated smoke evaluation sets.
   - Manual execution policy: prepare sets automatically, run evaluations only when the user asks.

   This plan avoids changing the core execution harness.

2. Precision loop plan:
   `/Users/chester/.codex/worktrees/0d2b/natural-mold/docs/superpowers/plans/2026-06-16-skill-evaluation-precision-loop.md`

   Use this after the foundation plan is merged and verified.

   Scope:
   - Claude Code skill-creator style with-skill and baseline execution.
   - Real per-case execution artifacts.
   - LLM grading over real outputs.
   - No-skill baseline.
   - Previous-revision baseline.
   - Cancellation, timeout, and sandbox handling for long-running evaluation runs.

   This plan changes runtime behavior and should be reviewed separately.

## Why The Work Is Split

The foundation work is mostly schema, import, persistence, and product policy. It can be
tested with focused backend tests and should be low risk.

The precision loop work touches runtime execution, credentials, sandbox policy,
cancellation, artifacts, token usage, and cost. It needs a separate review surface because
bugs there can affect security and user-visible run behavior.

## Recommended Order

1. Complete and merge the foundation plan.
2. Manually verify that uploaded and marketplace-installed skills get prepared eval sets
   but do not auto-run.
3. Use the prepared eval-set contract as the input for the precision loop.
4. Implement the precision loop in its own branch or PR.
5. Add the richer evaluation dashboard only after the precision loop output is stable.

## Product Decision Locked By These Plans

Moldy should automatically prepare evaluation sets when a skill is uploaded or installed,
but evaluation execution should remain manual by default.

Reasons:
 - Users immediately see that a skill can be evaluated.
 - The platform avoids surprise LLM cost.
 - Network and credential use stays user-intentional.
 - Imported third-party skills are safer because they are not executed automatically.

## Claude Code Reference Split

Claude Code skill-creator informed both plans, but different parts apply to each phase.

Foundation plan uses:
 - `evals/evals.json` as a portable package-adjacent asset.
 - small realistic eval sets.
 - structured result fields such as evidence, feedback, claims, timing, and metrics.

Precision loop plan uses:
 - with-skill execution.
 - baseline execution.
 - grader/analyzer/comparator behavior.
 - iterative improvement signals.

## Current Development Rule

If a change can be shipped without executing user skills, it belongs in the foundation
plan. If it requires running a skill, comparing agent outputs, or storing per-run
execution artifacts, it belongs in the precision loop plan.
