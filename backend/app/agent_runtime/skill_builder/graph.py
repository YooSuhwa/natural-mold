from __future__ import annotations

import json
import re
from typing import Protocol, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from app.agent_runtime.skill_builder.state import SkillBuilderState
from app.schemas.skill_builder import JsonValue, SkillBuilderMode, SkillDraftPackage
from app.services.skill_builder_changelog import build_revision_changelog
from app.skills.service import slugify
from app.skills.validator import validate_draft_package


class SkillBuilderDraftWorker(Protocol):
    async def draft(self, state: SkillBuilderState) -> SkillDraftPackage: ...


class HeuristicDraftWorker:
    async def draft(self, state: SkillBuilderState) -> SkillDraftPackage:
        intent = _intent_summary(state)
        name = _skill_name(intent)
        slug = slugify(name)
        skill_md = _skill_md(name=name, slug=slug, intent=intent)
        return SkillDraftPackage(
            name=name,
            slug=slug,
            description=f"Use when {intent[:180]}",
            files=[
                {"path": "SKILL.md", "content": skill_md, "role": "skill"},
                {
                    "path": "agents/openai.yaml",
                    "content": f'interface:\n  default_prompt: "${slug}"\n',
                    "role": "metadata",
                },
            ],
            credential_requirements=[],
            execution_profile={"requires_network": False},
        )


class JsonChatDraftWorker:
    def __init__(
        self,
        model: BaseChatModel,
        *,
        fallback: SkillBuilderDraftWorker | None = None,
    ) -> None:
        self.model = model
        self.fallback = fallback

    async def draft(self, state: SkillBuilderState) -> SkillDraftPackage:
        response = await self.model.ainvoke(
            [
                SystemMessage(content=_json_worker_prompt()),
                HumanMessage(content=json.dumps(_json_worker_input(state), ensure_ascii=False)),
            ]
        )
        try:
            return SkillDraftPackage.model_validate_json(_json_payload(_message_text(response)))
        except ValidationError:
            if self.fallback is None:
                raise
            return await self.fallback.draft(state)


def compile_skill_builder_graph(
    *,
    draft_worker: SkillBuilderDraftWorker | None = None,
    checkpointer: object | None = None,
):
    worker = draft_worker or HeuristicDraftWorker()
    graph = StateGraph(SkillBuilderState)
    graph.add_node("collect_intent", _collect_intent)
    graph.add_node("draft_package", _draft_package_node(worker))
    graph.add_node("validate_package", _validate_package)
    graph.add_node("generate_changelog", _generate_changelog)
    graph.add_node("review_response", _review_response)
    graph.add_edge(START, "collect_intent")
    graph.add_edge("collect_intent", "draft_package")
    graph.add_edge("draft_package", "validate_package")
    graph.add_edge("validate_package", "generate_changelog")
    graph.add_edge("generate_changelog", "review_response")
    graph.add_edge("review_response", END)
    return graph.compile(checkpointer=checkpointer)


async def run_skill_builder_graph(
    *,
    state: SkillBuilderState,
    draft_worker: SkillBuilderDraftWorker | None = None,
    checkpointer: object | None = None,
) -> SkillBuilderState:
    graph = compile_skill_builder_graph(draft_worker=draft_worker, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": f"skill_builder_{state['session_id']}"}}
    result = await graph.ainvoke(state, config)
    return cast(SkillBuilderState, result)


def _collect_intent(state: SkillBuilderState) -> dict[str, JsonValue]:
    summary = _intent_summary(state)
    return {
        "intent": {
            "summary": summary,
            "mode": state.get("mode") or SkillBuilderMode.CREATE.value,
        },
        "current_phase": 1,
    }


def _draft_package_node(worker: SkillBuilderDraftWorker):
    async def node(state: SkillBuilderState) -> dict[str, JsonValue]:
        draft = await worker.draft(state)
        return {
            "draft_package": cast(dict[str, JsonValue], draft.model_dump(mode="json")),
            "current_phase": 2,
        }

    return node


def _validate_package(state: SkillBuilderState) -> dict[str, JsonValue]:
    draft = SkillDraftPackage.model_validate(state["draft_package"])
    result = validate_draft_package(
        files=draft.files,
        credential_requirements=draft.credential_requirements,
        execution_profile=draft.execution_profile,
    )
    compatibility = result.get("compatibility_result")
    return {
        "validation_result": cast(dict[str, JsonValue], result),
        "compatibility_result": cast(dict[str, JsonValue], compatibility or {}),
        "current_phase": 3,
    }


def _generate_changelog(state: SkillBuilderState) -> dict[str, JsonValue]:
    draft = SkillDraftPackage.model_validate(state["draft_package"])
    mode = SkillBuilderMode(state.get("mode") or SkillBuilderMode.CREATE.value)
    changelog = build_revision_changelog(
        mode=mode,
        base_snapshot=state.get("base_snapshot"),
        draft=draft,
        provided=None,
    )
    payload: dict[str, JsonValue] = {
        "summary": changelog.summary,
        "items": changelog.items or [],
        "risk_notes": [],
    }
    return {"changelog_draft": payload, "current_phase": 4}


def _review_response(state: SkillBuilderState) -> dict[str, JsonValue]:
    draft = SkillDraftPackage.model_validate(state["draft_package"])
    validation = state.get("validation_result") or {}
    error_count = validation.get("error_count")
    if isinstance(error_count, int) and error_count > 0:
        message = f"Drafted {len(draft.files)} files, but validation found {error_count} errors."
    else:
        message = f"Draft ready with {len(draft.files)} files. Review, validate, then confirm."
    return {"review_message": message, "next_action": "review", "current_phase": 5}


def _intent_summary(state: SkillBuilderState) -> str:
    request = (state.get("user_request") or "").strip()
    if request:
        return request
    messages = state.get("messages") or []
    for message in reversed(messages):
        if isinstance(message, HumanMessage) and isinstance(message.content, str):
            return message.content.strip()
    return "creating a helpful reusable skill"


def _skill_name(intent: str) -> str:
    cleaned = re.sub(r"\s+", " ", intent).strip(" .,/\\")
    if not cleaned:
        return "Custom Skill"
    words = cleaned.split(" ")[:6]
    return " ".join(words).strip()[:80] or "Custom Skill"


def _skill_md(*, name: str, slug: str, intent: str) -> str:
    description = json.dumps(f"Use when {intent[:180]}", ensure_ascii=False)
    return (
        "---\n"
        f"name: {slug}\n"
        f"description: {description}\n"
        "---\n\n"
        f"# {name}\n\n"
        "## Instructions\n"
        f"- Help with this task: {intent}\n"
        "- Ask a brief clarifying question if the request is ambiguous.\n"
        "- Keep outputs concise, actionable, and easy to reuse.\n"
    )


def _json_worker_prompt() -> str:
    return (
        "Return only JSON matching SkillDraftPackage: name, slug, description, "
        "files, credential_requirements, execution_profile."
    )


def _json_worker_input(state: SkillBuilderState) -> dict[str, JsonValue]:
    return {
        "intent": state.get("intent") or {},
        "mode": state.get("mode") or SkillBuilderMode.CREATE.value,
        "base_snapshot": state.get("base_snapshot"),
    }


def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return json.dumps(content)


def _json_payload(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped
