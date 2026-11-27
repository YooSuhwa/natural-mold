"""Prepared snapshots must not restore forbidden nested child capabilities."""

from pathlib import Path
from uuid import uuid4

import pytest

from app.agent_runtime.lease_model_factory import LeaseModelSpec
from app.agent_runtime.prepared_run_input_builder import (
    PreparedChildSource,
    PreparedRunSources,
    build_preparation_inputs,
)
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.runtime_preparation_bindings import _runtime_preparation_bindings
from tests.authz.unit_scope_fixture import unit_context


@pytest.mark.parametrize("mode", ["chat", "independent_job", "goal_followup"])
def test_prepared_control_descriptors_follow_admitted_children(tmp_path: Path, mode: str) -> None:
    child_id = uuid4()
    model = LeaseModelSpec(provider="openai", model_name="synthetic", api_key="fixture")
    config = AgentConfig(
        provider="openai",
        model_name=model.model_name,
        base_url=None,
        api_key=None,
        org_context=unit_context(uuid4()),
        system_prompt="",
        tools_config=[],
        thread_id=str(uuid4()),
        goal_followup=mode == "goal_followup",
        independent_job=mode == "independent_job",
    )
    sources = PreparedRunSources(
        cfg=config,
        model=model,
        messages_history=[],
        data_dir=tmp_path / "data",
        run_root=tmp_path / "run",
        # Present a genuine linked source, even for previously prepared inputs.
        children=(
            PreparedChildSource("researcher", "Researcher", "", "", model, agent_id=child_id),
        ),
        track_child_tasks=True,
    )

    inputs = build_preparation_inputs(sources, bindings=_runtime_preparation_bindings())

    if mode == "chat":
        assert [child["name"] for child in inputs.subagents] == ["researcher"]
        assert inputs.child_task_agents == {"root/researcher": child_id}
        assert set(inputs.child_tools) == {"root/researcher"}
    else:
        assert not inputs.subagents
        assert not inputs.child_task_agents
        assert not inputs.child_tools
