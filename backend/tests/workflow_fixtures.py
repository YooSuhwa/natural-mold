"""The customer-inquiry workflow (workflow-plan Q3) used by the W1 tests."""

from __future__ import annotations

import copy
from typing import Any

CLASSIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": ["refund", "bug", "other"]},
        "urgent": {"type": "boolean"},
    },
    "required": ["category", "urgent"],
}


def ref(node: str, path: str = "") -> dict[str, Any]:
    return {"ref": {"node": node, "path": path}}


def text(value: str) -> dict[str, Any]:
    return {"text": value}


def inquiry_workflow(agent_id: str) -> dict[str, Any]:
    return copy.deepcopy(
        {
            "schema_version": "moldy.workflow.v1",
            "inputs": [{"name": "inquiry", "type": "string", "max_length": 4000}],
            "nodes": [
                {"id": "start", "type": "start"},
                {
                    "id": "classify",
                    "type": "agent",
                    "agent_id": agent_id,
                    "prompt": [text("다음 문의를 분류하세요: "), ref("input", "/inquiry")],
                    "output_schema": CLASSIFY_SCHEMA,
                },
                {
                    "id": "is_urgent",
                    "type": "decision_bool",
                    "condition": {
                        "all": [
                            {"source": "classify", "path": "/urgent", "op": "eq", "value": True}
                        ]
                    },
                },
                {
                    "id": "review",
                    "type": "approval",
                    "title": [text("긴급 문의: "), ref("classify", "/category")],
                    "timeout_minutes": 1440,
                },
                {
                    "id": "draft",
                    "type": "transform",
                    "template": [text("분류: "), ref("classify", "/category")],
                },
                {
                    "id": "done",
                    "type": "output",
                    "fields": {
                        "category": [ref("classify", "/category")],
                        "note": [ref("draft", "/text")],
                    },
                },
                {"id": "fail", "type": "fail", "reason": "거절됨"},
            ],
            "edges": [
                {"from": "start", "to": "classify"},
                {"from": "classify", "to": "is_urgent"},
                {"from": "is_urgent", "to": "review", "when": "true"},
                {"from": "is_urgent", "to": "draft", "when": "false"},
                {"from": "review", "to": "draft", "when": "approved"},
                {"from": "review", "to": "fail", "when": "rejected"},
                {"from": "draft", "to": "done"},
            ],
        }
    )


def node(definition: dict[str, Any], node_id: str) -> dict[str, Any]:
    return next(item for item in definition["nodes"] if item["id"] == node_id)
