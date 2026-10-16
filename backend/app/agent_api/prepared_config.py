"""Agent API ``AgentConfig`` resolution under the prepared-run gateway (Task 10, I17).

``materialize_and_activate`` calls the prepared adapter's resolver only after it
locked the queued run (Tenant -> Organization -> memberships -> Agent lifecycle
-> Conversation -> Run). This resolver re-reads, through that same locked
session, everything the legacy request path read before acceptance: the exact
API envelope linked to the run, the API key (revocation/grants/membership via
``authorize_deployment``), the active deployment, the scope for the run mode,
the scoped conversation and its runtime-policy snapshot, and the root agent
config (without legacy child compilation; the adapter resolves children).
Failures surface as the existing typed errors of those checks: a missing or
mismatched envelope/key/deployment/conversation is ``AGENT_API_RUN_NOT_FOUND``
(or the builder's ``AGENT_API_DEPLOYMENT_NOT_FOUND`` / ``AGENT_API_THREAD_NOT_FOUND``),
key state is ``AGENT_API_KEY_INVALID`` / ``AGENT_API_KEY_REVOKED`` (etc.), a
missing scope ``AGENT_API_SCOPE_REQUIRED``, policy problems ``ConflictError``.
A run the gateway cannot lock (removed member, archived org) never reaches this
resolver: the transaction raises ``prepared_run_not_materializable``. Nothing
is read after the marker (``assert_pre_marker``).

The resolved deployment/key/envelope/thread identifiers are folded into the
lease digest ``versions`` so the lease is bound to exactly this envelope.
"""

from __future__ import annotations

import dataclasses
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_api.dependencies import ApiKeyPrincipal
from app.agent_api.run_execution import build_config_for_run
from app.agent_runtime.prepared_run_inputs import PreparationInputs
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.runtime_hydration_guard import assert_pre_marker
from app.authz.org_context import OrgContext
from app.exceptions import NotFoundError
from app.models.agent_api import AgentApiKey, AgentApiRun, AgentDeployment
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.services.prepared_run_adapter import PreparedRunResolver

ApiScope = Literal["invoke", "stream"]


def _not_found() -> NotFoundError:
    return NotFoundError("AGENT_API_RUN_NOT_FOUND", "run not found")


@dataclasses.dataclass(frozen=True, slots=True)
class ApiRunTarget:
    """What the accepted request knows: the public envelope ID and the run mode."""

    public_run_id: str
    context: OrgContext
    scope: ApiScope


class ApiConfigResolver:
    """``ResolveConfig`` for one accepted API run; records the bound envelope."""

    def __init__(self, target: ApiRunTarget) -> None:
        self._target = target
        self.versions: dict[str, str] = {}

    async def __call__(self, db: AsyncSession, run: ConversationRun) -> AgentConfig:
        assert_pre_marker("agent_api_config")
        target = self._target
        envelope = await db.scalar(
            select(AgentApiRun).where(
                AgentApiRun.public_id == target.public_run_id,
                AgentApiRun.conversation_run_id == run.id,
                AgentApiRun.conversation_id == run.conversation_id,
                AgentApiRun.user_id == target.context.user_id,
            )
        )
        if envelope is None or envelope.api_key_id is None or envelope.deployment_id is None:
            raise _not_found()
        key = await db.scalar(select(AgentApiKey).where(AgentApiKey.id == envelope.api_key_id))
        deployment = await db.scalar(
            select(AgentDeployment).where(AgentDeployment.id == envelope.deployment_id)
        )
        conversation = await db.scalar(
            select(Conversation).where(Conversation.id == run.conversation_id)
        )
        if key is None or deployment is None or conversation is None:
            raise _not_found()
        if deployment.agent_id != run.agent_id:
            raise _not_found()
        principal = ApiKeyPrincipal(key=key, user_id=key.user_id, context=target.context)
        cfg = await build_config_for_run(
            db,
            principal=principal,
            deployment=deployment,
            conversation=conversation,
            external_user=None,
            required_scope=target.scope,
            include_subagents=False,
        )
        self.versions = {
            "api_envelope": str(envelope.id),
            "api_public_run_id": envelope.public_id,
            "api_key": str(key.id),
            "api_deployment": str(deployment.id),
            "api_deployment_public_id": envelope.deployment_public_id_snapshot,
            "api_thread": str(envelope.thread_id or ""),
        }
        return cfg


class ApiPreparedRunResolver:
    """``prepare_inputs(db, run)``: the shared adapter plus the API envelope binding."""

    def __init__(self, inner: PreparedRunResolver, config: ApiConfigResolver) -> None:
        self._inner = inner
        self._config = config

    @property
    def execution_cfg(self) -> AgentConfig | None:
        return self._inner.execution_cfg

    async def __call__(self, db: AsyncSession, run: ConversationRun) -> PreparationInputs:
        inputs = await self._inner(db, run)
        if not self._config.versions:
            raise _not_found()
        versions = {**inputs.versions, **self._config.versions}
        return dataclasses.replace(inputs, versions=versions)
