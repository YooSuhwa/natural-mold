"""``validate_skill``/``finalize_skill`` 도구 결과 → ``moldy.skill_validation``
projection (memory_event_projection 패턴, 스펙 AD-5).

페이로드는 기존 ``validation_result`` 스키마 그대로 실어 프론트 검증 레일이
v1 패널(ValidationPanel/PortableCompatibilityPanel)을 재사용하게 한다.
issues는 code/severity/message/path만 담는다 — 파일 내용/시크릿 값은 검증기
계약상 포함되지 않는다 (SecretFinding은 path+kind만, §6-7).
"""

from __future__ import annotations

import json
from typing import Any, Final

SKILL_VALIDATION_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {"validate_skill", "finalize_skill"}
)

# 검증 결과로 인정하는 최소 shape — 임의 JSON 도구 결과 오인 방지.
_REQUIRED_KEYS: Final[frozenset[str]] = frozenset({"valid", "issues"})


def skill_validation_event_from_tool_result(
    tool_name: str,
    result: str,
) -> dict[str, Any] | None:
    """도구 결과 JSON에서 검증 페이로드를 추출한다 (아니면 ``None``)."""

    if tool_name not in SKILL_VALIDATION_TOOL_NAMES:
        return None
    try:
        parsed = json.loads(result)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    validation = parsed.get("validation_result")
    if not isinstance(validation, dict):
        # validate_skill은 결과 dict 자체가 validation_result 스키마.
        validation = parsed
    if not set(validation) >= _REQUIRED_KEYS:
        return None
    payload: dict[str, Any] = {"tool_name": tool_name, "validation_result": validation}
    session_id = parsed.get("session_id")
    if isinstance(session_id, str):
        payload["session_id"] = session_id
    return payload
