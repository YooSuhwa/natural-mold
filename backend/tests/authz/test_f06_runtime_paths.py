"""F-06 / 16.2 "런타임 경로 테스트": the 13 run paths of plan 11.0 and the boundary guard.

Each 11.0 row either reaches ``prepare_authorized_runtime`` (directly or through a carrier
that calls it) or is a classified separate path whose decisions live elsewhere (owning
task named). Outside the boundary, ``build_tools_config``, ``build_agent``,
``_build_mcp_tools``, ``build_agent_skills`` and ``run_model_test`` may only be called from
the listed sites, each with its reason; a new call site fails here.
"""

import ast
from pathlib import Path
from typing import NamedTuple

import pytest

APP = Path(__file__).resolve().parents[2] / "app"
BOUNDARY = "prepare_authorized_runtime"
# The boundary's entry points (``plan_`` + ``bind_`` are its two steps).
BOUNDARY_CALLS = {BOUNDARY, "plan_authorized_runtime", "bind_authorized_runtime"}
# Functions whose bodies enter the boundary; run paths may reach it through them.
# The separately authorized 11.0 rows (F-13): each entry decides before it binds.
SEPARATE = {
    "get_agent_with_eager_load": (
        "agent_runtime/assistant/tools/helpers.py",
        "assistant_target_scope",
    ),
    "load_skill_builder_run_session": (
        "services/skill_builder_run_access.py",
        "authorize_skill_builder_run",
    ),
    "authorize_skill_builder_run": (
        "services/skill_builder_run_access.py",
        "authorize_builder_target",
    ),
    "authorize": ("services/skill_evaluation_access.py", "require"),
    "authorize_binding": ("mcp/apps_binding_store.py", "linked_tool"),
    "probe_registered_model": ("services/model_test_run.py", "model_test_credential"),
    "probe_preview_model": ("services/model_test_run.py", "load_owned_credential"),
}
CARRIERS = {
    "resolve_agent_context": "services/conversation_agent_context.py",
    "build_agent_config_for_loaded_agent": "services/agent_invocation_service.py",
    "build_subagents_config": "agent_runtime/subagents.py",
    "resolve_prepared_children": "services/prepared_run_children.py",
    "claim_trigger_run": "agent_runtime/trigger_execution_claim.py",
    "trigger_config_resolver": "services/prepared_trigger_resolver.py",
}


class Path11(NamedTuple):
    modules: tuple[str, ...]  # entry modules of the 11.0 row
    reaches: str | None  # a carrier/boundary they call; None: classified separately
    note: str = ""


RAC = "resolve_agent_context"
PATHS: dict[str, Path11] = {  # plan 11.0, in table order
    "chat": Path11(("routers/chat_run_entries.py", "routers/conversation_protocol_start.py"), RAC),
    "edit/regenerate": Path11(("routers/conversation_branch_entries.py",), RAC),
    "resume": Path11(
        ("routers/chat_resume_entries.py", "routers/conversation_protocol_fork.py"), RAC
    ),
    "conversation queue": Path11(("services/conversation_run_queue_launch.py",), RAC),
    "trigger": Path11(
        ("agent_runtime/trigger_execution_claim.py", "services/prepared_trigger_resolver.py"),
        BOUNDARY,
    ),
    "API key": Path11(("services/agent_invocation_service.py",), BOUNDARY),
    "subagents": Path11(
        ("agent_runtime/subagents.py", "services/prepared_run_children.py"), BOUNDARY
    ),
    "Assistant": Path11(
        ("agent_runtime/assistant/assistant_agent.py",),
        "get_agent_with_eager_load",
        "11.7 separate authorized run: can_edit at build and per tool call (D-08, F-13)",
    ),
    "skill builder hidden agent": Path11(
        ("services/conversation_agent_context.py", "services/skill_builder_run_access.py"),
        "load_skill_builder_run_session",
        "inside resolve_agent_context: session owner + target can_edit/cap_create_skill (F-13)",
    ),
    "skill evaluation": Path11(
        ("services/skill_evaluation_worker_state.py", "services/skill_evaluation_access.py"),
        "SkillEvaluationAccessPlanner",
        "F-13: creator's skill can_edit when the run executes, own bindings, redaction",
    ),
    "MCP Apps": Path11(
        ("routers/mcp_apps.py", "services/mcp_apps_service.py"),
        "authorize_binding",
        "per-command can_invoke (D-08) + F-05 credential re-resolution per call (F-13)",
    ),
    "model test": Path11(
        ("routers/models.py", "services/model_test_run.py"),
        "probe_registered_model",
        "F-13: model can_use + credential can_use before the probe",
    ),
    "E2E MCP Apps": Path11(("routers/e2e_mcp_apps_helpers.py",), None, "e2e_test_helpers only"),
}
GUARDED = {
    "build_tools_config",
    "build_agent",
    "_build_mcp_tools",
    "build_agent_skills",
    "run_model_test",
}
ALLOWED: dict[tuple[str, str], str] = {
    ("services/authorized_runtime.py", "build_tools_config"): "the boundary",
    ("services/authorized_runtime.py", "build_agent_skills"): "the boundary",
    ("agent_runtime/assistant/assistant_agent.py", "build_agent"): "11.7 Assistant",
    ("routers/e2e_mcp_apps_helpers.py", "_build_mcp_tools"): "E2E fixed path",
    ("services/chat/secrets.py", "build_tools_config"): "read-only response masking",
    ("services/chat/trigger_blocked_tools.py", "build_tools_config"): "trigger probe (plan)",
    ("services/chat/trigger_blocked_tools.py", "build_agent_skills"): "trigger probe (plan)",
    ("agent_runtime/trigger_execution_materialization.py", "build_agent_skills"): (
        "claim without boundary skills (legacy callers)"
    ),
    ("services/model_test_run.py", "run_model_test"): "F-13 model test after its decisions",
    ("services/health_check.py", "run_model_test"): "operator health probe",
}


