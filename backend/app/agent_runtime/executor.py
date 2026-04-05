from __future__ import annotations

import asyncio
import logging
import shlex
import sys
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, StructuredTool

from app.agent_runtime.message_utils import convert_to_langchain_messages
from app.agent_runtime.middleware_registry import (
    build_middleware_instances,
    get_provider_middleware,
)
from app.agent_runtime.model_factory import create_chat_model
from app.agent_runtime.streaming import stream_agent_response
from app.agent_runtime.tool_factory import (
    create_builtin_tool,
    create_prebuilt_tool,
    create_tool_from_db,
)

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def _create_skill_execute_tool(output_dir: Path) -> BaseTool:
    """스킬 디렉토리에서 Python 스크립트를 실행하는 도구를 생성."""

    async def execute_in_skill(skill_directory: str, command: str) -> str:
        """스킬 디렉토리에서 Python 스크립트를 실행합니다.

        Args:
            skill_directory: 스킬 디렉토리의 가상 경로 (예: /skills/146ecc62.../)
            command: 실행할 명령어 (예: python scripts/mark_seat.py search 이상윤)
        """
        resolved = (_DATA_DIR / skill_directory.strip("/")).resolve()
        if not resolved.is_relative_to(_DATA_DIR.resolve()) or not resolved.is_dir():
            return f"Error: invalid skill directory: {skill_directory}"

        args = shlex.split(command)
        if not args or args[0] != "python":
            return "Error: only python commands are allowed."
        args[0] = sys.executable

        await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
        out = str(output_dir)
        env = {
            "PATH": "/usr/bin:/usr/local/bin",
            "PYTHONPATH": str(resolved),
            "HOME": str(resolved),
            "SKILL_OUTPUT_DIR": out,
            "OUTPUTS_DIR": out,
        }

        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(resolved),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return "Error: script execution timed out (30s)."

        result = stdout.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace")
            result += f"\nSTDERR: {err}"

        # 출력 파일 수집
        def _collect_outputs() -> list[str]:
            if output_dir.exists():
                return [f.name for f in output_dir.iterdir() if f.is_file()]
            return []

        files = await asyncio.to_thread(_collect_outputs)
        if files:
            result += "\n\nOUTPUT_FILES: " + ", ".join(files)

        return result

    return StructuredTool.from_function(
        coroutine=execute_in_skill,
        name="execute_in_skill",
        description=(
            "Execute a Python script inside a skill directory. "
            "Use this when SKILL.md instructs you to run a script. "
            "Output files (images etc.) will be in OUTPUT_FILES."
        ),
    )


def build_agent(
    model: BaseChatModel,
    tools: list[BaseTool],
    system_prompt: str,
    *,
    middleware: list | None = None,
    checkpointer: Any | None = None,
    store: Any | None = None,
    backend: Any | None = None,
    skills: list[str] | None = None,
    memory: list[str] | None = None,
    name: str | None = None,
) -> Any:
    """Build a deep agent. Returns CompiledStateGraph."""
    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=middleware or (),
        checkpointer=checkpointer,
        store=store,
        backend=backend,
        skills=skills,
        memory=memory,
        name=name,
    )


# ---------------------------------------------------------------------------
# MCP tool helpers — langchain-mcp-adapters
# ---------------------------------------------------------------------------


def _auth_config_to_headers(auth_config: dict | None) -> dict[str, str]:
    """auth_config를 HTTP 헤더로 변환."""
    if not auth_config:
        return {}
    if "headers" in auth_config:
        return auth_config["headers"]
    token = auth_config.get("jwt_token") or auth_config.get("api_key")
    if token:
        header_name = auth_config.get("header_name", "Authorization")
        if auth_config.get("jwt_token"):
            return {"Authorization": f"Bearer {token}"}
        return {header_name: token}
    return {}


def _url_to_server_key(url: str) -> str:
    """MCP 서버 URL을 고유 키로 변환 (호스트 + 경로 포함)."""
    parsed = urlparse(url)
    key = parsed.netloc + parsed.path.rstrip("/")
    return key.replace(".", "_").replace(":", "_").replace("/", "_")


def _create_mcp_error_stub(name: str) -> BaseTool:
    """MCP 서버 연결 실패 시 에러를 반환하는 stub 도구."""
    from langchain_core.tools import StructuredTool

    async def _call(**kwargs: Any) -> str:
        return f"MCP tool '{name}' is temporarily unavailable. Please try again later."

    return StructuredTool.from_function(
        coroutine=_call,
        name=name,
        description=f"MCP tool (currently unavailable): {name}",
    )


