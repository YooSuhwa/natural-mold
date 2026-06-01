# Tavily Deep Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a hosted Tavily Search tool and a Deep Research system marketplace skill that automatically receives Tavily at runtime, so users only attach the Deep Research skill.

**Architecture:** Keep Tavily as a registry tool backed by server `.env`, not by per-user credentials. Add a small runtime-only skill dependency resolver in `agent_runtime` that injects `tavily_search` when an attached skill declares it in `execution_profile.tool_dependencies`. Do not persist hidden dependency tools into `agent_tools`; the agent configuration remains user-owned and clean.

**Tech Stack:** FastAPI, SQLAlchemy async, LangChain `StructuredTool`, Deep Agents `create_deep_agent`, existing Moldy skill marketplace, Pydantic settings, pytest, ruff, Next.js/React frontend metadata display.

---

## Current Code Map

- `backend/app/tools/domain.py` defines `ToolDefinition`, `ToolRunContext`, risk metadata, and the async runner contract used by registry tools.
- `backend/app/tools/definitions/naver_search.py` is the closest existing pattern for a hosted API search tool definition.
- `backend/app/tools/definitions/__init__.py` registers in-memory tool definitions.
- `backend/app/agent_runtime/tool_factory.py` converts registry `ToolDefinition` entries into LangChain `StructuredTool` instances at runtime.
- `backend/app/services/chat_service.py` currently builds `tools_config` only from explicit `agent.tool_links` and `agent.mcp_tool_links`.
- `backend/app/agent_runtime/executor.py` creates Deep Agents, injects `execute_in_skill`, and mounts marketplace skill packages into the runtime filesystem.
- `backend/app/skills/runtime.py` already includes `execution_profile` in runtime skill descriptors when present.
- `backend/app/seed/default_marketplace_skills.py` already seeds one system marketplace skill package from `backend/app/seed/system_skill_packages/image-generation`.
- `/Users/chester/dev/ref/deep-research-kit` is a Claude Code plugin/skill workflow, not a Python package. Its value is the research process and prompts; the Claude-specific host tools (`Task`, `WebSearch`, `WebFetch`, `AskUserQuestion`) must be translated to Moldy tool instructions.

## Product Decision

The recommended shape is:

- Add `tavily_search` as a system-hosted registry tool that reads `TAVILY_API_KEY` from backend settings or process env.
- Add `deep-research` as a system marketplace skill package with `execution_profile.tool_dependencies = ["tavily_search"]`.
- Inject `tavily_search` at runtime when an attached skill declares that dependency.
- Do not require users to add `tavily_search` manually to the agent.
- Do not copy Claude Code's agent/subagent implementation directly. Convert the workflow into a single Moldy skill first. A later version can add script helpers under `execute_in_skill` if measurable quality gaps remain.

This avoids the user-facing failure mode where a user installs only Deep Research and the agent cannot search.

## Task 1: Add Hosted Tavily Tool Definition

**Files:**

- `backend/app/config.py`
- `backend/.env.example`
- `backend/app/tools/definitions/tavily_search.py`
- `backend/app/tools/definitions/__init__.py`
- `backend/tests/test_tools.py`

**Steps:**

- [ ] Add a backend setting in `backend/app/config.py`:

```python
tavily_api_key: str = ""
```

- [ ] Add the env template entry in `backend/.env.example` near the existing search provider keys:

```dotenv
TAVILY_API_KEY=
```

- [ ] Create `backend/app/tools/definitions/tavily_search.py` with a registry `ToolDefinition`.

