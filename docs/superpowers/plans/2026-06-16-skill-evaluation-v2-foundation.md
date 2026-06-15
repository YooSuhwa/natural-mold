# Skill Evaluation V2 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the low-risk foundation for Moldy skill evaluation: backward-compatible JSON v2 result output, portable `evals/evals.json` import, automatic evaluation-set preparation on upload and marketplace install, and an explicit manual-run policy.

**Architecture:** Keep the existing `SkillEvaluationSet` and `SkillEvaluationRun` tables, add shared backend services for result normalization and eval-set preparation, wire preparation into upload/install transactions, and preserve the current quick LLM evaluator as the only execution mode in this phase.

**Tech Stack:** FastAPI, SQLAlchemy async, Pydantic, existing Moldy skill package storage, current skill evaluation worker, current system LLM settings, Next.js/TanStack Query UI compatibility.

---

## 1. Scope

This document is the implementation plan for the work that should happen now.

Included:

- JSON v2 result standardization.
- Legacy result-field compatibility for the current UI.
- Claude Code style `evals/evals.json` import.
- Moldy style `evals/evals.json` import.
- Automatic evaluation-set preparation after skill upload.
- Automatic evaluation-set preparation after marketplace skill install.
- Optional LLM-generated smoke eval sets when no embedded eval file exists.
- Manual execution policy: preparation creates sets, not runs.
- Optional manual re-prepare API for existing skills.

Excluded:

- Real with-skill/baseline execution.
- Previous revision baseline execution.
- Precision benchmark artifacts.
- New evaluation dashboard UI.
- Automatic evaluation execution after install.

The excluded work belongs in:

- `/Users/chester/.codex/worktrees/0d2b/natural-mold/docs/superpowers/plans/2026-06-16-skill-evaluation-precision-loop.md`

---

## 2. Source Baseline

### 2.1 Evaluation Models

Current model file:

- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/models/skill_evaluation.py`

Relevant tables:

- `SkillEvaluationSet`
  - `skill_id`
  - `name`
  - `description`
  - `source_kind`
  - `evals`
  - `created_by`

- `SkillEvaluationRun`
  - `evaluation_set_id`
  - `status`
  - `run_config`
  - `estimate`
  - `summary`
  - `benchmark`
  - `case_results`
  - `artifact_path`

No migration is needed for this phase because the fields being standardized are already JSON columns.

### 2.2 Evaluation API

Current route file:

- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/routers/skill_evaluations.py`

Current behavior:

- Users can create/list evaluation sets.
- Users can estimate runs.
- Users can manually create runs.
- Users can cancel queued/running runs.

This already supports the manual-run policy. Upload and marketplace install should add evaluation sets only.

### 2.3 Evaluation Schemas

Current schema file:

- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/schemas/skill_evaluation.py`

The response shape already allows flexible JSON:

- `summary: dict[str, JsonValue] | None`
- `benchmark: dict[str, JsonValue] | None`
- `case_results: list[JsonValue] | None`

The JSON v2 fields can be added without changing the public Pydantic response schema.

### 2.4 Current Quick Evaluator

Current evaluator files:

- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_evaluation_worker.py`
- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_evaluation_worker_state.py`
- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_evaluation_llm.py`
- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_evaluation_llm_payload.py`
- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_evaluation_llm_results.py`

Current quick evaluator behavior:

- Builds skill preview.
- Runs deterministic `execute_in_skill` checks for cases that explicitly contain execution metadata.
- Sends skill preview, eval cases, and deterministic results to the system LLM.
- Parses LLM JSON.
- Stores summary, benchmark, and case results.

This phase should keep that behavior and only standardize the resulting JSON shape.

### 2.5 Current Builder Eval Path

Current files:

- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_builder_eval_service.py`
- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_builder_evaluations.py`
- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/agent_runtime/skill_builder/eval_case_generator.py`
- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/agent_runtime/skill_builder/eval_templates.py`
- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/agent_runtime/skill_builder/deterministic_eval_runner.py`

Builder eval currently uses generated/template cases and synthetic baseline values. This phase should make that path emit JSON v2 too, but should not try to turn it into a real precision loop.

### 2.6 Upload Flow

Current upload route:

- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/routers/skill_uploads.py`

Current behavior:

- Validates package.
- Extracts package.
- Scans for secrets.
- Creates `Skill`.
- Creates initial skill revision.
- Emits audit event.

