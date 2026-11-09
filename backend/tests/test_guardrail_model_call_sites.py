"""Every place that builds or resolves a chat model has a reviewed guardrail decision.

The guardrail middleware only sees agent runs (plan §3.4.1). A model call made elsewhere
sends whatever it builds straight to the provider, so a new call site must be reviewed here:
guarded (and how), or left out (and why).
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
MODEL_ENTRY_POINTS = frozenset(
    {
        "create_chat_model",
        "create_chat_model_for_test",
        "create_chat_model_with_fallback",
        "create_lease_chat_model",
        "resolve_system_model",
        "build_skill_builder_chat_model",
    }
)

REVIEWED: dict[str, str] = {
    "agent_runtime/runtime/models.py": "agent runs: GuardrailMiddleware + guarded summarizer",
    "services/followup_service.py": "guarded: _guarded_transcript applies the agent's rules",
    "agent_runtime/assistant/assistant_agent.py": "left out: assistant panel, not an agent run",
    "agent_runtime/skill_builder/agent.py": "left out: skill builder (Moldy's prompts)",
    "services/skill_builder_hidden_agent.py": "left out: skill builder (Moldy's prompts)",
    "routers/skill_builder_support.py": "left out: skill builder availability check",
    "services/conversation_agent_context.py": "left out: skill builder conversation context",
    "agent_runtime/builder/sub_agents/helpers.py": "left out: agent builder (Moldy's prompts)",
    "agent_runtime/builder_v3/image_gen.py": "left out: agent image generation prompt",
    "services/image_service.py": "left out: agent image generation prompt",
    "routers/skill_evaluations_support.py": "left out: skill evaluation availability check",
    "marketplace/evaluation_preparation.py": "left out: skill evaluation (author's test cases)",
    "services/model_test.py": "left out: fixed test prompt, no user content",
    "agent_runtime/prepared_run_inputs.py": "lease runs: a guardrail falls back to default",
    "services/memory_consolidation.py": "left out: the person's own checked memories in, "
    "ids and actions out (plan 15, 5.6)",
    "workflows/ai_edit.py": "left out: workflow AI editing, the person's own request and "
    "draft in (Moldy's prompt), edit commands the server applies and checks out (W5)",
}


def _call_sites() -> set[str]:
    found: set[str] = set()
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name in MODEL_ENTRY_POINTS:
                found.add(path.relative_to(APP).as_posix())
    return found


def test_every_model_call_site_has_a_guardrail_decision() -> None:
    sites = _call_sites()

    unreviewed = sorted(sites - set(REVIEWED))
    assert not unreviewed, (
        f"review guardrail coverage (plan §3.4.1) and add to REVIEWED: {unreviewed}"
    )
    assert not sorted(set(REVIEWED) - sites), "REVIEWED lists call sites that no longer exist"
