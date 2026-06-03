"""Date/time context helpers for agent runtime prompts and tools."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Asia/Seoul"

_WEEKDAYS_KO = [
    "월요일",
    "화요일",
    "수요일",
    "목요일",
    "금요일",
    "토요일",
    "일요일",
]

_WEEKDAY_INDEX = {
    "월요일": 0,
    "화요일": 1,
    "수요일": 2,
    "목요일": 3,
    "금요일": 4,
    "토요일": 5,
    "일요일": 6,
}


def _zone(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except Exception:  # noqa: BLE001 - invalid user input falls back safely
        return ZoneInfo(DEFAULT_TIMEZONE)


def _coerce_now(now: datetime | None = None, *, timezone: str = DEFAULT_TIMEZONE) -> datetime:
    tz = _zone(timezone)
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        return now.replace(tzinfo=tz)
    return now.astimezone(tz)


def parse_reference_datetime(
    value: str | None,
    *,
    timezone: str = DEFAULT_TIMEZONE,
) -> datetime | None:
    """Parse an optional ISO reference date supplied by a tool caller."""

    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        try:
            parsed_date = date.fromisoformat(raw)
        except ValueError:
            return None
        parsed = datetime.combine(parsed_date, datetime.min.time())
    return _coerce_now(parsed, timezone=timezone)


def _weekday_ko(value: date) -> str:
    return _WEEKDAYS_KO[value.weekday()]


def _date_payload(
    *,
    label: str,
    start: date,
    end: date,
    now: datetime,
    timezone: str,
) -> dict[str, Any]:
    return {
        "success": True,
        "label": label,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "weekday_start": _weekday_ko(start),
        "weekday_end": _weekday_ko(end),
        "timezone": timezone,
        "reference_date": now.date().isoformat(),
        "reference_weekday": _weekday_ko(now.date()),
    }


def _week_start(today: date) -> date:
    return today - timedelta(days=today.weekday())


def _week_range(today: date, *, offset_weeks: int) -> tuple[date, date]:
    start = _week_start(today) + timedelta(days=7 * offset_weeks)
    return start, start + timedelta(days=6)


def _weekend_range(today: date, *, offset_weeks: int) -> tuple[date, date]:
    start = _week_start(today) + timedelta(days=7 * offset_weeks + 5)
    return start, start + timedelta(days=1)


def build_temporal_context(
    *,
    now: datetime | None = None,
    timezone: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    """Return a compact KST-anchored context for relative date reasoning."""

    current = _coerce_now(now, timezone=timezone)
    today = current.date()
    weekend_start, weekend_end = _weekend_range(today, offset_weeks=0)
    next_week_start, next_week_end = _week_range(today, offset_weeks=1)
    return {
        "now_iso": current.isoformat(),
        "date": today.isoformat(),
        "time": current.strftime("%H:%M:%S"),
        "weekday": _weekday_ko(today),
        "timezone": timezone,
        "today": _date_payload(
            label="오늘",
            start=today,
            end=today,
            now=current,
            timezone=timezone,
        ),
        "this_weekend": _date_payload(
            label="이번 주말",
            start=weekend_start,
            end=weekend_end,
            now=current,
            timezone=timezone,
        ),
        "next_week": _date_payload(
            label="다음 주",
            start=next_week_start,
            end=next_week_end,
            now=current,
            timezone=timezone,
        ),
    }


def build_temporal_context_prompt(
    *,
    now: datetime | None = None,
    timezone: str = DEFAULT_TIMEZONE,
) -> str:
    """Build the runtime prompt block that grounds relative dates."""

    ctx = build_temporal_context(now=now, timezone=timezone)
    weekend = ctx["this_weekend"]
    next_week = ctx["next_week"]
    hour_bucket = f"{int(str(ctx['time']).split(':', 1)[0]):02d}시대"
    return (
        "\n\n## 현재 기준 날짜\n"
        f"- 현재: {ctx['date']} {ctx['weekday']} {hour_bucket} ({ctx['timezone']})\n"
        f"- 이번 주말: {weekend['start_date']}({weekend['weekday_start']})"
        f" ~ {weekend['end_date']}({weekend['weekday_end']})\n"
        f"- 다음 주: {next_week['start_date']}({next_week['weekday_start']})"
        f" ~ {next_week['end_date']}({next_week['weekday_end']})\n"
        "- 오늘, 내일, 이번 주말, 다음주 수요일, 최근 같은 상대 날짜 표현은 "
        "반드시 위 기준으로 해석하세요.\n"
        "- 날씨, 뉴스, 일정, 예약처럼 날짜가 중요한 외부 조회 전에는 "
        "필요하면 resolve_relative_date 도구로 ISO 날짜 범위를 확인하세요.\n"
        "- 분 단위의 정확한 현재 시각, 마감, 예약 가능 여부처럼 시간 정밀도가 "
        "중요한 작업은 current_datetime 도구로 확인하세요."
    )


def _compact_expression(value: str) -> str:
    return "".join(value.lower().split())


def _resolve_weekday(
    compact: str,
    *,
    today: date,
) -> tuple[str, date] | None:
    for token, weekday_index in sorted(_WEEKDAY_INDEX.items(), key=lambda item: -len(item[0])):
        if token not in compact:
            continue
        if "다음주" in compact or "다음 주" in compact:
            week_offset = 1
            label_prefix = "다음 주"
        elif "이번주" in compact or "이번 주" in compact:
            week_offset = 0
            label_prefix = "이번 주"
        elif "지난주" in compact or "지난 주" in compact:
            week_offset = -1
            label_prefix = "지난 주"
        else:
            base = today + timedelta(days=(weekday_index - today.weekday()) % 7)
            return f"{token if token.endswith('요일') else token + '요일'}", base

        start = _week_start(today) + timedelta(days=7 * week_offset)
        weekday_label = token if token.endswith("요일") else f"{token}요일"
        return f"{label_prefix} {weekday_label}", start + timedelta(days=weekday_index)
    return None


def resolve_relative_date_expression(
    expression: str,
    *,
    now: datetime | None = None,
    timezone: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    """Resolve common Korean relative date expressions into an ISO date range."""

    current = _coerce_now(now, timezone=timezone)
    today = current.date()
    compact = _compact_expression(expression)

    if not compact:
        return {
            "success": False,
            "error": "EMPTY_EXPRESSION",
            "message": "날짜 표현이 비어 있습니다.",
            "timezone": timezone,
            "reference_date": today.isoformat(),
            "reference_weekday": _weekday_ko(today),
        }

    if "어제" in compact:
        target = today - timedelta(days=1)
        return _date_payload(label="어제", start=target, end=target, now=current, timezone=timezone)
    if "오늘" in compact:
        return _date_payload(label="오늘", start=today, end=today, now=current, timezone=timezone)
    if "모레" in compact:
        target = today + timedelta(days=2)
        return _date_payload(label="모레", start=target, end=target, now=current, timezone=timezone)
    if "내일" in compact:
        target = today + timedelta(days=1)
        return _date_payload(label="내일", start=target, end=target, now=current, timezone=timezone)

    if "주말" in compact:
        offset = 1 if "다음" in compact else -1 if "지난" in compact else 0
        start, end = _weekend_range(today, offset_weeks=offset)
        label = "다음 주말" if offset == 1 else "지난 주말" if offset == -1 else "이번 주말"
        return _date_payload(label=label, start=start, end=end, now=current, timezone=timezone)

    if "다음주" in compact or "다음 주" in compact:
        start, end = _week_range(today, offset_weeks=1)
        weekday_result = _resolve_weekday(compact, today=today)
        if weekday_result is not None:
            label, target = weekday_result
            return _date_payload(
                label=label,
                start=target,
                end=target,
                now=current,
                timezone=timezone,
            )
        return _date_payload(label="다음 주", start=start, end=end, now=current, timezone=timezone)

    if "이번주" in compact or "이번 주" in compact:
        start, end = _week_range(today, offset_weeks=0)
        weekday_result = _resolve_weekday(compact, today=today)
        if weekday_result is not None:
            label, target = weekday_result
            return _date_payload(
                label=label,
                start=target,
                end=target,
                now=current,
                timezone=timezone,
            )
        return _date_payload(label="이번 주", start=start, end=end, now=current, timezone=timezone)

    if "최근" in compact or "최신" in compact:
        start = today - timedelta(days=6)
        return _date_payload(
            label="최근 7일",
            start=start,
            end=today,
            now=current,
            timezone=timezone,
        )

    weekday_result = _resolve_weekday(compact, today=today)
    if weekday_result is not None:
        label, target = weekday_result
        return _date_payload(label=label, start=target, end=target, now=current, timezone=timezone)

    return {
        "success": False,
        "error": "UNRESOLVED_RELATIVE_DATE",
        "message": "지원하는 상대 날짜 표현을 찾지 못했습니다.",
        "expression": expression,
        "timezone": timezone,
        "reference_date": today.isoformat(),
        "reference_weekday": _weekday_ko(today),
    }
