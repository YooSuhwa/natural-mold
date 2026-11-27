"""Public prompts, card data and answer schemas for ordinary chat interrupts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from app.agent_runtime.memory_event_projection import MEMORY_TOOL_NAMES
from app.agent_runtime.protocol_egress import project_and_redact_protocol_data
from app.agent_runtime.tools.ask_user import AskUserInput

type JsonObject = dict[str, Any]

_DECISIONS = ("approve", "reject", "edit", "respond")
# Tool parameters are user-facing, but protocol internals are never input to a review card.
_PRIVATE_FIELDS = frozenset(
    {
        "raw",
        "rawevent",
        "rawstate",
        "rawpayload",
        "rawproviderpayload",
        "checkpoint",
        "checkpointid",
        "checkpointns",
        "checkpointmap",
        "reasoning",
        "reasoningcontent",
        "metadata",
        "langgraphcheckpointns",
        "thinking",
        "systemprompt",
        "systemmessage",
        "responsemetadata",
        "additionalkwargs",
        "providermetadata",
        "internalstate",
        "privatestate",
        "agentstate",
        "graphstate",
        "traceback",
        "stacktrace",
    }
)


def _private_block(value: object) -> bool:
    return isinstance(value, Mapping) and value.get("type") in (
        "thinking",
        "reasoning",
        "redacted_thinking",
        "analysis",
        "signature",
    )


def _public_parameters(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _public_parameters(item)
            for key, item in value.items()
            if isinstance(key, str)
            and not _private_block(item)
            and not key.startswith("_")
            and key.casefold().replace("_", "").replace("-", "") not in _PRIVATE_FIELDS
        }
    if isinstance(value, list):
        return [_public_parameters(item) for item in value if not _private_block(item)]
    return value


def _preview(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _action_summary(action: JsonObject) -> str:
    name = _preview(action["name"], 80)
    details = action.get("description")
    if not details and action["args"]:
        details = json.dumps(action["args"], ensure_ascii=False, separators=(", ", ": "))
    return f"{name} — {_preview(details, 160)}" if details else name


def _object(properties: JsonObject, *required: str) -> JsonObject:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _answers(schemas: list[JsonObject]) -> JsonObject:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **_object(
            {
                "decisions": {
                    "type": "array",
                    "minItems": len(schemas),
                    "maxItems": len(schemas),
                    "prefixItems": schemas,
                    "items": False,
                }
            },
            "decisions",
        ),
    }


def _decision(kind: str, name: str, *, session_consent: bool) -> JsonObject:
    fields: JsonObject = {"type": {"const": kind}}
    required = ["type"]
    if kind == "approve" and session_consent:
        fields["scope"] = {"const": "session"}
    elif kind == "edit":
        fields["edited_action"] = _object(
            {"name": {"const": name}, "args": {"type": "object"}}, "args"
        )
        required.append("edited_action")
    elif kind in ("respond", "reject"):
        fields["message"] = {"type": "string"}
        if kind == "respond":
            required.append("message")
    return _object(fields, *required)


def _prompt(reason: str, message: str, data: JsonObject, schemas: list[JsonObject]) -> JsonObject:
    return {
        "reason": reason,
        "message": message,
        "metadata": {"moldy": data},
        "response_schema": _answers(schemas),
    }


def _ask_args(value: Mapping[str, Any]) -> JsonObject | None:
    try:
        return AskUserInput.model_validate(value, strict=True).model_dump(
            mode="json", exclude_none=True
        )
    except ValidationError:
        return None


def _ask(value: Mapping[str, Any]) -> JsonObject | None:
    args = _ask_args(value)
    if args is None:
        return None
    message = args.get("title") or args.get("question") or "질문에 답해주세요."
    return _prompt(
        "moldy.ask_user",
        message,
        {"type": "ask_user", **args},
        [_decision("respond", "ask_user", session_consent=False)],
    )


def _auth_item(value: object) -> JsonObject | None:
    if not isinstance(value, Mapping):
        return None
    name = value.get("resource_name")
    if not isinstance(name, str) or not name:
        return None
    item: JsonObject = {"resource_name": name}
    if value.get("resource_type") in ("tool", "mcp_server"):
        item["resource_type"] = value["resource_type"]
    names = value.get("tool_names")
    if isinstance(names, list):
        item["tool_names"] = [name for name in names if isinstance(name, str)]
    # The client interprets a missing fixable_by as "me". Unknown ownership must
    # therefore be kept non-actionable, including for records from older versions.
    item["fixable_by"] = "me" if value.get("fixable_by") == "me" else "owner"
    if item["fixable_by"] == "me":
        credential = value.get("credential_id")
        if isinstance(credential, str) and credential:
            item["credential_id"] = credential
        connect = value.get("connect")
        if isinstance(connect, Mapping) and connect.get("kind") in ("oauth", "api_key", "binding"):
            item["connect"] = {"kind": connect["kind"]}
    return item


def _auth(value: Mapping[str, Any]) -> JsonObject | None:
    attempt = value.get("attempt", 1)
    allowed = value.get("allowed_decisions", ["respond"])
    if (
        type(attempt) is not int
        or attempt < 1
        or not isinstance(allowed, list)
        or "respond" not in allowed
    ):
        return None
    items = value.get("items")
    safe_items = [_auth_item(item) for item in items] if isinstance(items, list) else []
    reason = value.get("reason")
    data = {
        "type": "auth_required",
        "reason": reason
        if reason in ("expired", "scope_upgrade", "auth_needed")
        else "auth_needed",
        "attempt": attempt,
        "items": [item for item in safe_items if item is not None],
    }
    request = value.get("auth_request_id")
    if isinstance(request, str) and request:
        data["auth_request_id"] = request
    answer = _object(
        {
            "type": {"const": "respond"},
            "message": {"enum": ["retry", "skip", "cancel"]},
            "attempt": {"type": "integer", "const": attempt},
        },
        "type",
        "message",
        "attempt",
    )
    resources = list(dict.fromkeys(item["resource_name"] for item in data["items"]))
    target = _preview(", ".join(resources), 160)
    message = "계정 연결을 확인하고 계속할 방법을 선택해주세요."
    if target:
        message = f"{target} 계정 연결을 확인하고 계속할 방법을 선택해주세요."
    return _prompt("moldy.auth_required", message, data, [answer])


def _matching_config(configs: list[Any], index: int, name: str) -> Mapping[str, Any] | None:
    config = configs[index] if index < len(configs) else None
    if isinstance(config, Mapping) and config.get("action_name") == name:
        return config
    return next(
        (c for c in configs if isinstance(c, Mapping) and c.get("action_name") == name), None
    )


def _approvals(value: Mapping[str, Any]) -> JsonObject | None:
    actions, configs = value.get("action_requests"), value.get("review_configs")
    if not isinstance(actions, list) or not actions or not isinstance(configs, list):
        return None

    public_actions: list[JsonObject] = []
    public_configs: list[JsonObject] = []
    schemas: list[JsonObject] = []
    for index, action in enumerate(actions):
        if not isinstance(action, Mapping):
            return None
        name, args = action.get("name"), action.get("args")
        if not isinstance(name, str) or not name or not isinstance(args, Mapping):
            return None
        config = _matching_config(configs, index, name)
        if config is None or not isinstance(allowed := config.get("allowed_decisions"), list):
            return None
        kinds = [kind for kind in _DECISIONS if kind in allowed]
        if not kinds:
            return None
        session_consent = config.get("session_consent_eligible") is True and "approve" in kinds
        review: JsonObject = {"action_name": name, "allowed_decisions": kinds}
        if session_consent:
            review["session_consent_eligible"] = True
        public_configs.append(review)
        if name == "ask_user":
            safe_args = _ask_args(args)
            if safe_args is None:
                return None
        else:
            safe_args = project_and_redact_protocol_data(
                "tools", {"name": name, "args": _public_parameters(args)}
            )["args"]
        public_action = {"name": name, "args": safe_args}
        description = action.get("description")
        # LangChain's default description embeds the original repr of all arguments.
        # A public description must never reintroduce masked parameter values.
        if (
            isinstance(description, str)
            and description
            and "\nArgs:" not in description
            and "<redacted>" not in json.dumps(safe_args, ensure_ascii=False)
            and safe_args == args
            and name not in MEMORY_TOOL_NAMES
        ):
            public_action["description"] = description
        public_actions.append(public_action)
        choices = [_decision(kind, name, session_consent=session_consent) for kind in kinds]
        schemas.append(choices[0] if len(choices) == 1 else {"oneOf": choices})
    summary = _preview(" / ".join(_action_summary(a) for a in public_actions), 640)
    return _prompt(
        "moldy.tool_approval",
        f"도구 실행 요청을 검토해주세요. {summary}",
        {"action_requests": public_actions, "review_configs": public_configs},
        schemas,
    )


def interrupt_fields(value: object) -> JsonObject:
    """Return only protocol-owned fields the current client can safely act on."""
    if isinstance(value, Mapping):
        data = project_and_redact_protocol_data("input.requested", value)
        kind = data.get("type")
        if kind == "workflow_wait":
            return {
                "reason": "moldy.workflow_wait",
                "message": "워크플로가 끝나면 대화를 이어갑니다.",
            }
        if kind == "ask_user":
            fields = _ask(data)
        elif kind == "auth_required":
            fields = _auth(data)
        else:
            fields = _approvals(data) if kind is None else None
        if fields is not None:
            return fields
    return {
        "reason": "moldy.input_required",
        "message": "작업을 계속하려면 추가 입력이 필요합니다.",
    }
