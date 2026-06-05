from __future__ import annotations

import re
import uuid

import pytest

from app.agent_runtime.identity import (
    AGENT_IDENTITY_FIXED,
    AGENT_IDENTITY_PER_USER,
    AgentIdentityError,
    AgentRunSource,
    derive_child_agent_run_identity,
    make_agent_runtime_name,
    resolve_agent_run_identity,
)
from app.models.agent import Agent


def test_make_agent_runtime_name_is_tool_safe_and_agent_stable() -> None:
    agent_id = uuid.UUID("12345678-90ab-cdef-1234-567890abcdef")

    runtime_name = make_agent_runtime_name(agent_id)

    assert runtime_name == "agent_12345678"
    assert re.fullmatch(r"agent_[a-f0-9]{8}", runtime_name)


def test_fixed_identity_uses_agent_owner_as_credential_subject() -> None:
    agent_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    caller_id = uuid.uuid4()

    identity = resolve_agent_run_identity(
        agent_id=agent_id,
        agent_owner_user_id=owner_id,
        runtime_name=make_agent_runtime_name(agent_id),
        identity_mode=AGENT_IDENTITY_FIXED,
        source=AgentRunSource.CHAT,
        caller_user_id=caller_id,
    )

    assert identity.credential_subject_user_id == owner_id
    assert identity.caller_user_id == caller_id
    assert identity.runtime_name == make_agent_runtime_name(agent_id)


def test_per_user_chat_identity_uses_authenticated_caller() -> None:
    agent_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    caller_id = uuid.uuid4()

    identity = resolve_agent_run_identity(
        agent_id=agent_id,
        agent_owner_user_id=owner_id,
        runtime_name=make_agent_runtime_name(agent_id),
        identity_mode=AGENT_IDENTITY_PER_USER,
        source=AgentRunSource.CHAT,
        caller_user_id=caller_id,
    )

    assert identity.credential_subject_user_id == caller_id


def test_per_user_trigger_identity_is_rejected() -> None:
    agent_id = uuid.uuid4()

    with pytest.raises(AgentIdentityError):
        resolve_agent_run_identity(
            agent_id=agent_id,
            agent_owner_user_id=uuid.uuid4(),
            runtime_name=make_agent_runtime_name(agent_id),
            identity_mode=AGENT_IDENTITY_PER_USER,
            source=AgentRunSource.TRIGGER,
            caller_user_id=None,
        )


def test_child_per_user_identity_uses_parent_caller_subject() -> None:
    child_id = uuid.uuid4()
    child_owner_id = uuid.uuid4()
    caller_id = uuid.uuid4()
    parent_identity = resolve_agent_run_identity(
        agent_id=uuid.uuid4(),
        agent_owner_user_id=uuid.uuid4(),
        runtime_name=make_agent_runtime_name(uuid.uuid4()),
        identity_mode=AGENT_IDENTITY_FIXED,
        source=AgentRunSource.CHAT,
        caller_user_id=caller_id,
    )
    child = Agent(
        id=child_id,
        user_id=child_owner_id,
        name="Child",
        system_prompt="Help",
        model_id=uuid.uuid4(),
        runtime_name=make_agent_runtime_name(child_id),
        identity_mode=AGENT_IDENTITY_PER_USER,
    )

    identity = derive_child_agent_run_identity(parent_identity, child)

    assert identity.source == AgentRunSource.SUBAGENT
    assert identity.agent_id == child_id
    assert identity.agent_owner_user_id == child_owner_id
    assert identity.caller_user_id == caller_id
    assert identity.credential_subject_user_id == caller_id
    assert identity.runtime_name == child.runtime_name


def test_child_fixed_identity_uses_child_owner_subject() -> None:
    child_id = uuid.uuid4()
    child_owner_id = uuid.uuid4()
    parent_identity = resolve_agent_run_identity(
        agent_id=uuid.uuid4(),
        agent_owner_user_id=uuid.uuid4(),
        runtime_name=make_agent_runtime_name(uuid.uuid4()),
        identity_mode=AGENT_IDENTITY_FIXED,
        source=AgentRunSource.CHAT,
        caller_user_id=uuid.uuid4(),
    )
    child = Agent(
        id=child_id,
        user_id=child_owner_id,
        name="Child",
        system_prompt="Help",
        model_id=uuid.uuid4(),
        runtime_name=make_agent_runtime_name(child_id),
        identity_mode=AGENT_IDENTITY_FIXED,
    )

    identity = derive_child_agent_run_identity(parent_identity, child)

    assert identity.source == AgentRunSource.SUBAGENT
    assert identity.credential_subject_user_id == child_owner_id
