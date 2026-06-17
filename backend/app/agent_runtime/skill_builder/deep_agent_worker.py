from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from app.agent_runtime.skill_builder.graph import HeuristicDraftWorker
from app.agent_runtime.skill_builder.state import SkillBuilderState
from app.schemas.skill_builder import SkillDraftPackage


@dataclass(frozen=True, slots=True)
class SandboxedDeepAgentDraftWorker:
    draft_root: Path
    fallback: HeuristicDraftWorker = HeuristicDraftWorker()

    async def draft(self, state: SkillBuilderState) -> SkillDraftPackage:
        self.session_root(uuid.UUID(state["session_id"])).mkdir(parents=True, exist_ok=True)
        return await self.fallback.draft(state)

    def session_root(self, session_id: uuid.UUID) -> Path:
        return (self.draft_root / str(session_id)).resolve()