async def _build_mcp_tools(mcp_configs: list[dict]) -> list[BaseTool]:
    """MCP 도구를 langchain-mcp-adapters로 생성."""
    if not mcp_configs:
        return []

    from langchain_mcp_adapters.client import MultiServerMCPClient

    # 1. MCP 서버 URL별로 그룹화 — 서버 키 기준으로 도구 필터링
    servers: dict[str, dict] = {}
    tool_filter: dict[str, set[str]] = {}  # server_key → {tool_names}

    for tc in mcp_configs:
        url = tc["mcp_server_url"]
        tool_name = tc.get("mcp_tool_name", tc["name"])
        key = _url_to_server_key(url)

        if key not in servers:
            headers = _auth_config_to_headers(tc.get("auth_config"))
            servers[key] = {
                "transport": "streamable_http",
                "url": url,
                "headers": headers or None,
            }
            tool_filter[key] = set()

        tool_filter[key].add(tool_name)

    # 2. 서버별로 도구 로딩 + 필터링 — (tool, origin) 쌍으로 추적
    collected: list[tuple[BaseTool, str]] = []

    for key, config in servers.items():
        try:
            client = MultiServerMCPClient({key: config})
            server_tools = await client.get_tools()
            needed = tool_filter[key]
            for t in server_tools:
                if t.name in needed:
                    collected.append((t, key))
        except Exception:
            logger.warning("MCP tool loading failed for %s", key, exc_info=True)
            for tool_name in tool_filter[key]:
                collected.append((_create_mcp_error_stub(tool_name), key))

    # 3. 중복 이름 disambiguation — 서버 키를 prefix로 추가
    name_counts: dict[str, int] = {}
    for tool, _ in collected:
        name_counts[tool.name] = name_counts.get(tool.name, 0) + 1

    if any(c > 1 for c in name_counts.values()):
        for tool, origin in collected:
            if name_counts.get(tool.name, 0) > 1:
                tool.name = f"{origin}_{tool.name}"

    return [tool for tool, _ in collected]


async def execute_agent_stream(
    provider: str,
    model_name: str,
    api_key: str | None,
    base_url: str | None,
    system_prompt: str,
    tools_config: list[dict[str, Any]],
    messages_history: list[dict[str, str]],
    thread_id: str,
    model_params: dict[str, Any] | None = None,
    middleware_configs: list[dict[str, Any]] | None = None,
    agent_skills: list[dict] | None = None,
    agent_id: str | None = None,
) -> AsyncGenerator[str, None]:
    model = create_chat_model(provider, model_name, api_key, base_url, **(model_params or {}))

    # 1. 도구 생성 — builtin/prebuilt/custom은 기존 방식 유지
    langchain_tools: list[BaseTool] = []
    mcp_configs: list[dict] = []

    for tc in tools_config:
        tool_type = tc.get("type")
        if tool_type == "builtin":
            langchain_tools.append(create_builtin_tool(tc["name"]))
        elif tool_type == "prebuilt":
            langchain_tools.append(
                create_prebuilt_tool(tc["name"], auth_config=tc.get("auth_config"))
            )
        elif tool_type == "custom" and tc.get("api_url"):
            langchain_tools.append(
                create_tool_from_db(
                    name=tc["name"],
                    description=tc.get("description"),
                    api_url=tc["api_url"],
                    http_method=tc.get("http_method", "GET"),
                    parameters_schema=tc.get("parameters_schema"),
                    auth_type=tc.get("auth_type"),
                    auth_config=tc.get("auth_config"),
                )
            )
        elif tool_type == "mcp" and tc.get("mcp_server_url"):
            mcp_configs.append(tc)

    # 2. MCP 도구 — langchain-mcp-adapters 사용
    mcp_tools = await _build_mcp_tools(mcp_configs)
    langchain_tools.extend(mcp_tools)

    # 3. 미들웨어 — 기존 방식 유지
    middleware = build_middleware_instances(middleware_configs or [])
    middleware += get_provider_middleware(provider)

    # 4. Backend + Skills + Memory 구성
    backend = FilesystemBackend(root_dir=str(_DATA_DIR), virtual_mode=True)

    skills_sources: list[str] | None = None
    if agent_skills:
        skills_sources = ["/skills/"]
        # 스킬 스크립트 실행 도구 추가 — 출력은 대화 세션 폴더에 저장 (절대경로)
        conv_output_dir = (_DATA_DIR / "conversations" / thread_id).resolve()
        langchain_tools.append(_create_skill_execute_tool(conv_output_dir))
        system_prompt += (
            "\n\n## 스킬 사용 규칙\n"
            "스킬을 사용할 때는 반드시 read_file 도구로 SKILL.md를 먼저 읽고 "
            "그 안의 지시를 직접 따르세요. "
            "스크립트 실행이 필요하면 execute_in_skill 도구를 사용하세요. "
            "task 도구의 subagent_type에 스킬 이름을 넣지 마세요. "
            "유일하게 허용된 subagent_type은 'general-purpose'입니다.\n"
            "스크립트 실행 후 OUTPUT_FILES에 이미지가 있으면 "
            "![image](/api/conversations/" + thread_id + "/files/<파일명>) 형식으로 표시하세요."
        )

    memory_sources: list[str] | None = None
    if agent_id:
        (_DATA_DIR / "agents" / agent_id).mkdir(parents=True, exist_ok=True)
        memory_sources = [f"/agents/{agent_id}/AGENTS.md"]

    # 5. 에이전트 빌드 — create_deep_agent + checkpointer
    from app.agent_runtime.checkpointer import get_checkpointer

    agent = build_agent(
        model,
        langchain_tools,
        system_prompt,
        middleware=middleware or None,
        checkpointer=get_checkpointer(),
        backend=backend,
        skills=skills_sources,
        memory=memory_sources,
        name=f"agent_{thread_id[:8]}",
    )

    # 6. 스트리밍
    lc_messages = convert_to_langchain_messages(messages_history)
    config = {"configurable": {"thread_id": thread_id}}

    async for chunk in stream_agent_response(agent, lc_messages, config):
        yield chunk