```python
from __future__ import annotations

import os
from typing import Any

from app.config import settings
from app.tools.domain import FieldDef, ToolDefinition, ToolRunContext
from app.tools.risk import ToolRiskLevel


def _string_param(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _bool_param(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _int_param(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


async def _run_tavily_search(
    params: dict[str, Any],
    ctx: ToolRunContext,
) -> dict[str, Any]:
    api_key = settings.tavily_api_key.strip() or os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        raise ValueError("TAVILY_API_KEY is not configured on the backend server")

    query = _string_param(params.get("query"))
    if not query:
        raise ValueError("query is required")

    time_range = _string_param(params.get("time_range"))
    payload: dict[str, Any] = {
        "query": query,
        "search_depth": _string_param(params.get("search_depth"), "basic") or "basic",
        "topic": _string_param(params.get("topic"), "general") or "general",
        "max_results": _int_param(params.get("max_results"), default=5, minimum=1, maximum=10),
        "include_answer": _bool_param(params.get("include_answer"), True),
        "include_raw_content": _bool_param(params.get("include_raw_content"), False),
    }
    if time_range:
        payload["time_range"] = time_range

    response = await ctx.http_client.post(
        "https://api.tavily.com/search",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
    )
    response.raise_for_status()
    data = response.json()

    results = []
    for item in data.get("results") or []:
        raw_content = item.get("raw_content")
        if isinstance(raw_content, str) and len(raw_content) > 6000:
            raw_content = raw_content[:6000] + "\n...[truncated]"
        results.append(
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "content": item.get("content"),
                "raw_content": raw_content,
                "score": item.get("score"),
                "published_date": item.get("published_date"),
            }
        )

    return {
        "query": data.get("query", query),
        "answer": data.get("answer"),
        "results": results,
        "response_time": data.get("response_time"),
    }


tavily_search_definition = ToolDefinition(
    key="tavily_search",
    display_name="Tavily Search",
    description="Search the live web with Tavily using a backend-hosted API key.",
    icon_id="search",
    category="Search",
    risk_level=ToolRiskLevel.READ_ONLY,
    trigger_safe=True,
    credential_definition_keys=[],
    parameters=[
        FieldDef(
            name="query",
            label="Query",
            type="string",
            required=True,
            runtime_only=True,
            description="Search query.",
        ),
        FieldDef(
            name="max_results",
            label="Max Results",
            type="number",
            required=False,
            default=5,
            min=1,
            max=10,
        ),
        FieldDef(
            name="search_depth",
            label="Search Depth",
            type="select",
            required=False,
            default="basic",
            options=[
                {"label": "Basic", "value": "basic"},
                {"label": "Advanced", "value": "advanced"},
            ],
        ),
        FieldDef(
            name="topic",
            label="Topic",
            type="select",
            required=False,
            default="general",
            options=[
                {"label": "General", "value": "general"},
                {"label": "News", "value": "news"},
                {"label": "Finance", "value": "finance"},
            ],
        ),
        FieldDef(
            name="time_range",
            label="Time Range",
            type="select",
            required=False,
            default="",
            options=[
                {"label": "Any time", "value": ""},
                {"label": "Past day", "value": "day"},
                {"label": "Past week", "value": "week"},
                {"label": "Past month", "value": "month"},
                {"label": "Past year", "value": "year"},
            ],
        ),
        FieldDef(
            name="include_answer",
            label="Include Answer",
            type="boolean",
            required=False,
            default=True,
        ),
        FieldDef(
            name="include_raw_content",
            label="Include Raw Content",
            type="boolean",
            required=False,
            default=False,
        ),
    ],
    runner=_run_tavily_search,
)
```

- [ ] Register it in `backend/app/tools/definitions/__init__.py`.

```python
from .tavily_search import tavily_search_definition

register_tool(tavily_search_definition)
```

- [ ] Add tests in `backend/tests/test_tools.py`.

```python
async def test_run_tavily_search_uses_hosted_env_key(monkeypatch):
    monkeypatch.setattr("app.tools.definitions.tavily_search.settings.tavily_api_key", "tvly-test")
    ...
```

The test must assert:

- The tool catalog includes `tavily_search`.
- `credential_definition_keys` is empty.
- The outbound request uses `Authorization: Bearer tvly-test`.
- `max_results` is clamped to 10.
- Missing `TAVILY_API_KEY` raises `ValueError("TAVILY_API_KEY is not configured on the backend server")`.

**Verification:**

```bash
cd backend
uv run pytest tests/test_tools.py::test_tool_types_catalog tests/test_tools.py::test_run_tavily_search_uses_hosted_env_key tests/test_tools.py::test_run_tavily_search_requires_hosted_key -q
uv run ruff check app/tools/definitions/tavily_search.py tests/test_tools.py
```

