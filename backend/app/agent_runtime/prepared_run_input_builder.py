"""Assemble ``PreparationInputs`` from already-resolved run sources (parent Task 10).

Pure assembly, called by the service adapter inside ``prepare_inputs(db, run)``
while the canonical gateway locks are held. Every byte the lease may read is
frozen here into one ``RuntimeSnapshotCatalog``: selected skills (stored policy
only, remapped under the run's virtual skills prefix) and the agent
``AGENTS.md``; the long-term memory prompt is resolved by the caller and baked
into the system prompt. Children become declarative specs whose ``model`` is a
``LeaseModelSpec`` and whose stored tool policy is their exact tool-name set.
The shared data root is the backend's forbidden root; writes go only to the
run root's ``output`` directory mapped at ``/conversations/<thread_id>``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agent_runtime.lease_model_factory import LeaseModelSpec
from app.agent_runtime.message_utils import RuntimeMessage, convert_to_langchain_messages
from app.agent_runtime.prepared_run_input_policy import unsupported
from app.agent_runtime.prepared_run_inputs import PreparationInputs
from app.agent_runtime.prepared_run_tool_inputs import (
    PreparedToolSet,
    merged_origins,
    prepared_tool_set,
)
from app.agent_runtime.runtime.reliability import EmptyContentRetryMiddleware
from app.agent_runtime.runtime.shared_memo import memo_governed
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.runtime_preparation import _without_stored_filesystem_interrupts
from app.agent_runtime.runtime_preparation_support import RuntimePreparationBindings
from app.agent_runtime.runtime_snapshot_catalog import SnapshotCatalogBuilder
from app.storage.paths import resolve_data_path

_STORED_SKILL_RULES = (
    "\n\n## 스킬 사용 규칙\n"
    "스킬을 사용할 때는 반드시 read_file 도구로 SKILL.md를 먼저 읽고 "
    "그 안의 지시를 직접 따르세요. "
    "task 도구의 subagent_type에 스킬 이름을 넣지 마세요. "
    "task 도구를 사용할 때 subagent_type은 task 도구 설명에 표시된 "
    "available subagent types 중 하나여야 합니다."
)

# Deep Agents' ``DeltaChannel`` state channels (``messages``, filesystem
# ``files``): a resume/continuation replays their history, so the seed captures
# it in the preparation window. Pinned to the compiled graph by a test.
DELTA_CHANNELS: tuple[str, ...] = ("files", "messages")


@dataclass(frozen=True, slots=True)
class PreparedChildSource:
    """One stored linked child, resolved under the locks."""

    runtime_name: str
    display_name: str
    description: str
    base_prompt: str
    model: LeaseModelSpec
    tool_configs: tuple[Mapping[str, Any], ...] = ()
    middleware_configs: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class PreparedRunSources:
    cfg: AgentConfig
    model: LeaseModelSpec
    messages_history: Sequence[RuntimeMessage]
    data_dir: Path
    run_root: Path
    children: tuple[PreparedChildSource, ...] = ()
    memory_prompt: str = ""
    is_trigger_mode: bool = False
    secret_reference_ids: tuple[str, ...] = ()
    versions: Mapping[str, str] | None = None


def _stored(cfg: AgentConfig) -> bool:
    return cfg.runtime_policy.source == "stored"


def _write_allowed(cfg: AgentConfig) -> bool:
    return not _stored(cfg) or cfg.runtime_policy.effective.filesystem.mode == "artifact_write"


def _prompt(base: str, cfg: AgentConfig, bindings: RuntimePreparationBindings) -> str:
    prompt = bindings.system_prompt_with_temporal_context(base)
    if _write_allowed(cfg):
        prompt += "\n\n" + bindings.artifact_file_instruction_prompt(cfg.thread_id)
    return prompt


def _freeze_skills(cfg: AgentConfig, builder: SnapshotCatalogBuilder) -> str | None:
    if not cfg.agent_skills:
        return None
    owner = f"agents/{cfg.agent_runtime_name}/" if cfg.agent_runtime_name else ""
    prefix = f"/runtime/{cfg.thread_id}/{owner}skills"
    for skill in cfg.agent_skills:
        slug, storage = str(skill.get("slug") or ""), str(skill.get("storage_path") or "")
        source = resolve_data_path(storage) if storage else None
        if not slug or source is None:
            raise unsupported(f"skill_source_missing:{slug}")
        if source.is_file():
            builder.add_file(
                f"{prefix}/{slug}/SKILL.md", root=source.parent, source=source, kind="skill"
            )
        elif source.is_dir():
            builder.add_tree(f"{prefix}/{slug}", root=source, kind="skill")
        else:
            raise unsupported(f"skill_source_missing:{slug}")
    return f"{prefix}/"


def _freeze_agents_md(cfg: AgentConfig, data_dir: Path, builder: SnapshotCatalogBuilder) -> list:
    if not cfg.agent_id or memo_governed():  # F-09: governed memo is prompt-only
        return []
    source = data_dir / "agents" / cfg.agent_id / "AGENTS.md"
    if not source.is_file():
        return []
    virtual = f"/agents/{cfg.agent_id}/AGENTS.md"
    builder.add_file(virtual, root=data_dir, source=source, kind="memory")
    return [virtual]


def _interrupts(
    configs: Any, tools: PreparedToolSet, *, trigger: bool, stored: bool, bindings: Any
) -> dict[str, Any] | None:
    policy = bindings.build_interrupt_on_policy(
        list(configs or []) or None,
        list(tools.tools),
        include_ask_user=any(tool.name == "ask_user" for tool in tools.tools),
        is_trigger_mode=trigger,
    )
    return _without_stored_filesystem_interrupts(policy) if stored else policy


def _child_specs(
    sources: PreparedRunSources, bindings: RuntimePreparationBindings
) -> tuple[list[dict[str, Any]], dict[str, frozenset[str]], list[PreparedToolSet]]:
    cfg = sources.cfg
    specs: list[dict[str, Any]] = []
    policy: dict[str, frozenset[str]] = {}
    sets: list[PreparedToolSet] = []
    for child in sources.children:
        subject = f"child:{child.runtime_name}:"
        tools = prepared_tool_set(
            child.tool_configs,
            create_tool=bindings.create_tool_for_runtime,
            allow_mcp=False,
            stored_policy=_stored(cfg),
            subject=subject,
        )
        spec: dict[str, Any] = {
            "name": child.runtime_name,
            "description": child.description,
            "system_prompt": _prompt(child.base_prompt, cfg, bindings),
            "model": child.model,
            "tools": list(tools.tools),
        }
        interrupts = _interrupts(
            child.middleware_configs,
            tools,
            trigger=sources.is_trigger_mode,
            stored=_stored(cfg),
            bindings=bindings,
        )
        if interrupts is not None:
            spec["interrupt_on"] = interrupts
        specs.append(spec)
        policy[f"root/{child.runtime_name}"] = tools.names
        sets.append(tools)
    return specs, policy, sets


def build_preparation_inputs(
    sources: PreparedRunSources, *, bindings: RuntimePreparationBindings
) -> PreparationInputs:
    """Return complete lease inputs; raises ``PreparedRunUnsupportedError`` early."""

    cfg = sources.cfg
    trigger = sources.is_trigger_mode
    builder = SnapshotCatalogBuilder()
    skills_prefix = _freeze_skills(cfg, builder)
    memory = _freeze_agents_md(cfg, sources.data_dir, builder)
    prompt = _prompt(cfg.system_prompt, cfg, bindings)
    if not trigger:
        prompt += "\n\n" + bindings.interactive_tool_instruction_prompt()
    if skills_prefix is not None:
        prompt += _STORED_SKILL_RULES
        block = bindings.build_skills_prompt(cfg.agent_skills or [])
        if block:
            prompt += "\n" + block.replace("/skills/", skills_prefix)
    if sources.memory_prompt:
        prompt += "\n\n" + sources.memory_prompt
    configs = [
        *cfg.tools_config,
        *bindings.build_skill_dependency_tool_configs(
            agent_skills=cfg.agent_skills or [],
            existing_tool_configs=cfg.tools_config,
            user_id=cfg.user_id,
            agent_id=cfg.agent_id,
        ),
    ]
    root_tools = prepared_tool_set(
        configs,
        create_tool=bindings.create_tool_for_runtime,
        allow_mcp=True,
        stored_policy=_stored(cfg),
        ask_user_tool=None if trigger else bindings.ask_user_tool,
    )
    children, child_tools, child_sets = _child_specs(sources, bindings)
    output = sources.run_root / "output"
    output.mkdir(parents=True, exist_ok=True)
    conversation_prefix = f"/conversations/{cfg.thread_id}"
    # Same write surface as the stored policy: inspect mode gets no write route.
    writes = _write_allowed(cfg)
    return PreparationInputs(
        thread_id=cfg.thread_id,
        system_prompt=prompt,
        messages=tuple(convert_to_langchain_messages(sources.messages_history)),
        model=sources.model,
        snapshot_catalog=builder.build(),
        run_root=sources.run_root,
        writable_roots={conversation_prefix: output} if writes else {},
        forbidden_root=sources.data_dir,
        artifacts_root=conversation_prefix if writes else None,
        tools=root_tools.tools,
        tool_origins=merged_origins([root_tools, *child_sets]),
        mcp_configs=root_tools.mcp_configs,
        subagents=tuple(children),
        custom_middleware=(EmptyContentRetryMiddleware(max_retries=1),),
        mcp_interrupts=not trigger,
        child_tools=child_tools,
        interrupt_on=_interrupts(
            cfg.middleware_configs,
            root_tools,
            trigger=trigger,
            stored=_stored(cfg),
            bindings=bindings,
        ),
        skills=None if skills_prefix is None else [skills_prefix],
        memory=memory or None,
        checkpoint_id=cfg.checkpoint_id,
        delta_channels=DELTA_CHANNELS,
        versions=dict(sources.versions or {}),
        secret_values=frozenset(cfg.secret_values),
        secret_reference_ids=tuple(
            sorted(
                {
                    *sources.secret_reference_ids,
                    *root_tools.secret_reference_ids,
                    *(ref for tool_set in child_sets for ref in tool_set.secret_reference_ids),
                }
            )
        ),
    )
