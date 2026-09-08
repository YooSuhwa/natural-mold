"""Stable identifiers for leader-owned APScheduler maintenance jobs."""

from enum import StrEnum


class LeaderSchedulerJobId(StrEnum):
    CREDENTIAL_ROTATION = "credential_rotation"
    CATALOG_UPDATE = "catalog_update"
    CATALOG_BOOTSTRAP = "catalog_update_bootstrap"
    HEALTH_CHECK = "health_check_sweep"
    REFRESH_TOKEN_GC = "refresh_token_gc"  # noqa: S105 - stable scheduler job id
    DRAFT_CONVERSATION_GC = "draft_conversation_gc"
    SKILL_DRAFT_GC = "skill_draft_workspace_gc"
    ORPHAN_ATTACHMENT_GC = "orphan_attachment_gc"
    MCP_HEALTH = "mcp_health_poll"
    CONVERSATION_QUEUE_RECOVERY = "conversation_queue_recovery"
    CONVERSATION_RUN_STALE_SWEEP = "conversation_run_stale_sweep"
    SKILL_RUNTIME_CLEANUP = "skill_runtime_cleanup"
    BROKER_EVICTION = "broker_eviction"
