"""Builder v3 — frontend Tool UI와 backend 노드가 공유하는 매직 스트링 상수.

frontend `lib/chat/tool-ui-registry.ts` 와 동기화 필요 (변경 시 둘 다 수정).
"""

from __future__ import annotations


class ToolNames:
    """Tool UI registry의 tool_name 상수."""

    PHASE_TIMELINE = "phase_timeline"
    ASK_USER = "ask_user"
    RECOMMENDATION_APPROVAL = "recommendation_approval"
    PROMPT_APPROVAL = "prompt_approval"
    IMAGE_CHOICE = "image_choice"
    IMAGE_APPROVAL = "image_approval"
    DRAFT_CONFIG_CARD = "draft_config_card"
    DRAFT_APPROVAL = "draft_approval"
