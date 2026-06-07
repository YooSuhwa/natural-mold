# Executor Runtime Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `backend/app/agent_runtime/executor.py` into focused runtime modules without changing agent execution, streaming, skill, MCP, memory, fallback, HiTL, or Langfuse behavior.

**Architecture:** Keep `app.agent_runtime.executor` as a compatibility facade while moving real implementation into focused modules: `runtime_config`, `skill_executor`, `mcp_tool_loader`, `runtime_component_builder`, and `agent_stream_runner`. Update production imports to the focused modules where appropriate, but keep legacy re-exports so existing public imports continue to work during this pass.

**Tech Stack:** Python 3.12, FastAPI backend, LangChain/LangGraph/deepagents runtime, pytest, ruff, Playwright E2E.

---

## Current Source Map

`backend/app/agent_runtime/executor.py` is currently 1,547 lines.

Move these existing symbols by responsibility:

- `runtime_config.py`
  - `_DATA_DIR`
  - `AgentConfig`
  - `RuntimeComponents`

- `skill_executor.py`
  - `_SHELL_ASSIGNMENT_RE`
  - `_SHELL_DEFAULT_RE`
  - `_SHELL_VAR_RE`
  - `_DEFAULT_SKILL_TIMEOUT_SECONDS`
  - `_MAX_SKILL_TIMEOUT_SECONDS`
  - `_expand_shell_vars`
  - `_prepare_skill_subprocess_args`
  - `_skill_timeout_seconds`
  - `_create_skill_execute_tool`

- `mcp_tool_loader.py`
  - `_auth_config_to_headers`
  - `_url_to_server_key`
  - `_AuthInjectorInterceptor`
  - `_hide_auth_params_from_schema`
  - `_create_mcp_error_stub`
  - `_build_mcp_tools`

- `runtime_component_builder.py`
  - `MiddlewareModelCredentialRequiredError`
  - `build_agent`
  - `_MIDDLEWARE_MODEL_FIELDS`
  - `_resolve_middleware_model_params`
  - `_model_constructor_params`
  - `_configured_recursion_limit`
  - `_model_chain`
  - `_build_model_candidates`
  - `_build_model_with_fallback`
  - `_is_retryable_model_error`
  - `_has_visible_ai_content`
  - `EmptyContentRetryMiddleware`
  - `_build_default_reliability_middleware`
  - `_append_temporal_tools`
  - `_default_interrupt_on_from_tools`
  - `_build_interrupt_on_policy`
  - `_selected_skill_slugs`
  - `_system_prompt_with_temporal_context`
  - `_parse_uuid`
  - `_load_memory_prompt`
  - `_memory_write_policy_for_run`
  - `_memory_tool_instruction_prompt`
  - `_artifact_file_instruction_prompt`
  - `_prepare_runtime_components`
  - `_prepare_agent`

- `agent_stream_runner.py`
  - `_hook_ctx_for_agent`
  - `_hook_result_from_usage`
  - `_run_agent_stream`
  - `_USE_PREPPED_LC_MESSAGES`
  - `execute_agent_stream`
  - `resume_agent_stream`
  - `execute_agent_invoke`

Out of scope for this plan:

- No behavior changes.
- No frontend changes except E2E execution.
- No DB migration.
- No broad `dict[str, Any]` cleanup in this pass.
- No deeper split of `streaming.py`, `model_factory.py`, or `tool_factory.py`.

## Compatibility Contract

The following import paths must still work after the refactor:

```python
from app.agent_runtime.executor import AgentConfig
from app.agent_runtime.executor import execute_agent_stream
from app.agent_runtime.executor import resume_agent_stream
from app.agent_runtime.executor import execute_agent_invoke
from app.agent_runtime.executor import build_agent
from app.agent_runtime.executor import _create_skill_execute_tool
from app.agent_runtime.executor import _build_mcp_tools
from app.agent_runtime.executor import _prepare_runtime_components
from app.agent_runtime.executor import _prepare_agent
from app.agent_runtime.executor import _DATA_DIR
```

Private test monkeypatch paths should move to the new focused modules. Product code should prefer focused modules after the split.

---

### Task 1: Baseline And Import Surface Guard

**Files:**
- Modify: `backend/tests/test_executor.py`
- Modify: `backend/tests/test_runtime_isolation.py`
- Test: existing targeted runtime tests

- [ ] **Step 1: Run the baseline targeted tests**

Run:

```bash
cd backend && uv run pytest \
  tests/test_executor.py \
  tests/test_runtime_isolation.py \
  tests/test_redaction.py \
  tests/test_tool_risk_policy.py \
  tests/test_hitl_middleware.py \
  tests/test_filesystem_permissions.py \
  tests/test_model_fallback.py \
  tests/agent_runtime/test_mcp_runtime_cache.py \
  tests/agent_runtime/test_subagents_runtime.py
```

Expected: all selected tests pass before any refactor. If this fails, stop and record the pre-existing failure.

