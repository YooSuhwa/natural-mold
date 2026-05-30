from __future__ import annotations

from pathlib import Path

from app.agent_runtime.executor import _create_mcp_error_stub, _create_skill_execute_tool
from app.agent_runtime.tool_factory import create_builtin_tool, create_tool_for_runtime
from app.marketplace.skill_runtime import SkillToolContext
from app.tools.risk import ToolRiskLevel, get_tool_risk


def test_builtin_read_only_tool_has_read_only_risk_metadata():
    tool = create_builtin_tool("builtin:web_search")

    assert tool is not None
    risk = get_tool_risk(tool)
    assert risk.risk_level == ToolRiskLevel.READ_ONLY
    assert risk.requires_approval is False
    assert risk.trigger_safe is True


def test_registry_mutation_tool_has_external_mutation_metadata():
    tool = create_tool_for_runtime(
        {
            "definition_key": "gmail_send",
            "name": "Gmail Send",
            "parameters": {},
        }
    )

    assert tool is not None
    risk = get_tool_risk(tool)
    assert risk.risk_level == ToolRiskLevel.EXTERNAL_MUTATION
    assert risk.requires_approval is True
    assert risk.trigger_safe is False


def test_mcp_tool_defaults_to_external_mutation_metadata():
    tool = _create_mcp_error_stub("remote_send")

    risk = get_tool_risk(tool)
    assert risk.risk_level == ToolRiskLevel.EXTERNAL_MUTATION
    assert risk.requires_approval is True
    assert risk.trigger_safe is False


def test_execute_in_skill_has_code_execution_metadata(tmp_path: Path):
    ctx = SkillToolContext(
        thread_id="thread-1",
        output_dir=tmp_path / "outputs",
        runtime_root=tmp_path / "skills",
        descriptors={},
    )

    tool = _create_skill_execute_tool(ctx)
    risk = get_tool_risk(tool)
    assert risk.risk_level == ToolRiskLevel.CODE_EXECUTION
    assert risk.requires_approval is True
    assert risk.trigger_safe is False
