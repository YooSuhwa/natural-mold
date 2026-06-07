"""에이전트 이미지 생성 어댑터 (Builder v3 Phase 6 전용).

기존 ``image_service.py``의 OpenRouter + Gemini Flash Image (Moldy 캐릭터)
로직을 빌더 컨텍스트(Agent가 아직 없음)용으로 재사용한다.

저장 경로: ``{settings.agent_image_dir}/_builder/{session_id}/{uuid}.png``
공개 URL: ``/api/builder/{session_id}/image/{filename}`` (라우터에서 서빙)
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

import httpx

from app.database import async_session
from app.services.agent_image_paths import agent_image_dir
from app.services.image_service import (
    IMAGE_GEN_SYSTEM_PROMPT,
    _extract_image_data,
    _load_reference_image_base64,
    resolve_image_base_url,
)
from app.services.system_credential_resolver import (
    SystemModelNotConfiguredError,
    resolve_system_model,
)

logger = logging.getLogger(__name__)


class ImageGenerationError(RuntimeError):
    """이미지 생성 실패."""


def _builder_image_dir(session_id: str) -> Path:
    base = agent_image_dir() / "_builder" / session_id
    base.mkdir(parents=True, exist_ok=True)
    return base


async def is_image_generation_available() -> bool:
    """이미지 ``image`` system role이 설정되었는지 여부.

    ADR-019: 운영자가 System LLM 설정에서 image 슬롯(credential + model)을
    선택했으면 True. node가 이 결과로 phase 진입을 결정한다.
    """
    async with async_session() as db:
        try:
            await resolve_system_model(db, "image")
        except SystemModelNotConfiguredError:
            return False
    return True


def build_default_prompt(
    *,
    agent_name: str,
    agent_description: str,
    primary_task_type: str = "",
) -> str:
    """에이전트 메타데이터를 image_service의 user prompt 형식으로 변환."""
    descriptor = primary_task_type or (agent_description or "")[:200]
    return (
        f"Agent Name: {agent_name}\n"
        f"Description: {agent_description}\n"
        f"Role/Primary Task: {descriptor}"
    )


def public_url_for(session_id: str, filename: str) -> str:
    """프론트가 fetch할 수 있는 공개 URL."""
    return f"/api/builder/{session_id}/image/{filename}"


def resolve_local_path(session_id: str, filename: str) -> Path | None:
    """공개 URL의 filename으로 디스크 경로를 찾는다 (라우터에서 서빙용)."""
    safe = Path(filename).name  # path traversal 방어
    candidate = _builder_image_dir(session_id) / safe
    if candidate.exists() and candidate.is_file():
        return candidate
    return None


async def generate_agent_image(
    *,
    prompt: str,
    session_id: str,
) -> tuple[str, Path]:
    """OpenRouter + Gemini Flash Image로 이미지를 생성하여 저장한다.

    Returns:
        (public_url, local_path)

    Raises:
        ImageGenerationError: provider 미설정 또는 호출 실패
    """
    async with async_session() as db:
        try:
            resolved = await resolve_system_model(db, "image")
        except SystemModelNotConfiguredError as exc:
            raise ImageGenerationError(
                "운영자가 System LLM 설정에서 이미지 모델을 선택해야 합니다."
            ) from exc
    api_key = resolved.api_key
    base_url = resolve_image_base_url(resolved)

    try:
        ref_b64 = _load_reference_image_base64()
    except FileNotFoundError as exc:  # pragma: no cover
        raise ImageGenerationError(
            "Moldy reference image (static/moldy_main.png)를 찾을 수 없습니다."
        ) from exc

    body = {
        "model": resolved.model_name,
        "modalities": ["image", "text"],
        "image_config": {"aspect_ratio": "1:1"},
        "messages": [
            {"role": "system", "content": IMAGE_GEN_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{ref_b64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            },
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.exception("OpenRouter image API failed")
        raise ImageGenerationError(f"이미지 API 호출 실패: {exc}") from exc

    payload = resp.json()
    try:
        message = payload["choices"][0]["message"]
    except (KeyError, IndexError) as exc:
        raise ImageGenerationError(f"응답 형식 오류: {payload}") from exc

    images = message.get("images")
    content = message.get("content")

    try:
        if images and isinstance(images, list):
            image_bytes = _extract_image_data(images)
        elif content:
            image_bytes = _extract_image_data(content)
        else:
            raise ImageGenerationError("응답에 이미지 데이터가 없습니다.")
    except RuntimeError as exc:
        raise ImageGenerationError(str(exc)) from exc

    # 파일 확장자 magic bytes로 판별
    if image_bytes[:3] == b"\xff\xd8\xff":
        ext = "jpg"
    elif image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        ext = "webp"
    else:
        ext = "png"

    filename = f"{uuid.uuid4().hex[:12]}.{ext}"
    local_path = _builder_image_dir(session_id) / filename
    local_path.write_bytes(image_bytes)

    public_url = public_url_for(session_id, filename)
    return public_url, local_path