- [ ] **Step 2: Add a test pinning the legacy facade import surface**

Append this test to `backend/tests/test_runtime_isolation.py` inside `TestImportSurfaceUnchanged`:

```python
    def test_executor_facade_exports_runtime_entrypoints(self) -> None:
        from app.agent_runtime import executor

        for name in (
            "AgentConfig",
            "RuntimeComponents",
            "_DATA_DIR",
            "build_agent",
            "_create_skill_execute_tool",
            "_build_mcp_tools",
            "_prepare_runtime_components",
            "_prepare_agent",
            "execute_agent_stream",
            "resume_agent_stream",
            "execute_agent_invoke",
        ):
            assert hasattr(executor, name), f"executor facade missing {name!r}"
```

- [ ] **Step 3: Run the new guard test**

Run:

```bash
cd backend && uv run pytest tests/test_runtime_isolation.py::TestImportSurfaceUnchanged -q
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_runtime_isolation.py
git commit -m "test(runtime): pin executor facade exports"
```

---

### Task 2: Extract Runtime Config Types

**Files:**
- Create: `backend/app/agent_runtime/runtime_config.py`
- Modify: `backend/app/agent_runtime/executor.py`
- Modify: `backend/app/marketplace/skill_runtime.py`
- Modify: production imports listed below
- Test: `backend/tests/test_runtime_isolation.py`

- [ ] **Step 1: Create `runtime_config.py`**

Create `backend/app/agent_runtime/runtime_config.py` with the existing `AgentConfig` and `RuntimeComponents` definitions moved from `executor.py`. Keep `_DATA_DIR` here because it is shared by scheduler, filesystem setup, skill runtime, and tests.

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deepagents.middleware.filesystem import FilesystemPermission
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


@dataclass
class AgentConfig:
    provider: str
    model_name: str
    api_key: str | None
    base_url: str | None
    system_prompt: str
    tools_config: list[dict[str, Any]]
    thread_id: str
    model_params: dict[str, Any] | None = None
    middleware_configs: list[dict[str, Any]] | None = None
    agent_skills: list[dict[str, Any]] | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    provider_api_keys: dict[str, str | None] | None = None
    cost_per_input_token: float | None = None
    cost_per_output_token: float | None = None
    user_id: str | None = None
    model_id: str | None = None
    llm_credential_id: str | None = None
    agent_owner_user_id: str | None = None
    caller_user_id: str | None = None
    credential_subject_user_id: str | None = None
    identity_mode: str | None = None
    agent_runtime_name: str | None = None
    subagents_config: list[dict[str, Any]] | None = None
    subagent_display_names: dict[str, str] | None = None
    model_fallback_chain: list[dict[str, Any]] | None = None
    checkpoint_id: str | None = None

    def __post_init__(self) -> None:
        if self.agent_id and not self.user_id:
            raise ValueError(
                "AgentConfig.user_id is required when agent_id is set "
                "(production callsite forgot to propagate authenticated user)."
            )


@dataclass
class RuntimeComponents:
    model_candidates: list[BaseChatModel]
    model: BaseChatModel
    tools: list[BaseTool]
    middleware: list[Any]
    system_prompt: str
    skills_sources: list[str] | None
    backend: Any | None
    memory_sources: list[str] | None
    permissions: list[FilesystemPermission]
    interrupt_on: dict[str, Any] | None
```

- [ ] **Step 2: Re-export config types from `executor.py`**

At the top of `backend/app/agent_runtime/executor.py`, replace the local `_DATA_DIR`, `AgentConfig`, and `RuntimeComponents` definitions with imports:

```python
from app.agent_runtime.runtime_config import AgentConfig, RuntimeComponents, _DATA_DIR
```

Remove the old local dataclass definitions from `executor.py`.

- [ ] **Step 3: Update type-only import in `skill_runtime.py`**

Change:

```python
from app.agent_runtime.executor import AgentConfig
```

to:

```python
from app.agent_runtime.runtime_config import AgentConfig
```

This is inside `if TYPE_CHECKING`, so runtime behavior is unchanged.

- [ ] **Step 4: Update production `AgentConfig` imports to focused module**

Change these imports:

```python
from app.agent_runtime.executor import AgentConfig
```

to:

```python
from app.agent_runtime.runtime_config import AgentConfig
```

Files:

- `backend/app/services/conversation_stream_service.py`
- `backend/app/services/conversation_branch_service.py`
- `backend/app/services/agent_invocation_service.py`
- `backend/app/agent_runtime/trigger_executor.py`
- `backend/app/agent_runtime/subagents.py`
- `backend/app/marketplace/skill_runtime.py` type-checking import

Change scheduler import:

```python
from app.agent_runtime.executor import _DATA_DIR
```

to:

```python
from app.agent_runtime.runtime_config import _DATA_DIR
```

File:

- `backend/app/scheduler.py`

- [ ] **Step 5: Run targeted tests**

```bash
cd backend && uv run pytest \
  tests/test_runtime_isolation.py::TestImportSurfaceUnchanged \
  tests/test_marketplace_phase1_gates.py \
  tests/agent_runtime/test_subagents_runtime.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/agent_runtime/runtime_config.py \
  backend/app/agent_runtime/executor.py \
  backend/app/marketplace/skill_runtime.py \
  backend/app/services/conversation_stream_service.py \
  backend/app/services/conversation_branch_service.py \
  backend/app/services/agent_invocation_service.py \
  backend/app/agent_runtime/trigger_executor.py \
  backend/app/agent_runtime/subagents.py \
  backend/app/scheduler.py \
  backend/tests/test_runtime_isolation.py