**Expected result:** The test run passes and the Tavily tool appears as a read-only registry tool with no credential binding requirement.

**Commit:**

```bash
git add backend/app/config.py backend/.env.example backend/app/tools/definitions/tavily_search.py backend/app/tools/definitions/__init__.py backend/tests/test_tools.py
git commit -m "feat(tools): add hosted tavily search"
```

## Task 2: Add Runtime-Only Skill Tool Dependencies

**Files:**

- `backend/app/agent_runtime/skill_tool_dependencies.py`
- `backend/app/agent_runtime/executor.py`
- `backend/tests/test_executor.py`

**Steps:**

- [ ] Create `backend/app/agent_runtime/skill_tool_dependencies.py`.

```python
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any


SUPPORTED_SKILL_TOOL_DEPENDENCIES = {"tavily_search"}


def _dependency_names(agent_skills: Sequence[Mapping[str, Any]]) -> list[str]:
    names: list[str] = []
    for skill in agent_skills:
        profile = skill.get("execution_profile")
        if not isinstance(profile, Mapping):
            continue
        raw_dependencies = profile.get("tool_dependencies")
        if not isinstance(raw_dependencies, Iterable) or isinstance(raw_dependencies, str):
            continue
        for item in raw_dependencies:
            if isinstance(item, str):
                name = item.strip()
                if name and name not in names:
                    names.append(name)
    return names


def build_skill_dependency_tool_configs(
    *,
    agent_skills: Sequence[Mapping[str, Any]],
    existing_tool_configs: Sequence[Mapping[str, Any]],
    user_id: str | None,
    agent_id: str | None,
) -> list[dict[str, Any]]:
    dependencies = _dependency_names(agent_skills)
    unsupported = sorted(set(dependencies) - SUPPORTED_SKILL_TOOL_DEPENDENCIES)
    if unsupported:
        joined = ", ".join(unsupported)
        raise ValueError(f"Unsupported skill tool dependency: {joined}")

    existing_names = {
        str(config.get("name", "")).strip()
        for config in existing_tool_configs
        if str(config.get("name", "")).strip()
    }

    injected: list[dict[str, Any]] = []
    for name in dependencies:
        if name in existing_names:
            continue
        injected.append(
            {
                "tool_id": f"skill-dependency:{name}",
                "definition_key": name,
                "name": name,
                "description": "Hosted Tavily web search used by attached skills.",
                "parameters": {},
                "credential_id": None,
                "credentials": None,
                "user_id": user_id,
                "agent_id": agent_id,
                "is_skill_dependency": True,
            }
        )
    return injected
```

- [ ] Modify `backend/app/agent_runtime/executor.py` to inject dependencies after `skill_runtime_context` is available and before registry tools are converted.

```python
from app.agent_runtime.skill_tool_dependencies import build_skill_dependency_tool_configs

...

runtime_tool_configs = [
    *cfg.tools_config,
    *build_skill_dependency_tool_configs(
        agent_skills=cfg.agent_skills,
        existing_tool_configs=cfg.tools_config,
        user_id=cfg.user_id,
        agent_id=cfg.agent_id,
    ),
]

for tool_config in runtime_tool_configs:
    ...
```

- [ ] Keep the dependency runtime-only. Do not create `AgentToolLink` rows and do not mutate `cfg.tools_config`.
- [ ] Deduplicate by runtime name. If a user explicitly adds a tool named `tavily_search`, the resolver must not inject a second tool.
- [ ] Preserve the stable alias. If a user adds the same definition under another name, still inject `tavily_search` so the Deep Research skill has a predictable tool name.
- [ ] Add tests in `backend/tests/test_executor.py`:

```python
async def test_execute_stream_injects_skill_tool_dependency(...):
    ...

async def test_execute_stream_dedupes_exact_skill_dependency_name(...):
    ...

async def test_execute_stream_keeps_stable_dependency_alias_when_explicit_tool_has_different_name(...):
    ...
```

