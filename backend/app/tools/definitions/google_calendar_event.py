"""Google Calendar — create event via the Calendar v3 REST endpoint."""

from __future__ import annotations

from typing import Any

from app.credentials.authenticate import apply_authentication
from app.credentials.registry import registry as credential_registry
from app.tools.domain import ToolDefinition, ToolRunContext
from app.tools.parameters import FieldDef, FieldKind

_TIMEZONE = "Asia/Seoul"


def _events_url(calendar_id: str) -> str:
    return (
        "https://www.googleapis.com/calendar/v3/calendars/"
        f"{calendar_id}/events"
    )


async def _runner(ctx: ToolRunContext) -> dict[str, Any]:
    if ctx.credentials is None:
        raise ValueError("google_workspace_oauth2 credential is required")

    title = ctx.parameters["title"]
    start = ctx.parameters["start_time"]
    end = ctx.parameters["end_time"]
    attendees_raw = ctx.parameters.get("attendees") or []
    description = ctx.parameters.get("description") or ""
    location = ctx.parameters.get("location") or ""
    calendar_id = ctx.parameters.get("calendar_id") or "primary"

    if isinstance(attendees_raw, str):
        attendees_raw = [a.strip() for a in attendees_raw.split(",") if a.strip()]

    body: dict[str, Any] = {
        "summary": title,
        "start": {"dateTime": start, "timeZone": _TIMEZONE},
        "end": {"dateTime": end, "timeZone": _TIMEZONE},
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees_raw:
        body["attendees"] = [{"email": a} for a in attendees_raw]

    cred_def = credential_registry.require("google_workspace_oauth2")
    request_opts = apply_authentication(
        cred_def.authenticate,
        {"method": "POST", "url": _events_url(calendar_id), "json": body},
        ctx.credentials,
    )
    response = await ctx.http_client.request(**request_opts)
    response.raise_for_status()
    body_json = response.json()
    return {
        "http_status": response.status_code,
        "event_id": body_json.get("id"),
        "html_link": body_json.get("htmlLink"),
    }


definition = ToolDefinition(
    key="google_calendar_event",
    display_name="Google Calendar — Create Event",
    description="Create a Google Calendar event using a Workspace OAuth2 credential.",
    icon_id="calendar",
    category="calendar",
    parameters=[
        FieldDef(
            name="title",
            display_name="Title",
            kind=FieldKind.STRING,
            required=True,
        ),
        FieldDef(
            name="start_time",
            display_name="Start (ISO 8601)",
            kind=FieldKind.STRING,
            required=True,
            placeholder="2026-04-30T10:00:00+09:00",
        ),
        FieldDef(
            name="end_time",
            display_name="End (ISO 8601)",
            kind=FieldKind.STRING,
            required=True,
            placeholder="2026-04-30T11:00:00+09:00",
        ),
        FieldDef(
            name="attendees",
            display_name="Attendees (comma-separated emails)",
            kind=FieldKind.STRING,
            default="",
        ),
        FieldDef(
            name="description",
            display_name="Description",
            kind=FieldKind.MULTILINE,
            default="",
        ),
        FieldDef(
            name="location",
            display_name="Location",
            kind=FieldKind.STRING,
            default="",
        ),
        FieldDef(
            name="calendar_id",
            display_name="Calendar ID",
            kind=FieldKind.STRING,
            default="primary",
        ),
    ],
    credential_definition_keys=["google_workspace_oauth2"],
    runner=_runner,
)
