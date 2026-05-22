"""Curated credential requirements for k-skill imports (Spec §5.6).

The upstream ``NomaDamas/k-skill`` repo ships skills whose runtime
contracts (env var names + which definition_key to bind) aren't
machine-derivable from SKILL.md alone. This module is the *source of
truth* for that mapping — the importer consults it instead of regex-
guessing requirement keys.

``REGEX_HINTS`` is review-only: the importer logs hints discovered in
each skill's source but never auto-attaches them. New k-skill releases
that need a new credential trigger a human review + a PR to this file.

Schema mirrors ``app.marketplace.credential_requirements.CredentialRequirement``
and ``schemas.CredentialRequirementIn`` so the dicts can be fed
directly into ``MarketplaceVersion.credential_requirements`` and
``Skill.credential_requirements``.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Curated map: upstream skill name → requirement entries
# ---------------------------------------------------------------------------


def _account_req(
    key: str,
    *,
    definition_key: str,
    label: str,
    description: str,
    env_user: str,
    env_pass: str,
) -> dict[str, Any]:
    """Two-field username/password account binding. Reused for SRT /
    KTX / forest-trip whose upstream scripts read the same env shape."""

    return {
        "key": key,
        "definition_key": definition_key,
        "required": True,
        "label": label,
        "description": description,
        "fields": ["username", "password"],
        "injection": "env",
        "scope": "user",
        "env_map": {"username": env_user, "password": env_pass},
    }


def _api_key_req(
    key: str,
    *,
    definition_key: str,
    label: str,
    description: str,
    env_name: str,
    required: bool = True,
) -> dict[str, Any]:
    """Single ``api_key`` binding — covers KIPRIS, DART, ODsay."""

    return {
        "key": key,
        "definition_key": definition_key,
        "required": required,
        "label": label,
        "description": description,
        "fields": ["api_key"],
        "injection": "env",
        "scope": "user",
        "env_map": {"api_key": env_name},
    }


def _hosted_proxy_req(
    key: str,
    *,
    label: str,
    description: str,
) -> dict[str, Any]:
    """운영자 proxy 키를 거치는 skill — 사용자는 별도 credential 등록 불필요
    (PRD §9 ``hosted_proxy`` 상태). UI는 "Uses hosted proxy" 칩만 표시하고
    install wizard에서 credential dropdown을 띄우지 않는다.
    """

    return {
        "key": key,
        "definition_key": "k_skill_proxy",
        "required": False,
        "label": label,
        "description": description,
        "fields": ["base_url"],
        "injection": "env",
        # ``system_dependency`` 표기 (Spec §10.8). 사용자 binding 흐름에서
        # 제외되고 시스템 credential resolver가 처리한다.
        "scope": "system_dependency",
        "env_map": {"base_url": "KSKILL_PROXY_BASE_URL"},
    }


def _manual_login_req(
    key: str,
    *,
    label: str,
    description: str,
) -> dict[str, Any]:
    """사용자가 브라우저/로컬 앱에서 직접 로그인해야 하는 skill (PRD §9
    ``manual_login``). UI는 "Manual login required" 칩으로 안내하고 install
    wizard credential step을 skip한다 — 실제 인증은 skill 실행 시점에 외부
    환경에서 이루어진다.
    """

    return {
        "key": key,
        "definition_key": "",
        "required": False,
        "label": label,
        "description": description,
        "fields": [],
        "injection": "manual",
        "scope": "manual",
        "env_map": {},
    }


# Each entry is a list because future skills may bind more than one
# credential (e.g. Google Workspace OAuth + a project ID API key).
K_SKILL_REQUIREMENT_MAP: dict[str, list[dict[str, Any]]] = {
    # Travel / booking
    "srt-booking": [
        _account_req(
            "srt_login",
            definition_key="srt_account",
            label="SRT 계정",
            description="SRT 예매에 사용할 회원 자격증명.",
            env_user="KSKILL_SRT_ID",
            env_pass="KSKILL_SRT_PASSWORD",
        ),
    ],
    "ktx-booking": [
        _account_req(
            "ktx_login",
            definition_key="ktx_account",
            label="KTX (Korail) 계정",
            description="KTX 예매에 사용할 Korail 회원 자격증명.",
            env_user="KSKILL_KTX_ID",
            env_pass="KSKILL_KTX_PASSWORD",
        ),
    ],
    "foresttrip-vacancy": [
        _account_req(
            "foresttrip_login",
            definition_key="foresttrip_account",
            label="숲나들e 계정",
            description="국립휴양림 예약 조회용 회원 자격증명.",
            env_user="KSKILL_FORESTTRIP_ID",
            env_pass="KSKILL_FORESTTRIP_PASSWORD",
        ),
    ],
    # Public-data APIs
    "korean-patent-search": [
        _api_key_req(
            "kipris_key",
            definition_key="kipris_plus_api",
            label="KIPRIS Plus API",
            description="KIPRIS Plus 서비스 키.",
            env_name="KSKILL_KIPRIS_KEY",
        ),
    ],
    "k-dart": [
        _api_key_req(
            "dart_key",
            definition_key="dart_api",
            label="DART Open API",
            description="OpenDART 인증키.",
            env_name="KSKILL_DART_KEY",
        ),
    ],
    "korean-transit-route": [
        _api_key_req(
            "odsay_key",
            definition_key="odsay_api",
            label="ODsay API",
            description="ODsay LAB 발급 apiKey.",
            env_name="KSKILL_ODSAY_KEY",
        ),
    ],
    # Optional — install proceeds even when the credential is missing.
    "coupang-product-search": [
        {
            "key": "coupang_partners",
            "definition_key": "coupang_partners",
            # ``required=False`` → install never blocks. Affiliate-link
            # enrichment is skipped at runtime when the binding is absent.
            "required": False,
            "label": "Coupang Partners (optional)",
            "description": (
                "쿠팡 파트너스 어필리에이트 키. 미설정 시 검색은 동작하나 "
                "어필리에이트 링크 생성이 비활성화됨."
            ),
            "fields": ["access_key", "secret_key"],
            "injection": "env",
            "scope": "user",
            "env_map": {
                "access_key": "KSKILL_COUPANG_ACCESS_KEY",
                "secret_key": "KSKILL_COUPANG_SECRET_KEY",
            },
        }
    ],
    # Hosted proxy — PRD §9 ``hosted_proxy``. 운영자 proxy key 사용.
    "seoul-density": [
        _hosted_proxy_req(
            "seoul_density_proxy",
            label="서울 실시간 인구 proxy",
            description="운영자가 발급한 서울 열린데이터광장 proxy를 통해 호출.",
        ),
    ],
    # Manual login — PRD §9 ``manual_login``. 외부 앱/브라우저 세션 사용.
    "kakaotalk-mac": [
        _manual_login_req(
            "kakaotalk_macos_session",
            label="카카오톡 (macOS) 세션",
            description="카카오톡 macOS 앱이 로그인된 상태로 실행되어야 한다.",
        ),
    ],
    # No credentials required.
    "korean-spell-check": [],
}


# ---------------------------------------------------------------------------
# Review-only regex hints
# ---------------------------------------------------------------------------


# When the importer scans a k-skill's source it logs hits against these
# patterns so the next manual review can spot a new credential
# requirement. **Never auto-bound** — the curated map above is the only
# authoritative source.
REGEX_HINTS: tuple[re.Pattern[str], ...] = (
    re.compile(r"KSKILL_[A-Z][A-Z0-9_]+", re.IGNORECASE),
    re.compile(r"API[_-]?KEY", re.IGNORECASE),
    re.compile(r"SECRET[_-]?KEY", re.IGNORECASE),
    re.compile(r"ACCESS[_-]?TOKEN", re.IGNORECASE),
    re.compile(r"os\.environ\[[\"'][A-Z_]+[\"']\]"),
)


def requirements_for(upstream_name: str) -> list[dict[str, Any]]:
    """Return the requirement entries for ``upstream_name``.

    Empty list = "no credentials". ``None`` is never returned — callers
    should not need a missing-key branch.
    """

    return list(K_SKILL_REQUIREMENT_MAP.get(upstream_name, ()))


def known_skills() -> set[str]:
    """Names that are explicitly known to the curated map. Importer
    logs a hint when a new upstream skill is encountered."""

    return set(K_SKILL_REQUIREMENT_MAP)


__all__ = [
    "K_SKILL_REQUIREMENT_MAP",
    "REGEX_HINTS",
    "known_skills",
    "requirements_for",
]