def _called(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _source(module: str) -> ast.AST:
    return ast.parse((APP / module).read_text())


def offenders(sources: dict[str, str]) -> list[tuple[str, str]]:
    found = []
    for module, text in sources.items():
        for name in sorted(_called(ast.parse(text)) & GUARDED):
            if (module, name) not in ALLOWED:
                found.append((module, name))
    return found


def test_the_table_has_the_13_rows_of_plan_11_0() -> None:
    assert len(PATHS) == 13


def _reached(module: str) -> set[str]:
    """Calls, defined functions and facade lookups by name (``facade_attr("...")``)."""
    tree = _source(module)
    found = _called(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            found.add(node.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.add(node.value)
    return found


@pytest.mark.parametrize("name", list(PATHS))
def test_every_run_path_reaches_the_boundary_or_is_classified(name: str) -> None:
    row = PATHS[name]
    for module in row.modules:
        assert (APP / module).is_file(), module
        if row.reaches is None:
            assert row.note, f"{name}: a separately authorized path names where it is decided"
        else:
            wanted = BOUNDARY_CALLS if row.reaches == BOUNDARY else {row.reaches}
            assert wanted & _reached(module), f"{name}: {module} skips {row.reaches}"


def _body(module: str, name: str) -> ast.AST:
    return next(
        n
        for n in ast.walk(_source(module))
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == name
    )


@pytest.mark.parametrize("entry", sorted(SEPARATE))
def test_separate_paths_decide_before_binding(entry: str) -> None:
    module, decision = SEPARATE[entry]
    assert decision in _called(_body(module, entry)), f"{entry} skips {decision}"


@pytest.mark.parametrize("carrier", sorted(CARRIERS))
def test_carriers_enter_the_boundary(carrier: str) -> None:
    tree = _source(CARRIERS[carrier])
    body = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == carrier
    )
    assert BOUNDARY_CALLS & _called(body), f"{carrier} bypasses {BOUNDARY}"


def test_guarded_builders_are_only_called_from_listed_sites() -> None:
    sources = {
        str(path.relative_to(APP)): path.read_text()
        for path in APP.rglob("*.py")
        if "__pycache__" not in path.parts
    }
    assert offenders(sources) == []
    stale = [site for site in ALLOWED if site not in {(m, n) for m in sources for n in GUARDED}]
    assert stale == []


def test_the_guard_flags_a_new_bypass() -> None:
    bypass = "async def run(a):\n    return await chat_service.build_tools_config(a)\n"
    assert offenders({"services/new_path.py": bypass}) == [
        ("services/new_path.py", "build_tools_config")
    ]


def test_the_e2e_fixed_path_is_mounted_only_with_e2e_helpers() -> None:
    registry = (APP / "router_registry.py").read_text()
    guard = registry.index("if settings.e2e_test_helpers_enabled:")
    assert registry.index("e2e_mcp_apps_helpers.router") > guard
