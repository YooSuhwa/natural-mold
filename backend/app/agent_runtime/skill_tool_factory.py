from __future__ import annotations

from pathlib import Path

from langchain_core.tools import StructuredTool

from app.agent_runtime.skill_executor import execute_skill_script
from app.config import settings


def create_skill_tools(
    skill_id: str,
    skill_dir: str,
    timeout: int = 30,
    conversation_id: str | None = None,
    output_dir: str | None = None,
) -> list[StructuredTool]:
    """Create LangChain tools for a package skill (run_command + read_skill_file)."""
    suffix = skill_id[:8]
    skill_path = Path(skill_dir).resolve()

    if conversation_id:
        file_base_url = f"/api/conversations/{conversation_id}/files"
    else:
        file_base_url = f"/api/skills/{skill_id}/files/_outputs"

    async def run_command(command: str) -> str:
        """Run a python command inside the skill directory."""
        try:
            result = await execute_skill_script(
                skill_dir,
                command,
                script_timeout=timeout,
                output_dir=output_dir,
            )
        except ValueError as exc:
            return f"Error: {exc}"
        lines: list[str] = []
        if result.stdout:
            lines.append(result.stdout)
        if result.stderr:
            lines.append(f"[stderr] {result.stderr}")
        if result.return_code != 0:
            lines.append(f"[exit code: {result.return_code}]")
        # Convert output file paths to serving URLs (markdown-ready)
        for fname in result.output_files:
            url = f"{file_base_url}/{fname}"
            lines.append(f"[output file] {url}")
            lines.append(f"![{fname}]({url})")
        return "\n".join(lines) or "(no output)"

    async def read_skill_file(file_path: str) -> str:
        """Read a file from the skill package directory."""
        target = (skill_path / file_path).resolve()
        if not target.is_relative_to(skill_path):
            return "Error: path traversal not allowed"
        if not target.is_file():
            return f"Error: file not found — {file_path}"
        size = target.stat().st_size
        max_bytes = settings.skill_max_output_bytes
        if size > max_bytes:
            return f"Error: file too large ({size} bytes, max {max_bytes})"
        return target.read_text(encoding="utf-8", errors="replace")

    return [
        StructuredTool.from_function(
            coroutine=run_command,
            name=f"run_command_{suffix}",
            description=(
                "Run a python command in the skill directory. "
                "Only 'python ...' commands are allowed. "
                "Output files are saved to _outputs/ and returned as URLs."
            ),
        ),
        StructuredTool.from_function(
            coroutine=read_skill_file,
            name=f"read_skill_file_{suffix}",
            description=(
                "Read a text file from the skill package. "
                "Provide a relative path (e.g. 'scripts/main.py', 'references/data.md')."
            ),
        ),
    ]