Missing behavior:

- It does not inspect `evals/evals.json`.
- It does not create `SkillEvaluationSet`.

### 2.7 Marketplace Install Flow

Current install service:

- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/marketplace/install_service.py`

Current marketplace router:

- `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/routers/marketplace.py`

Current behavior:

- Installs marketplace skill as a local `Skill`.
- Creates installation record.
- Persists credential bindings.

Missing behavior:

- It does not inspect installed package eval files.
- It does not create `SkillEvaluationSet`.

### 2.8 Current Frontend Compatibility Surface

Current files:

- `/Users/chester/.codex/worktrees/0d2b/natural-mold/frontend/src/lib/types/skill-evaluation.ts`
- `/Users/chester/.codex/worktrees/0d2b/natural-mold/frontend/src/components/skill/skill-evaluation-run-detail.tsx`
- `/Users/chester/.codex/worktrees/0d2b/natural-mold/frontend/src/components/skill/skill-evaluation-set-card.tsx`

The current UI reads legacy top-level fields. JSON v2 must preserve those fields.

---

## 3. Claude Code Skill-Creator Findings For This Phase

The actual installed Claude Code skill-creator was reviewed at:

- `/Users/chester/.claude/plugins/marketplaces/claude-plugins-official/plugins/skill-creator/skills/skill-creator/SKILL.md`
- `/Users/chester/.claude/plugins/marketplaces/claude-plugins-official/plugins/skill-creator/skills/skill-creator/references/schemas.md`
- `/Users/chester/.claude/plugins/marketplaces/claude-plugins-official/plugins/skill-creator/skills/skill-creator/scripts/run_eval.py`
- `/Users/chester/.claude/plugins/marketplaces/claude-plugins-official/plugins/skill-creator/skills/skill-creator/scripts/run_loop.py`
- `/Users/chester/.claude/plugins/marketplaces/claude-plugins-official/plugins/skill-creator/skills/skill-creator/agents/grader.md`
- `/Users/chester/.claude/plugins/marketplaces/claude-plugins-official/plugins/skill-creator/skills/skill-creator/agents/analyzer.md`
- `/Users/chester/.claude/plugins/marketplaces/claude-plugins-official/plugins/skill-creator/skills/skill-creator/agents/comparator.md`

Findings that apply to this foundation phase:

- Claude stores evaluation prompts under `evals/evals.json`.
- Claude eval cases use `prompt`, `expected_output`, `files`, and `expectations`.
- Claude starts with small realistic eval sets before expanding.
- Claude treats eval definitions as portable package-adjacent assets.
- Claude separates eval definition from eval execution.
- Claude grader output includes evidence, expectations, claims, timing, execution metrics, and eval feedback.

Findings deferred to the precision-loop phase:

- Running with-skill and baseline executions.
- Capturing per-case transcripts and output directories.
- Running blind comparison.
- Running iterative description optimization loops.

---

## 4. Product Rules

### 4.1 Auto-Prepare, Manual-Run

When a skill is uploaded or installed:

- Create a prepared evaluation set when possible.
- Do not create an evaluation run.
- Do not enqueue the worker.
- Do not execute skill code.
- Let the user explicitly run the evaluation later.

This avoids surprise cost, credential usage, script execution, and network access.

### 4.2 Embedded Evals First

Order of preparation:

1. Use embedded `evals/evals.json` if present.
2. If embedded evals are absent and system LLM is configured, generate a small smoke set.
3. If generation is unavailable, skip preparation and let upload/install succeed.

### 4.3 Backward-Compatible Result JSON

Current UI and API clients should keep working. Add new fields, do not remove old fields.

### 4.4 Preparation Failure Is Non-Fatal

Skill upload/install is the primary operation. Eval preparation should not block it unless the user explicitly invokes a future strict/manual preparation endpoint.

---

## 5. JSON V2 Contract

### 5.1 Summary

Every completed run should store:

```json
{
  "schema_version": 2,
  "case_count": 3,
  "passed_count": 2,
  "failed_count": 1,
  "pass_rate": 0.67,
  "trigger_accuracy": null,
  "average_duration_ms": null,
  "average_tokens": null,
  "token_delta": null,
  "kpis": {
    "pass_rate": {
      "value": 0.67,
      "passed": 2,
      "total": 3,
      "delta": null,
      "direction": "higher_is_better"
    },
    "trigger_accuracy": {
      "value": null,
      "passed": null,
      "total": 3,
      "delta": null,
      "direction": "higher_is_better"
    },
    "average_duration_ms": {
      "value": null,
      "baseline_value": null,
      "delta": null,
      "direction": "lower_is_better"
    },
    "average_tokens": {
      "value": null,
      "baseline_value": null,
      "delta": null,
      "delta_rate": null,
      "direction": "lower_is_better"
    },
    "error_count": {
      "value": 0,
      "baseline_value": null,
      "delta": null,
      "direction": "lower_is_better"
    }
  },
  "claims": [],
  "eval_feedback": [],
  "execution_metrics": {},
  "timing": {}
}
```

### 5.2 Benchmark

Every completed run should store:

```json
{
  "with_skill_pass_rate": 0.67,
  "without_skill_pass_rate": null,
  "pass_rate_delta": null,
  "comparison": {
    "pass_rate": {
      "with_skill": 0.67,
      "baseline": null,
      "delta": null,
      "direction": "higher_is_better"
    },
    "duration_ms": {
      "with_skill": null,
      "baseline": null,
      "delta": null,
      "direction": "lower_is_better"
    },
    "tokens": {
      "with_skill": null,
      "baseline": null,
      "delta": null,
      "direction": "lower_is_better"
    }
  }
}
```

### 5.3 Case Result

Every case result should store:

```json
{
  "case_index": 0,
  "name": "Case name",
  "input": "User-like task prompt",
  "expected": "Observable expected behavior",
  "status": "passed",
  "score": 0.9,
  "baseline_status": null,
  "baseline_score": null,
  "triggered": null,
  "baseline_triggered": null,
  "duration_ms": null,
  "baseline_duration_ms": null,
  "tokens": null,
  "baseline_tokens": null,
  "error": null,
  "evidence": "Short evidence from the grader.",
  "grader_feedback": "Short grader feedback.",
  "review_status": "unreviewed"
}
```

Allowed `status` values:

- `passed`
- `failed`
- `error`
- `skipped`

Allowed `review_status` values:

- `unreviewed`
- `accepted`
- `rejected`
- `needs_rerun`

Only `unreviewed` is needed in this phase.

---

## 6. Implementation Tasks

### Task 1: Shared JSON V2 Result Normalizer

- [ ] Create `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_evaluation_result_schema.py`.

Public function:

```python
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