git commit -m "refactor(runtime): extract agent config types"
```

---

### Task 3: Extract Skill Executor

**Files:**
- Create: `backend/app/agent_runtime/skill_executor.py`
- Modify: `backend/app/agent_runtime/executor.py`
- Modify: `backend/app/agent_runtime/runtime_component_builder.py` later in Task 5
- Modify: `backend/tests/test_runtime_isolation.py`
- Modify: `backend/tests/test_redaction.py`
- Modify: `backend/tests/test_tool_risk_policy.py`

- [ ] **Step 1: Create `skill_executor.py`**

Move the existing implementation of these symbols from `executor.py` to `backend/app/agent_runtime/skill_executor.py` unchanged:

```python
_SHELL_ASSIGNMENT_RE
_SHELL_DEFAULT_RE
_SHELL_VAR_RE
_DEFAULT_SKILL_TIMEOUT_SECONDS
_MAX_SKILL_TIMEOUT_SECONDS
_expand_shell_vars
_prepare_skill_subprocess_args
_skill_timeout_seconds
_create_skill_execute_tool
```

The new file must import:

```python
from __future__ import annotations

import asyncio
import os
import re
import shlex
import sys
from pathlib import Path

from langchain_core.tools import BaseTool, StructuredTool

from app.marketplace.skill_runtime import SkillRuntimeDescriptor, SkillToolContext
from app.tools.risk import attach_tool_risk, execute_in_skill_risk
```

- [ ] **Step 2: Re-export skill executor symbols from `executor.py`**

In `executor.py`, import:

```python
from app.agent_runtime.skill_executor import (
    _create_skill_execute_tool,
    _expand_shell_vars,
    _prepare_skill_subprocess_args,
    _skill_timeout_seconds,
)
```

Remove the moved implementation from `executor.py`.

- [ ] **Step 3: Update tests to import the focused module**

Change direct imports:

```python
from app.agent_runtime.executor import _create_skill_execute_tool
```

to:

```python
from app.agent_runtime.skill_executor import _create_skill_execute_tool
```

Files:

- `backend/tests/test_runtime_isolation.py`
- `backend/tests/test_redaction.py`
- `backend/tests/test_tool_risk_policy.py`

Keep `AgentConfig` imports from `app.agent_runtime.executor` only where the test is explicitly checking compatibility. Otherwise prefer `app.agent_runtime.runtime_config`.

- [ ] **Step 4: Update monkeypatch paths that target skill executor internals**

In `backend/tests/test_runtime_isolation.py`, change:

```python
real_wait_for = executor_runtime.asyncio.wait_for
monkeypatch.setattr(executor_runtime.asyncio, "wait_for", _recording_wait_for)
```

to:

```python
from app.agent_runtime import skill_executor

real_wait_for = skill_executor.asyncio.wait_for
monkeypatch.setattr(skill_executor.asyncio, "wait_for", _recording_wait_for)
```

- [ ] **Step 5: Run skill-focused tests**

```bash
cd backend && uv run pytest \
  tests/test_runtime_isolation.py \
  tests/test_redaction.py \
  tests/test_tool_risk_policy.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/agent_runtime/skill_executor.py \
  backend/app/agent_runtime/executor.py \
  backend/tests/test_runtime_isolation.py \
  backend/tests/test_redaction.py \
  backend/tests/test_tool_risk_policy.py
git commit -m "refactor(runtime): extract skill executor"
```

---

### Task 4: Extract MCP Tool Loader

**Files:**
- Create: `backend/app/agent_runtime/mcp_tool_loader.py`
- Modify: `backend/app/agent_runtime/executor.py`
- Modify: `backend/tests/agent_runtime/test_mcp_runtime_cache.py`
- Modify: `backend/tests/test_tool_risk_policy.py`

- [ ] **Step 1: Create `mcp_tool_loader.py`**

Move these symbols from `executor.py` to `backend/app/agent_runtime/mcp_tool_loader.py` unchanged:

```python
_auth_config_to_headers
_url_to_server_key
_AuthInjectorInterceptor
_hide_auth_params_from_schema
_create_mcp_error_stub
_build_mcp_tools
```

The new file must import:

```python
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any
from urllib.parse import urlparse

