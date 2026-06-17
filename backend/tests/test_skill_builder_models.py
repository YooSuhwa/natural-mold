from __future__ import annotations

from app.database import Base


def test_skill_builder_models_are_registered() -> None:
    from app.models import (
        SkillBuilderSession,
        SkillEvaluationRun,
        SkillEvaluationSet,
        SkillRevision,
    )

    assert SkillBuilderSession.__tablename__ == "skill_builder_sessions"
    assert SkillEvaluationSet.__tablename__ == "skill_evaluation_sets"
    assert SkillEvaluationRun.__tablename__ == "skill_evaluation_runs"
    assert SkillRevision.__tablename__ == "skill_revisions"


def test_skill_builder_metadata_contains_expected_tables_and_indexes() -> None:
    tables = Base.metadata.tables

    assert {
        "skill_builder_sessions",
        "skill_evaluation_sets",
        "skill_evaluation_runs",
        "skill_revisions",
    } <= set(tables.keys())

    builder_columns = set(tables["skill_builder_sessions"].columns.keys())
    assert {
        "id",
        "user_id",
        "mode",
        "status",
        "source_skill_id",
        "finalized_skill_id",
        "draft_package",
        "compatibility_result",
        "eval_result",
        "created_at",
        "updated_at",
    } <= builder_columns

    revision_columns = set(tables["skill_revisions"].columns.keys())
    assert {
        "skill_id",
        "revision_number",
        "operation",
        "object_key",
        "content_hash",
        "changelog_items",
        "compatibility_result",
        "evaluation_summary",
    } <= revision_columns

    skill_columns = set(tables["skills"].columns.keys())
    assert "current_revision_id" in skill_columns

    revision_indexes = {index.name for index in tables["skill_revisions"].indexes}
    assert "ix_skill_revisions_skill_created" in revision_indexes
    assert "ix_skill_revisions_user_created" in revision_indexes

    revision_constraints = {constraint.name for constraint in tables["skill_revisions"].constraints}
    assert "uq_skill_revisions_number" in revision_constraints