The tests should patch the agent creation path or `create_tool_for_runtime` so they can assert the exact `definition_key` and `name` values without calling Tavily.

**Verification:**

```bash
cd backend
uv run pytest tests/test_executor.py::test_execute_stream_injects_skill_tool_dependency tests/test_executor.py::test_execute_stream_dedupes_exact_skill_dependency_name tests/test_executor.py::test_execute_stream_keeps_stable_dependency_alias_when_explicit_tool_has_different_name -q
uv run ruff check app/agent_runtime/skill_tool_dependencies.py app/agent_runtime/executor.py tests/test_executor.py
```

**Expected result:** An agent with only the Deep Research skill receives a `tavily_search` runtime tool, while the database and agent settings remain unchanged.

**Commit:**

```bash
git add backend/app/agent_runtime/skill_tool_dependencies.py backend/app/agent_runtime/executor.py backend/tests/test_executor.py
git commit -m "feat(skills): inject runtime tool dependencies"
```

## Task 3: Seed Deep Research as a System Marketplace Skill

**Files:**

- `backend/app/seed/system_skill_packages/deep-research/SKILL.md`
- `backend/app/seed/default_marketplace_skills.py`
- `backend/tests/test_default_image_skill_seed.py`

**Steps:**

- [ ] Create `backend/app/seed/system_skill_packages/deep-research/SKILL.md`.

```markdown
---
name: deep-research
description: Conduct multi-step, citation-backed web research using Tavily Search.
version: 0.1.0
---

# Deep Research

Use this skill when the user asks for deep research, market research, competitor research, technical landscape research, fact-gathering across multiple web sources, or a citation-backed report.

## Required Tool

You have a runtime tool named `tavily_search`.

If `tavily_search` is unavailable or returns a backend configuration error, stop and tell the user that the backend operator must configure `TAVILY_API_KEY`.

## Workflow

1. Restate the research question and identify scope constraints.
2. Create 6-12 search queries that cover the main question, opposing evidence, recent updates, and primary-source candidates.
3. Call `tavily_search` for each query. Use `search_depth="advanced"` when accuracy matters more than latency.
4. Track source title, URL, publication date when available, relevance, and the claim each source supports.
5. Cross-check important claims against at least two independent sources when possible.
6. Treat company pages, official docs, standards bodies, filings, and primary research as stronger evidence than summaries.
7. Do not invent citations, URLs, publication dates, statistics, or quotes.

## Output Format

Return a report with these sections:

- Executive Summary
- Research Question
- Methodology
- Findings
- Source Quality
- Open Questions
- References

Every factual claim that depends on web evidence should include a URL in the same paragraph or bullet.
```

- [ ] Add a Deep Research execution profile in `backend/app/seed/default_marketplace_skills.py`.

```python
DEEP_RESEARCH_EXECUTION_PROFILE = {
    "support_level": "ready_python",
    "runners": ["python"],
    "requires_network": True,
    "tool_dependencies": ["tavily_search"],
    "timeout_seconds": 420,
    "notes": "Uses hosted Tavily Search through a runtime tool dependency; no user credential binding is required.",
}
```

- [ ] Add a seed function for the Deep Research skill using the existing image-generation marketplace seed pattern.
- [ ] Set stable marketplace identifiers:

```python
source_external_id="deep-research"
title="Deep Research"
slug="deep-research"
```

- [ ] Keep `is_system=True` and `user_id=None`.
- [ ] Store the copied package under the same skill package storage mechanism used by the image-generation skill.
- [ ] Make the seed idempotent. Re-running startup seed must not create duplicate marketplace items or duplicate current versions.
- [ ] Extend `backend/tests/test_default_image_skill_seed.py` or create `backend/tests/test_default_marketplace_skills.py` to assert:

- The Deep Research marketplace item is created.
- Its latest version has `execution_profile.tool_dependencies == ["tavily_search"]`.
- The seeded skill has `execution_profile.tool_dependencies == ["tavily_search"]`.
- Running the seed twice leaves one Deep Research item and one current version.

**Verification:**

