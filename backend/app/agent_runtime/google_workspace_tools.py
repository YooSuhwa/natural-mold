from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from functools import partial
from typing import Any
from zoneinfo import ZoneInfo

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


# ---------------------------------------------------------------------------
# Helper: build Gmail service (sync, run in thread)
# ---------------------------------------------------------------------------

def _build_gmail_service(auth_config: dict[str, Any] | None = None) -> Any:
    """Build a Gmail API service object. Returns (service, error_msg)."""
    from app.agent_runtime.google_auth import get_google_credentials
    from googleapiclient.discovery import build

    creds = get_google_credentials(auth_config)
    if creds is None:
        return None, (
            "Error: Google OAuth2 인증 정보가 설정되지 않았습니다. "
            ".env 파일에 GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, "
            "GOOGLE_OAUTH_REFRESH_TOKEN을 설정하세요."
        )

    service = build("gmail", "v1", credentials=creds)
    return service, None


# ---------------------------------------------------------------------------
# Gmail Read (list + get)
# ---------------------------------------------------------------------------

class GmailReadArgs(BaseModel):
    query: str = Field(
        default="is:inbox",
        description="Gmail 검색 쿼리 (예: 'is:unread', 'from:boss@company.com', 'subject:회의')",
    )
    max_results: int = Field(
        default=5,
        description="가져올 이메일 수 (1-20)",
        ge=1,
        le=20,
    )


def build_gmail_read_tool(
    auth_config: dict[str, Any] | None = None,
) -> BaseTool:
    """Build a LangChain tool that reads emails from Gmail."""

    async def read_emails(query: str = "is:inbox", max_results: int = 5) -> str:
        service, err = await asyncio.to_thread(
            partial(_build_gmail_service, auth_config)
        )
        if err:
            return err

        try:
            # List messages
            result = await asyncio.to_thread(
                lambda: service.users().messages().list(
                    userId="me", q=query, maxResults=max_results,
                ).execute()
            )
        except Exception as e:
            return f"Error: Gmail 메시지 목록 조회 실패 — {e}"

        messages = result.get("messages", [])
        if not messages:
            return f"'{query}' 조건에 맞는 이메일이 없습니다."

        output_parts: list[str] = []
        for i, msg_ref in enumerate(messages, 1):
            try:
                msg = await asyncio.to_thread(
                    lambda mid=msg_ref["id"]: service.users().messages().get(
                        userId="me", id=mid, format="metadata",
                        metadataHeaders=["From", "To", "Subject", "Date"],
                    ).execute()
                )
            except Exception:
                continue

            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            snippet = msg.get("snippet", "")
            labels = msg.get("labelIds", [])

            parts = [f"[{i}]"]
            if "Subject" in headers:
                parts.append(f"제목: {headers['Subject']}")
            if "From" in headers:
                parts.append(f"보낸 사람: {headers['From']}")
            if "Date" in headers:
                parts.append(f"날짜: {headers['Date']}")
            if snippet:
                parts.append(f"미리보기: {snippet}")
            if "UNREAD" in labels:
                parts.append("상태: 읽지 않음")
            output_parts.append("\n".join(parts))

        total = result.get("resultSizeEstimate", len(messages))
        header = f"총 약 {total}건 중 {len(output_parts)}건 표시 (검색: {query})\n"
        return header + "\n\n".join(output_parts)

    return StructuredTool.from_function(
        coroutine=read_emails,
        name="gmail_read",
        description=(
            "Gmail에서 이메일을 검색하고 읽습니다. "
            "검색 쿼리로 필터링할 수 있습니다 (예: 'is:unread', 'from:someone@example.com')."
        ),
        args_schema=GmailReadArgs,
    )


# ---------------------------------------------------------------------------
# Gmail Send
# ---------------------------------------------------------------------------

class GmailSendArgs(BaseModel):
    to: str = Field(description="수신자 이메일 주소")
    subject: str = Field(description="이메일 제목")
    body: str = Field(description="이메일 본문 (텍스트)")


def build_gmail_send_tool(
    auth_config: dict[str, Any] | None = None,
) -> BaseTool:
    """Build a LangChain tool that sends emails via Gmail."""

    async def send_email(to: str, subject: str, body: str) -> str:
        service, err = await asyncio.to_thread(
            partial(_build_gmail_service, auth_config)
        )
        if err:
            return err

        message = MIMEText(body, "plain", "utf-8")
        message["to"] = to
        message["subject"] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

        try:
            sent = await asyncio.to_thread(
                lambda: service.users().messages().send(
                    userId="me", body={"raw": raw},
                ).execute()
            )
        except Exception as e:
            return f"Error: 이메일 전송 실패 — {e}"

        return f"이메일이 {to}에게 전송되었습니다. (메시지 ID: {sent.get('id', 'unknown')})"

    return StructuredTool.from_function(
        coroutine=send_email,
        name="gmail_send",
        description=(
            "Gmail로 이메일을 전송합니다. "
            "수신자, 제목, 본문을 지정하여 이메일을 보냅니다."
        ),
        args_schema=GmailSendArgs,
    )


