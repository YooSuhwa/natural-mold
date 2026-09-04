"""Deterministic scripted-model protocol for runtime filesystem policy E2E."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Final

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

RUNTIME_FILESYSTEM_INSPECT_MARKER: Final = "E2E_RUNTIME_FILESYSTEM_INSPECT"
RUNTIME_FILESYSTEM_ARTIFACT_WRITE_MARKER: Final = "E2E_RUNTIME_FILESYSTEM_ARTIFACT_WRITE"
RUNTIME_FILESYSTEM_INSPECT_FINAL_CONTENT: Final = (
    "E2E runtime inspect policy completed with scoped read-only access."
)
RUNTIME_FILESYSTEM_ARTIFACT_NAME: Final = "e2e-runtime-policy-artifact.md"
RUNTIME_FILESYSTEM_ARTIFACT_CONTENT: Final = "E2E runtime artifact draft.\n"
RUNTIME_FILESYSTEM_ARTIFACT_FINAL_CONTENT: Final = "E2E runtime artifact final content.\n"

_CONVERSATION_DIR_RE: Final = re.compile(r"/conversations/([0-9a-f-]{36})/")
_RUNTIME_THREAD_ID_RE: Final = re.compile(r"/runtime/([0-9a-f-]{36})/")
_RUNTIME_SKILL_FILE_RE: Final = re.compile(
    r"(?P<path>/runtime/[0-9a-f-]{36}/(?:agents/[a-zA-Z0-9_-]+/)?skills/[a-zA-Z0-9_-]+/SKILL\.md)"
)


def runtime_filesystem_policy_message(
    messages: Sequence[BaseMessage],
    human_text: str,
) -> AIMessage | None:
    """Return the fixed filesystem-policy E2E tool sequence for an explicit marker."""
    if RUNTIME_FILESYSTEM_INSPECT_MARKER in human_text:
        return _runtime_filesystem_inspect_message(messages)
    if RUNTIME_FILESYSTEM_ARTIFACT_WRITE_MARKER in human_text:
        return _runtime_filesystem_artifact_write_message(messages)
    return None


def _runtime_filesystem_inspect_message(messages: Sequence[BaseMessage]) -> AIMessage:
    skill_file = _runtime_skill_file(messages)
    workspace = _conversation_workspace(messages)
    if skill_file is None or workspace is None:
        return AIMessage(content="E2E runtime inspect fixture setup was unavailable.")
    skill_directory = skill_file.removesuffix("/SKILL.md")
    seen = _tool_message_ids(messages)
    if "call_e2e_runtime_inspect_ls" not in seen:
        return _tool_call_message("call_e2e_runtime_inspect_ls", "ls", {"path": skill_directory})
    if "call_e2e_runtime_inspect_glob" not in seen:
        return _tool_call_message(
            "call_e2e_runtime_inspect_glob",
            "glob",
            {"pattern": "SKILL.md", "path": skill_directory},
        )
    if "call_e2e_runtime_inspect_grep" not in seen:
        return _tool_call_message(
            "call_e2e_runtime_inspect_grep",
            "grep",
            {"pattern": RUNTIME_FILESYSTEM_INSPECT_MARKER, "path": skill_directory},
        )
    if "call_e2e_runtime_inspect_read" not in seen:
        return _tool_call_message(
            "call_e2e_runtime_inspect_read",
            "read_file",
            {"file_path": skill_file},
        )
    if "call_e2e_runtime_inspect_write_denied" not in seen:
        return _tool_call_message(
            "call_e2e_runtime_inspect_write_denied",
            "write_file",
            {
                "file_path": f"{workspace}/inspect-denied.md",
                "content": "E2E inspect write must be denied.",
            },
        )
    if "call_e2e_runtime_inspect_sibling_escape" not in seen:
        return _tool_call_message(
            "call_e2e_runtime_inspect_sibling_escape",
            "read_file",
            {"file_path": f"{skill_directory}/../sibling/SKILL.md"},
        )
    return AIMessage(content=RUNTIME_FILESYSTEM_INSPECT_FINAL_CONTENT)


def _runtime_filesystem_artifact_write_message(messages: Sequence[BaseMessage]) -> AIMessage:
    workspace = _conversation_workspace(messages)
    if workspace is None:
        return AIMessage(content="E2E runtime artifact fixture setup was unavailable.")
    artifact_path = f"{workspace}/{RUNTIME_FILESYSTEM_ARTIFACT_NAME}"
    seen = _tool_message_ids(messages)
    if "call_e2e_runtime_artifact_write" not in seen:
        return _tool_call_message(
            "call_e2e_runtime_artifact_write",
            "write_file",
            {"file_path": artifact_path, "content": RUNTIME_FILESYSTEM_ARTIFACT_CONTENT},
        )
    if "call_e2e_runtime_artifact_edit" not in seen:
        return _tool_call_message(
            "call_e2e_runtime_artifact_edit",
            "edit_file",
            {
                "file_path": artifact_path,
                "old_string": RUNTIME_FILESYSTEM_ARTIFACT_CONTENT,
                "new_string": RUNTIME_FILESYSTEM_ARTIFACT_FINAL_CONTENT,
            },
        )
    if "call_e2e_runtime_artifact_read" not in seen:
        return _tool_call_message(
            "call_e2e_runtime_artifact_read",
            "read_file",
            {"file_path": artifact_path},
        )
    if "call_e2e_runtime_artifact_ls" not in seen:
        return _tool_call_message(
            "call_e2e_runtime_artifact_ls",
            "ls",
            {"path": workspace},
        )
    if "call_e2e_runtime_artifact_root_escape" not in seen:
        return _tool_call_message(
            "call_e2e_runtime_artifact_root_escape",
            "ls",
            {"path": "/"},
        )
    return AIMessage(content=RUNTIME_FILESYSTEM_ARTIFACT_FINAL_CONTENT)


def _tool_call_message(call_id: str, name: str, args: Mapping[str, str]) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"id": call_id, "name": name, "args": dict(args)}])


def _conversation_workspace(messages: Sequence[BaseMessage]) -> str | None:
    thread_id = _match_message_content(messages, _CONVERSATION_DIR_RE)
    if thread_id is None:
        skill_file = _runtime_skill_file(messages)
        match = _RUNTIME_THREAD_ID_RE.search(skill_file) if skill_file else None
        thread_id = match.group(1) if match else None
    return f"/conversations/{thread_id}" if thread_id is not None else None


def _runtime_skill_file(messages: Sequence[BaseMessage]) -> str | None:
    """Extract the one scoped skill file injected into a deterministic E2E prompt."""
    return _match_message_content(messages, _RUNTIME_SKILL_FILE_RE, group="path")


def _match_message_content(
    messages: Sequence[BaseMessage],
    pattern: re.Pattern[str],
    *,
    group: str | int = 1,
) -> str | None:
    for message in messages:
        content = message.content if isinstance(message.content, str) else str(message.content)
        match = pattern.search(content)
        if match:
            return match.group(group)
    return None


def _tool_message_ids(messages: Sequence[BaseMessage]) -> set[str]:
    return {
        message.tool_call_id
        for message in messages
        if isinstance(message, ToolMessage) and message.tool_call_id
    }


__all__ = [
    "RUNTIME_FILESYSTEM_ARTIFACT_CONTENT",
    "RUNTIME_FILESYSTEM_ARTIFACT_FINAL_CONTENT",
    "RUNTIME_FILESYSTEM_ARTIFACT_NAME",
    "RUNTIME_FILESYSTEM_ARTIFACT_WRITE_MARKER",
    "RUNTIME_FILESYSTEM_INSPECT_FINAL_CONTENT",
    "RUNTIME_FILESYSTEM_INSPECT_MARKER",
    "runtime_filesystem_policy_message",
]
