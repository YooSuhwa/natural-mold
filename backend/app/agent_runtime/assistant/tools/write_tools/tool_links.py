"""Assistant 쓰기 도구 — 도구/MCP 도구 연결 그룹 (add/remove)."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from sqlalchemy import func, select

from app.agent_runtime.assistant.tools.write_tools.context import (
    WriteToolContext,
    get_agent_with_session,
)
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.tool import AgentToolLink, Tool


def build_tool_link_tools(ctx: WriteToolContext) -> list[StructuredTool]:
    """도구/MCP 도구 연결 도구 4개를 생성한다."""

    # ------ 1. add_tool_to_agent ------

    async def add_tool_to_agent(tool_names: list[str]) -> str:
        """에이전트에 도구를 추가합니다 (배치 지원).

        Args:
            tool_names: 추가할 도구 이름 목록
        """
        async with ctx.session_factory() as session:
            agent = await get_agent_with_session(ctx, session)
            if not agent:
                return "에이전트를 찾을 수 없습니다."

            existing = {link.tool.name.lower() for link in agent.tool_links}
            lower_names = [n.lower() for n in tool_names if n.lower() not in existing]
            if not lower_names:
                return "모든 도구가 이미 추가되어 있습니다."

            result = await session.execute(
                select(Tool).where(
                    Tool.visible_to(ctx.user_id),
                    func.lower(Tool.name).in_(lower_names),
                )
            )
            found_tools = list(result.scalars().all())
            if not found_tools:
                return f"도구를 찾을 수 없습니다: {', '.join(tool_names)}"

            for t in found_tools:
                agent.tool_links.append(AgentToolLink(tool_id=t.id))
            await session.commit()

            added = [t.name for t in found_tools]
            return f"도구 추가 완료: {', '.join(added)}"

    # ------ 2. remove_tool_from_agent ------

    async def remove_tool_from_agent(tool_names: list[str]) -> str:
        """에이전트에서 도구를 제거합니다 (배치 지원).

        Args:
            tool_names: 제거할 도구 이름 목록
        """
        async with ctx.session_factory() as session:
            agent = await get_agent_with_session(ctx, session)
            if not agent:
                return "에이전트를 찾을 수 없습니다."

            lower_names = {n.lower() for n in tool_names}
            removed = []
            for link in agent.tool_links:
                if link.tool.name.lower() in lower_names:
                    removed.append(link.tool.name)
                    await session.delete(link)
            if not removed:
                return f"해당 도구가 에이전트에 없습니다: {', '.join(tool_names)}"

            await session.commit()
            return f"도구 제거 완료: {', '.join(removed)}"

    # ------ 2-1. add_mcp_tool_to_agent ------

    async def add_mcp_tool_to_agent(mcp_tool_names: list[str]) -> str:
        """에이전트에 MCP 도구를 추가합니다 (배치 지원).

        Args:
            mcp_tool_names: 추가할 MCP 도구 이름 목록 (서버명 아님 — 도구명).
        """
        async with ctx.session_factory() as session:
            agent = await get_agent_with_session(ctx, session)
            if not agent:
                return "에이전트를 찾을 수 없습니다."

            existing = {link.mcp_tool.name.lower() for link in agent.mcp_tool_links}
            lower_names = [n.lower() for n in mcp_tool_names if n.lower() not in existing]
            if not lower_names:
                return "모든 MCP 도구가 이미 추가되어 있습니다."

            # MCP 도구는 사용자 소유 server에 묶여 있음 → server.user_id 필터.
            result = await session.execute(
                select(McpTool)
                .join(McpServer, McpServer.id == McpTool.server_id)
                .where(
                    McpServer.user_id == ctx.user_id,
                    func.lower(McpTool.name).in_(lower_names),
                )
            )
            found = list(result.scalars().all())
            if not found:
                return f"MCP 도구를 찾을 수 없습니다: {', '.join(mcp_tool_names)}"

            for mt in found:
                agent.mcp_tool_links.append(AgentMcpToolLink(mcp_tool_id=mt.id))
            await session.commit()

            added = [mt.name for mt in found]
            return f"MCP 도구 추가 완료: {', '.join(added)}"

    # ------ 2-2. remove_mcp_tool_from_agent ------

    async def remove_mcp_tool_from_agent(mcp_tool_names: list[str]) -> str:
        """에이전트에서 MCP 도구를 제거합니다 (배치 지원).

        Args:
            mcp_tool_names: 제거할 MCP 도구 이름 목록.
        """
        async with ctx.session_factory() as session:
            agent = await get_agent_with_session(ctx, session)
            if not agent:
                return "에이전트를 찾을 수 없습니다."

            lower_names = {n.lower() for n in mcp_tool_names}
            removed = []
            for link in agent.mcp_tool_links:
                if link.mcp_tool.name.lower() in lower_names:
                    removed.append(link.mcp_tool.name)
                    await session.delete(link)
            if not removed:
                return f"해당 MCP 도구가 에이전트에 없습니다: {', '.join(mcp_tool_names)}"

            await session.commit()
            return f"MCP 도구 제거 완료: {', '.join(removed)}"

    return [
        StructuredTool.from_function(
            coroutine=add_tool_to_agent,
            name="add_tool_to_agent",
            description="에이전트에 도구 배치 추가",
        ),
        StructuredTool.from_function(
            coroutine=remove_tool_from_agent,
            name="remove_tool_from_agent",
            description="에이전트에서 도구 배치 제거",
        ),
        StructuredTool.from_function(
            coroutine=add_mcp_tool_to_agent,
            name="add_mcp_tool_to_agent",
            description="에이전트에 MCP 도구 배치 추가 (도구 이름으로)",
        ),
        StructuredTool.from_function(
            coroutine=remove_mcp_tool_from_agent,
            name="remove_mcp_tool_from_agent",
            description="에이전트에서 MCP 도구 배치 제거",
        ),
    ]