from langchain_core.tools import BaseTool, StructuredTool

from app.tools.risk import attach_tool_risk, mcp_tool_risk
```

Keep the lazy imports already present inside `_build_mcp_tools`:

```python
from langchain_mcp_adapters.client import MultiServerMCPClient
from app.agent_runtime.mcp_cache import MCPToolWithRetry, get_cached_mcp_tools
from app.config import settings as _settings
```

- [ ] **Step 2: Re-export MCP symbols from `executor.py`**

In `executor.py`, import:

```python
from app.agent_runtime.mcp_tool_loader import (
    _build_mcp_tools,
    _create_mcp_error_stub,
)
```

Remove the moved MCP implementation from `executor.py`.

- [ ] **Step 3: Update MCP tests to focused paths**

Change:

```python
from app.agent_runtime.executor import _build_mcp_tools
```

to:

```python
from app.agent_runtime.mcp_tool_loader import _build_mcp_tools
```

File:

- `backend/tests/agent_runtime/test_mcp_runtime_cache.py`

Change:

```python
from app.agent_runtime.executor import _create_mcp_error_stub
```

to:

```python
from app.agent_runtime.mcp_tool_loader import _create_mcp_error_stub
```

File:

- `backend/tests/test_tool_risk_policy.py`

- [ ] **Step 4: Run MCP-focused tests**

```bash
cd backend && uv run pytest \
  tests/agent_runtime/test_mcp_runtime_cache.py \
  tests/test_tool_risk_policy.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent_runtime/mcp_tool_loader.py \
  backend/app/agent_runtime/executor.py \
  backend/tests/agent_runtime/test_mcp_runtime_cache.py \
  backend/tests/test_tool_risk_policy.py
git commit -m "refactor(runtime): extract mcp tool loader"
```

---

### Task 5: Extract Runtime Component Builder

**Files:**
- Create: `backend/app/agent_runtime/runtime_component_builder.py`
- Modify: `backend/app/agent_runtime/executor.py`
- Modify: `backend/app/agent_runtime/subagents.py`
- Modify: `backend/app/agent_runtime/assistant/assistant_agent.py`
- Modify: `backend/tests/test_executor.py`
- Modify: `backend/tests/test_model_fallback.py`
- Modify: `backend/tests/test_filesystem_permissions.py`
- Modify: `backend/tests/test_hitl_middleware.py`

- [ ] **Step 1: Create `runtime_component_builder.py`**

Move the runtime build implementation from `executor.py` into `backend/app/agent_runtime/runtime_component_builder.py`.

The new file owns:

```python
MiddlewareModelCredentialRequiredError
build_agent
_MIDDLEWARE_MODEL_FIELDS
_resolve_middleware_model_params
_model_constructor_params
_configured_recursion_limit
_model_chain
_build_model_candidates
_build_model_with_fallback
_is_retryable_model_error
_has_visible_ai_content
EmptyContentRetryMiddleware
_build_default_reliability_middleware
_append_temporal_tools
_default_interrupt_on_from_tools
_build_interrupt_on_policy
_selected_skill_slugs
_system_prompt_with_temporal_context
_parse_uuid
_load_memory_prompt
_memory_write_policy_for_run
_memory_tool_instruction_prompt
_artifact_file_instruction_prompt
_prepare_runtime_components
_prepare_agent
```

The file must import the moved dependencies from focused modules:

```python
from app.agent_runtime.mcp_tool_loader import _build_mcp_tools
from app.agent_runtime.runtime_config import AgentConfig, RuntimeComponents, _DATA_DIR
from app.agent_runtime.skill_executor import _create_skill_execute_tool
```

It must keep these imports because tests monkeypatch them in the new module:

```python
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from app.agent_runtime.message_utils import convert_to_langchain_messages
from app.agent_runtime.model_factory import create_chat_model
from app.agent_runtime.tool_factory import create_builtin_tool, create_tool_for_runtime
from app.agent_runtime.tools.memory import build_memory_tools
from app.marketplace.skill_runtime import build_skill_runtime_context, resolve_runtime_credentials
```

- [ ] **Step 2: Re-export builder symbols from `executor.py`**

In `executor.py`, import:

```python
from app.agent_runtime.runtime_component_builder import (
    EmptyContentRetryMiddleware,
    MiddlewareModelCredentialRequiredError,
    _build_default_reliability_middleware,
    _build_model_candidates,
    _build_model_with_fallback,
    _configured_recursion_limit,
    _load_memory_prompt,
    _memory_write_policy_for_run,
    _prepare_agent,
    _prepare_runtime_components,
    _resolve_middleware_model_params,
    build_agent,
)
```

Remove the moved implementation from `executor.py`.

- [ ] **Step 3: Update production imports to focused modules**

Change `backend/app/agent_runtime/subagents.py`:

```python
from app.agent_runtime.executor import AgentConfig, _prepare_runtime_components
```

to:

```python
from app.agent_runtime.runtime_component_builder import _prepare_runtime_components
from app.agent_runtime.runtime_config import AgentConfig
```

Change `backend/app/agent_runtime/assistant/assistant_agent.py`:

```python
from app.agent_runtime.executor import build_agent
```

to:

```python
from app.agent_runtime.runtime_component_builder import build_agent
```

- [ ] **Step 4: Update test patch paths**

In tests that validate component building, change patch paths from `app.agent_runtime.executor` to `app.agent_runtime.runtime_component_builder`.

Use this mapping:

```text
app.agent_runtime.executor.create_deep_agent
  -> app.agent_runtime.runtime_component_builder.create_deep_agent

