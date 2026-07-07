"""스킬 드래프트 워크스페이스 파일시스템 서비스 (스펙 AD-2).

세션마다 ``data/skill-drafts/<session_id>/`` 물리 디렉토리를 만들고, 런타임은
이를 가상 경로 ``/skill-drafts/<session_id>/``로 마운트한다. 경로는 전부
ADR-018 상대경로 계약(``storage/paths``)을 따른다.

M1은 생성만 담당한다 — 시드(improve 원본 복사)/첨부 복사(``inputs/``)/
디렉토리→SkillDraftFile 어댑터/GC는 M2에서 이 모듈에 추가된다.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from app.storage.paths import ensure_relative, resolve_data_path

SKILL_DRAFTS_ROOT = "skill-drafts"


def workspace_storage_path(session_id: uuid.UUID) -> str:
    """세션 워크스페이스의 data_root 기준 상대 경로 (ADR-018)."""

    return ensure_relative(f"{SKILL_DRAFTS_ROOT}/{session_id}")


def resolve_workspace_dir(storage_path: str) -> Path:
    """``draft_workspace_path`` 컬럼 값 → 절대 경로."""

    return resolve_data_path(storage_path)


def create_workspace(session_id: uuid.UUID) -> str:
    """워크스페이스 디렉토리를 만들고 상대 storage path를 반환한다 (멱등)."""

    storage_path = workspace_storage_path(session_id)
    resolve_data_path(storage_path).mkdir(parents=True, exist_ok=True)
    return storage_path
