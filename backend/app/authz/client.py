"""Bounded native SDK adapter; unavailable authorization never becomes an allow."""

from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from time import monotonic
from typing import Literal, TypeVar

import anyio
from aiohttp import ClientError
from openfga_sdk.client import (
    ClientCheckRequest,
    ClientConfiguration,
    OpenFgaClient,
)
from openfga_sdk.client.models.batch_check_item import ClientBatchCheckItem
from openfga_sdk.client.models.batch_check_request import ClientBatchCheckRequest
from openfga_sdk.client.models.list_objects_request import ClientListObjectsRequest
from openfga_sdk.configuration import RetryParams
from openfga_sdk.credentials import CredentialConfiguration, Credentials
from openfga_sdk.exceptions import ApiException, FgaValidationException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, TypeAdapter, ValidationError

from app.authz.config import AuthzSettings

T = TypeVar("T")
Consistency = Literal["MINIMIZE_LATENCY", "HIGHER_CONSISTENCY"]


class CheckQuery(BaseModel):
    model_config = ConfigDict(frozen=True)
    user: str
    relation: str
    object: str
    context: dict[str, int | str] = Field(default_factory=dict)


class ListQuery(BaseModel):
    model_config = ConfigDict(frozen=True)
    user: str
    relation: str
    type: str
    context: dict[str, int | str] = Field(default_factory=dict)


class AuthzUnavailable(RuntimeError):
    """An operation could not produce a trustworthy authorization answer."""

    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(f"OpenFGA {operation} unavailable")


@dataclass(slots=True)
class ClientMetrics:
    """Mutable low-cardinality counters; no principal/resource/token labels."""

    counts: Counter[tuple[str, str]] = field(default_factory=Counter)
    seconds: dict[str, float] = field(default_factory=dict)

    def record(self, operation: str, outcome: str, seconds: float) -> None:
        self.counts[(operation, outcome)] += 1
        self.seconds[operation] = self.seconds.get(operation, 0) + seconds


def sdk_configuration(settings: AuthzSettings) -> ClientConfiguration:
    """SDK owns transient retry; outer operation budget includes those retries."""
    return ClientConfiguration(
        api_url=settings.openfga_api_url,
        store_id=settings.openfga_store_id or None,
        authorization_model_id=settings.openfga_model_id or None,
        credentials=Credentials(
            method="api_token",
            configuration=CredentialConfiguration(
                api_token=settings.openfga_api_token.get_secret_value()
            ),
        ),
        timeout_millisec=settings.openfga_timeout_ms,
        retry_params=RetryParams(max_retry=2, min_wait_in_ms=25),
        ssl_ca_cert=settings.openfga_ca_cert or None,
    )


class FgaClient:
    """Typed adapter over an owned SDK session; no request-to-request result cache."""

    def __init__(self, sdk: OpenFgaClient, timeout_ms: int) -> None:
        self.sdk = sdk
        self.timeout_seconds = timeout_ms / 1000
        self.metrics = ClientMetrics()

    async def _run(self, operation: str, call: Awaitable[T]) -> T:
        started = monotonic()
        outcome = "error"
        try:
            with anyio.fail_after(self.timeout_seconds):
                result = await call
            outcome = "success"
            return result
        except (
            TimeoutError,
            ClientError,
            ApiException,
            FgaValidationException,
            ValidationError,
        ) as exc:
            raise AuthzUnavailable(operation) from exc
        finally:
            self.metrics.record(operation, outcome, monotonic() - started)

    async def check(
        self, query: CheckQuery, *, consistency: Consistency = "MINIMIZE_LATENCY"
    ) -> bool:
        return await self._run("check", self._check(query, consistency))

    async def _check(self, query: CheckQuery, consistency: Consistency) -> bool:
        response = await self.sdk.check(
            ClientCheckRequest(
                user=query.user, relation=query.relation, object=query.object, context=query.context
            ),
            {"consistency": consistency},
        )
        return TypeAdapter(StrictBool).validate_python(response.allowed)

    async def batch_check(
        self, queries: Sequence[CheckQuery], *, consistency: Consistency = "MINIMIZE_LATENCY"
    ) -> tuple[bool, ...]:
        if not queries:
            return ()
        return await self._run("batch_check", self._batch_check(queries, consistency))

    async def _batch_check(
        self, queries: Sequence[CheckQuery], consistency: Consistency
    ) -> tuple[bool, ...]:
        response = await self.sdk.batch_check(
            ClientBatchCheckRequest(
                checks=[
                    ClientBatchCheckItem(
                        user=query.user,
                        relation=query.relation,
                        object=query.object,
                        context=query.context,
                        correlation_id=str(index),
                    )
                    for index, query in enumerate(queries)
                ]
            ),
            {"consistency": consistency, "max_batch_size": 50, "max_parallel_requests": 4},
        )
        answers: dict[str, bool] = {}
        for item in response.result:
            if item.error is not None or item.correlation_id in answers:
                raise AuthzUnavailable("batch_check")
            answers[item.correlation_id] = TypeAdapter(StrictBool).validate_python(item.allowed)
        expected = {str(index) for index in range(len(queries))}
        if answers.keys() != expected:
            raise AuthzUnavailable("batch_check")
        return tuple(answers[str(index)] for index in range(len(queries)))

    async def list_objects(self, query: ListQuery) -> tuple[str, ...]:
        return await self._run("list_objects", self._list_objects(query))

    async def _list_objects(self, query: ListQuery) -> tuple[str, ...]:
        response = await self.sdk.list_objects(
            ClientListObjectsRequest(
                user=query.user, relation=query.relation, type=query.type, context=query.context
            )
        )
        return TypeAdapter(tuple[str, ...]).validate_python(response.objects)


@asynccontextmanager
async def open_client(settings: AuthzSettings) -> AsyncIterator[FgaClient]:
    """Close the SDK's HTTP pool even when permission checks are cancelled."""
    async with OpenFgaClient(sdk_configuration(settings)) as sdk:
        yield FgaClient(sdk, settings.openfga_timeout_ms)
