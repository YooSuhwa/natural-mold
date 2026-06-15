# Skill Evaluation Precision Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Claude Code skill-creator style precision evaluation mode that performs real with-skill and baseline executions, grades real outputs, writes safe run artifacts, and produces JSON v2 benchmark results.

**Architecture:** Extend the existing manual `SkillEvaluationRun` worker with a second mode, `precision_with_baseline`, backed by a Moldy-native execution harness, a grader service, artifact storage, and optional previous-revision baseline support.

**Tech Stack:** FastAPI, SQLAlchemy async, existing Moldy agent runtime, existing skill runtime mounting, existing credential and sandbox policies, system LLM settings, JSON v2 result schema from the foundation plan.

---

## 1. Prerequisite

Implement this only after the foundation plan is merged:

- `/Users/chester/.codex/worktrees/0d2b/natural-mold/docs/superpowers/plans/2026-06-16-skill-evaluation-v2-foundation.md`

Required foundation deliverables:

- `summary.schema_version = 2`
- `summary.kpis`
- `benchmark.comparison`
- standard `case_results`
- portable eval file import
- prepared eval sets on upload/install
- manual execution policy

This precision loop should not be bundled into the foundation PR. It touches runtime execution, artifact storage, baseline semantics, cost, cancellation, and sandbox boundaries.

---

## 2. Scope

Included:

- `run_config.mode = "precision_with_baseline"`
- worker evaluator selection by mode
- real with-skill execution per eval case
- real baseline execution per eval case
- LLM grading over real outputs
- JSON v2 summary, benchmark, and case results
- safe artifact storage
- cancellation checks between cases
- timeout behavior
- no-skill baseline
- previous-revision baseline as a follow-up task inside this plan

Excluded from first precision milestone:

- automatic execution after upload/install
- full new dashboard UI
- long-running description optimization loop
- train/test trigger description optimizer
- blind user-facing comparison viewer

Those can be added after the core precision loop is reliable.

---

## 3. Claude Code Skill-Creator Findings For This Phase

The actual installed Claude Code skill-creator was reviewed at:

- `/Users/chester/.claude/plugins/marketplaces/claude-plugins-official/plugins/skill-creator/skills/skill-creator/SKILL.md`
- `/Users/chester/.claude/plugins/marketplaces/claude-plugins-official/plugins/skill-creator/skills/skill-creator/scripts/run_eval.py`
- `/Users/chester/.claude/plugins/marketplaces/claude-plugins-official/plugins/skill-creator/skills/skill-creator/scripts/run_loop.py`
- `/Users/chester/.claude/plugins/marketplaces/claude-plugins-official/plugins/skill-creator/skills/skill-creator/scripts/aggregate_benchmark.py`
- `/Users/chester/.claude/plugins/marketplaces/claude-plugins-official/plugins/skill-creator/skills/skill-creator/agents/grader.md`
- `/Users/chester/.claude/plugins/marketplaces/claude-plugins-official/plugins/skill-creator/skills/skill-creator/agents/analyzer.md`
- `/Users/chester/.claude/plugins/marketplaces/claude-plugins-official/plugins/skill-creator/skills/skill-creator/agents/comparator.md`
- `/Users/chester/.claude/plugins/marketplaces/claude-plugins-official/plugins/skill-creator/skills/skill-creator/references/schemas.md`

### 3.1 Runtime Loop To Mirror

Claude Code's loop:

1. For each eval case, run with the skill available.
2. For each eval case, run a baseline.
3. Capture transcript, output, metadata, timing, and token usage.
4. Grade outputs with a dedicated grader.
5. Aggregate benchmark.
6. Analyze weak cases, regressions, and skill value.
7. Let the user review and iterate.

Moldy should mirror the behavior, not the implementation. Do not call `claude -p`. Use Moldy's runtime, credentials, sandbox policy, audit model, and worker queue.

### 3.2 Baseline Semantics From Claude

Claude uses:

- no-skill baseline for new skills
- previous-version baseline when improving an existing skill

Moldy should support both:

- milestone 1: no-skill baseline
- milestone 2: previous skill revision baseline

### 3.3 Grader Semantics From Claude

Claude's grader:

- evaluates expectation satisfaction
- compares outputs to expected behavior
- records evidence
- extracts claims
- summarizes timing and execution metrics
- flags weak evals
- emits strict JSON

Moldy's grader should produce JSON that feeds the foundation plan's result normalizer.

### 3.4 Analyzer Semantics From Claude

Claude's analyzer finds:

- cases where both skill and baseline pass
- cases where both fail
- cases where the skill regresses quality
- token/time regressions
- flaky or non-discriminating evals
- concrete skill improvement suggestions

Moldy can store this in:

- `summary.eval_feedback`
- `summary.claims`
- `summary.execution_metrics`
- `benchmark.comparison`

No separate analyzer UI is required in the first implementation.

---

## 4. Current Moldy Runtime Surfaces

### 4.1 Evaluation Worker

Current files:

- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_evaluation_worker.py`
- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_evaluation_worker_state.py`
- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_evaluation_service.py`

Current worker:

- dequeues manual runs
- loads evaluation context
- uses one evaluator by default
- marks running/grading/completed/failed/cancelled
- checks cancellation

Needed change:

- choose evaluator by `run.run_config["mode"]`

### 4.2 Current Quick Evaluator

Current file:

- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_evaluation_llm.py`

Keep it as:

- `quick_llm`

Do not degrade the fast path.

### 4.3 Skill Runtime Context

Current files:

- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_evaluation_worker_state.py`
- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/skills/runtime.py`
- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/skills/prompt.py`
- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/agent_runtime/skill_executor.py`

Precision mode should use the same runtime contract:

- selected skill mounted
- `SKILL.md` visible to the agent
- `execute_in_skill` policy respected
- credentials resolved through existing binding model
- execution profile respected

### 4.4 Agent Runtime

Relevant runtime files from project architecture:

- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/agent_runtime/runtime_config.py`
- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/agent_runtime/runtime_component_builder.py`
- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/agent_runtime/agent_stream_runner.py`
- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/agent_runtime/model_factory.py`
- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/agent_runtime/tool_factory.py`

Implementation must inspect these files before coding the execution harness. The plan assumes a small evaluation harness can reuse existing model and skill mounting logic without requiring a persisted user-created agent.

---

## 5. Run Mode Contract

Use `SkillEvaluationRun.run_config`.

Default:

```json
{
  "mode": "quick_llm"
}
```

Precision mode:

```json
{
  "mode": "precision_with_baseline",
  "baseline": {
    "kind": "none"
  }
}
```

Future previous-revision baseline:

```json
{
  "mode": "precision_with_baseline",
  "baseline": {
    "kind": "skill_revision",
    "revision_id": "uuid"
  }
}
```

Allowed `mode` values:

- `quick_llm`
- `precision_with_baseline`

Allowed `baseline.kind` values:

- `none`
- `skill_revision`

---

## 6. Artifact Contract

Precision mode should set `SkillEvaluationRun.artifact_path`.

Recommended path:

```text
backend/data/skill-evaluation-runs/<run_id>/
├── metadata.json
├── benchmark.json
└── cases/
    └── case-000/
        ├── input.json
        ├── with_skill.json
        ├── baseline.json
        ├── grading.json
        └── timing.json
```

### 6.1 metadata.json

```json
{
  "run_id": "uuid",
  "evaluation_set_id": "uuid",
  "skill_id": "uuid",
  "mode": "precision_with_baseline",
  "baseline": {
    "kind": "none"
  },
  "case_count": 3,
  "schema_version": 2
}
```

### 6.2 input.json

```json
{
  "case_index": 0,
  "name": "Case name",
  "input": "User-like task prompt",
  "expected": "Observable expected behavior",
  "metadata": {
    "expectations": ["Expectation 1"]
  }
}
```

### 6.3 with_skill.json and baseline.json

```json
{
  "status": "completed",
  "output": "Assistant response or structured output.",
  "triggered": true,
  "duration_ms": 2100,
  "tokens": 1300,
  "error": null,
  "redactions_applied": true
}
```

### 6.4 grading.json

```json
{
  "status": "passed",
  "score": 0.92,
  "baseline_status": "failed",
  "baseline_score": 0.2,
  "evidence": "The with-skill output satisfied all expectations.",
  "grader_feedback": "The skill improved the task outcome.",
  "eval_feedback": []
}
```

### 6.5 Secret Safety

Artifacts may contain user prompts and model outputs, so they must not be emitted to audit logs.

Before writing runtime outputs:

- apply existing redaction helpers where available
- redact credential env values
- avoid storing raw subprocess stderr if it may contain secrets
- store only bounded output sizes

---

## 7. Implementation Tasks

### Task 1: Add Run Mode Validation And Worker Selection

- [ ] Update `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/routers/skill_evaluations.py`.

Requirements:

- Accept optional run mode in create-run request.
- Default missing mode to `quick_llm`.
- Reject unknown modes with 422.
- Preserve existing manual run behavior.

- [ ] Update `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_evaluation_worker.py`.

Requirements:

- Select evaluator by `run.run_config["mode"]`.
- Keep `LlmSkillEvaluationEvaluator` as default for `quick_llm`.
- Use new precision evaluator for `precision_with_baseline`.

Tests:

- [ ] Add or extend worker/API tests.

Required tests:

```python
async def test_run_defaults_to_quick_llm_mode(...) -> None:
    ...