# ---------------------------------------------------------------------------
# Helper: build Calendar service (sync, run in thread)
# ---------------------------------------------------------------------------

def _build_calendar_service(auth_config: dict[str, Any] | None = None) -> tuple[Any, str | None]:
    """Build a Google Calendar API service object. Returns (service, error_msg)."""
    from app.agent_runtime.google_auth import get_google_credentials
    from googleapiclient.discovery import build

    creds = get_google_credentials(auth_config)
    if creds is None:
        return None, (
            "Error: Google OAuth2 인증 정보가 설정되지 않았습니다. "
            ".env 파일에 GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, "
            "GOOGLE_OAUTH_REFRESH_TOKEN을 설정하세요."
        )

    service = build("calendar", "v3", credentials=creds)
    return service, None


# ---------------------------------------------------------------------------
# Calendar List Events
# ---------------------------------------------------------------------------

class CalendarListEventsArgs(BaseModel):
    days: int = Field(
        default=1,
        description="오늘부터 며칠간의 일정을 조회할지 (기본 1 = 오늘만)",
        ge=1,
        le=30,
    )
    max_results: int = Field(
        default=10,
        description="가져올 일정 수 (1-50)",
        ge=1,
        le=50,
    )
    calendar_id: str = Field(
        default="primary",
        description="캘린더 ID (기본: primary = 기본 캘린더)",
    )


def build_calendar_list_events_tool(
    auth_config: dict[str, Any] | None = None,
) -> BaseTool:
    """Build a LangChain tool that lists Google Calendar events."""

    async def list_events(
        days: int = 1, max_results: int = 10, calendar_id: str = "primary",
    ) -> str:
        service, err = await asyncio.to_thread(
            partial(_build_calendar_service, auth_config)
        )
        if err:
            return err

        tz = ZoneInfo("Asia/Seoul")
        now = datetime.now(tz)
        time_min = now.isoformat()
        time_max = (now + timedelta(days=days)).replace(
            hour=23, minute=59, second=59,
        ).isoformat()

        try:
            result = await asyncio.to_thread(
                lambda: service.events().list(
                    calendarId=calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    maxResults=max_results,
                    singleEvents=True,
                    orderBy="startTime",
                ).execute()
            )
        except Exception as e:
            return f"Error: 캘린더 일정 조회 실패 — {e}"

        events = result.get("items", [])
        if not events:
            return f"향후 {days}일간 예정된 일정이 없습니다."

        output_parts: list[str] = []
        for i, event in enumerate(events, 1):
            parts = [f"[{i}]"]
            summary = event.get("summary", "(제목 없음)")
            parts.append(f"제목: {summary}")

            start = event.get("start", {})
            end = event.get("end", {})
            if "dateTime" in start:
                parts.append(f"시작: {start['dateTime']}")
                parts.append(f"종료: {end.get('dateTime', '')}")
            elif "date" in start:
                parts.append(f"날짜: {start['date']} (종일)")

            if event.get("location"):
                parts.append(f"장소: {event['location']}")
            if event.get("description"):
                desc = event["description"][:100]
                parts.append(f"설명: {desc}")
            if event.get("hangoutLink"):
                parts.append(f"화상회의: {event['hangoutLink']}")

            status = event.get("status", "")
            if status == "cancelled":
                parts.append("상태: 취소됨")

            output_parts.append("\n".join(parts))

        header = f"향후 {days}일간 일정 {len(output_parts)}건\n"
        return header + "\n\n".join(output_parts)

    return StructuredTool.from_function(
        coroutine=list_events,
        name="calendar_list_events",
        description=(
            "Google Calendar에서 일정을 조회합니다. "
            "오늘 또는 며칠간의 일정을 확인할 수 있습니다."
        ),
        args_schema=CalendarListEventsArgs,
    )


# ---------------------------------------------------------------------------
# Calendar Create Event
# ---------------------------------------------------------------------------

class CalendarCreateEventArgs(BaseModel):
    summary: str = Field(description="일정 제목")
    start_datetime: str = Field(
        description="시작 일시 (ISO 8601 형식, 예: '2026-04-05T10:00:00+09:00')",
    )
    end_datetime: str = Field(
        description="종료 일시 (ISO 8601 형식, 예: '2026-04-05T11:00:00+09:00')",
    )
    description: str = Field(default="", description="일정 설명 (선택)")
    location: str = Field(default="", description="장소 (선택)")
    calendar_id: str = Field(default="primary", description="캘린더 ID")


