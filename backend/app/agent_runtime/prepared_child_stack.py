"""Installed-parity per-child middleware stack for prepared children (I12).

Prepared children are compiled before the materialization marker, so the
stock ``create_deep_agent`` never processes them. This module rebuilds each
child's processed spec exactly as the installed ``deepagents.graph`` does for
declarative subagents and the implicit ``general-purpose`` child: filesystem
middleware with the child's permissions, summarization, tool-call patching,
skills, harness-profile extras, prompt caching, profile middleware exclusion,
inherited ``interrupt_on`` merged with permission interrupts, and profile
prompt/tool-description rewrites. The installed helpers are reused (imported,
never edited); only the order of calls is mirrored. ``head``/``tail`` splice
app wrappers around the spec's own middleware so a nested interceptor stays the
first app tool-call wrapper. A ``_moldy_actor_id`` scopes the base stack's
backend the way the app normalizer scopes declarative children.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from deepagents._excluded_middleware import (
    _apply_excluded_middleware,
    _validate_excluded_middleware_config,
    _verify_excluded_middleware_coverage,
)
from deepagents._models import resolve_model
from deepagents._tools import _apply_tool_description_overrides
from deepagents.graph import (
    _REQUIRED_MIDDLEWARE_CLASSES,
    _REQUIRED_MIDDLEWARE_NAMES,
    _apply_custom_middleware,
    _merge_fs_interrupt_on,
)
from deepagents.middleware._fs_interrupt import _build_interrupt_on_from_permissions
from deepagents.middleware._prompt_caching import append_prompt_caching_middleware
from deepagents.middleware._tool_exclusion import _ToolExclusionMiddleware
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from deepagents.middleware.skills import SkillsMiddleware
from deepagents.middleware.summarization import create_summarization_middleware
from deepagents.profiles.harness.harness_profiles import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    _apply_profile_prompt,
    _harness_profile_for_model,
)
from langchain.agents.middleware import AgentMiddleware

from app.agent_runtime.deep_agent_factory import actor_backend_impl
from app.agent_runtime.offload_storage import GENERAL_PURPOSE_ACTOR
from app.agent_runtime.prepared_subagent_catalog import ACTOR_ID_KEY, PreparedChild

type _Middleware = AgentMiddleware[Any, Any, Any]


def _apply_profile_middleware(
    stack: list[_Middleware],
    profile: HarnessProfile,
    custom: Sequence[_Middleware],
    *,
    core_names: set[str] | None,
    verify: bool,
) -> list[_Middleware]:
    required = {
        "required_classes": _REQUIRED_MIDDLEWARE_CLASSES,
        "required_names": _REQUIRED_MIDDLEWARE_NAMES,
    }
    classes: set[type[_Middleware]] = set()
    names: set[str] = set()
    _validate_excluded_middleware_config(profile, **required)
    matched = {"matched_classes": classes, "matched_names": names}
    stack = _apply_excluded_middleware(stack, profile, **matched)
    stack = _apply_custom_middleware(stack, custom, core_names=core_names)
    stack = _apply_excluded_middleware(stack, profile, **matched)
    if verify:
        _verify_excluded_middleware_coverage(profile, classes, names, **required)
    if profile.excluded_tools:
        stack.append(_ToolExclusionMiddleware(excluded=profile.excluded_tools))
    return stack


def _system_prompt(profile: HarnessProfile, spec: dict[str, Any], general: bool) -> str:
    gp_profile = profile.general_purpose_subagent or GeneralPurposeSubagentProfile()
    if general and gp_profile.system_prompt is not None:
        suffix = profile.system_prompt_suffix
        return gp_profile.system_prompt + ("" if suffix is None else "\n\n" + suffix)
    return _apply_profile_prompt(profile, spec["system_prompt"])


def prepared_child_spec(
    child: PreparedChild,
    *,
    backend: Any,
    interrupt_on: dict[str, Any] | None = None,
    skills: list[str] | None = None,
    parent_middleware: Sequence[_Middleware] = (),
    head: Sequence[_Middleware] = (),
    tail: Sequence[_Middleware] = (),
) -> dict[str, Any]:
    """Return the installed-equivalent processed spec for one prepared child.

    ``interrupt_on`` is the creating level's raw ``interrupt_on``; ``skills``
    and ``parent_middleware`` are the root's and only feed the implicit
    ``general-purpose`` child, as in the installed graph.
    """

    spec = dict(child.spec)
    general = child.origin == "general_purpose"
    actor = spec.pop(ACTOR_ID_KEY, GENERAL_PURPOSE_ACTOR if general else None)
    child_backend = backend if actor is None else actor_backend_impl(backend, actor)
    raw_model = spec["model"]
    model = resolve_model(raw_model)
    profile = _harness_profile_for_model(model, raw_model if isinstance(raw_model, str) else None)
    permissions = spec.get("permissions")
    stack: list[_Middleware] = [
        FilesystemMiddleware(
            backend=child_backend,
            custom_tool_descriptions=profile.tool_description_overrides,
            _permissions=permissions,
        ),
        create_summarization_middleware(model, child_backend),
        PatchToolCallsMiddleware(),
    ]
    child_skills = skills if general else spec.get("skills")
    wants_skills = child_skills is not None if general else bool(child_skills)
    if wants_skills and child_skills is not None:
        stack.append(SkillsMiddleware(backend=child_backend, sources=child_skills))
    core_names = {m.name for m in stack}
    stack.extend(profile.materialize_extra_middleware())
    append_prompt_caching_middleware(stack)
    if general:
        slots = {m.name for m in stack}
        custom = [m for m in parent_middleware if m.name in slots]
    else:
        custom = list(spec.get("middleware", ()))
    stack = _apply_profile_middleware(
        stack,
        profile,
        [*head, *custom, *tail],
        core_names=None if general else core_names,
        verify=not general,
    )
    raw_interrupt = interrupt_on if general else spec.get("interrupt_on", interrupt_on)
    merged = _merge_fs_interrupt_on(
        _build_interrupt_on_from_permissions(permissions or []), raw_interrupt
    )
    tools = _apply_tool_description_overrides(spec["tools"], profile.tool_description_overrides)
    processed = {
        **spec,
        "model": model,
        "tools": tools or [],
        "middleware": stack,
        "system_prompt": _system_prompt(profile, spec, general),
    }
    if merged is not None:
        processed["interrupt_on"] = merged
    return processed
