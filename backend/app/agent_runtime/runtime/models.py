"""모델 후보/폴백/재시도 판정 — ``runtime_component_builder`` 에서 분리 (BE-S10).

Patch-contract: 테스트는 ``runtime_component_builder.create_chat_model`` 을
patch 한다. 이 모듈의 함수들은 ``create_chat_model`` 을 builder 모듈 경유
call-time import 로 조회해 그 patch 가 계속 유효하다 (BE-S8 recorder→facade
패턴). 직접 top-level import 로 바꾸면 patch 가 우회된다.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models import BaseChatModel

from app.agent_runtime.runtime_config import AgentConfig
from app.exceptions import AppError

logger = logging.getLogger(__name__)


class MiddlewareModelCredentialRequiredError(AppError):
    """Raised when middleware model config has no user-owned provider key."""

    def __init__(self, provider: str) -> None:
        super().__init__(
            code="middleware_model_credential_required",
            message=(
                f"미들웨어 모델({provider})에 사용할 본인의 LLM API 키가 등록되어 있지 않습니다. "
                "/credentials 페이지에서 해당 제공자의 키를 등록하거나 미들웨어 모델 설정을 "
                "변경해주세요."
            ),
            status=422,
        )


_MIDDLEWARE_MODEL_FIELDS = frozenset({"model", "fallback_model"})


def _resolve_middleware_model_params(
    configs: list[dict[str, Any]],
    provider_api_keys: dict[str, str | None],
) -> list[dict[str, Any]]:
    """미들웨어 config의 model 문자열을 BaseChatModel 객체로 사전 해석.

    User-facing agent runtime must not fall through to env/system credentials.
    The caller provides only user-owned provider keys; missing keys become a
    clear 422 error before LangChain model construction.
    """
    # Call-time facade lookup: 테스트가 runtime_component_builder.create_chat_model
    # 을 patch 하므로 builder 모듈 경유로 조회해야 patch 가 유효하다 (monkeypatch 투명성).
    from app.agent_runtime.runtime_component_builder import create_chat_model

    resolved = []
    for config in configs:
        params = dict(config.get("params", {}))
        for field_name in _MIDDLEWARE_MODEL_FIELDS:
            val = params.get(field_name)
            if isinstance(val, str) and ":" in val:
                prov, mname = val.split(":", 1)
                api_key = provider_api_keys.get(prov)
                if not api_key:
                    raise MiddlewareModelCredentialRequiredError(prov)
                params[field_name] = create_chat_model(
                    prov,
                    mname,
                    api_key=api_key,
                    allow_env_fallback=False,
                )
        resolved.append({**config, "params": params})
    return resolved


def _model_constructor_params(cfg: AgentConfig) -> dict[str, Any]:
    params = dict(cfg.model_params or {})
    params.pop("recursion_limit", None)
    # Forward the model context limit so ``create_chat_model`` injects it into
    # ``model.profile`` (auto-summarization threshold = single source of truth).
    if cfg.context_window:
        params["context_window"] = cfg.context_window
    return params


def _model_chain(cfg: AgentConfig) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = [
        {
            "provider": cfg.provider,
            "model_name": cfg.model_name,
            "base_url": cfg.base_url,
        }
    ]
    chain.extend(cfg.model_fallback_chain or [])
    return chain


def _build_model_candidates(cfg: AgentConfig) -> list[BaseChatModel]:
    """Construct the primary chat model, walking ``model_fallback_chain``
    when the primary raises a recoverable error.

    This mirrors :func:`app.agent_runtime.model_factory.create_chat_model_with_fallback`
    but operates on the pre-resolved chain in ``AgentConfig`` so the executor
    can stay synchronous and DB-free. The chain entries are resolved by the
    caller (chat_service / trigger_executor) which has the DB session.
    """

    from app.agent_runtime.model_factory import _is_fallback_recoverable

    # Call-time facade lookup: 테스트가 runtime_component_builder.create_chat_model
    # 을 patch 하므로 builder 모듈 경유로 조회해야 patch 가 유효하다 (monkeypatch 투명성).
    from app.agent_runtime.runtime_component_builder import create_chat_model

    last_error: BaseException | None = None
    candidates: list[BaseChatModel] = []
    params = _model_constructor_params(cfg)
    chain = _model_chain(cfg)

    for idx, entry in enumerate(chain):
        try:
            candidates.append(
                create_chat_model(
                    entry["provider"],
                    entry["model_name"],
                    cfg.api_key,
                    entry.get("base_url"),
                    **params,
                )
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if not candidates:
                if idx == len(chain) - 1 or not _is_fallback_recoverable(exc):
                    raise
                logger.info(
                    "model %s/%s failed; trying fallback (%d remaining)",
                    entry["provider"],
                    entry["model_name"],
                    len(chain) - idx - 1,
                )
                continue
            logger.warning(
                "fallback model %s/%s could not be constructed; runtime fallback will skip it",
                entry["provider"],
                entry["model_name"],
                exc_info=True,
            )

    if candidates:
        return candidates
    assert last_error is not None  # noqa: S101 — loop invariant (type narrowing)
    raise last_error


def _build_model_with_fallback(cfg: AgentConfig) -> BaseChatModel:
    """Backward-compatible helper that returns the first constructible candidate."""

    return _build_model_candidates(cfg)[0]


def _is_retryable_model_error(exc: Exception) -> bool:
    from app.agent_runtime.model_factory import _is_fallback_recoverable

    if _is_fallback_recoverable(exc):
        return True
    return isinstance(exc, ValueError) and "No generations found in stream" in str(exc)
