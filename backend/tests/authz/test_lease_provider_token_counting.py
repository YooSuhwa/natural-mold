"""Token counting and the Google sync SDK side never bypass the permit (I12a, M1)."""

from __future__ import annotations

from typing import Any, cast

import httpx
import pytest
from langchain_core.messages import HumanMessage

from app.agent_runtime.lease_bound_model import LeaseProviderTransportError
from app.agent_runtime.lease_google_chat_model import LeaseGoogleChatModel
from app.agent_runtime.lease_google_transport import (
    GoogleTransportBundle,
    new_google_transport_bundle,
    verify_and_freeze_google_transport,
)
from tests.authz.lease_provider_wire import Wire, build, guard_for, install_wire


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    return install_wire(monkeypatch)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method", ["get_num_tokens", "get_num_tokens_from_messages", "get_token_ids"]
)
@pytest.mark.parametrize("case", ["openai", "anthropic", "google"])
async def test_token_counting_is_refused_with_zero_sends(
    wire: Wire, case: str, method: str
) -> None:
    owned = build(case, guard_for(wire))
    argument: Any = [HumanMessage("hello")] if method.endswith("messages") else "hello"
    with pytest.raises(LeaseProviderTransportError) as caught:
        getattr(owned.model, method)(argument)
    assert caught.value.code == "provider_transport_not_single_send"
    assert wire.events == [] and wire.hosts == []
    await owned.aclose()


@pytest.mark.asyncio
async def test_google_sync_sdk_client_is_the_owned_seam_client_closed_once(wire: Wire) -> None:
    owned = build("google", guard_for(wire))
    model = cast(LeaseGoogleChatModel, owned.model)
    api_client = cast(Any, model.client)._api_client
    sync_http = owned._cleanup.sync_http
    assert isinstance(sync_http, httpx.Client)
    assert api_client._httpx_client is sync_http
    assert api_client._http_options.httpx_client is sync_http

    # Even a direct SDK sync call that skips the mixin lands on the in-process wire.
    with pytest.raises(httpx.ConnectError):
        cast(Any, model.client).models.count_tokens(model=model.model, contents="hello")
    assert wire.sends == 1 and wire.hosts == ["generativelanguage.googleapis.com"]

    closes: list[int] = []
    real_close = sync_http.close

    def counted() -> None:
        closes.append(1)
        real_close()

    sync_http.close = counted
    await owned.aclose()
    await owned.aclose()
    assert closes == [1] and sync_http.is_closed


@pytest.mark.asyncio
async def test_google_freeze_rejects_a_foreign_sync_client(wire: Wire) -> None:
    owned = build("google", guard_for(wire))
    cleanup = owned._cleanup
    foreign = new_google_transport_bundle()
    mixed = GoogleTransportBundle(
        async_http=cleanup.async_http, sync_http=foreign.sync_http, cleanup=cleanup
    )
    with pytest.raises(LeaseProviderTransportError):
        verify_and_freeze_google_transport(cast(Any, owned.model).client, mixed, headers={})
    await owned.aclose()
    await foreign.cleanup.aclose()
