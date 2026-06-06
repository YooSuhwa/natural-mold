# Greenfield credentials/tools/skills/mcp models — registered first so other
# modules picking them up via ``Base.metadata`` see the new schema.
# Existing domain models.
from app.models.agent import Agent
from app.models.agent_api import (
    AgentApiKey,
    AgentApiKeyDeployment,
    AgentApiRun,
    AgentApiThread,
    AgentDeployment,
)
from app.models.agent_subagent import AgentSubAgentLink
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.audit_event import AuditEvent
from app.models.builder_session import BuilderSession
from app.models.conversation import Conversation
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.models.credential import Credential
from app.models.credential_audit_log import CredentialAuditLog
from app.models.credential_default import CredentialDefault
from app.models.daily_spend_agent import DailySpendAgent
from app.models.daily_spend_model import DailySpendModel
from app.models.daily_spend_user import DailySpendUser
from app.models.health_check_history import HealthCheckHistory
from app.models.marketplace import (
    MarketplaceInstallation,
    MarketplaceItem,
    MarketplaceItemACL,
    MarketplacePublicationLink,
    MarketplaceVersion,
    SkillCredentialBinding,
)
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.memory import (
    AgentMemorySettings,
    MemoryProposal,
    MemoryRecord,
    UserMemorySettings,
)
from app.models.message_attachment import MessageAttachment
from app.models.message_event import MessageEvent, MessageEventChunk
from app.models.message_feedback import MessageFeedback
from app.models.model import Model
from app.models.refresh_token import RefreshToken
from app.models.share_link import ShareLink
from app.models.skill import AgentSkillLink, Skill
from app.models.system_llm_setting import SystemLlmSetting
from app.models.template import Template
from app.models.token_usage import TokenUsage
from app.models.tool import AgentToolLink, Tool
from app.models.user import User

__all__ = [
    "Agent",
    "AgentApiKey",
    "AgentApiKeyDeployment",
    "AgentApiRun",
    "AgentApiThread",
    "AgentDeployment",
    "AgentMcpToolLink",
    "AgentSkillLink",
    "AgentSubAgentLink",
    "AgentToolLink",
    "AgentTrigger",
    "AgentTriggerRun",
    "AuditEvent",
    "BuilderSession",
    "Conversation",
    "ConversationArtifact",
    "Credential",
    "CredentialAuditLog",
    "CredentialDefault",
    "DailySpendAgent",
    "DailySpendModel",
    "DailySpendUser",
    "HealthCheckHistory",
    "MarketplaceInstallation",
    "MarketplaceItem",
    "MarketplaceItemACL",
    "MarketplacePublicationLink",
    "MarketplaceVersion",
    "AgentMemorySettings",
    "ArtifactVersion",
    "MemoryProposal",
    "MemoryRecord",
    "McpServer",
    "McpTool",
    "MessageAttachment",
    "MessageEvent",
    "MessageEventChunk",
    "MessageFeedback",
    "Model",
    "RefreshToken",
    "ShareLink",
    "Skill",
    "SkillCredentialBinding",
    "SystemLlmSetting",
    "Template",
    "TokenUsage",
    "Tool",
    "UserMemorySettings",
    "User",
]
