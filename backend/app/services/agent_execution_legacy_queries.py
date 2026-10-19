"""Legacy read-only lifecycle queries re-exported by ``agent_execution_owner``.

Task 10 I4: ``agent_lifecycle_projection`` imports these names from
``agent_execution_owner``; they stay the original lock-free query objects.
"""

from __future__ import annotations

from app.services.conversation_run_envelope_projection import (
    UnlinkedEnvelopeConflicts,
    active_unlinked_envelopes,
)
from app.services.conversation_run_ownership_cancellation import participant_run_conflicts

__all__ = ["UnlinkedEnvelopeConflicts", "active_unlinked_envelopes", "participant_run_conflicts"]
