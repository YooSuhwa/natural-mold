from __future__ import annotations

from typing import Any

import httpx
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from app.config import settings


# ---------------------------------------------------------------------------
# Google Chat Webhook
# ---------------------------------------------------------------------------

class GoogleChatSendArgs(BaseModel):
    text: str = Field(description="전송할 메시지 텍스트")


def build_google_chat_webhook_tool(
    auth_config: dict[str, Any] | None = None,
) -> BaseTool:
    """Build a LangChain tool that sends messages via Google Chat Webhook."""

    async def send_message(text: str) -> str:
        webhook_url = (auth_config or {}).get("webhook_url") or settings.google_chat_webhook_url

        if not webhook_url:
            return (
                "Error: Google Chat Webhook URL이 설정되지 않았습니다. "
                ".env 파일에 GOOGLE_CHAT_WEBHOOK_URL을 설정하거나 "
                "도구의 auth_config에 webhook_url을 추가하세요."
            )

        payload = {"text": text}

        try:
            async with httpx.AsyncClient(timeout=settings.tool_call_timeout) as client:
                resp = await client.post(webhook_url, json=payload)
                resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                return "Error: Webhook URL이 유효하지 않거나 권한이 없습니다."
            return f"Error: Google Chat 메시지 전송 실패 — {e.response.status_code}: {e.response.text[:200]}"
        except httpx.HTTPError as e:
            return f"Error: Google Chat에 연결할 수 없습니다 — {e}"

        return "메시지가 Google Chat에 전송되었습니다."

    return StructuredTool.from_function(
        coroutine=send_message,
        name="google_chat_send",
        description=(
            "Google Chat 채널에 메시지를 전송합니다. "
            "알림, 보고, 요약 결과 공유 등에 사용하세요."
        ),
        args_schema=GoogleChatSendArgs,
    )
