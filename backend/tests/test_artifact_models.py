from __future__ import annotations

from app.models import ArtifactVersion, ConversationArtifact


def test_artifact_models_are_registered() -> None:
    assert ConversationArtifact.__tablename__ == "conversation_artifacts"
    assert ArtifactVersion.__tablename__ == "artifact_versions"

