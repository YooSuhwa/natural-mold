#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import binascii
import html
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

DEFAULT_MODEL = "gpt-image-2"
OPENROUTER_DEFAULT_MODEL = "openai/gpt-5.4-image-2"
DEFAULT_PROVIDER = "openai-compatible"
DEFAULT_TIMEOUT_SECONDS = 360.0

_DATA_URL_RE = re.compile(
    r"data:(image/[a-zA-Z0-9.+-]+);base64,([A-Za-z0-9+/=\s]+)"
)
_RAW_BASE64_RE = re.compile(r"\b([A-Za-z0-9+/]{32,}={0,2})\b")
_TRAVEL_HINT_RE = re.compile(r"(여행|관광|투어).*(가이드|가이드맵|지도|맵)")
_ENGLISH_TRAVEL_HINT_RE = re.compile(
    r"\b(?:travel|tourism|tourist|itinerary|guide\s*map|map[- ]style|landmarks?)\b",
    re.IGNORECASE,
)
_BRACKET_LOCATION_RE = re.compile(r"^\s*\[([^\]]+)\]")
_TRAVEL_SPLIT_RE = re.compile(r"\s*(?:주말\s*)?(?:여행|관광|투어|가이드맵|가이드|지도|맵)")
_ENGLISH_TRAVEL_SPLIT_RE = re.compile(
    r"\b(?:south\s+korea|korea|japan|travel|tourism|tourist|itinerary|guide\s*map|guide|map|illustration|poster)\b|,",
    re.IGNORECASE,
)
_LEADING_TRAVEL_FILLER_RE = re.compile(
    r"^(?:이번|다음|주말|당일|하루|1박\s*2일|2박\s*3일|어디|어느|좀|간단한)\s+"
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_DIRECT_IMAGE_MODELS = ("gpt-image", "dall-e")
_IMAGE_API_SIZES = {"auto", "1024x1024", "1536x1024", "1024x1536"}
_TRANSIENT_HTTP_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
_DEFAULT_HTTP_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY_SECONDS = 1.0
_KOREAN_LOCATION_NAMES = {
    "seoul": "서울",
    "busan": "부산",
    "ulsan": "울산",
    "daegu": "대구",
    "daejeon": "대전",
    "gwangju": "광주",
    "incheon": "인천",
    "jeju": "제주",
    "jeju island": "제주",
    "gyeongju": "경주",
    "gangneung": "강릉",
    "sokcho": "속초",
    "jeonju": "전주",
    "yeosu": "여수",
    "fukuoka": "후쿠오카",
    "tokyo": "도쿄",
    "osaka": "오사카",
    "kyoto": "교토",
    "sapporo": "삿포로",
    "okinawa": "오키나와",
    "taipei": "타이베이",
    "bangkok": "방콕",
}


@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    mime_type: str
    metadata: dict[str, Any]


class SkillError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ImageAdapter:
    def generate(
        self,
        *,
        prompt: str,
        model: str | None,
        aspect_ratio: str | None,
        image_size: str | None,
        timeout: float,
    ) -> GeneratedImage:
        raise NotImplementedError


def prepare_prompt(prompt: str) -> str:
    """Normalize high-value casual requests before they hit the image model."""

    stripped = " ".join(prompt.strip().split())
    if not stripped:
        return stripped
    if not _looks_like_travel_guide_request(stripped):
        return stripped

    location = _extract_travel_location(stripped)
    return (
        f"[{location}] 관광 가이드맵을 미니멀한 라인 아트 캐릭터를 사용한 "
        "모던한 에디토리얼 일러스트레이션으로 만들어 줘. "
        "대표 랜드마크, 로컬 음식, 이동 동선, 주말 여행 분위기를 깔끔한 "
        "지도형 구성으로 표현해 줘. 영어 문구를 넣지 말고, 제목과 짧은 "
        "한글 라벨만 사용해 줘. 긴 문장은 피하고, 밝고 세련된 색감과 "
        "넉넉한 여백을 사용해 줘."
    )


def _looks_like_travel_guide_request(prompt: str) -> bool:
    if _TRAVEL_HINT_RE.search(prompt):
        return True
    if "여행" in prompt and any(token in prompt for token in ("이미지", "그림", "일러스트")):
        return True
    lowered = prompt.lower()
    return bool(_ENGLISH_TRAVEL_HINT_RE.search(lowered)) and any(
        token in lowered
        for token in (
            "travel",
            "tourism",
            "tourist",
            "guide",
            "guide map",
            "landmark",
            "route",
        )
    )


def _extract_travel_location(prompt: str) -> str:
    bracket = _BRACKET_LOCATION_RE.search(prompt)
    if bracket:
        return bracket.group(1).strip() or "여행지"

    prefix = _TRAVEL_SPLIT_RE.split(prompt, maxsplit=1)[0].strip(" ,.-")
    if prefix == prompt.strip(" ,.-"):
        prefix = _ENGLISH_TRAVEL_SPLIT_RE.split(prompt, maxsplit=1)[0].strip(" ,.-")
    while True:
        cleaned = _LEADING_TRAVEL_FILLER_RE.sub("", prefix).strip()
        if cleaned == prefix:
            break
        prefix = cleaned
    return _korean_location_name(prefix) or "여행지"


def _korean_location_name(location: str) -> str:
    stripped = location.strip()
    if not stripped:
        return ""
    if re.search(r"[가-힣]", stripped):
        return stripped

    normalized = re.sub(r"[^a-zA-Z\s-]", " ", stripped).lower()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized in _KOREAN_LOCATION_NAMES:
        return _KOREAN_LOCATION_NAMES[normalized]

    tokens = normalized.split()
    for size in range(min(3, len(tokens)), 0, -1):
        candidate = " ".join(tokens[:size])
        if candidate in _KOREAN_LOCATION_NAMES:
            return _KOREAN_LOCATION_NAMES[candidate]
    return stripped


class OpenAICompatibleAdapter(ImageAdapter):
    def __init__(self) -> None:
        base_url = os.environ.get("IMAGE_API_BASE_URL", "").strip()
        if not base_url:
            raise SkillError(
                "MISSING_CONFIG",
                "IMAGE_API_BASE_URL is not set. Bind an OpenAI-compatible credential.",
            )
        self.base_url = base_url.rstrip("/")
        self.api_key = os.environ.get("IMAGE_API_KEY", "").strip()
        self.provider_profile = _provider_profile_for_base_url(self.base_url)

    def generate(
        self,
        *,
        prompt: str,
        model: str | None,
        aspect_ratio: str | None,
        image_size: str | None,
        timeout: float,
    ) -> GeneratedImage:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        selected_model = self._model_for_request(model)

        if self.provider_profile != "openrouter" and _uses_images_generations(selected_model):
            response = self._generate_with_images_api(
                headers=headers,
                prompt=prompt,
                model=selected_model,
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                timeout=timeout,
            )
            endpoint = "images/generations"
        else:
            response = self._generate_with_chat_completions(
                headers=headers,
                prompt=prompt,
                model=selected_model,
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                timeout=timeout,
            )
            endpoint = "chat/completions"

        image = _extract_image(response)
        return GeneratedImage(
            data=image.data,
            mime_type=image.mime_type,
            metadata={
                "provider": DEFAULT_PROVIDER,
                "provider_profile": self.provider_profile,
                "model": selected_model,
                "endpoint": endpoint,
                "aspect_ratio": aspect_ratio,
                "image_size": image_size,
                "prompt": prompt,
                **image.metadata,
            },
        )

    def _model_for_request(self, model: str | None) -> str:
        explicit = (model or "").strip()
        if explicit:
            return explicit
        env_model = os.environ.get("IMAGE_MODEL", "").strip()
        if env_model:
            return env_model
        if self.provider_profile == "openrouter":
            return OPENROUTER_DEFAULT_MODEL
        return DEFAULT_MODEL

    def _generate_with_images_api(
        self,
        *,
        headers: dict[str, str],
        prompt: str,
        model: str,
        aspect_ratio: str | None,
        image_size: str | None,
        timeout: float,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "size": _image_api_size(aspect_ratio, image_size),
        }
        return _post_json(
            f"{self.base_url}/images/generations",
            headers=headers,
            payload=payload,
            timeout=timeout,
        )

    def _generate_with_chat_completions(
        self,
        *,
        headers: dict[str, str],
        prompt: str,
        model: str,
        aspect_ratio: str | None,
        image_size: str | None,
        timeout: float,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "modalities": ["image", "text"],
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.provider_profile != "openrouter":
            payload["messages"] = [
                {
                    "role": "system",
                    "content": (
                        "Create the requested image. Return image data in the "
                        "response when the model supports it."
                    ),
                },
                {"role": "user", "content": prompt},
            ]
            image_config: dict[str, str] = {}
            if aspect_ratio:
                image_config["aspect_ratio"] = aspect_ratio
            if image_size:
                image_config["image_size"] = image_size
            if image_config:
                payload["image_config"] = image_config

        return _post_json(
            f"{self.base_url}/chat/completions",
            headers=headers,
            payload=payload,
            timeout=timeout,
        )


def _provider_profile_for_base_url(base_url: str) -> str:
    lowered = base_url.strip().lower()
    if "openrouter.ai" in lowered:
        return "openrouter"
    return "openai-compatible"


def _uses_images_generations(model: str) -> bool:
    normalized = model.strip().lower()
    model_name = normalized.rsplit("/", maxsplit=1)[-1]
    return any(model_name.startswith(prefix) for prefix in _DIRECT_IMAGE_MODELS)


def _image_api_size(aspect_ratio: str | None, image_size: str | None) -> str:
    normalized_size = (image_size or "").strip().lower().replace("×", "x")
    normalized_size = re.sub(r"\s+", "", normalized_size)
    if normalized_size in _IMAGE_API_SIZES:
        return normalized_size

    ratio = _parse_ratio(aspect_ratio)
    if ratio is None:
        return "1024x1024"
    width, height = ratio
    if width > height:
        return "1536x1024"
    if height > width:
        return "1024x1536"
    return "1024x1024"


def _parse_ratio(aspect_ratio: str | None) -> tuple[float, float] | None:
    if not aspect_ratio:
        return None
    normalized = aspect_ratio.strip().lower().replace("×", ":").replace("x", ":")
    parts = normalized.split(":", maxsplit=1)
    if len(parts) != 2:
        return None
    try:
        width = float(parts[0])
        height = float(parts[1])
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def _post_json(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
    max_attempts: int = _DEFAULT_HTTP_MAX_ATTEMPTS,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    attempts = max(1, int(max_attempts))
    deadline = time.monotonic() + timeout

    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        remaining_timeout = max(1.0, deadline - time.monotonic())
        try:
            with urllib.request.urlopen(req, timeout=remaining_timeout) as res:
                raw = res.read()
            break
        except urllib.error.HTTPError as exc:
            detail = _read_http_error_detail(exc)
            transient = exc.code in _TRANSIENT_HTTP_STATUS_CODES
            if transient and attempt < attempts:
                delay = _retry_delay_seconds(attempt)
                if time.monotonic() + delay < deadline:
                    time.sleep(delay)
                    continue
            code = "TRANSIENT_API_ERROR" if transient else "API_ERROR"
            raise SkillError(
                code,
                _format_http_error(exc, detail=detail, attempts=attempt),
            ) from exc
        except TimeoutError as exc:
            if attempt < attempts:
                delay = _retry_delay_seconds(attempt)
                if time.monotonic() + delay < deadline:
                    time.sleep(delay)
                    continue
            raise SkillError(
                "TRANSIENT_API_ERROR",
                _format_timeout_error(exc, attempts=attempt),
            ) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError):
                if attempt < attempts:
                    delay = _retry_delay_seconds(attempt)
                    if time.monotonic() + delay < deadline:
                        time.sleep(delay)
                        continue
                raise SkillError(
                    "TRANSIENT_API_ERROR",
                    _format_timeout_error(reason, attempts=attempt),
                ) from exc
            raise SkillError(
                "NETWORK_ERROR", f"Image API request failed: {exc.reason}"
            ) from exc
    else:
        raise SkillError("NETWORK_ERROR", "Image API request failed before sending.")

    try:
        decoded = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise SkillError(
            "INVALID_RESPONSE", f"Image API returned non-JSON: {exc}"
        ) from exc
    if not isinstance(decoded, dict):
        raise SkillError("INVALID_RESPONSE", "Image API returned a non-object response.")
    return decoded


def _retry_delay_seconds(attempt: int) -> float:
    return _RETRY_BASE_DELAY_SECONDS * (2 ** max(0, attempt - 1))


def _read_http_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")[:2000]
    except Exception:
        return ""


def _format_http_error(
    exc: urllib.error.HTTPError, *, detail: str, attempts: int
) -> str:
    reason = str(exc.reason or "").strip()
    status = f"HTTP {exc.code}" + (f" {reason}" if reason else "")
    retry_note = f" after {attempts} attempts" if attempts > 1 else ""
    message = f"Image API returned {status}{retry_note}."
    if exc.code in _TRANSIENT_HTTP_STATUS_CODES:
        message += (
            " The configured image endpoint is busy, unavailable, or timing out."
        )
    summary = _summarize_error_detail(detail)
    if summary:
        message += f" Detail: {summary}"
    return message


def _format_timeout_error(exc: TimeoutError, *, attempts: int) -> str:
    detail = str(exc).strip()
    retry_note = f" after {attempts} attempts" if attempts > 1 else ""
    message = f"Image API request timed out while waiting for a response{retry_note}."
    if detail:
        message += f" Detail: {detail}"
    return message


def _summarize_error_detail(detail: str) -> str:
    text = html.unescape(detail.strip())
    if not text:
        return ""
    text = _HTML_TAG_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    if len(text) > 500:
        text = text[:497].rstrip() + "..."
    return text


def _extract_image(response: dict[str, Any]) -> GeneratedImage:
    choices = response.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            image = _extract_image_from_value(message)
            if image is not None:
                return image

    image = _extract_image_from_value(response)
    if image is not None:
        return image

    raise SkillError("NO_IMAGE", "Image API response did not contain image data.")


def _extract_image_from_value(value: Any) -> GeneratedImage | None:
    if isinstance(value, dict):
        image = _extract_image_from_dict(value)
        if image is not None:
            return image
        for child in value.values():
            image = _extract_image_from_value(child)
            if image is not None:
                return image
    if isinstance(value, list):
        for child in value:
            image = _extract_image_from_value(child)
            if image is not None:
                return image
    if isinstance(value, str):
        return _extract_image_from_text(value)
    return None


def _extract_image_from_dict(value: dict[str, Any]) -> GeneratedImage | None:
    b64_json = value.get("b64_json")
    if isinstance(b64_json, str):
        return _decode_base64(
            b64_json,
            _mime_type_from_format(value.get("output_format"))
            or value.get("mime_type")
            or "image/png",
        )

    image_url = value.get("image_url")
    if isinstance(image_url, dict):
        url = image_url.get("url")
        if isinstance(url, str):
            image = _decode_data_url(url)
            if image is not None:
                return image
    if isinstance(image_url, str):
        image = _decode_data_url(image_url)
        if image is not None:
            return image

    url = value.get("url")
    if isinstance(url, str):
        image = _decode_data_url(url)
        if image is not None:
            return image

    if value.get("type") == "image" and isinstance(value.get("data"), str):
        return _decode_base64(value["data"], value.get("mime_type") or "image/png")
    return None


def _mime_type_from_format(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().lstrip(".")
    if normalized in {"jpg", "jpeg"}:
        return "image/jpeg"
    if normalized in {"png", "webp", "gif"}:
        return f"image/{normalized}"
    return None


def _extract_image_from_text(text: str) -> GeneratedImage | None:
    image = _decode_data_url(text)
    if image is not None:
        return image
    match = _RAW_BASE64_RE.search(text)
    if match:
        return _decode_base64(match.group(1), "image/png")
    return None


def _decode_data_url(value: str) -> GeneratedImage | None:
    match = _DATA_URL_RE.search(value)
    if not match:
        return None
    return _decode_base64(match.group(2), match.group(1))


def _decode_base64(value: str, mime_type: str) -> GeneratedImage | None:
    compact = "".join(value.split())
    try:
        data = base64.b64decode(compact, validate=True)
    except (ValueError, binascii.Error):
        return None
    if not data:
        return None
    return GeneratedImage(data=data, mime_type=mime_type, metadata={})


def _extension_for(mime_type: str, data: bytes) -> str:
    if mime_type == "image/jpeg" or data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if mime_type == "image/webp" or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        return "webp"
    if mime_type == "image/gif" or data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return "png"


def _adapter_for(provider: str) -> ImageAdapter:
    normalized = provider.strip().lower().replace("_", "-")
    if normalized == "openai-compatible":
        return OpenAICompatibleAdapter()
    raise SkillError("UNSUPPORTED_PROVIDER", f"Unsupported image provider: {provider}")


def generate_image(
    *,
    prompt: str,
    output_dir: str | os.PathLike[str],
    aspect_ratio: str | None = None,
    image_size: str | None = None,
    model: str | None = None,
    provider: str = DEFAULT_PROVIDER,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    prompt = prompt.strip()
    if not prompt:
        raise SkillError("EMPTY_PROMPT", "Prompt is empty.")
    prompt = prepare_prompt(prompt)

    adapter = _adapter_for(provider)
    image = adapter.generate(
        prompt=prompt,
        model=model,
        aspect_ratio=aspect_ratio,
        image_size=image_size,
        timeout=timeout,
    )
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = _extension_for(image.mime_type, image.data)
    output_path = out_dir / f"image-{int(time.time() * 1000)}.{ext}"
    output_path.write_bytes(image.data)

    return {
        "success": True,
        "output_path": str(output_path.resolve()),
        "mime_type": image.mime_type,
        "file_size_kb": round(len(image.data) / 1024, 1),
        **image.metadata,
    }


def _json_exit(code: str, message: str) -> NoReturn:
    print(json.dumps({"success": False, "error": code, "message": message}))
    raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an image.")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--provider", default=DEFAULT_PROVIDER)
    parser.add_argument("--model", default=None)
    parser.add_argument("--aspect-ratio", default=None)
    parser.add_argument("--image-size", default="1K")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args()

    output_dir = os.environ.get("OUTPUTS_DIR") or os.environ.get("SKILL_OUTPUT_DIR")
    if not output_dir:
        _json_exit("MISSING_OUTPUT_DIR", "OUTPUTS_DIR is not set.")

    try:
        result = generate_image(
            prompt=args.prompt,
            output_dir=output_dir,
            aspect_ratio=args.aspect_ratio,
            image_size=args.image_size,
            model=args.model,
            provider=args.provider,
            timeout=args.timeout,
        )
    except SkillError as exc:
        _json_exit(exc.code, exc.message)

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _json_exit("INTERRUPTED", "Image generation was interrupted.")
