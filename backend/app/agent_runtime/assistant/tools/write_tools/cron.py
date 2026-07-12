"""Assistant 쓰기 도구 — 크론 스케줄 그룹 (create/update/delete/enable/disable)."""

from __future__ import annotations

import uuid
from typing import Any

from langchain_core.tools import StructuredTool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.assistant.tools.write_tools.context import WriteToolContext
from app.models.agent_trigger import AgentTrigger
from app.schemas.trigger import TriggerCreate, TriggerUpdate
from app.services import trigger_service


def _format_trigger_candidate(trigger: AgentTrigger) -> str:
    return (
        f"ID: {trigger.id}, 이름: {trigger.name}, 상태: {trigger.status}, "
        f"다음 실행: {trigger.next_run_at or '미정'}"
    )


async def _resolve_trigger_for_write(
    ctx: WriteToolContext,
    session: AsyncSession,
    schedule_id: str | None = None,
    schedule_name: str | None = None,
) -> tuple[AgentTrigger | None, str | None]:
    if schedule_id:
        try:
            sid = uuid.UUID(schedule_id)
        except ValueError:
            return None, "유효하지 않은 스케줄 ID입니다."
        result = await session.execute(
            select(AgentTrigger).where(
                AgentTrigger.id == sid,
                AgentTrigger.agent_id == ctx.agent_id,
                AgentTrigger.user_id == ctx.user_id,
            )
        )
        trigger = result.scalar_one_or_none()
        if not trigger:
            return None, "스케줄을 찾을 수 없습니다."
        return trigger, None

    if not schedule_name:
        return None, "schedule_id 또는 schedule_name이 필요합니다."

    result = await session.execute(
        select(AgentTrigger)
        .where(
            AgentTrigger.agent_id == ctx.agent_id,
            AgentTrigger.user_id == ctx.user_id,
            AgentTrigger.name == schedule_name,
        )
        .order_by(AgentTrigger.created_at.desc())
    )
    matches = list(result.scalars().all())
    if not matches:
        return None, "스케줄을 찾을 수 없습니다."
    if len(matches) > 1:
        candidates = "\n".join(_format_trigger_candidate(trigger) for trigger in matches)
        return (
            None,
            (
                f"'{schedule_name}' 이름의 스케줄이 여러 개 있습니다. "
                f"ID를 지정해 주세요.\n{candidates}"
            ),
        )
    return matches[0], None