async def test_invalid_run_mode_is_rejected(...) -> None:
    ...


async def test_worker_uses_quick_evaluator_for_quick_mode(...) -> None:
    ...


async def test_worker_uses_precision_evaluator_for_precision_mode(...) -> None:
    ...
```

Verification:

```bash
cd /Users/chester/.codex/worktrees/0d2b/natural-mold/backend
uv run pytest -q tests/test_skill_evaluations.py tests/test_skill_evaluation_worker.py
uv run ruff check app/routers/skill_evaluations.py app/services/skill_evaluation_worker.py
```

### Task 2: Add Precision Artifact Writer

- [ ] Create `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_evaluation_artifacts.py`.

Responsibilities:

- Create run artifact directory.
- Write bounded JSON files.
- Return relative artifact path.
- Prevent path traversal.
- Redact sensitive values.
- Clean up partial artifacts on unrecoverable setup failure.

Public functions:

```python
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import UUID


class SkillEvaluationArtifactWriter:
    def __init__(self, *, run_id: UUID, base_dir: Path) -> None:
        ...

    def write_metadata(self, payload: Mapping[str, Any]) -> None:
        ...

    def write_case_input(self, *, case_index: int, payload: Mapping[str, Any]) -> None:
        ...

    def write_case_result(
        self,
        *,
        case_index: int,
        name: str,
        payload: Mapping[str, Any],
    ) -> None:
        ...

    def write_benchmark(self, payload: Mapping[str, Any]) -> None:
        ...

    @property
    def relative_path(self) -> str:
        ...
```

Tests:

- [ ] Add `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/tests/test_skill_evaluation_artifacts.py`.

Required tests:

```python
def test_artifact_writer_creates_expected_layout(tmp_path: Path) -> None:
    ...


def test_artifact_writer_bounds_large_output(tmp_path: Path) -> None:
    ...


def test_artifact_writer_rejects_path_traversal(tmp_path: Path) -> None:
    ...


def test_artifact_writer_redacts_secret_values(tmp_path: Path) -> None:
    ...
```

Verification:

```bash
cd /Users/chester/.codex/worktrees/0d2b/natural-mold/backend
uv run pytest -q tests/test_skill_evaluation_artifacts.py
uv run ruff check app/services/skill_evaluation_artifacts.py
```

### Task 3: Add Moldy-Native Evaluation Execution Harness

- [ ] Create `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_evaluation_execution_harness.py`.

Responsibilities:

- Run a single eval case with a specified skill mounting mode.
- Return normalized execution output.
- Capture duration and tokens when available.
- Capture whether the skill was triggered if the runtime exposes that signal.
- Respect cancellation boundaries.

Suggested types:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class SkillEvaluationExecutionMode(StrEnum):
    WITH_SKILL = "with_skill"
    BASELINE_NO_SKILL = "baseline_no_skill"
    BASELINE_SKILL_REVISION = "baseline_skill_revision"


@dataclass(frozen=True)
class SkillEvaluationExecutionResult:
    status: str
    output: str
    triggered: bool | None
    duration_ms: int | None
    tokens: int | None
    error: str | None
    metadata: dict[str, Any]
```

Public function:

