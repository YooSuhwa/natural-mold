"""Assistant 쓰기 도구 — DB 수정 도구 (Verify First).

도구 목록:
1. add_tool_to_agent (배치)
2. remove_tool_from_agent (배치)
3. add_middleware_to_agent (배치)
4. remove_middleware_from_agent (배치)
5. add_subagent_to_agent (배치)
6. remove_subagent_from_agent (배치)
7. add_skill_to_agent (배치)
8. remove_skill_from_agent (배치)
9. edit_system_prompt (부분 수정)
10. update_system_prompt (전체 교체)
11. update_model_config
12. update_middleware_config
13. update_chat_openers
14. update_agent_metadata
15. update_agent_identity_mode
16. update_recursion_limit
17. create_cron_schedule
18. update_cron_schedule
19. delete_cron_schedule
20. enable_cron_schedule
21. disable_cron_schedule
"""

from __future__ import annotations

import uuid

from langchain_core.tools import StructuredTool
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.assistant.tools.write_tools.agent_config import build_agent_config_tools
from app.agent_runtime.assistant.tools.write_tools.composition import build_composition_tools
from app.agent_runtime.assistant.tools.write_tools.context import WriteToolContext
from app.agent_runtime.assistant.tools.write_tools.cron import build_cron_tools
from app.agent_runtime.assistant.tools.write_tools.tool_links import build_tool_link_tools
from app.database import async_session as async_session_factory


def build_write_tools(
    db: AsyncSession,  # noqa: ARG001 — kept for interface compatibility
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
) -> list[StructuredTool]:
    """Assistant 쓰기 도구 18개를 생성한다.

    각 도구는 호출 시마다 fresh DB 세션을 생성하여 사용한다.
    LangGraph 에이전트의 도구 실행은 빌드 시점의 클로저 DB 세션이
    이미 닫혀 있을 수 있으므로, 매 호출마다 새 세션을 열어야 안전하다.
    """
    # call-time global lookup: 테스트가 이 모듈의 `async_session_factory`를
    # monkeypatch하므로, import 시점 바인딩이 아니라 호출 시점에 모듈 전역을 읽어
    # 컨텍스트에 주입한다. 그룹 모듈은 ctx.session_factory만 사용해야 한다.
    ctx = WriteToolContext(
        session_factory=async_session_factory,
        agent_id=agent_id,
        user_id=user_id,
    )
    return [
        *build_tool_link_tools(ctx),
        *build_composition_tools(ctx),
        *build_agent_config_tools(ctx),
        *build_cron_tools(ctx),
    ]