def build_calendar_create_event_tool(
    auth_config: dict[str, Any] | None = None,
) -> BaseTool:
    """Build a LangChain tool that creates a Google Calendar event."""

    async def create_event(
        summary: str,
        start_datetime: str,
        end_datetime: str,
        description: str = "",
        location: str = "",
        calendar_id: str = "primary",
    ) -> str:
        service, err = await asyncio.to_thread(
            partial(_build_calendar_service, auth_config)
        )
        if err:
            return err

        event_body: dict[str, Any] = {
            "summary": summary,
            "start": {"dateTime": start_datetime, "timeZone": "Asia/Seoul"},
            "end": {"dateTime": end_datetime, "timeZone": "Asia/Seoul"},
        }
        if description:
            event_body["description"] = description
        if location:
            event_body["location"] = location

        try:
            created = await asyncio.to_thread(
                lambda: service.events().insert(
                    calendarId=calendar_id, body=event_body,
                ).execute()
            )
        except Exception as e:
            return f"Error: 일정 생성 실패 — {e}"

        link = created.get("htmlLink", "")
        return (
            f"일정이 생성되었습니다.\n"
            f"제목: {summary}\n"
            f"시작: {start_datetime}\n"
            f"종료: {end_datetime}\n"
            f"링크: {link}"
        )

    return StructuredTool.from_function(
        coroutine=create_event,
        name="calendar_create_event",
        description=(
            "Google Calendar에 새 일정을 생성합니다. "
            "제목, 시작/종료 시간, 설명, 장소를 지정할 수 있습니다."
        ),
        args_schema=CalendarCreateEventArgs,
    )


# ---------------------------------------------------------------------------
# Calendar Update Event
# ---------------------------------------------------------------------------

class CalendarUpdateEventArgs(BaseModel):
    event_id: str = Field(description="수정할 일정의 ID")
    summary: str = Field(default="", description="새 일정 제목 (빈 문자열이면 변경 안 함)")
    start_datetime: str = Field(
        default="",
        description="새 시작 일시 (ISO 8601, 빈 문자열이면 변경 안 함)",
    )
    end_datetime: str = Field(
        default="",
        description="새 종료 일시 (ISO 8601, 빈 문자열이면 변경 안 함)",
    )
    description: str = Field(default="", description="새 설명 (빈 문자열이면 변경 안 함)")
    location: str = Field(default="", description="새 장소 (빈 문자열이면 변경 안 함)")
    calendar_id: str = Field(default="primary", description="캘린더 ID")


def build_calendar_update_event_tool(
    auth_config: dict[str, Any] | None = None,
) -> BaseTool:
    """Build a LangChain tool that updates a Google Calendar event."""

    async def update_event(
        event_id: str,
        summary: str = "",
        start_datetime: str = "",
        end_datetime: str = "",
        description: str = "",
        location: str = "",
        calendar_id: str = "primary",
    ) -> str:
        service, err = await asyncio.to_thread(
            partial(_build_calendar_service, auth_config)
        )
        if err:
            return err

        # Fetch existing event first
        try:
            existing = await asyncio.to_thread(
                lambda: service.events().get(
                    calendarId=calendar_id, eventId=event_id,
                ).execute()
            )
        except Exception as e:
            return f"Error: 일정을 찾을 수 없습니다 — {e}"

        # Apply changes
        if summary:
            existing["summary"] = summary
        if start_datetime:
            existing["start"] = {"dateTime": start_datetime, "timeZone": "Asia/Seoul"}
        if end_datetime:
            existing["end"] = {"dateTime": end_datetime, "timeZone": "Asia/Seoul"}
        if description:
            existing["description"] = description
        if location:
            existing["location"] = location

        try:
            updated = await asyncio.to_thread(
                lambda: service.events().update(
                    calendarId=calendar_id, eventId=event_id, body=existing,
                ).execute()
            )
        except Exception as e:
            return f"Error: 일정 수정 실패 — {e}"

        return (
            f"일정이 수정되었습니다.\n"
            f"제목: {updated.get('summary', '')}\n"
            f"시작: {updated.get('start', {}).get('dateTime', '')}\n"
            f"종료: {updated.get('end', {}).get('dateTime', '')}"
        )

    return StructuredTool.from_function(
        coroutine=update_event,
        name="calendar_update_event",
        description=(
            "Google Calendar의 기존 일정을 수정합니다. "
            "일정 ID와 변경할 필드를 지정합니다."
        ),
        args_schema=CalendarUpdateEventArgs,
    )