JsonDict = dict[str, Any]


def normalize_skill_evaluation_result(
    *,
    evals: Sequence[Mapping[str, Any]],
    raw_case_results: Sequence[Mapping[str, Any]],
    raw_summary: Mapping[str, Any] | None = None,
    raw_benchmark: Mapping[str, Any] | None = None,
) -> tuple[JsonDict, JsonDict, list[JsonDict]]:
    """Return backward-compatible v2 summary, benchmark, and case_results."""
```

Implementation requirements:

- Preserve legacy fields.
- Add `summary.schema_version = 2`.
- Add `summary.kpis`.
- Add `benchmark.comparison`.
- Add null defaults for unavailable duration/token metrics.
- Clamp scores into `0.0 <= score <= 1.0`.
- Normalize unknown status to `error`.
- Preserve `claims`, `eval_feedback`, `execution_metrics`, and `timing` if the grader provides them.

- [ ] Update `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_evaluation_llm_results.py` to delegate final shaping to the shared normalizer.

- [ ] Update `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_evaluation_llm.py` so the returned payload is always JSON v2.

- [ ] Update `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/agent_runtime/skill_builder/deterministic_eval_runner.py` to emit JSON v2.

- [ ] Update `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_builder_eval_service.py` to emit JSON v2.

Tests:

- [ ] Add `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/tests/test_skill_evaluation_result_schema.py`.

Required tests:

```python
def test_summary_v2_keeps_legacy_fields_and_adds_kpis() -> None:
    ...


def test_benchmark_v2_keeps_legacy_fields_and_adds_comparison() -> None:
    ...


def test_case_results_v2_adds_review_and_metric_defaults() -> None:
    ...


def test_result_schema_accepts_missing_duration_and_token_metrics() -> None:
    ...


def test_result_schema_clamps_scores_and_normalizes_statuses() -> None:
    ...
