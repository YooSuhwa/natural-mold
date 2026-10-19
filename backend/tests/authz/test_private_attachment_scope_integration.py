"""Actual m83 upload writes and reads preserve the registered user's scope."""

from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.message_attachment import MessageAttachment
from app.models.user import User
from tests.authz.native_company_selection import select_second_company
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("other_company", [False, True])
async def test_native_upload_writer_preserves_private_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, other_company: bool
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    async with signup_app(monkeypatch) as stack:
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-upload@example.com",
                "name": "Upload fixture",
                "password": "native-upload-password",
            },
        )
        assert registered.status_code == 201, registered.text
        headers = {"X-CSRF-Token": registered.json()["csrf_token"]}
        if other_company:
            await select_second_company(stack, headers, "native-upload@example.com")
        upload = await stack.client.post(
            "/api/uploads",
            files={"file": ("fixture.txt", b"fixture", "text/plain")},
            headers={"X-CSRF-Token": registered.json()["csrf_token"]},
        )
        assert upload.status_code == 201, upload.text
        upload_id = UUID(upload.json()["id"])
        async with stack.sessions() as db:
            user = await db.scalar(select(User).where(User.email == "native-upload@example.com"))
            row = await db.get(MessageAttachment, upload_id)
            assert user is not None and row is not None
            assert row.user_id == user.id and row.org_id == user.last_active_org_id
            assert row.tenant_id is not None
        fetched = await stack.client.get(f"/api/uploads/{upload_id}")
        assert fetched.status_code == 200 and fetched.content == b"fixture"