```python
async def run_skill_evaluation_case(
    *,
    context: SkillEvaluationWorkerContext,
    case: Mapping[str, Any],
    mode: SkillEvaluationExecutionMode,
    baseline_revision_id: UUID | None = None,
) -> SkillEvaluationExecutionResult:
    ...
```

Implementation notes:

- Inspect the existing agent runtime before implementing.
- Prefer reusing model/tool/skill component builders.
- Do not create persisted `Agent` rows only for evaluation.
- Do not bypass credential checks.
- Do not bypass sandbox policies.
- Bound runtime output size.

Fallback milestone:

- If full agent runtime reuse is too coupled, implement the harness first for deterministic `execute_in_skill` cases and keep conversational precision as the next subtask. Do not label that as complete precision mode in user-facing copy.

Tests:

- [ ] Add `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/tests/test_skill_evaluation_execution_harness.py`.

Use fake model/runtime objects. Do not call real LLMs.

Required tests:

```python
async def test_harness_runs_case_with_skill_mount(...) -> None:
    ...


async def test_harness_runs_case_without_skill_mount(...) -> None:
    ...


async def test_harness_returns_error_result_for_runtime_error(...) -> None:
    ...


async def test_harness_bounds_output_size(...) -> None:
    ...


async def test_harness_uses_existing_credential_policy(...) -> None:
    ...
```

Verification:

```bash
cd /Users/chester/.codex/worktrees/0d2b/natural-mold/backend
uv run pytest -q tests/test_skill_evaluation_execution_harness.py
uv run ruff check app/services/skill_evaluation_execution_harness.py
```

### Task 4: Add Precision Grader

- [ ] Create `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_evaluation_precision_grader.py`.

Responsibilities:

- Grade one with-skill/baseline pair.
- Use system LLM.
- Emit strict JSON.
- Include evidence and feedback.
- Include weak-eval feedback when relevant.

Prompt should include:

- eval case input
- expected behavior
- explicit expectations
- with-skill output
- baseline output
- execution metadata

Prompt must instruct:

- do not reward verbosity alone
- compare against expected behavior
- mark both-pass cases as weak if the skill adds no observable value
- mark both-fail cases clearly
- output JSON only

Expected grader result:

```json
{
  "status": "passed",
  "score": 0.92,
  "baseline_status": "failed",
  "baseline_score": 0.2,
  "evidence": "The with-skill output satisfied all expectations.",
  "grader_feedback": "The skill improved structure and completeness.",
  "eval_feedback": [
    {
      "kind": "baseline_also_passed",
      "message": "The case may not distinguish the skill strongly."
    }
  ]
}
```

Tests:

- [ ] Add `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/tests/test_skill_evaluation_precision_grader.py`.

Required tests:

```python
async def test_precision_grader_parses_valid_json(...) -> None:
    ...


async def test_precision_grader_rejects_invalid_json(...) -> None:
    ...


async def test_precision_grader_clamps_scores(...) -> None:
    ...


async def test_precision_grader_includes_eval_feedback(...) -> None:
    ...
```

Verification:

```bash
cd /Users/chester/.codex/worktrees/0d2b/natural-mold/backend
uv run pytest -q tests/test_skill_evaluation_precision_grader.py
uv run ruff check app/services/skill_evaluation_precision_grader.py
```

### Task 5: Add Precision Evaluator

- [ ] Create `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_evaluation_precision.py`.

Responsibilities:

- Iterate eval cases.
- Check cancellation before each case and between with/baseline/grading steps.
- Run with-skill execution.
- Run baseline execution.
- Write artifacts.
- Grade pair.
- Normalize final output through JSON v2 result normalizer.
- Return `summary`, `benchmark`, `case_results`, and `artifact_path`.

Pseudo-flow:

```python
async def evaluate(context: SkillEvaluationWorkerContext) -> SkillEvaluationResult:
    artifact_writer = SkillEvaluationArtifactWriter(...)
    case_results = []

    for case_index, case in enumerate(context.evaluation_set.evals):
        await context.check_cancelled()
        with_skill = await run_skill_evaluation_case(..., mode=WITH_SKILL)

        await context.check_cancelled()
        baseline = await run_skill_evaluation_case(..., mode=BASELINE_NO_SKILL)

        await context.check_cancelled()
        graded = await grade_precision_case(...)

        artifact_writer.write_case_result(...)
        case_results.append(merge_execution_and_grade(...))

    summary, benchmark, normalized_cases = normalize_skill_evaluation_result(...)
    artifact_writer.write_benchmark(benchmark)
    return SkillEvaluationResult(...)
```