def build_cron_tools(ctx: WriteToolContext) -> list[StructuredTool]:
    """크론 스케줄 도구 5개를 생성한다."""

    # ------ 14. create_cron_schedule ------

    async def create_cron_schedule(
        schedule_type: str,
        message: str,
        name: str | None = None,
        cron_expression: str | None = None,
        interval_minutes: int | None = None,
        scheduled_at: str | None = None,
        timezone: str | None = None,
        conversation_policy: str | None = None,
        target_conversation_id: str | None = None,
        max_runs: int | None = None,
        end_at: str | None = None,
        auto_pause_after_failures: int | None = None,
    ) -> str:
        """크론 스케줄을 생성합니다.

        Args:
            schedule_type: "recurring", "cron", "interval" 또는 "one_time"
            message: 실행 시 전달할 메시지
            name: 스케줄 이름
            cron_expression: 반복 스케줄의 cron 표현식 (recurring/cron일 때 필수)
            interval_minutes: 간격 분 수 (interval일 때 필수)
            scheduled_at: 1회 실행 시점 ISO 8601 (one_time일 때 필수)
            timezone: IANA timezone (기본 Asia/Seoul)
            conversation_policy: 결과 저장 정책 (기본 schedule_thread)
            target_conversation_id: selected_conversation 정책에서 사용할 대화 ID
            max_runs: 최대 성공 실행 횟수
            end_at: 종료 시각 ISO 8601
            auto_pause_after_failures: 연속 실패 자동 일시정지 임계치
        """
        normalized_type = schedule_type.strip().lower()
        trigger_type = "cron" if normalized_type in {"recurring", "cron"} else normalized_type
        schedule_config: dict[str, Any] = {}
        if trigger_type == "cron":
            if not cron_expression:
                return "반복 스케줄에는 cron_expression이 필요합니다."
            parts = cron_expression.strip().split()
            if len(parts) != 5:
                return (
                    f"유효하지 않은 cron 표현식입니다: '{cron_expression}'. "
                    "5개 필드 (분 시 일 월 요일)가 필요합니다."
                )
            schedule_config = {"cron_expression": cron_expression}
        elif trigger_type == "interval":
            if interval_minutes is None:
                return "간격 스케줄에는 interval_minutes가 필요합니다."
            schedule_config = {"interval_minutes": interval_minutes}
        elif trigger_type == "one_time":
            if not scheduled_at:
                return "1회 스케줄에는 scheduled_at이 필요합니다."
            schedule_config = {"scheduled_at": scheduled_at}
        else:
            return (
                "schedule_type은 'recurring' 또는 'one_time'이어야 합니다. "
                "추가로 'cron', 'interval'도 사용할 수 있습니다."
            )

        async with ctx.session_factory() as session:
            try:
                trigger = await trigger_service.create_trigger(
                    session,
                    ctx.agent_id,
                    ctx.user_id,
                    TriggerCreate.model_validate(
                        {
                            "name": name,
                            "trigger_type": trigger_type,
                            "schedule_config": schedule_config,
                            "input_message": message,
                            "timezone": timezone or "Asia/Seoul",
                            "conversation_policy": conversation_policy or "schedule_thread",
                            "target_conversation_id": target_conversation_id,
                            "max_runs": max_runs,
                            "end_at": end_at,
                            "auto_pause_after_failures": auto_pause_after_failures,
                        }
                    ),
                )
            except ValueError as exc:
                return f"스케줄 설정이 올바르지 않습니다: {exc}"
            return (
                f"스케줄 생성 완료 (ID: {trigger.id}, 다음 실행: {trigger.next_run_at or '미정'})"
            )

    # ------ 15. update_cron_schedule ------

    async def update_cron_schedule(
        schedule_id: str | None = None,
        schedule_name: str | None = None,
        cron_expression: str | None = None,
        interval_minutes: int | None = None,
        scheduled_at: str | None = None,
        message: str | None = None,
        name: str | None = None,
        timezone: str | None = None,
        conversation_policy: str | None = None,
        target_conversation_id: str | None = None,
        status: str | None = None,
        max_runs: int | None = None,
        end_at: str | None = None,
        auto_pause_after_failures: int | None = None,
    ) -> str:
        """크론 스케줄을 수정합니다.

        Args:
            schedule_id: 스케줄 UUID
            schedule_name: 스케줄 이름 (동명이인이 있으면 ID 필요)
            cron_expression: 새 cron 표현식
            interval_minutes: 새 interval 분 수
            scheduled_at: 새 1회 실행 시점
            message: 새 실행 메시지
            name: 새 스케줄 이름
            timezone: 새 timezone
            conversation_policy: 새 결과 저장 정책
            target_conversation_id: selected_conversation 정책에서 사용할 대화 ID
            status: 새 상태
            max_runs: 새 최대 성공 실행 횟수
            end_at: 새 종료 시각 ISO 8601
            auto_pause_after_failures: 새 연속 실패 자동 일시정지 임계치
        """
        async with ctx.session_factory() as session:
            trigger, error = await _resolve_trigger_for_write(
                ctx, session, schedule_id, schedule_name
            )
            if error or not trigger:
                return error or "스케줄을 찾을 수 없습니다."

            update_payload: dict[str, Any] = {}
            if cron_expression:
                update_payload["trigger_type"] = "cron"
                update_payload["schedule_config"] = {"cron_expression": cron_expression}
            if interval_minutes is not None:
                update_payload["trigger_type"] = "interval"
                update_payload["schedule_config"] = {"interval_minutes": interval_minutes}
            if scheduled_at:
                update_payload["trigger_type"] = "one_time"
                update_payload["schedule_config"] = {"scheduled_at": scheduled_at}
            if message:
                update_payload["input_message"] = message
            if name is not None:
                update_payload["name"] = name
            if timezone is not None:
                update_payload["timezone"] = timezone
            if conversation_policy is not None:
                update_payload["conversation_policy"] = conversation_policy
            if target_conversation_id is not None:
                update_payload["target_conversation_id"] = target_conversation_id
            if status is not None:
                update_payload["status"] = status
            if max_runs is not None:
                update_payload["max_runs"] = max_runs
            if end_at is not None:
                update_payload["end_at"] = end_at
            if auto_pause_after_failures is not None:
                update_payload["auto_pause_after_failures"] = auto_pause_after_failures
            try:
                update = TriggerUpdate.model_validate(update_payload)
                await trigger_service.update_trigger(session, trigger, update)
            except ValueError as exc:
                return f"스케줄 설정이 올바르지 않습니다: {exc}"
            return "스케줄 수정 완료."

    # ------ 16. delete_cron_schedule ------

    async def delete_cron_schedule(
        schedule_id: str | None = None,
        schedule_name: str | None = None,
    ) -> str:
        """크론 스케줄을 삭제합니다.

        Args:
            schedule_id: 스케줄 UUID
            schedule_name: 스케줄 이름
        """
        async with ctx.session_factory() as session:
            trigger, error = await _resolve_trigger_for_write(
                ctx, session, schedule_id, schedule_name
            )
            if error or not trigger:
                return error or "스케줄을 찾을 수 없습니다."
            await trigger_service.delete_trigger(session, trigger)
            return "스케줄 삭제 완료."

    # ------ 17. enable_cron_schedule ------

    async def enable_cron_schedule(
        schedule_id: str | None = None,
        schedule_name: str | None = None,
    ) -> str:
        """크론 스케줄을 활성화합니다.

        Args:
            schedule_id: 스케줄 UUID
            schedule_name: 스케줄 이름
        """
        async with ctx.session_factory() as session:
            trigger, error = await _resolve_trigger_for_write(
                ctx, session, schedule_id, schedule_name
            )
            if error or not trigger:
                return error or "스케줄을 찾을 수 없습니다."
            try:
                await trigger_service.update_trigger(
                    session,
                    trigger,
                    TriggerUpdate(status="active"),
                )
            except ValueError as exc:
                return f"스케줄 설정이 올바르지 않습니다: {exc}"
            return "스케줄 활성화 완료."

    # ------ 18. disable_cron_schedule ------

    async def disable_cron_schedule(
        schedule_id: str | None = None,
        schedule_name: str | None = None,
    ) -> str:
        """크론 스케줄을 비활성화합니다.

        Args:
            schedule_id: 스케줄 UUID
            schedule_name: 스케줄 이름
        """
        async with ctx.session_factory() as session:
            trigger, error = await _resolve_trigger_for_write(
                ctx, session, schedule_id, schedule_name
            )
            if error or not trigger:
                return error or "스케줄을 찾을 수 없습니다."
            await trigger_service.update_trigger(session, trigger, TriggerUpdate(status="paused"))
            return "스케줄 비활성화 완료."

    return [
        StructuredTool.from_function(
            coroutine=create_cron_schedule,
            name="create_cron_schedule",
            description="크론 스케줄 생성 (반복 또는 1회)",
        ),
        StructuredTool.from_function(
            coroutine=update_cron_schedule,
            name="update_cron_schedule",
            description="크론 스케줄 수정",
        ),
        StructuredTool.from_function(
            coroutine=delete_cron_schedule,
            name="delete_cron_schedule",
            description="크론 스케줄 삭제",
        ),
        StructuredTool.from_function(
            coroutine=enable_cron_schedule,
            name="enable_cron_schedule",
            description="크론 스케줄 활성화",
        ),
        StructuredTool.from_function(
            coroutine=disable_cron_schedule,
            name="disable_cron_schedule",
            description="크론 스케줄 비활성화",
        ),
    ]