app.agent_runtime.executor.build_agent
  -> app.agent_runtime.runtime_component_builder.build_agent

app.agent_runtime.executor.convert_to_langchain_messages
  -> app.agent_runtime.runtime_component_builder.convert_to_langchain_messages

app.agent_runtime.executor.create_chat_model
  -> app.agent_runtime.runtime_component_builder.create_chat_model

app.agent_runtime.executor.create_tool_for_runtime
  -> app.agent_runtime.runtime_component_builder.create_tool_for_runtime

app.agent_runtime.executor.FilesystemBackend
  -> app.agent_runtime.runtime_component_builder.FilesystemBackend

app.agent_runtime.executor.build_skill_runtime_context
  -> app.agent_runtime.runtime_component_builder.build_skill_runtime_context

app.agent_runtime.executor.resolve_runtime_credentials
  -> app.agent_runtime.runtime_component_builder.resolve_runtime_credentials

app.agent_runtime.executor._DATA_DIR
  -> app.agent_runtime.runtime_config._DATA_DIR

app.agent_runtime.executor._load_memory_prompt
  -> app.agent_runtime.runtime_component_builder._load_memory_prompt

app.agent_runtime.executor._memory_write_policy_for_run
  -> app.agent_runtime.runtime_component_builder._memory_write_policy_for_run

app.agent_runtime.executor._build_mcp_tools
  -> app.agent_runtime.runtime_component_builder._build_mcp_tools

app.agent_runtime.executor._build_model_candidates
  -> app.agent_runtime.runtime_component_builder._build_model_candidates
```

Files with expected edits:

- `backend/tests/test_executor.py`
- `backend/tests/test_model_fallback.py`
- `backend/tests/test_filesystem_permissions.py`
- `backend/tests/test_hitl_middleware.py`

- [ ] **Step 5: Update direct imports in tests**

Change:

```python
from app.agent_runtime.executor import _build_model_with_fallback
from app.agent_runtime.executor import _build_model_candidates
from app.agent_runtime.executor import _build_default_reliability_middleware
from app.agent_runtime.executor import _resolve_middleware_model_params
from app.agent_runtime.executor import build_agent
```

to:

```python
from app.agent_runtime.runtime_component_builder import _build_model_with_fallback
from app.agent_runtime.runtime_component_builder import _build_model_candidates
from app.agent_runtime.runtime_component_builder import _build_default_reliability_middleware
from app.agent_runtime.runtime_component_builder import _resolve_middleware_model_params
from app.agent_runtime.runtime_component_builder import build_agent
```

Keep one facade import surface test in `test_runtime_isolation.py`; do not convert that one.

- [ ] **Step 6: Run builder-focused tests**

```bash
cd backend && uv run pytest \
  tests/test_executor.py \
  tests/test_model_fallback.py \
  tests/test_filesystem_permissions.py \
  tests/test_hitl_middleware.py \
  tests/agent_runtime/test_subagents_runtime.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/agent_runtime/runtime_component_builder.py \
  backend/app/agent_runtime/executor.py \
  backend/app/agent_runtime/subagents.py \
  backend/app/agent_runtime/assistant/assistant_agent.py \
  backend/tests/test_executor.py \
  backend/tests/test_model_fallback.py \
  backend/tests/test_filesystem_permissions.py \
  backend/tests/test_hitl_middleware.py
git commit -m "refactor(runtime): extract component builder"
```

---

### Task 6: Extract Agent Stream Runner

**Files:**
- Create: `backend/app/agent_runtime/agent_stream_runner.py`
- Modify: `backend/app/agent_runtime/executor.py`
- Modify: `backend/app/routers/agent_runtime_api.py`
- Modify: `backend/app/services/conversation_stream_service.py`
- Modify: `backend/app/agent_runtime/trigger_executor.py`
- Modify: `backend/tests/test_executor.py`
- Modify: `backend/tests/test_hitl_middleware.py`

- [ ] **Step 1: Create `agent_stream_runner.py`**

Move these symbols from `executor.py` to `backend/app/agent_runtime/agent_stream_runner.py`:

```python
_hook_ctx_for_agent
_hook_result_from_usage
_run_agent_stream
_USE_PREPPED_LC_MESSAGES
execute_agent_stream
resume_agent_stream
execute_agent_invoke
```

The new file must import:

```python
from __future__ import annotations

