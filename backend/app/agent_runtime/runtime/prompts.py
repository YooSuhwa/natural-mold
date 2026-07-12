"""시스템 프롬프트 블록 빌더 (BE-S10 분리)."""

from __future__ import annotations

from app.agent_runtime.temporal import build_temporal_context_prompt


def _system_prompt_with_temporal_context(system_prompt: str) -> str:
    block = build_temporal_context_prompt().strip()
    prompt = system_prompt.strip()
    return f"{prompt}\n\n{block}" if prompt else block


def _memory_tool_instruction_prompt() -> str:
    return (
        "## Long-term Memory Tool Rules\n"
        "- If the user explicitly asks you to remember, save, or persist a durable "
        "preference or fact, call `propose_memory`, `save_user_memory`, or "
        "`save_agent_memory` instead of only describing what you would do.\n"
        "- Use `propose_memory` when you are unsure whether the memory should be "
        "user-wide or agent-specific; use `save_user_memory` for user-wide "
        "preferences and `save_agent_memory` for this agent's operating notes.\n"
        "- The server enforces the user's memory policy. In ask mode, save tools "
        "create an approval proposal rather than directly storing the memory.\n"
        "- Do not claim a memory was saved unless a memory tool result says "
        "`memory_saved`. If the tool reports `memory_proposed`, tell the user it "
        "is waiting for approval.\n"
        "- Never store API keys, passwords, tokens, credentials, or government ID "
        "numbers. Ordinary test labels or preference IDs are not secrets by "
        "themselves."
    )


def _interactive_tool_instruction_prompt() -> str:
    return (
        "## Interactive Tool Rules\n"
        "- If the user explicitly asks you to ask the user, use ask_user, "
        "let them choose, or pick from options, call the `ask_user` tool. "
        "Do not answer with plain text that only describes asking.\n"
        "- For a single-choice option request, call `ask_user` with "
        '`mode="option_list"`, a concise title, the requested options, '
        "`minSelections=1`, and `maxSelections=1`.\n"
        "- If the user explicitly asks to use an available tool or MCP tool, "
        "call the matching tool instead of simulating the tool result in text.\n"
        "- If a tool requires HITL approval, wait for the approval result before "
        "claiming that the tool ran or that the requested side effect happened."
    )


def _artifact_file_instruction_prompt(thread_id: str) -> str:
    return (
        "## Generated File Rules\n"
        f"- When the user asks you to create, save, or output a file, call `write_file` "
        f"with an absolute path under `/conversations/{thread_id}/`.\n"
        f"- Example: `/conversations/{thread_id}/report.md` or "
        f"`/conversations/{thread_id}/charts/summary.csv`.\n"
        "- Do not use `/tmp`, `/runtime`, `/skills`, or `/agents` for user-visible "
        "generated files; those paths are not shown as chat artifacts and may be rejected.\n"
        "- After a file tool succeeds, briefly tell the user the file is ready. "
        "Do not claim the file was saved if the tool result reports an error."
    )