Tests:

- [ ] Add `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/tests/test_skill_evaluation_precision.py`.

Required tests:

```python
async def test_precision_evaluator_runs_with_skill_and_baseline_for_each_case(...) -> None:
    ...


async def test_precision_evaluator_writes_artifacts(...) -> None:
    ...


async def test_precision_evaluator_returns_json_v2(...) -> None:
    ...


async def test_precision_evaluator_marks_case_error_without_aborting_all_cases(...) -> None:
    ...


async def test_precision_evaluator_respects_cancel_check_between_cases(...) -> None:
    ...
```

Verification:

```bash
cd /Users/chester/.codex/worktrees/0d2b/natural-mold/backend
uv run pytest -q tests/test_skill_evaluation_precision.py tests/test_skill_evaluation_worker.py
uv run ruff check app/services/skill_evaluation_precision.py
```

### Task 6: Add No-Skill Baseline Estimate

- [ ] Update `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_evaluation_service.py`.

Requirements:

- Estimate `precision_with_baseline` as higher cost than `quick_llm`.
- Include two executions per case plus grader call.
- Preserve existing estimate behavior for quick mode.

Example estimate metadata:

```json
{
  "mode": "precision_with_baseline",
  "case_count": 3,
  "model_calls_per_case": 3,
  "execution_runs_per_case": 2,
  "estimated_model_calls": 9
}
```

Tests:

- [ ] Add estimate tests.

Required tests:

```python
async def test_precision_estimate_counts_two_executions_and_grader_call(...) -> None:
    ...


async def test_quick_estimate_is_unchanged(...) -> None:
    ...
```

Verification:

```bash
cd /Users/chester/.codex/worktrees/0d2b/natural-mold/backend
uv run pytest -q tests/test_skill_evaluation_service.py
uv run ruff check app/services/skill_evaluation_service.py
```

### Task 7: Add Previous Revision Baseline

Implement after no-skill precision works.

- [ ] Inspect and reuse `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_revision_service.py`.

Requirements:

- Validate revision belongs to the same skill.
- Materialize previous revision snapshot safely.
- Mount current skill for with-skill run.
- Mount previous revision for baseline run.
- Store baseline metadata in summary and artifacts.

Run config:

```json
{
  "mode": "precision_with_baseline",
  "baseline": {
    "kind": "skill_revision",
    "revision_id": "uuid"
  }
}
```

Tests:

- [ ] Add revision baseline tests.

Required tests:

```python
async def test_precision_baseline_can_use_previous_revision_snapshot(...) -> None:
    ...


async def test_revision_baseline_rejects_revision_from_other_skill(...) -> None:
    ...


async def test_revision_baseline_handles_missing_snapshot_as_failed_run(...) -> None:
    ...
```

Verification:

```bash
cd /Users/chester/.codex/worktrees/0d2b/natural-mold/backend
uv run pytest -q tests/test_skill_evaluation_precision.py tests/test_skill_revision_service.py
uv run ruff check app/services/skill_evaluation_precision.py app/services/skill_revision_service.py
```

### Task 8: Minimal Frontend Mode Selector

This task should happen only after backend precision mode is stable.

Files:

- `/Users/chester/.codex/worktrees/0d2b/natural-mold/frontend/src/components/skill/skill-evaluation-set-card.tsx`
- `/Users/chester/.codex/worktrees/0d2b/natural-mold/frontend/src/lib/types/skill-evaluation.ts`
- `/Users/chester/.codex/worktrees/0d2b/natural-mold/frontend/messages/ko.json`
- `/Users/chester/.codex/worktrees/0d2b/natural-mold/frontend/messages/en.json`

Requirements:

- Default button remains quick run.
- Add explicit option for precision comparison.
- Show estimate before precision run.
- Do not auto-run precision.
- User-facing strings must use `next-intl`.

Possible Korean copy:

- `빠른 LLM 평가`
- `정밀 비교 평가`
- `스킬 사용 결과와 기준 결과를 모두 실행해서 비교합니다.`

Possible English copy:

- `Quick LLM evaluation`
- `Precision comparison`
- `Runs both with-skill and baseline outputs for comparison.`

Verification:

```bash
cd /Users/chester/.codex/worktrees/0d2b/natural-mold/frontend
pnpm lint:i18n
pnpm lint:design-system
pnpm lint
```

---

## 8. Security Requirements

Precision mode is manual only.

Before run:

- Check missing credentials.
- Check evaluation set size.
- Estimate cost.
- Respect user's explicit run action.

During run:

- Use existing sandbox and skill execution policy.
- No unrestricted subprocess execution.
- No unbounded output capture.
- No credential values in audit logs.
- Redact known credential values from artifacts.
- Check cancellation between steps.
- Apply timeout to each case and overall run.

Audit logs may include:

- run id
- skill id
- evaluation set id
- mode
- baseline kind
- case count
- artifact path
- status

Audit logs must not include:

- full prompts
- full outputs
- transcripts
- stdout/stderr
- raw tool args
- credential values

---

## 9. Manual QA

Use local app surface after tests pass.

Backend:

```bash
cd /Users/chester/.codex/worktrees/0d2b/natural-mold/backend
uv run uvicorn app.main:app --reload --port 8001 --reload-dir app
```

Frontend:

```bash
cd /Users/chester/.codex/worktrees/0d2b/natural-mold/frontend
NEXT_PUBLIC_API_BASE_URL=http://localhost:8001 pnpm dev -- --port 3000
```

Manual checks:

- Open a skill with a prepared evaluation set.
- Run quick mode and confirm existing behavior still works.
- Run precision mode on a small set.
- Confirm run status updates to completed.
- Confirm JSON v2 fields are present.
- Confirm artifact path exists.
- Confirm no secrets are present in artifacts.
- Cancel a running precision evaluation and confirm it stops between cases.
- If previous-revision baseline is implemented, improve a skill and compare current vs previous revision.

---

## 10. Verification Commands

Focused backend:

```bash
cd /Users/chester/.codex/worktrees/0d2b/natural-mold/backend
uv run pytest -q tests/test_skill_evaluation_artifacts.py
uv run pytest -q tests/test_skill_evaluation_execution_harness.py
uv run pytest -q tests/test_skill_evaluation_precision_grader.py
uv run pytest -q tests/test_skill_evaluation_precision.py
uv run pytest -q tests/test_skill_evaluation_worker.py
uv run pytest -q tests/test_skill_evaluation_service.py
```

Full backend:

```bash
cd /Users/chester/.codex/worktrees/0d2b/natural-mold/backend
uv run pytest
uv run ruff check .
```

Frontend only after UI changes:

```bash
cd /Users/chester/.codex/worktrees/0d2b/natural-mold/frontend
pnpm lint:i18n
pnpm lint:design-system
pnpm lint
```

---

## 11. Definition of Done

Milestone 1, no-skill precision baseline:

- [ ] `precision_with_baseline` run mode is accepted.
- [ ] Worker selects precision evaluator by mode.
- [ ] Each case runs once with the evaluated skill.
- [ ] Each case runs once without the skill.
- [ ] Grader compares real outputs.
- [ ] Run stores JSON v2 summary, benchmark, and case results.
- [ ] Run writes bounded safe artifacts.
- [ ] Cancellation works between cases.
- [ ] Quick eval behavior is unchanged.
- [ ] Backend focused tests pass.
- [ ] Backend `ruff check` passes.
- [ ] Manual QA confirms precision run through the app surface.

Milestone 2, previous-revision baseline:

- [ ] `baseline.kind = "skill_revision"` is accepted.
- [ ] Revision ownership is validated.
- [ ] Previous revision snapshot can be mounted as baseline.
- [ ] Current vs previous skill comparison works.
- [ ] Tests cover wrong-skill revision rejection.
- [ ] Manual QA confirms improve-flow comparison.

Milestone 3, minimal UI:

- [ ] User can choose quick or precision mode.
- [ ] Precision run shows estimate first.
- [ ] User-facing strings are i18n-backed.
- [ ] Frontend lint and design-system checks pass.