import time
import uuid as _uuid
from collections.abc import AsyncGenerator
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any

from app.agent_runtime.message_utils import convert_to_langchain_messages
from app.agent_runtime.runtime_component_builder import _configured_recursion_limit, _prepare_agent
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.streaming import StreamErrorRecord, stream_agent_response
from app.hooks import HookContext, HookResult, hooks
from app.observability.langfuse import LangfuseTraceRecord, build_langfuse_run_context
```

Note: `convert_to_langchain_messages` is only needed if the current `_prepare_agent` split leaves conversion in the runner. If `_prepare_agent` still returns `lc_messages`, do not import it here.

- [ ] **Step 2: Re-export stream runner symbols from `executor.py`**

In `executor.py`, import:

```python
from app.agent_runtime.agent_stream_runner import (
    _hook_ctx_for_agent,
    _hook_result_from_usage,
    _run_agent_stream,
    execute_agent_invoke,
    execute_agent_stream,
    resume_agent_stream,
)
```

Remove the moved runner implementation from `executor.py`.

- [ ] **Step 3: Update production imports to focused module**

Change `backend/app/routers/agent_runtime_api.py`:

```python
from app.agent_runtime.executor import execute_agent_invoke, execute_agent_stream
```

to:

```python
from app.agent_runtime.agent_stream_runner import execute_agent_invoke, execute_agent_stream
```

Change `backend/app/services/conversation_stream_service.py`:

```python
from app.agent_runtime.executor import AgentConfig, execute_agent_stream, resume_agent_stream
```

to:

```python
from app.agent_runtime.agent_stream_runner import execute_agent_stream, resume_agent_stream
from app.agent_runtime.runtime_config import AgentConfig
```

Change `backend/app/agent_runtime/trigger_executor.py`:

```python
from app.agent_runtime.executor import AgentConfig, execute_agent_invoke
```

to:

```python
from app.agent_runtime.agent_stream_runner import execute_agent_invoke
from app.agent_runtime.runtime_config import AgentConfig
```

- [ ] **Step 4: Update stream runner test patch paths**

Use this mapping:

```text
app.agent_runtime.executor._prepare_agent
  -> app.agent_runtime.agent_stream_runner._prepare_agent

app.agent_runtime.executor.stream_agent_response
  -> app.agent_runtime.agent_stream_runner.stream_agent_response

app.agent_runtime.executor.build_langfuse_run_context
  -> app.agent_runtime.agent_stream_runner.build_langfuse_run_context

app.agent_runtime.executor.hooks
  -> app.agent_runtime.agent_stream_runner.hooks
```

Files:

- `backend/tests/test_executor.py`
- `backend/tests/test_hitl_middleware.py`

Tests that patch `build_agent`, `create_chat_model`, `create_tool_for_runtime`, or `FilesystemBackend` should already point at `runtime_component_builder` after Task 5.

- [ ] **Step 5: Update direct imports in tests**

Change:

```python
from app.agent_runtime.executor import execute_agent_stream
from app.agent_runtime.executor import resume_agent_stream
from app.agent_runtime.executor import execute_agent_invoke
```

to:

```python
from app.agent_runtime.agent_stream_runner import execute_agent_stream
from app.agent_runtime.agent_stream_runner import resume_agent_stream
from app.agent_runtime.agent_stream_runner import execute_agent_invoke
```

Files:

- `backend/tests/test_executor.py`
- `backend/tests/test_hitl_middleware.py`

Leave facade compatibility covered by `TestImportSurfaceUnchanged`.

- [ ] **Step 6: Run stream-focused tests**

```bash
cd backend && uv run pytest \
  tests/test_executor.py \
  tests/test_hitl_middleware.py \
  tests/test_agent_api_compat_adapters.py \
  tests/test_agent_runtime_public_api.py \
  tests/test_trigger_executor.py \
  tests/test_conversations_router.py \
  tests/test_thread_branch.py \
  tests/test_hitl_wire.py \
  tests/integration/test_stream_resume.py \
  tests/integration/test_broker_dual_write.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/agent_runtime/agent_stream_runner.py \
  backend/app/agent_runtime/executor.py \
  backend/app/routers/agent_runtime_api.py \
  backend/app/services/conversation_stream_service.py \
  backend/app/agent_runtime/trigger_executor.py \
  backend/tests/test_executor.py \
  backend/tests/test_hitl_middleware.py
git commit -m "refactor(runtime): extract agent stream runner"
```

---

### Task 7: Shrink `executor.py` Into A Facade

**Files:**
- Modify: `backend/app/agent_runtime/executor.py`
- Test: import and full targeted runtime tests

- [ ] **Step 1: Replace `executor.py` body with explicit re-exports**

After Tasks 2-6, `backend/app/agent_runtime/executor.py` should contain only imports, `__all__`, and a short module docstring.

Target shape:

```python
from __future__ import annotations

