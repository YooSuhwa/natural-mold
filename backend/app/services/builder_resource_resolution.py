"""Builder draft tool-name resolution (split from ``builder_service``)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate, resource_candidates
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.skill import Skill
from app.models.tool import Tool


async def _resolve_tools(
    db: AsyncSession,
    context: OrgContext,
    tool_names: list[str],
) -> tuple[list[Tool], list[McpTool], list[Skill]]:
    """이름 목록 → (Tool, McpTool, Skill) 3-way 분리 반환.

    Builder phase3 카탈로그 (``get_tools_catalog``) 는 ``Tool`` + ``McpTool`` +
    ``Skill`` 모두 노출하므로 phase8 confirm 도 세 테이블을 모두 매칭해야 한다.

    매칭 우선순위는 ``Tool > McpTool > Skill``. 같은 이름이 여러 테이블에
    있으면 더 명시적으로 등록된 쪽 (Tool 우선) 을 선택. ``McpTool`` 은
    ``McpServer.user_id`` ownership 필터, ``Skill`` 은 ``Skill.user_id``
    ownership 필터로 cross-user 링킹 차단.
    """
    if not tool_names:
        return [], [], []
    lower_names = [n.lower() for n in tool_names]

    tool_result = await db.execute(
        resource_candidates(
            select(Tool).where(func.lower(Tool.name).in_(lower_names)), Tool, context
        )
    )
    tools = list(tool_result.scalars().all())
    matched = {t.name.lower() for t in tools}

    remaining = [n for n in lower_names if n not in matched]
    if not remaining:
        return tools, [], []

    mcp_result = await db.execute(
        select(McpTool)
        .join(McpServer, McpServer.id == McpTool.server_id)
        .where(
            private_scope_predicate(McpServer, context),
            func.lower(McpTool.name).in_(remaining),
        )
    )
    mcp_tools = list(mcp_result.scalars().all())
    matched.update(mt.name.lower() for mt in mcp_tools)

    remaining = [n for n in remaining if n not in matched]
    if not remaining:
        return tools, mcp_tools, []

    skill_result = await db.execute(
        select(Skill).where(
            private_scope_predicate(Skill, context),
            func.lower(Skill.name).in_(remaining),
        )
    )
    skills = list(skill_result.scalars().all())
    return tools, mcp_tools, skills
