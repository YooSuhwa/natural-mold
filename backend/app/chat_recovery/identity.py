"""Content pins for replaying an ordinary chat run on a different worker.

The configured pin covers the permission-checked AgentConfig. The prepared pin
covers what the ordinary graph actually received after resolving memory and
runtime tools. Only per-tenant keyed digests leave this module; rotating the
installation key invalidates a suspended run's pin.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import stat
import uuid
from dataclasses import fields
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.runtime_snapshot_catalog import (
    SnapshotCatalogBuilder,
    normalize_virtual_path,
)
from app.agent_runtime.stream_retry_policy import stream_retry_enabled
from app.chat_recovery.fence import current_run_fence, lock_run_fence, open_run_session
from app.config import settings
from app.guardrails.pinning import _snapshot
from app.security import key_provider

PREPARATION_DIGEST = "recovery_preparation_digest"


class RecoveryPolicyChanged(RuntimeError):
    code = "recovery_policy_changed"

    def __init__(self) -> None:
        super().__init__(self.code)


@lru_cache(maxsize=1)
def _runtime_versions() -> dict[str, str]:
    return {
        package: version(package)
        for package in ("deepagents", "langgraph", "langchain", "langchain-core")
    }


def recovery_digest(tenant_id: uuid.UUID, purpose: str, material: Any) -> str:
    """Canonical JSON HMAC using a purpose- and tenant-specific key for recovery records."""
    message = json.dumps(
        material, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()
    domain = f"moldy-chat-recovery-v1|{tenant_id}|{purpose}".encode()
    key = hmac.digest(key_provider.get_active_key().raw, domain, "sha256")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _scope(cfg: AgentConfig) -> dict[str, Any] | None:
    plan = cfg.access_plan
    if plan is None:
        return None
    return {
        "account": str(plan.subject.account_user_id),
        "principal": plan.subject.principal.fga(),
        "acting_user": str(plan.subject.principal.acting_user_id),
        "decided": sorted(plan.decided),
        "excluded": sorted(
            (
                item.kind,
                str(item.resource_id),
                item.reason,
                item.name or "",
                item.definition_key or "",
            )
            for item in plan.excluded
        ),
        "tools": sorted(str(link.tool_id) for link in plan.tool_links),
    }


# All other AgentConfig fields travel as their resolved values, including credentials
# and tool arguments, inside the keyed digest. Unknown opaque values disable recovery.
_SEPARATE_FIELDS = frozenset(
    {
        "org_context",
        "runtime_policy",
        "access_plan",
        "tool_grants",
        "secret_values",
        "guardrail_rules",
        "tool_risk",
        # LangGraph selects and independently validates the new cursor. Memory is
        # rebuilt against the same run id and pinned at the materialization boundary.
        "checkpoint_id",
        "memory_run_id",
        "memory_search_scopes",
        "memory_recall_block",
        "recalled_memories",
        # Implementation bound by the server; authorization runs again on every claim.
        "steer_store",
    }
)


def recovery_runtime_digest(cfg: AgentConfig) -> str | None:
    """The authorized configuration before graph preparation, or None for an unpinnable run."""
    if (
        cfg.runtime_profile != "standard"
        or cfg.run_path != "chat"
        or not cfg.guardrail_resolved
        or not cfg.agent_id
        or cfg.user_id != str(cfg.org_context.user_id)
        or not cfg.model_id
        or not cfg.resolved_model_credential_id
        or not cfg.api_key  # An SDK could select an unpinned environment credential.
        or cfg.attachment_inputs is not None
        or cfg.subagents_config
        or cfg.agent_skills
        or cfg.workflow_tools
        or cfg.middleware_configs
        or cfg.model_fallback_chain
        or any(
            t.get("definition_key") == "mcp" or t.get("mcp_server_url") for t in cfg.tools_config
        )
    ):
        return None
    if cfg.access_plan and (
        cfg.access_plan.skill_links
        or cfg.access_plan.sub_agent_links
        or cfg.access_plan.mcp_tool_links
    ):
        return None
    values = {
        field.name: getattr(cfg, field.name)
        for field in fields(cfg)
        if field.name not in _SEPARATE_FIELDS
    }
    # A missing delivery backend changes an eligible graph. Preserve existing pins
    # for real chats, which have always had the database-backed implementation.
    if settings.chat_steer_enabled and cfg.steer_store is None:
        values["steer_delivery_unavailable"] = True
    context = cfg.org_context
    material = {
        "versions": _runtime_versions(),
        "config": values,
        "tenant": str(context.tenant_id),
        "org": str(context.org_id),
        "user": str(context.user_id),
        "is_super_user": context.is_super_user,
        "scope": _scope(cfg),
        "runtime_policy": [cfg.runtime_policy.source, cfg.runtime_policy.canonical_json],
        "tool_grants": sorted(cfg.tool_grants),
        "redaction_values": sorted(cfg.secret_values),
        "guardrails": _snapshot(cfg.guardrail_rules, cfg.tool_risk),
    }
    # Eligibility above excludes fallback chains and custom middleware. Pin
    # whether this root provider requires a durable send budget. A policy
    # change or a pre-reservation worker must not resume an uncounted call.
    if stream_retry_enabled(cfg.provider):
        material["model_attempt_budget_version"] = 1
    try:
        return recovery_digest(context.tenant_id, "config", material)
    except (TypeError, ValueError):
        return None


def _memo_hash(root: Path, virtual_path: str) -> str | None:
    """Use the same bounded regular-file snapshot as prepared Deep Agents runs."""
    path = root / normalize_virtual_path(virtual_path).lstrip("/")
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError("unversioned memo source")
    except FileNotFoundError:
        return None
    return SnapshotCatalogBuilder().add_file(virtual_path, root=root, source=path, kind="memory")


async def verify_prepared_recovery_inputs(
    cfg: AgentConfig,
    *,
    system_prompt: str,
    tools: list[BaseTool],
    middleware: list[Any],
    interrupts: dict[str, Any] | None,
    permissions: list[Any],
    write_policy: str,
    memory_sources: list[str] | None,
    data_dir: Path,
) -> None:
    """Bind the inputs the root graph will consume, before invoking any model/tool."""
    fence = current_run_fence()
    if fence is None:
        return
    if (
        cfg.thread_id != str(fence.conversation_id)
        or cfg.org_context.tenant_id != fence.tenant.tenant_id
        or cfg.org_context.org_id != fence.tenant.org_id
        or cfg.org_context.user_id != fence.tenant.user_id
    ):
        raise RecoveryPolicyChanged
    try:
        material = {
            "prompt": system_prompt,
            "tools": [convert_to_openai_tool(tool) for tool in tools],
            "middleware": [f"{type(m).__module__}.{type(m).__qualname__}" for m in middleware],
            "interrupts": interrupts,
            "permissions": [(item.operations, item.paths, item.mode) for item in permissions],
            "memory_write": write_policy,
            "memory_read": cfg.memory_search_scopes,
            "recall": cfg.memory_recall_block,
            "recall_refs": cfg.recalled_memories,
            "memos": {
                path: await asyncio.to_thread(_memo_hash, data_dir, path)
                for path in memory_sources or ()
            },
        }
        digest = recovery_digest(cfg.org_context.tenant_id, "preparation", material)
    except (TypeError, ValueError, OSError):
        # The initial run can proceed normally. Without the materialization pin
        # the sweeper will refuse to reconstruct it after worker loss.
        if fence.generation == 1:
            return
        raise RecoveryPolicyChanged from None
    async with open_run_session(fence) as db:
        run = await lock_run_fence(db, fence)
        metadata = run.metadata_json or {}
        stored = metadata.get(PREPARATION_DIGEST)
        if stored is None and fence.generation == 1:
            run.metadata_json = {**metadata, PREPARATION_DIGEST: digest}
            await db.commit()
        elif not isinstance(stored, str) or not hmac.compare_digest(
            stored.encode(), digest.encode()
        ):
            raise RecoveryPolicyChanged