```bash
cd backend
uv run pytest tests/test_default_image_skill_seed.py -q
uv run ruff check app/seed/default_marketplace_skills.py tests/test_default_image_skill_seed.py
```

**Expected result:** Fresh backend startup seeds both image generation and Deep Research as system marketplace skills. Users can install Deep Research from the marketplace without manually installing Tavily.

**Commit:**

```bash
git add backend/app/seed/system_skill_packages/deep-research/SKILL.md backend/app/seed/default_marketplace_skills.py backend/tests/test_default_image_skill_seed.py
git commit -m "feat(marketplace): seed deep research skill"
```

## Task 4: Surface Dependency Metadata in the UI

**Files:**

- `backend/app/schemas/skill.py`
- `backend/app/routers/skills.py`
- `frontend/src/lib/types/marketplace.ts`
- `frontend/src/lib/types/skill.ts`
- `frontend/src/components/marketplace/install-wizard.tsx`
- `frontend/src/app/marketplace/[slug]/page.tsx`

**Steps:**

- [ ] Ensure backend skill responses include `execution_profile` for installed skills and skill briefs. If the schema already serializes it through `from_attributes`, add only the missing Pydantic field:

```python
execution_profile: dict[str, Any] | None = None
```

- [ ] Add `tool_dependencies` to the frontend execution profile type:

```ts
export interface ExecutionProfile {
  support_level?: string
  runners?: string[]
  requires_network?: boolean
  timeout_seconds?: number
  tool_dependencies?: string[]
  [key: string]: unknown
}
```

- [ ] Add an install wizard review line when dependency tools exist:

```tsx
const toolDependencies = item.execution_profile?.tool_dependencies ?? []
```

Render:

```tsx
{toolDependencies.length > 0 ? (
  <div className="rounded-md border border-border px-3 py-2 text-sm text-muted-foreground">
    Includes hosted tools: {toolDependencies.join(", ")}
  </div>
) : null}
```

- [ ] Add the same metadata to the marketplace detail page so operators and users understand why Tavily does not need separate installation.
- [ ] Keep agent tool selection unchanged. Do not automatically select or persist `tavily_search` in the Tools and Skills dialog.

**Verification:**

```bash
cd frontend
pnpm lint
pnpm build
```

**Expected result:** Marketplace users can see that Deep Research includes a hosted `tavily_search` dependency, while agent settings still show only the skills and tools the user explicitly selected.

**Commit:**

```bash
git add backend/app/schemas/skill.py backend/app/routers/skills.py frontend/src/lib/types/marketplace.ts frontend/src/lib/types/skill.ts frontend/src/components/marketplace/install-wizard.tsx frontend/src/app/marketplace/[slug]/page.tsx
git commit -m "feat(marketplace): show skill tool dependencies"
```

## Task 5: Preserve Risk and Trigger Behavior

**Files:**

- `backend/app/tools/definitions/tavily_search.py`
- `backend/app/tools/risk.py`
- `backend/app/agent_runtime/skill_tool_dependencies.py`
- `backend/tests/test_tool_risk_policy.py`

**Steps:**

- [ ] Confirm `tavily_search_definition.risk_level == ToolRiskLevel.READ_ONLY`.
- [ ] Confirm `tavily_search_definition.trigger_safe is True`.
- [ ] Add a risk policy test:

```python
def test_tavily_search_is_read_only_and_trigger_safe():
    config = {"definition_key": "tavily_search"}
    risk = risk_from_tool_config(config)
    assert risk.level == ToolRiskLevel.READ_ONLY
    assert risk.trigger_safe is True
```

- [ ] Add a dependency resolver test for unsupported dependencies:

```python
def test_skill_dependency_resolver_rejects_unsupported_dependency():
    with pytest.raises(ValueError, match="Unsupported skill tool dependency: unknown_tool"):
        build_skill_dependency_tool_configs(
            agent_skills=[{"execution_profile": {"tool_dependencies": ["unknown_tool"]}}],
            existing_tool_configs=[],
            user_id="user-1",
            agent_id="agent-1",
        )
```

