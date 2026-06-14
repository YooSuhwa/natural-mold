from __future__ import annotations

from typing import Any


class SkillBuilderSourceSkillNotFound(LookupError):
    pass


class SkillBuilderValidationError(ValueError):
    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__("skill builder draft validation failed")
        self.result = result


class SkillBuilderConflictError(RuntimeError):
    def __init__(self, *, base_content_hash: str | None, current_content_hash: str | None) -> None:
        super().__init__("skill builder source skill changed")
        self.base_content_hash = base_content_hash
        self.current_content_hash = current_content_hash
