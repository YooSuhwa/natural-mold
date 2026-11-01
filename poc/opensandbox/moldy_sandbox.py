"""PoC: a deepagents ``BaseSandbox`` backed by one OpenSandbox sandbox.

``BaseSandbox`` turns ``execute()`` plus file upload/download into the whole
deepagents file tool surface (ls, read_file, write_file, edit_file, grep,
glob). This adapter is the PoC shape of the ``MoldySandbox`` that the feature
plan v2 (chapter 15.3) proposes; it is not wired into the product runtime.
"""

from __future__ import annotations

from datetime import timedelta

from deepagents.backends.protocol import ExecuteResponse, FileDownloadResponse, FileUploadResponse
from deepagents.backends.sandbox import BaseSandbox
from opensandbox.exceptions import SandboxException
from opensandbox.models.execd import RunCommandOpts
from opensandbox.sync import SandboxSync

MAX_OUTPUT_CHARS = 100_000


class MoldySandbox(BaseSandbox):
    def __init__(
        self, sandbox: SandboxSync, *, default_timeout: int = 120, workdir: str = "/workspace"
    ):
        self._sandbox = sandbox
        self._default_timeout = default_timeout
        self._workdir = workdir
        # execd rejects a working_directory that does not exist yet.
        sandbox.commands.run(f"mkdir -p {workdir}")

    @property
    def id(self) -> str:
        return self._sandbox.id

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        seconds = timeout or self._default_timeout
        execution = self._sandbox.commands.run(
            command,
            opts=RunCommandOpts(
                timeout=timedelta(seconds=seconds), working_directory=self._workdir
            ),
        )
        output = "".join(m.text for m in execution.logs.stdout) + "".join(
            m.text for m in execution.logs.stderr
        )
        if execution.error is not None:
            output += f"\n[{execution.error.name}] {execution.error.value}"
        truncated = len(output) > MAX_OUTPUT_CHARS
        return ExecuteResponse(
            output=output[:MAX_OUTPUT_CHARS], exit_code=execution.exit_code, truncated=truncated
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses = []
        for path, content in files:
            try:
                self._sandbox.files.write_file(path, content)
                responses.append(FileUploadResponse(path=path, error=None))
            except SandboxException:
                responses.append(FileUploadResponse(path=path, error="permission_denied"))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses = []
        for path in paths:
            try:
                responses.append(
                    FileDownloadResponse(
                        path=path, content=self._sandbox.files.read_bytes(path), error=None
                    )
                )
            except SandboxException:
                responses.append(
                    FileDownloadResponse(path=path, content=None, error="file_not_found")
                )
        return responses
