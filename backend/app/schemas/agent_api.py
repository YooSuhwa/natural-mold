from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

AgentApiScope = Literal["invoke", "stream", "background", "read"]


class AgentDeploymentCreate(BaseModel):
    agent_id: uuid.UUID
    allow_streaming: bool = True
    allow_background: bool = False
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=600)
    daily_token_limit: int | None = Field(default=None, ge=1)


class AgentDeploymentUpdate(BaseModel):
    status: Literal["active", "disabled"] | None = None
    allow_streaming: bool | None = None
    allow_background: bool | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=600)
    daily_token_limit: int | None = Field(default=None, ge=1)


class AgentDeploymentResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    agent_name: str
    public_id: str
    status: str
    allow_streaming: bool
    allow_background: bool
    rate_limit_per_minute: int | None
    daily_token_limit: int | None
    created_at: datetime
    updated_at: datetime


class AgentDeploymentCandidateResponse(BaseModel):
    agent_id: uuid.UUID
    agent_name: str
    runtime_name: str | None
    existing_deployment_id: uuid.UUID | None
    existing_public_id: str | None
    eligible: bool
    ineligible_reason: str | None = None


class AgentApiKeyDeploymentRef(BaseModel):
    deployment_id: uuid.UUID
    agent_id: uuid.UUID
    agent_name: str
    public_id: str
    status: str


class AgentApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    scopes: list[AgentApiScope] = Field(default_factory=lambda: ["invoke", "stream"])
    allow_all_deployments: bool = False
    deployment_ids: list[uuid.UUID] = Field(default_factory=list)
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


class AgentApiKeyCreatedResponse(BaseModel):
    id: uuid.UUID
    key: str
    key_id: str
    prefix: str
    last_four: str
    scopes: list[str]
    allow_all_deployments: bool
    deployments: list[AgentApiKeyDeploymentRef]
    expires_at: datetime | None
    created_at: datetime


class AgentApiKeyListResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    key_id: str
    prefix: str
    last_four: str
    scopes: list[str]
    allow_all_deployments: bool
    deployments: list[AgentApiKeyDeploymentRef]
    revoked_at: datetime | None
    expires_at: datetime | None
    last_used_at: datetime | None
    usage_count: int
    created_at: datetime


class AgentThreadCreateRequest(BaseModel):
    agent_id: str
    user: str | None = None
    metadata: dict[str, Any] | None = None


class AgentThreadResponse(BaseModel):
    id: str
    agent_id: str
    conversation_id: uuid.UUID
    user: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class AgentRunInput(BaseModel):
    messages: list[dict[str, Any]]


class AgentRunRequest(BaseModel):
    agent_id: str
    input: AgentRunInput
    stream_mode: list[str] = Field(default_factory=lambda: ["messages"])
    user: str | None = None
    metadata: dict[str, Any] | None = None


class AgentRunResponse(BaseModel):
    id: str
    thread_id: str | None
    agent_id: str
    status: str
    output: dict[str, Any] | None = None
    created_at: datetime
    finished_at: datetime | None = None
