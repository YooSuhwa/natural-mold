"""Per-call preparation collaborators read from the live runtime facade."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from app.agent_runtime.runtime_facade_bindings import runtime_attr
from app.agent_runtime.runtime_hydration_guard import assert_pre_marker
from app.agent_runtime.runtime_preparation_support import (
    RuntimePreparationBindings as _RuntimePreparationBindings,
)
from app.config import settings


def _guarded_credentials(resolve: Any) -> Any:
    async def resolve_runtime_credentials(*args: Any, **kwargs: Any) -> Any:
        assert_pre_marker("resolve_runtime_credentials")
        return await resolve(*args, **kwargs)

    return resolve_runtime_credentials


def _runtime_preparation_bindings() -> _RuntimePreparationBindings:
    """Capture the facade's current preparation collaborators for one call."""

    return _RuntimePreparationBindings(
        data_dir=runtime_attr("_DATA_DIR"),
        conversation_output_dir=Path(settings.conversation_output_dir),
        deepagent_builtin_types=runtime_attr("DEEPAGENT_BUILTIN_TYPES"),
        state_backend=runtime_attr("StateBackend"),
        perf_counter=time.perf_counter,
        system_prompt_with_temporal_context=runtime_attr("_system_prompt_with_temporal_context"),
        artifact_file_instruction_prompt=runtime_attr("_artifact_file_instruction_prompt"),
        interactive_tool_instruction_prompt=runtime_attr("_interactive_tool_instruction_prompt"),
        build_model_candidates=runtime_attr("_build_model_candidates"),
        build_skill_dependency_tool_configs=runtime_attr("build_skill_dependency_tool_configs"),
        create_tool_for_runtime=runtime_attr("create_tool_for_runtime"),
        build_mcp_tools=runtime_attr("_build_mcp_tools"),
        append_temporal_tools=runtime_attr("_append_temporal_tools"),
        append_e2e_scripted_search_tool=runtime_attr("_append_e2e_scripted_search_tool"),
        append_e2e_ui_data_demo_tool=runtime_attr("_append_e2e_ui_data_demo_tool"),
        memory_write_policy_for_run=runtime_attr("_memory_write_policy_for_run"),
        build_memory_tools=runtime_attr("build_memory_tools"),
        resolve_middleware_model_params=runtime_attr("_resolve_middleware_model_params"),
        build_default_reliability_middleware=runtime_attr("_build_default_reliability_middleware"),
        build_middleware_instances=runtime_attr("build_middleware_instances"),
        get_provider_middleware=runtime_attr("get_provider_middleware"),
        scoped_runtime_backend=runtime_attr("_scoped_runtime_backend"),
        build_skill_runtime_context=runtime_attr("build_skill_runtime_context"),
        runtime_db_session_factory=runtime_attr("_runtime_db_session_factory_impl"),
        resolve_runtime_credentials=_guarded_credentials(
            runtime_attr("resolve_runtime_credentials")
        ),
        add_skill_secrets_to_run=runtime_attr("_add_skill_secrets_to_run"),
        create_skill_execute_tool=runtime_attr("_create_skill_execute_tool"),
        build_skills_prompt=runtime_attr("_build_runtime_skills_prompt_impl"),
        memory_tool_instruction_prompt=runtime_attr("_memory_tool_instruction_prompt"),
        load_memory_context=runtime_attr("_load_memory_context"),
        build_filesystem_permissions=runtime_attr("build_filesystem_permissions"),
        build_stored_filesystem_permissions=runtime_attr("build_stored_filesystem_permissions"),
        selected_skill_slugs=runtime_attr("_selected_skill_slugs"),
        ask_user_tool=runtime_attr("ask_user_tool"),
        build_interrupt_on_policy=runtime_attr("_build_interrupt_on_policy"),
        load_skill_builder_prompt=runtime_attr("load_skill_builder_prompt"),
        build_skill_builder_tools=runtime_attr("build_skill_builder_tools"),
        session_consent_eligible_tools=runtime_attr("_SESSION_CONSENT_ELIGIBLE_TOOLS"),
    )