- [ ] Do not special-case hidden dependencies in trigger blocking. The registry definition risk metadata must remain the source of truth for runtime dependency tools.
- [ ] Keep the existing behavior where any agent with skills is blocked from scheduled trigger execution because `execute_in_skill` is code execution. Tavily itself is trigger-safe, but the Deep Research skill path still includes skill execution capability.

**Verification:**

```bash
cd backend
uv run pytest tests/test_tool_risk_policy.py -q
uv run ruff check app/tools/definitions/tavily_search.py app/agent_runtime/skill_tool_dependencies.py tests/test_tool_risk_policy.py
```

**Expected result:** Adding a hidden Tavily dependency does not weaken the existing trigger safety model, and future unsupported dependencies fail explicitly.

**Commit:**

```bash
git add backend/app/tools/definitions/tavily_search.py backend/app/tools/risk.py backend/app/agent_runtime/skill_tool_dependencies.py backend/tests/test_tool_risk_policy.py
git commit -m "test(skills): cover dependency risk policy"
```

## Task 6: End-to-End Verification

**Files:**

- No new files required unless a smoke-test script is added.

**Steps:**

- [ ] Run targeted backend tests:

```bash
cd backend
uv run pytest tests/test_tools.py tests/test_executor.py tests/test_default_image_skill_seed.py tests/test_tool_risk_policy.py -q
```

- [ ] Run backend lint:

```bash
cd backend
uv run ruff check .
```

- [ ] Run frontend checks:

```bash
cd frontend
pnpm lint
pnpm build
```

- [ ] Run a manual local smoke test with a real Tavily key:

```bash
cd backend
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --reload-dir app --port 8001
```

In another terminal:

```bash
cd frontend
pnpm dev
```

Manual flow:

- Login as a super user.
- Confirm the marketplace contains `Deep Research`.
- Install `Deep Research`.
- Create or edit an agent with only the `Deep Research` skill attached.
- Do not attach `Tavily Search` in the agent tool picker.
- Ask: `Research the current state of LangGraph deep agents and cite sources.`
- Confirm the response uses web citations and no missing-tool error appears.
- Remove or blank `TAVILY_API_KEY`, restart the backend, repeat the prompt, and confirm the error tells the operator to configure `TAVILY_API_KEY`.

**Expected result:** Deep Research works when the user attaches only the Deep Research skill. Tavily credentials stay server-side. Existing tools, credentials, skills, MCP tools, and marketplace install flows continue to work.

**Commit:**

```bash
git status --short
git commit --allow-empty -m "test: verify tavily deep research integration"
```

## Risks and Mitigations

- **Risk:** Deep Research quality may be lower than the Claude Code kit because Moldy currently has no Claude `Task` subagent fan-out.
  **Mitigation:** The MVP converts the research method into explicit skill instructions and uses Tavily advanced search. Add dedicated subagent orchestration later only after comparing outputs.

- **Risk:** Hidden dependency injection could surprise users.
  **Mitigation:** Show `tool_dependencies` in marketplace install/detail UI and do not persist hidden dependencies into `agent_tools`.

- **Risk:** A future skill declares a powerful tool dependency and bypasses user approval.
  **Mitigation:** The resolver uses an allowlist and registry risk metadata remains authoritative. Unknown dependencies fail before agent execution.

- **Risk:** Backend `.env` key means all users share the same Tavily quota.
  **Mitigation:** Clamp `max_results`, default to `basic`, and keep `advanced` as an explicit skill choice. Add usage logging later if quota pressure appears.

- **Risk:** Tavily API request shape changes.
  **Mitigation:** Keep the Tavily integration isolated in `backend/app/tools/definitions/tavily_search.py` and cover request payload shape in tests.

## Later Enhancements

- Add a richer `dependency_requirements` schema for marketplace versions and installed skills if more skills need hosted tools.
- Add per-skill usage metering for hosted tools.
- Add a dedicated Deep Research evaluator that compares answer quality against the original Claude Code workflow.
- Add optional parallel search planning inside the Deep Research skill once Moldy exposes a stable subagent pattern for skills.
- Add Tavily extract/crawl/map tools only if Deep Research reports need page-level extraction beyond search snippets.