```

Verification:

```bash
cd /Users/chester/.codex/worktrees/0d2b/natural-mold/backend
uv run pytest -q tests/test_skill_evaluation_result_schema.py
uv run pytest -q tests/test_skill_evaluation_llm.py tests/test_skill_builder_eval_service.py
uv run ruff check app/services/skill_evaluation_result_schema.py app/services/skill_evaluation_llm_results.py app/services/skill_evaluation_llm.py app/services/skill_builder_eval_service.py
```

### Task 2: Portable Eval File Adapter

- [ ] Create `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_evaluation_file_adapter.py`.

Public function:

```python
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def normalize_evaluation_file_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize Moldy or Claude-style evals/evals.json into Moldy evaluation-set payload."""
```

Moldy input mapping:

- `name` -> `name`
- `description` -> `description`
- `evals[].input` -> `evals[].input`
- `evals[].expected` -> `evals[].expected`
- `evals[].tags` -> `evals[].tags`
- `evals[].metadata` -> `evals[].metadata`

Claude input mapping:

- `skill_name` -> default `name`
- `evals[].id` -> `metadata.external_id`
- `evals[].prompt` -> `input`
- `evals[].expected_output` -> `expected`
- `evals[].files` -> `metadata.files`
- `evals[].expectations` -> `metadata.expectations`
- `evals[].tags` -> `tags`
- Add `metadata.source_schema = "claude_skill_creator"`

Tests:

- [ ] Add `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/tests/test_skill_evaluation_file_adapter.py`.

Required tests:

```python
def test_normalizes_moldy_eval_file() -> None:
    ...


def test_normalizes_claude_skill_creator_eval_file() -> None:
    ...


def test_rejects_empty_eval_file() -> None:
    ...


def test_rejects_eval_file_without_prompt_or_input() -> None:
    ...


def test_preserves_expectations_and_files_metadata() -> None:
    ...
```

Verification:

```bash
cd /Users/chester/.codex/worktrees/0d2b/natural-mold/backend
uv run pytest -q tests/test_skill_evaluation_file_adapter.py
uv run ruff check app/services/skill_evaluation_file_adapter.py
```

### Task 3: Evaluation Set Preparation Service

- [ ] Create `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_evaluation_set_preparation.py`.

Public types:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class SkillEvaluationPreparationStatus(StrEnum):
    CREATED = "created"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    SKIPPED_NO_EVALS = "skipped_no_evals"
    SKIPPED_NO_SYSTEM_MODEL = "skipped_no_system_model"
    FAILED = "failed"


@dataclass(frozen=True)
class SkillEvaluationPreparationResult:
    status: SkillEvaluationPreparationStatus
    evaluation_set_id: UUID | None
    source_kind: str
    case_count: int
    payload_hash: str | None
    reason: str | None = None
```

Public function:

```python
async def prepare_skill_evaluation_set(
    *,
    db: AsyncSession,
    skill: Skill,
    user: User,
    source_kind: str,
    allow_llm_generation: bool,
    marketplace_item_id: UUID | None = None,
    marketplace_version_id: UUID | None = None,
) -> SkillEvaluationPreparationResult:
    ...
```

Requirements:

- Read `evals/evals.json` from package skill storage when present.
- Normalize through `skill_evaluation_file_adapter`.
- If no embedded evals exist and `allow_llm_generation` is true, call the LLM fallback generator from Task 4.
- Create one `SkillEvaluationSet`.
- Do not create `SkillEvaluationRun`.
- Do not commit inside the service.
- Do not execute skill code.
- Avoid duplicates by storing and checking a stable preparation hash in eval metadata.

Tests:

- [ ] Add `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/tests/test_skill_evaluation_set_preparation.py`.

Required tests:

```python
async def test_prepare_imports_embedded_claude_evals_for_package_skill(...) -> None:
    ...


async def test_prepare_imports_embedded_moldy_evals_for_package_skill(...) -> None:
    ...


async def test_prepare_skips_duplicate_payload(...) -> None:
    ...


async def test_prepare_skips_missing_evals_when_llm_generation_disabled(...) -> None:
    ...


async def test_prepare_does_not_create_run(...) -> None:
    ...
```

Verification:

```bash
cd /Users/chester/.codex/worktrees/0d2b/natural-mold/backend
uv run pytest -q tests/test_skill_evaluation_set_preparation.py
uv run ruff check app/services/skill_evaluation_set_preparation.py
```

### Task 4: LLM Smoke Eval Generator

- [ ] Create `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_evaluation_case_generator_llm.py`.

Requirements:

- Generate 3 eval cases by default.
- Cap generated cases at 5.
- Use configured system LLM.
- Reuse skill preview logic from `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/services/skill_evaluation_llm_payload.py` where practical.
- Output normalized Moldy eval payload.
- Avoid secret-like content.
- Avoid prompts that require private user data.
- Do not execute skill code.

Expected output shape:

```json
{
  "name": "Generated smoke evaluation",
  "description": "Smoke tests generated from the installed skill package.",
  "evals": [
    {
      "input": "User-like task prompt",
      "expected": "Observable expected behavior",
      "tags": ["smoke"],
      "metadata": {
        "expectations": ["Expectation 1", "Expectation 2"],
        "generated": true
      }
    }
  ]
}
```

Tests:

- [ ] Add `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/tests/test_skill_evaluation_case_generator_llm.py`.

Required tests:

```python
async def test_llm_generator_returns_normalized_eval_payload(...) -> None:
    ...


async def test_llm_generator_rejects_invalid_model_json(...) -> None:
    ...


async def test_llm_generator_caps_case_count(...) -> None:
    ...


async def test_llm_generator_does_not_run_when_system_model_missing(...) -> None:
    ...
```

Verification:

```bash
cd /Users/chester/.codex/worktrees/0d2b/natural-mold/backend
uv run pytest -q tests/test_skill_evaluation_case_generator_llm.py tests/test_skill_evaluation_set_preparation.py
uv run ruff check app/services/skill_evaluation_case_generator_llm.py
```

### Task 5: Wire Preparation Into Upload

- [ ] Modify `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/routers/skill_uploads.py`.

Call `prepare_skill_evaluation_set` after:

- package validation,
- secret scan,
- skill creation,
- initial revision creation.

Call it before the final transaction commit.

Use:

```python
await prepare_skill_evaluation_set(
    db=db,
    skill=skill,
    user=current_user,
    source_kind="package_import",
    allow_llm_generation=True,
)
```

Error behavior:

- Invalid eval file: upload succeeds, preparation records safe failure metadata.
- Missing system LLM: upload succeeds, preparation skipped.
- Database failure: transaction fails normally.

Tests:

- [ ] Extend upload tests.

Required tests:

```python
async def test_upload_package_with_claude_evals_creates_evaluation_set(...) -> None:
    ...


async def test_upload_package_with_moldy_evals_creates_evaluation_set(...) -> None:
    ...


async def test_upload_package_does_not_create_evaluation_run(...) -> None:
    ...


async def test_upload_package_succeeds_when_eval_preparation_skips(...) -> None:
    ...
```

Verification:

```bash
cd /Users/chester/.codex/worktrees/0d2b/natural-mold/backend
uv run pytest -q tests/test_skill_uploads.py tests/test_skill_evaluation_set_preparation.py
uv run ruff check app/routers/skill_uploads.py
```

### Task 6: Wire Preparation Into Marketplace Install

- [ ] Modify `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/routers/marketplace.py`.

Preferred approach:

- Keep `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/marketplace/install_service.py` focused on installing resources.
- Let the router call preparation after install returns and before commit.
- Query the installed `Skill` if the returned installation object does not already expose it.

Use:

```python
await prepare_skill_evaluation_set(
    db=db,
    skill=installed_skill,
    user=current_user,
    source_kind="marketplace_import",
    allow_llm_generation=True,
    marketplace_item_id=item_id,
    marketplace_version_id=version_id,
)
```

Tests:

- [ ] Extend marketplace install tests.

Required tests:

```python
async def test_marketplace_install_with_embedded_evals_creates_evaluation_set(...) -> None:
    ...


async def test_marketplace_install_does_not_create_evaluation_run(...) -> None:
    ...


async def test_marketplace_install_succeeds_when_eval_generation_unavailable(...) -> None:
    ...


async def test_marketplace_reinstall_does_not_duplicate_same_prepared_set(...) -> None:
    ...
```

Verification:

```bash
cd /Users/chester/.codex/worktrees/0d2b/natural-mold/backend
uv run pytest -q tests/test_marketplace_install.py tests/test_skill_evaluation_set_preparation.py
uv run ruff check app/routers/marketplace.py app/marketplace/install_service.py
```

### Task 7: Optional Manual Re-Prepare Endpoint

This is useful for already installed skills and should be included if the backend surface is being touched anyway.

- [ ] Add to `/Users/chester/.codex/worktrees/0d2b/natural-mold/backend/app/routers/skill_evaluations.py`.

Endpoint:

```text
POST /api/skills/{skill_id}/evaluations/prepare
```

Request:

```json
{
  "allow_llm_generation": true,
  "force": false
}
```

Response:

```json
{
  "status": "created",
  "evaluation_set_id": "uuid",
  "source_kind": "manual_prepare",
  "case_count": 3,
  "reason": null
}
```

Rules:

- Owner only.
- CSRF required.
- No run is created.
- `force = false` skips duplicates.

Tests:

- [ ] Add route tests.

Required tests:

```python
async def test_manual_prepare_creates_eval_set_for_existing_skill(...) -> None:
    ...


async def test_manual_prepare_does_not_enqueue_run(...) -> None:
    ...


async def test_manual_prepare_respects_ownership(...) -> None:
    ...
```

Verification:

```bash
cd /Users/chester/.codex/worktrees/0d2b/natural-mold/backend
uv run pytest -q tests/test_skill_evaluations.py
uv run ruff check app/routers/skill_evaluations.py
```

---

## 7. Audit Requirements

Add safe audit metadata for preparation events.

Recommended events:

- `skill_evaluation_set.imported`
- `skill_evaluation_set.generated`
- `skill_evaluation_set.prepare_skipped`
- `skill_evaluation_set.prepare_failed`

Allowed metadata:

- `skill_id`
- `evaluation_set_id`
- `source_kind`
- `case_count`
- `payload_hash`
- `reason`
- `error_kind`
- `model_provider`
- `model_id`

Do not log:

- Full eval prompts.
- Full expected outputs.
- Full skill file content.
- Raw model responses.
- Credential values.
- Subprocess args or output.

---

## 8. Manual QA

Use the app surface after tests pass.

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

- Upload a package containing Claude-style `evals/evals.json`.
- Confirm a prepared evaluation set appears.
- Confirm no evaluation run starts automatically.
- Upload a package without evals.
- Confirm LLM smoke generation creates a prepared set when system LLM exists.
- Install a marketplace skill.
- Confirm a prepared set appears and no run starts.
- Manually run an evaluation.
- Confirm completed run has `summary.schema_version = 2`.
- Confirm current UI still renders the run.

---

## 9. Verification Commands

Focused:

```bash
cd /Users/chester/.codex/worktrees/0d2b/natural-mold/backend
uv run pytest -q tests/test_skill_evaluation_result_schema.py
uv run pytest -q tests/test_skill_evaluation_file_adapter.py
uv run pytest -q tests/test_skill_evaluation_set_preparation.py
uv run pytest -q tests/test_skill_evaluation_case_generator_llm.py
uv run pytest -q tests/test_skill_uploads.py
uv run pytest -q tests/test_marketplace_install.py
uv run pytest -q tests/test_skill_evaluations.py
```

Full backend:

```bash
cd /Users/chester/.codex/worktrees/0d2b/natural-mold/backend
uv run pytest
uv run ruff check .
```

Frontend only if UI changes are made:

```bash
cd /Users/chester/.codex/worktrees/0d2b/natural-mold/frontend
pnpm lint:i18n
pnpm lint:design-system
pnpm lint
```

---

## 10. Definition of Done

- [ ] Quick LLM evaluation emits JSON v2.
- [ ] Deterministic/builder evaluation paths emit JSON v2.
- [ ] Current UI still works through legacy fields.
- [ ] Claude-style `evals/evals.json` imports correctly.
- [ ] Moldy-style `evals/evals.json` imports correctly.
- [ ] Upload creates a prepared evaluation set when possible.
- [ ] Marketplace install creates a prepared evaluation set when possible.
- [ ] Upload/install never creates a run automatically.
- [ ] Preparation failure does not block upload/install by default.
- [ ] Safe audit metadata is emitted.
- [ ] Backend focused tests pass.
- [ ] Backend `ruff check` passes.
- [ ] Manual QA confirms prepared sets and manual run behavior.