"""Compatibility facade for Moldy agent runtime execution.

Implementation lives in focused modules:

* runtime_config
* skill_executor
* mcp_tool_loader
* runtime_component_builder
* agent_stream_runner
"""

from app.agent_runtime.agent_stream_runner import (
    _hook_ctx_for_agent,
    _hook_result_from_usage,
    _run_agent_stream,
    execute_agent_invoke,
    execute_agent_stream,
    resume_agent_stream,
)
from app.agent_runtime.mcp_tool_loader import _build_mcp_tools, _create_mcp_error_stub
from app.agent_runtime.runtime_component_builder import (
    EmptyContentRetryMiddleware,
    MiddlewareModelCredentialRequiredError,
    _build_default_reliability_middleware,
    _build_model_candidates,
    _build_model_with_fallback,
    _configured_recursion_limit,
    _load_memory_prompt,
    _memory_write_policy_for_run,
    _prepare_agent,
    _prepare_runtime_components,
    _resolve_middleware_model_params,
    build_agent,
)
from app.agent_runtime.runtime_config import AgentConfig, RuntimeComponents, _DATA_DIR
from app.agent_runtime.skill_executor import (
    _create_skill_execute_tool,
    _expand_shell_vars,
    _prepare_skill_subprocess_args,
    _skill_timeout_seconds,
)

__all__ = [
    "AgentConfig",
    "RuntimeComponents",
    "_DATA_DIR",
    "build_agent",
    "MiddlewareModelCredentialRequiredError",
    "EmptyContentRetryMiddleware",
    "_build_default_reliability_middleware",
    "_build_model_candidates",
    "_build_model_with_fallback",
    "_configured_recursion_limit",
    "_load_memory_prompt",
    "_memory_write_policy_for_run",
    "_prepare_agent",
    "_prepare_runtime_components",
    "_resolve_middleware_model_params",
    "_create_skill_execute_tool",
    "_expand_shell_vars",
    "_prepare_skill_subprocess_args",
    "_skill_timeout_seconds",
    "_build_mcp_tools",
    "_create_mcp_error_stub",
    "_hook_ctx_for_agent",
    "_hook_result_from_usage",
    "_run_agent_stream",
    "execute_agent_stream",
    "resume_agent_stream",
    "execute_agent_invoke",
]
```

- [ ] **Step 2: Check line count**

Run:

```bash
wc -l backend/app/agent_runtime/executor.py
```

Expected: roughly 80-120 lines.

- [ ] **Step 3: Run compatibility tests**

```bash
cd backend && uv run pytest \
  tests/test_runtime_isolation.py::TestImportSurfaceUnchanged \
  tests/test_executor.py \
  tests/test_model_fallback.py \
  tests/test_filesystem_permissions.py \
  tests/test_hitl_middleware.py \
  tests/test_redaction.py \
  tests/test_tool_risk_policy.py \
  tests/agent_runtime/test_mcp_runtime_cache.py \
  tests/agent_runtime/test_subagents_runtime.py
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/app/agent_runtime/executor.py
git commit -m "refactor(runtime): keep executor as facade"
```

---

### Task 8: Lint, Full Backend Regression, And Real Server E2E

**Files:**
- No required source changes unless verification finds a bug.
- Output only: `output/e2e-captures/20260607-executor-runtime-split/`

- [ ] **Step 1: Run lint**

```bash
cd backend && uv run ruff check .
```

Expected: PASS.

- [ ] **Step 2: Run full backend tests**

```bash
cd backend && uv run pytest
```

Expected: full suite passes. Record exact pass count.

- [ ] **Step 3: Prepare worktree env**

```bash
bash scripts/worktree-setup.sh
```

Expected:

- `backend/.env` points to the main checkout `.env`.
- If `backend/data` is not a symlink, do not modify it unless the user explicitly asks.

- [ ] **Step 4: Start backend server**

Run in a long-lived shell:

```bash
cd backend
CORS_ALLOWED_ORIGINS=http://localhost:3010,http://127.0.0.1:3010 \
  uv run uvicorn app.main:app --port 8010 --reload-dir app
```

Expected: backend starts without import errors. Check `/docs` or `/api/health` if available.

- [ ] **Step 5: Start frontend server**

Run in a second long-lived shell:

```bash
cd frontend
NEXT_PUBLIC_API_BASE_URL=http://localhost:8010 pnpm dev -- --port 3010
```

Expected: frontend starts on `http://localhost:3010`.

- [ ] **Step 6: Run existing real-server Playwright smoke paths**

```bash
cd frontend
E2E_FRONTEND_PORT=3010 \
E2E_BACKEND_PORT=8010 \
E2E_BASE_URL=http://localhost:3010 \
E2E_API_BASE_URL=http://localhost:8010 \
E2E_WORKERS=1 \
pnpm test:e2e -- smoke.spec.ts draft-conversation.spec.ts -g "Dynamic Pages|clicking 새 대화|draft route"
```

