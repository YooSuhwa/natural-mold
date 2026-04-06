"""Discover available models from LLM providers."""

from __future__ import annotations

import logging

import httpx

from app.models.llm_provider import LLMProvider
from app.schemas.llm_provider import DiscoveredModel
from app.services.encryption import decrypt_api_key
from app.services.model_metadata import enrich_model, get_anthropic_models

logger = logging.getLogger(__name__)

_TIMEOUT = 15
_OPENAI_CHAT_PREFIXES = ("gpt-", "o1-", "o3-", "o4-", "chatgpt-")


async def discover_models(provider: LLMProvider) -> list[DiscoveredModel]:
    """Discover available models for a given provider."""
    api_key = decrypt_api_key(provider.api_key_encrypted) if provider.api_key_encrypted else None

    dispatch = {
        "openai": _discover_openai,
        "anthropic": _discover_anthropic,
        "google": _discover_google,
        "openrouter": _discover_openrouter,
        "openai_compatible": _discover_openai_compatible,
    }
    fn = dispatch.get(provider.provider_type)
    if not fn:
        return []
    return await fn(api_key=api_key, base_url=provider.base_url)


async def test_connection(provider: LLMProvider) -> tuple[bool, str, int | None]:
    """Test provider connection. Returns (success, message, models_count)."""
    try:
        models = await discover_models(provider)
        return True, f"{len(models)}개 모델 검색 성공", len(models)
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status == 401:
            return False, "인증 실패: API 키를 확인하세요", None
        if status == 403:
            return False, "접근 거부: API 키 권한을 확인하세요", None
        return False, f"HTTP {status} 오류", None
    except httpx.ConnectError:
        return False, "연결 실패: URL을 확인하세요", None
    except Exception as e:
        logger.warning("Provider connection test failed: %s", e)
        return False, "연결 테스트에 실패했습니다. 서버 로그를 확인하세요.", None


async def _discover_openai(
    api_key: str | None = None, base_url: str | None = None
) -> list[DiscoveredModel]:
    url = (base_url or "https://api.openai.com/v1").rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
    data = resp.json().get("data", [])
    models = []
    for m in data:
        mid = m.get("id", "")
        if any(mid.startswith(p) for p in _OPENAI_CHAT_PREFIXES):
            enriched = enrich_model(mid)
            models.append(
                DiscoveredModel(
                    model_name=mid,
                    display_name=enriched.get("display_name", mid),
                    context_window=enriched.get("context_window"),
                    input_modalities=enriched.get("input_modalities"),
                    output_modalities=enriched.get("output_modalities"),
                    cost_per_input_token=enriched.get("cost_per_input_token"),
                    cost_per_output_token=enriched.get("cost_per_output_token"),
                    max_output_tokens=enriched.get("max_output_tokens"),
                    supports_vision=enriched.get("supports_vision"),
                    supports_function_calling=enriched.get("supports_function_calling"),
                    supports_reasoning=enriched.get("supports_reasoning"),
                )
            )
    return sorted(models, key=lambda m: m.model_name)


async def _discover_anthropic(
    api_key: str | None = None, base_url: str | None = None
) -> list[DiscoveredModel]:
    # Anthropic has no /models endpoint — use static list + verify key.
    # NOTE: Key verification calls the actual API with max_tokens=1, which may incur minimal cost.
    if api_key:
        url = (base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                url,
                headers=headers,
                json={
                    "model": "claude-haiku-4-20250514",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
            # 200 or 400 (valid key), 401 (invalid key)
            if resp.status_code == 401:
                resp.raise_for_status()

    models = []
    for mid in get_anthropic_models():
        enriched = enrich_model(mid)
        models.append(
            DiscoveredModel(
                model_name=mid,
                display_name=enriched.get("display_name", mid),
                context_window=enriched.get("context_window"),
                input_modalities=enriched.get("input_modalities"),
                output_modalities=enriched.get("output_modalities"),
                cost_per_input_token=enriched.get("cost_per_input_token"),
                cost_per_output_token=enriched.get("cost_per_output_token"),
                max_output_tokens=enriched.get("max_output_tokens"),
                supports_vision=enriched.get("supports_vision"),
                supports_function_calling=enriched.get("supports_function_calling"),
                supports_reasoning=enriched.get("supports_reasoning"),
            )
        )
    return models


async def _discover_google(
    api_key: str | None = None, base_url: str | None = None
) -> list[DiscoveredModel]:
    url = "https://generativelanguage.googleapis.com/v1beta/models"
    params = {}
    if api_key:
        params["key"] = api_key
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
    data = resp.json().get("models", [])
    models = []
    for m in data:
        methods = m.get("supportedGenerationMethods", [])
        if "generateContent" not in methods:
            continue
        # name format: "models/gemini-2.0-flash"
        full_name = m.get("name", "")
        mid = full_name.removeprefix("models/")
        enriched = enrich_model(mid)
        models.append(
            DiscoveredModel(
                model_name=mid,
                display_name=enriched.get("display_name", m.get("displayName", mid)),
                context_window=enriched.get("context_window") or m.get("inputTokenLimit"),
                input_modalities=enriched.get("input_modalities"),
                output_modalities=enriched.get("output_modalities"),
                cost_per_input_token=enriched.get("cost_per_input_token"),
                cost_per_output_token=enriched.get("cost_per_output_token"),
                max_output_tokens=enriched.get("max_output_tokens"),
                supports_vision=enriched.get("supports_vision"),
                supports_function_calling=enriched.get("supports_function_calling"),
                supports_reasoning=enriched.get("supports_reasoning"),
            )
        )
    return sorted(models, key=lambda m: m.model_name)


async def _discover_openrouter(
    api_key: str | None = None, base_url: str | None = None
) -> list[DiscoveredModel]:
    url = "https://openrouter.ai/api/v1/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
    data = resp.json().get("data", [])
    models = []
    for m in data:
        mid = m.get("id", "")
        pricing = m.get("pricing", {})
        arch = m.get("architecture", {})
        top = m.get("top_provider", {})
        supported = m.get("supported_parameters", [])
        input_mod = arch.get("input_modalities")
        output_mod = arch.get("output_modalities")
        models.append(
            DiscoveredModel(
                model_name=mid,
                display_name=m.get("name", mid),
                context_window=m.get("context_length"),
                input_modalities=input_mod,
                output_modalities=output_mod,
                cost_per_input_token=pricing.get("prompt") if pricing else None,
                cost_per_output_token=pricing.get("completion") if pricing else None,
                max_output_tokens=top.get("max_completion_tokens"),
                supports_vision="image" in (input_mod or []),
                supports_function_calling="tools" in supported,
                supports_reasoning="reasoning" in supported,
            )
        )
    return sorted(models, key=lambda m: m.model_name)


async def _discover_openai_compatible(
    api_key: str | None = None, base_url: str | None = None
) -> list[DiscoveredModel]:
    if not base_url:
        return []

    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json().get("data", [])
        except httpx.HTTPStatusError:
            # Fallback: Ollama /api/tags
            ollama_url = base_url.rstrip("/").removesuffix("/v1") + "/api/tags"
            resp = await client.get(ollama_url, headers=headers)
            resp.raise_for_status()
            data = [{"id": m["name"]} for m in resp.json().get("models", [])]

    models = []
    for m in data:
        mid = m.get("id", m.get("name", ""))
        models.append(
            DiscoveredModel(
                model_name=mid,
                display_name=mid,
            )
        )
    return sorted(models, key=lambda m: m.model_name)
