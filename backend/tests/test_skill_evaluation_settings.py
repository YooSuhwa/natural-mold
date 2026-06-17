from __future__ import annotations

from app.config import Settings


def test_skill_evaluation_settings_have_safe_defaults() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.skill_evaluation_enabled is True
    assert settings.skill_evaluation_max_concurrent == 1
    assert settings.skill_evaluation_queue_max_size == 20
    assert settings.skill_evaluation_run_timeout_seconds == 180
    assert settings.skill_evaluation_case_timeout_seconds == 60


def test_skill_evaluation_settings_can_be_overridden_by_env_names(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SKILL_EVALUATION_ENABLED", "false")
    monkeypatch.setenv("SKILL_EVALUATION_MAX_CONCURRENT", "3")
    monkeypatch.setenv("SKILL_EVALUATION_QUEUE_MAX_SIZE", "7")
    monkeypatch.setenv("SKILL_EVALUATION_RUN_TIMEOUT_SECONDS", "90")
    monkeypatch.setenv("SKILL_EVALUATION_CASE_TIMEOUT_SECONDS", "15")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.skill_evaluation_enabled is False
    assert settings.skill_evaluation_max_concurrent == 3
    assert settings.skill_evaluation_queue_max_size == 7
    assert settings.skill_evaluation_run_timeout_seconds == 90
    assert settings.skill_evaluation_case_timeout_seconds == 15
