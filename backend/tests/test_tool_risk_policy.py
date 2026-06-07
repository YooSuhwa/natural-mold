from __future__ import annotations

from pathlib import Path

import pytest

from app.agent_runtime.mcp_tool_loader import _create_mcp_error_stub
from app.agent_runtime.skill_executor import _create_skill_execute_tool
from app.agent_runtime.skill_tool_dependencies import build_skill_dependency_tool_configs
from app.agent_runtime.tool_factory import create_builtin_tool, create_tool_for_runtime
from app.marketplace.skill_runtime import SkillToolContext
from app.tools.risk import ToolRiskLevel, get_tool_risk, mcp_tool_risk, risk_from_tool_config


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


def test_tavily_search_is_read_only_and_trigger_safe():
    risk = risk_from_tool_config({"definition_key": "tavily_search"})

    assert risk.risk_level == ToolRiskLevel.READ_ONLY
    assert risk.requires_approval is False
    assert risk.trigger_safe is True


def test_skill_dependency_resolver_rejects_unsupported_dependency():
    with pytest.raises(ValueError, match="Unsupported skill tool dependency: unknown_tool"):
        build_skill_dependency_tool_configs(
            agent_skills=[
                {"execution_profile": {"tool_dependencies": ["unknown_tool"]}},
            ],
            existing_tool_configs=[],
            user_id="user-1",
            agent_id="agent-1",
        )


def test_mcp_tool_defaults_to_external_mutation_metadata():
    tool = _create_mcp_error_stub("remote_send")

    risk = get_tool_risk(tool)
    assert risk.risk_level == ToolRiskLevel.EXTERNAL_MUTATION
    assert risk.requires_approval is True
    assert risk.trigger_safe is False


def test_mcp_tool_read_only_annotation_skips_approval():
    risk = mcp_tool_risk(
        "budget_status",
        metadata={"readOnlyHint": True},
        config={"mcp_server_url": "http://localhost:18003/mcp"},
    )

    assert risk.risk_level == ToolRiskLevel.READ_ONLY
    assert risk.requires_approval is False
    assert risk.trigger_safe is True


def test_untrusted_mcp_read_only_annotation_still_requires_approval():
    risk = mcp_tool_risk(
        "budget_status",
        metadata={"readOnlyHint": True},
        config={"mcp_server_url": "https://example.invalid/mcp"},
    )

    assert risk.risk_level == ToolRiskLevel.EXTERNAL_MUTATION
    assert risk.requires_approval is True
    assert risk.trigger_safe is False


def test_first_party_hancom_gw_get_tool_is_read_only():
    risk = risk_from_tool_config(
        {
            "definition_key": "mcp",
            "name": "get_budget",
            "description": "부서 예산 현황 조회. 계정별 예산/집행/잔액 및 집행률.",
            "mcp_server_url": "https://hancom-gw-mcp.apps.orca.cloud.hancom.com/mcp",
        }
    )

    assert risk.risk_level == ToolRiskLevel.READ_ONLY
    assert risk.requires_approval is False
    assert risk.trigger_safe is True


def test_untrusted_mcp_get_tool_still_requires_approval():
    risk = risk_from_tool_config(
        {
            "definition_key": "mcp",
            "name": "get_budget",
            "description": "부서 예산 현황 조회",
            "mcp_server_url": "https://example.invalid/mcp",
        }
    )

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
