"""Private builder file reads validate canonical session storage before filesystem access."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import skill_builder_service, skill_draft_workspace

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture
    from tests.authz.private_scope_fixture import PrivateScopes
pytest_plugins = ["tests.authz.private_scope_fixture"]


@pytest.mark.parametrize("surface", ["files", "files/content?path=SKILL.md"])
async def test_owned_session_foreign_workspace_denied_before_http_file_read(
    db: AsyncSession,
    raw_client: AsyncClient,
    private_scopes: PrivateScopes,
    org_fixture: OrgFixture,
    surface: str,
) -> None:
    # Given persisted A and B sessions and a real B workspace, A has a malformed path edge.
    own = await skill_builder_service.create_session(
        db, context=private_scopes.first, user_request="Owned A fixture"
    )
    foreign = await skill_builder_service.create_session(
        db, context=private_scopes.second, user_request="Foreign B fixture"
    )
    own_path = skill_draft_workspace.create_workspace(own.id)
    own.draft_workspace_path = own_path
    own_root = skill_draft_workspace.resolve_workspace_dir(own_path)
    (own_root / "SKILL.md").write_text("# B06 OWN A PRIVATE DUMMY\n", encoding="utf-8")
    foreign_path = skill_draft_workspace.create_workspace(foreign.id)
    foreign.draft_workspace_path = foreign_path
    root = skill_draft_workspace.resolve_workspace_dir(foreign_path)
    (root / "SKILL.md").write_text("# B06 FOREIGN B PRIVATE DUMMY\n", encoding="utf-8")
    await db.commit()
    own_response = await raw_client.get(
        f"/api/skill-builder/{own.id}/{surface}", headers=org_fixture.session.headers()
    )
    assert own_response.status_code == 200
    foreign_response = await raw_client.get(
        f"/api/skill-builder/{foreign.id}/{surface}", headers=org_fixture.session.headers()
    )
    assert foreign_response.status_code == 404
    own.draft_workspace_path = foreign.draft_workspace_path
    await db.commit()
    # When a real authenticated HTTP request uses the A session ID.
    response = await raw_client.get(
        f"/api/skill-builder/{own.id}/{surface}", headers=org_fixture.session.headers()
    )
    # Then the malformed workspace edge must deny before observing B files.
    assert response.status_code == 404, response.text
