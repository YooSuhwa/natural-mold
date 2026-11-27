"""The auxiliary model may see a private image only under the current org and run."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.company_catalog import CREDENTIAL_ORGS_KEY, with_designation
from app.models.organization import OrganizationMember, OrganizationRoleGrant, Tenant
from app.services.chat import image_description_provider as provider

if TYPE_CHECKING:
    from tests.authz.image_description_fixture import Case
    from tests.authz.org_context_fixture import OrgFixture
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.image_description_fixture"]


async def test_org_admin_opts_in_and_disables_without_widening_member_rights(
    case: Case,
    db: AsyncSession,
    raw_client: AsyncClient,
    org_fixture: OrgFixture,
) -> None:
    case.org.settings = {}
    await db.commit()
    path = f"/api/orgs/{case.org.id}"
    initial = await raw_client.get(path)
    assert initial.status_code == 200
    headers = {**org_fixture.session.headers(), "If-Match": initial.headers["etag"]}
    body = {"settings": {"image_description": case.policy_settings()}}
    denied = await raw_client.patch(path, headers=headers, json=body)
    assert denied.status_code == 403

    db.add(
        OrganizationRoleGrant(
            tenant_id=case.context.tenant_id,
            org_id=case.org.id,
            role="admin",
            principal_type="user",
            principal_id=case.context.user_id,
        )
    )
    await db.commit()
    enabled = await raw_client.patch(path, headers=headers, json=body)
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["settings"]["image_description"]["model_id"] == str(case.model.id)
    disabled = await raw_client.patch(
        path,
        headers={**headers, "If-Match": enabled.headers["etag"]},
        json={"settings": {"image_description": None}},
    )
    assert disabled.status_code == 200
    with pytest.raises(provider.ImageDescriptionDenied) as denied_call:
        await case.resolve(db, dispatch=True)
    assert denied_call.value.code == "policy_disabled"


async def test_pin_and_history_never_open_key_and_cancel_blocks_dispatch(
    case: Case,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    open_key = AsyncMock(side_effect=AssertionError("pin/history attempted to decrypt"))
    monkeypatch.setattr(provider, "open_values", open_key)
    pinned = await case.resolve(db)
    assert pinned.model_id == case.model.id
    assert pinned.attachment.id == case.attachment.id
    case.run.status, case.run.is_active = "completed", False
    await db.commit()
    assert (await case.resolve(db)).policy_fingerprint == pinned.policy_fingerprint
    with pytest.raises(provider.ImageDescriptionDenied):
        await case.resolve(db, dispatch=True)
    open_key.assert_not_awaited()


async def test_authorized_model_records_measured_usage_and_actual_cost(
    case: Case,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "private-fixture-key"
    open_key = AsyncMock(return_value={"api_key": key})
    send = AsyncMock(return_value=("Blue bar", 20, 3))
    monkeypatch.setattr(provider, "open_values", open_key)
    monkeypatch.setattr(provider, "send_image_description", send)
    target = await case.resolve(db, dispatch=True)
    result = await provider.describe_image(
        target, image=b"imagebits", mime_type="image/png", question="What is drawn?"
    )
    assert result.description == "Blue bar"
    assert (result.input_tokens, result.output_tokens) == (20, 3)
    assert result.estimated_cost_usd == Decimal("0.00026")
    assert result.credential_scope == "personal"
    assert key not in repr(target)
    send.assert_awaited_once()
    assert send.await_args.kwargs["endpoint"] == "https://api.openai.com/v1/chat/completions"
    assert send.await_args.kwargs["image"] == b"imagebits"
    assert send.await_args.kwargs["question"] == "What is drawn?"


async def test_same_user_in_other_company_cannot_read_or_send(
    case: Case,
    db: AsyncSession,
    private_scopes: PrivateScopes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    open_key = AsyncMock()
    monkeypatch.setattr(provider, "open_values", open_key)
    for dispatch in (False, True):
        with pytest.raises(provider.ImageDescriptionDenied):
            await case.resolve(db, context=private_scopes.second, dispatch=dispatch)
    open_key.assert_not_awaited()


async def test_current_member_removed_after_pin_loses_access(
    case: Case,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await case.resolve(db)
    member = await db.get(OrganizationMember, (case.org.id, case.context.user_id))
    assert member is not None
    member.status = "removed"
    await db.commit()
    open_key = AsyncMock()
    monkeypatch.setattr(provider, "open_values", open_key)
    with pytest.raises(provider.ImageDescriptionDenied):
        await case.resolve(db, dispatch=True)
    open_key.assert_not_awaited()


async def test_attachment_unlinked_between_pin_and_dispatch_is_refused(
    case: Case,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await case.resolve(db)
    case.attachment.conversation_id = None
    await db.commit()
    open_key = AsyncMock()
    monkeypatch.setattr(provider, "open_values", open_key)
    with pytest.raises(provider.ImageDescriptionDenied):
        await case.resolve(db, dispatch=True)
    open_key.assert_not_awaited()


async def test_visible_model_disabled_by_catalog_after_pin_is_refused(
    case: Case,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await case.resolve(db)
    case.org.settings = {
        **case.org.settings,
        "catalog_policy": {
            "items": [{"type": "model", "id": str(case.model.id), "enabled": False}]
        },
    }
    await db.commit()
    open_key = AsyncMock()
    monkeypatch.setattr(provider, "open_values", open_key)
    with pytest.raises(provider.ImageDescriptionDenied):
        await case.resolve(db, dispatch=True)
    open_key.assert_not_awaited()


async def test_over_budget_and_unknown_prices_stop_before_any_send(
    case: Case,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send = AsyncMock()
    monkeypatch.setattr(provider, "send_image_description", send)
    monkeypatch.setattr(provider, "open_values", AsyncMock(return_value={"api_key": "fixture"}))
    case.org.settings = {"image_description": {**case.policy_settings(), "max_cost_usd": "0.001"}}
    await db.commit()
    with pytest.raises(provider.ImageDescriptionDenied) as denied:
        await case.resolve(db, dispatch=True)
    assert denied.value.code == "budget_limit"
    case.org.settings = {"image_description": case.policy_settings()}
    case.model.cost_per_input_token = None
    await db.commit()
    with pytest.raises(provider.ImageDescriptionDenied):
        await case.resolve(db, dispatch=True)
    send.assert_not_awaited()


async def test_selected_credential_in_another_membership_is_never_decrypted(
    case: Case,
    db: AsyncSession,
    private_scopes: PrivateScopes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = private_scopes.second
    case.credential.org_id, case.credential.tenant_id = other.org_id, other.tenant_id
    await db.commit()
    open_key = AsyncMock()
    monkeypatch.setattr(provider, "open_values", open_key)
    with pytest.raises(provider.ImageDescriptionDenied):
        await case.resolve(db, dispatch=True)
    open_key.assert_not_awaited()


async def test_company_key_requires_live_designation_to_run_org(
    case: Case,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = case.credential
    credential.user_id, credential.org_id = None, None
    credential.is_system, credential.scope = True, "tenant"
    tenant = await db.get(Tenant, case.context.tenant_id)
    assert tenant is not None
    tenant.settings = with_designation(
        tenant.settings, CREDENTIAL_ORGS_KEY, credential.id, (case.org.id,)
    )
    await db.commit()
    open_key = AsyncMock(return_value={"api_key": "company-fixture"})
    monkeypatch.setattr(provider, "open_values", open_key)
    await case.resolve(db, dispatch=True)
    open_key.assert_awaited_once()
    tenant.settings = with_designation(tenant.settings, CREDENTIAL_ORGS_KEY, credential.id, ())
    await db.commit()
    with pytest.raises(provider.ImageDescriptionDenied):
        await case.resolve(db, dispatch=True)
    assert open_key.await_count == 1


async def test_model_permission_withdrawn_after_pin_is_not_memoized(
    case: Case, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.authz.f05_support import enforce

    actor = f"user:{case.context.user_id}"
    model_grant = (actor, "can_use", f"llm_model:{case.model.id}")
    key_grant = (actor, "can_use", f"credential:{case.credential.id}")
    monkeypatch.setattr(provider, "runtime_authz", lambda: enforce(model_grant, key_grant))
    await case.resolve(db)
    monkeypatch.setattr(provider, "runtime_authz", lambda: enforce(key_grant))
    open_key = AsyncMock()
    monkeypatch.setattr(provider, "open_values", open_key)
    with pytest.raises(provider.ImageDescriptionDenied):
        await case.resolve(db, dispatch=True)
    open_key.assert_not_awaited()


async def test_rotated_compatible_credential_base_cannot_redirect_private_image(
    case: Case, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = "https://vision.example/v1"
    case.model.provider, case.model.base_url = "openai_compatible", base
    case.credential.definition_key, case.credential.allowed_hosts = (
        "openai_compatible",
        ["vision.example"],
    )
    case.org.settings = {
        "image_description": {
            "model_id": str(case.model.id),
            "provider": "openai_compatible",
            "base_url": base,
        }
    }
    await db.commit()
    await case.resolve(db)
    monkeypatch.setattr(
        provider,
        "open_values",
        AsyncMock(return_value={"api_key": "fixture", "base_url": "https://changed.example/v1"}),
    )
    send = AsyncMock()
    monkeypatch.setattr(provider, "send_image_description", send)
    with pytest.raises(provider.ImageDescriptionDenied) as denied:
        await case.resolve(db, dispatch=True)
    assert denied.value.code == "access_denied"
    send.assert_not_awaited()


async def test_upload_caps_and_cancel_signal_win_before_key_open(
    case: Case, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime

    await case.resolve(db)
    open_key = AsyncMock()
    monkeypatch.setattr(provider, "open_values", open_key)
    case.attachment.size_bytes = 4 * 1024 * 1024 + 1
    await db.commit()
    with pytest.raises(provider.ImageDescriptionDenied) as oversized:
        await case.resolve(db, dispatch=True)
    assert oversized.value.code == "size_limit"
    case.attachment.size_bytes = 9
    case.run.cancel_requested_at = datetime.now(UTC).replace(tzinfo=None)
    await db.commit()
    with pytest.raises(provider.ImageDescriptionDenied) as cancelled:
        await case.resolve(db, dispatch=True)
    assert cancelled.value.code == "access_denied"
    open_key.assert_not_awaited()
