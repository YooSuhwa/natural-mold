# Greenfield credentials/tools/skills/mcp models — registered first so other
# modules picking them up via ``Base.metadata`` see the new schema.
from app.models.credential import Credential
from app.models.credential_audit_log import CredentialAuditLog
from app.models.credential_default import CredentialDefault
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.skill import AgentSkillLink, Skill
from app.models.tool import AgentToolLink, Tool

# Existing domain models.
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.agent_trigger import AgentTrigger
from app.models.builder_session import BuilderSession
from app.models.connection import Connection  # legacy — deleted in M5
from app.models.conversation import Conversation
from app.models.llm_provider import LLMProvider  # legacy — deleted in M5
from app.models.model import Model
from app.models.template import Template
from app.models.token_usage import TokenUsage
from app.models.user import User

__all__ = [
    "Agent",
    "AgentSkillLink",
    "AgentSubAgentLink",
    "AgentToolLink",
    "AgentTrigger",
    "BuilderSession",
    "Connection",
    "Conversation",
    "Credential",
    "CredentialAuditLog",
    "CredentialDefault",
    "LLMProvider",
    "McpServer",
    "McpTool",
    "Model",
    "Skill",
    "Template",
    "TokenUsage",
    "Tool",
    "User",
]
