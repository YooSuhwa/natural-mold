"""Control-plane operations on one provider sandbox tagged at creation by Moldy."""

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing, asynccontextmanager
from typing import cast
from uuid import UUID

from opensandbox import Sandbox, SandboxManager
from opensandbox.exceptions import SandboxApiException
from opensandbox.models.sandboxes import SandboxFilter, SandboxInfo

from app.agent_runtime import skill_sandbox
from app.agent_runtime.goal_sandbox_provider import goal_workspace_metadata, valid_output_name


class WorkspaceIdentityError(RuntimeError):
    """The provider returned an unrelated or ambiguous workspace."""


async def _get(manager: SandboxManager, provider_id: str) -> SandboxInfo | None:
    try:
        return await manager.get_sandbox_info(provider_id)
    except SandboxApiException as exc:
        if exc.status_code != 404:
            raise
        return None


async def _owned(
    manager: SandboxManager, workspace_id: UUID, provider_id: str | None
) -> SandboxInfo | None:
    metadata = goal_workspace_metadata(workspace_id)
    if provider_id:
        info = await _get(manager, provider_id)
        if info is None:
            return None
        if info.id != provider_id:
            raise WorkspaceIdentityError("Provider returned another sandbox.")
    else:
        # A worker can die between provider creation and recording its id.
        page = await manager.list_sandbox_infos(SandboxFilter(metadata=metadata, page_size=2))
        if not page.sandbox_infos and page.pagination.total_items == 0:
            return None
        if len(page.sandbox_infos) != 1 or page.pagination.total_items != 1:
            raise WorkspaceIdentityError("Provider workspace discovery was ambiguous.")
        info = page.sandbox_infos[0]
    if any((info.metadata or {}).get(key) != value for key, value in metadata.items()):
        raise WorkspaceIdentityError("Provider returned a workspace owned by another scope.")
    return info


@asynccontextmanager
async def _manager() -> AsyncIterator[SandboxManager]:
    manager = await SandboxManager.create(skill_sandbox.connection_config(15))
    try:
        yield manager
    finally:
        await manager.close()


async def pause(workspace_id: UUID, provider_id: str | None) -> bool:
    async with _manager() as manager:
        info = await _owned(manager, workspace_id, provider_id)
        if info is None:
            return False
        if info.status.state.lower() == "paused":
            return True
        if info.status.state.lower() != "running":
            return False
        await manager.pause_sandbox(info.id)
        current = await _owned(manager, workspace_id, info.id)
        return current is not None and current.status.state.lower() == "paused"


async def delete(workspace_id: UUID, provider_id: str | None) -> bool:
    async with _manager() as manager:
        info = await _owned(manager, workspace_id, provider_id)
        if info is None:
            # A known id disappeared, or the tagged discovery after a crash was empty.
            return True
        try:
            await manager.kill_sandbox(info.id)
        except SandboxApiException as exc:
            if exc.status_code != 404:
                raise
        # Kill is asynchronous; a subsequent tick checks the same resource if still present.
        return await _owned(manager, workspace_id, info.id) is None


async def read_completed_outputs(
    workspace_id: UUID, provider_id: str | None, names: set[str]
) -> dict[str, bytes] | None:
    async with _manager() as manager:
        info = await _owned(manager, workspace_id, provider_id)
        if info is None:
            return None
        if info.status.state.lower() != "running":
            # A stale executing attempt is not woken to run code while its files are recovered.
            raise WorkspaceIdentityError("Recovered writer is not in a readable state.")
        sandbox = await Sandbox.connect(
            info.id, connection_config=skill_sandbox.connection_config(15)
        )
        try:
            files: dict[str, bytes] = {}
            total = 0
            # Read only the root-owned export prepared before the worker persisted
            # the pending receipt. Re-running a skill or snapshot command here can
            # race the original process and replace the very result being recovered.
            for name in names:
                if not valid_output_name(name):
                    raise ValueError("Invalid filename in workspace receipt.")
                # The pinned SDK implements an async generator here. Its Protocol
                # annotates this method as an async def without a yield.
                chunks = cast(
                    AsyncGenerator[bytes, None],
                    sandbox.files.read_bytes_stream(f"/workspace/.moldy-export/{name}"),
                )
                data = bytearray()
                async with aclosing(chunks):
                    async for chunk in chunks:
                        total += len(chunk)
                        if (
                            len(data) + len(chunk) > skill_sandbox.MAX_OUTPUT_FILE_BYTES
                            or total > skill_sandbox.MAX_OUTPUT_TOTAL_BYTES
                        ):
                            raise ValueError("Workspace receipt exceeds the output limits.")
                        data.extend(chunk)
                files[name] = bytes(data)
            return files
        finally:
            await sandbox.close()