Expected: PASS or known skipped cases only.

- [ ] **Step 7: Run a no-mock runtime API/browser capture**

Create a temporary Playwright script under `output/e2e-captures/20260607-executor-runtime-split/runtime-smoke.mjs` that:

1. Logs in with the E2E user from env.
2. Opens `/`.
3. Creates or reuses a simple agent with an available model.
4. Creates a conversation.
5. Sends one short user message through the real backend stream.
6. Confirms the stream request returns `text/event-stream`.
7. Opens the conversation page.
8. Captures screenshots.

Capture paths:

```text
output/e2e-captures/20260607-executor-runtime-split/01-dashboard.png
output/e2e-captures/20260607-executor-runtime-split/02-conversation.png
output/e2e-captures/20260607-executor-runtime-split/03-runtime-report.png
```

If no usable LLM credential exists in the local dev DB, stop and report this as an environment blocker instead of mocking the runtime. This priority touches the real runtime path, so a mocked stream is not enough for final acceptance.

- [ ] **Step 8: Verify screenshots are real images**

```bash
file output/e2e-captures/20260607-executor-runtime-split/*.png
```

Expected: each file is a valid PNG with non-zero dimensions.

Open each screenshot with `view_image` and verify:

- UI is not blank.
- No obvious overlap.
- Conversation screen actually loaded.
- Runtime report shows all checks passing.

- [ ] **Step 9: Stop servers and confirm ports are free**

Stop both long-lived server sessions, then run:

```bash
lsof -nP -iTCP:8010 -sTCP:LISTEN
lsof -nP -iTCP:3010 -sTCP:LISTEN
```

Expected: both commands return no listener.

- [ ] **Step 10: Final diff checks**

```bash
git diff --check
git status --short --branch
```

Expected:

- `git diff --check` exits 0.
- only intended source/test/doc changes are present.
- `output/` capture artifacts are ignored and not staged.

- [ ] **Step 11: Final commit**

```bash
git add backend/app/agent_runtime \
  backend/app/routers/agent_runtime_api.py \
  backend/app/services/conversation_stream_service.py \
  backend/app/services/conversation_branch_service.py \
  backend/app/services/agent_invocation_service.py \
  backend/app/agent_runtime/trigger_executor.py \
  backend/app/agent_runtime/subagents.py \
  backend/app/agent_runtime/assistant/assistant_agent.py \
  backend/app/marketplace/skill_runtime.py \
  backend/app/scheduler.py \
  backend/tests
git commit -m "refactor(runtime): split executor modules"
```

If earlier task commits were already made, this final commit should include only cleanup edits and test path stabilization.

---

## Acceptance Checklist

- [ ] `executor.py` is a compatibility facade around 80-120 lines.
- [ ] `AgentConfig` is defined in `runtime_config.py`.
- [ ] skill subprocess execution lives in `skill_executor.py`.
- [ ] MCP loading and unavailable-tool stubs live in `mcp_tool_loader.py`.
- [ ] model/tool/middleware/filesystem/memory preparation lives in `runtime_component_builder.py`.
- [ ] stream/resume/invoke orchestration lives in `agent_stream_runner.py`.
- [ ] Existing product imports still work through `app.agent_runtime.executor`.
- [ ] Product code uses focused modules where that improves clarity.
- [ ] No behavior change to stream chunks, HiTL policy, memory tools, skill execution, MCP retry/stubs, model fallback, or Langfuse metadata.
- [ ] `cd backend && uv run ruff check .` passes.
- [ ] `cd backend && uv run pytest` passes.
- [ ] Actual backend/frontend E2E runs with real servers.
- [ ] E2E screenshots are saved, verified with `file`, and inspected with `view_image`.

## Risk Notes

- Most breakage risk comes from monkeypatch paths. Update tests to the module that owns the implementation, not the facade.
- Moving `AgentConfig` first reduces circular import pressure because `skill_runtime.py` currently type-check imports it from `executor.py`.
- `agent_stream_runner.py` functions close over their own module globals. Patching `app.agent_runtime.executor._prepare_agent` will not affect them after extraction; patch `app.agent_runtime.agent_stream_runner._prepare_agent`.
- `runtime_component_builder.py` functions close over their own module globals. Patching `app.agent_runtime.executor.create_chat_model` will not affect `_build_model_candidates`; patch `app.agent_runtime.runtime_component_builder.create_chat_model`.
- Do not remove legacy re-exports in this pass. They are cheap, and several modules/tests still use `executor` as the public runtime entrypoint.

## Suggested Execution Mode

Subagent-driven execution is appropriate after Task 1 because Tasks 3 and 4 are independent once `runtime_config.py` exists. Inline execution is also safe if each task is committed separately and verified before moving on.
