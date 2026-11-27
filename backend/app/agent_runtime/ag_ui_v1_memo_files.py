"""Recognize historical file calls against the agent's formerly writable shared memo."""

from collections.abc import Mapping

from app.agent_runtime.filesystem_permissions import UnsafeFilesystemPath, canonicalize_virtual_path

_MEMO_FILE_TOOLS = frozenset({"read_file", "write_file", "edit_file"})


def private_memo_file_call(name: str, args: object) -> bool:
    """Only disclose a file body when its actual target is known to be outside the memo."""
    if name not in _MEMO_FILE_TOOLS:
        return False
    if not isinstance(args, Mapping):
        return True
    target = args.get("file_path")
    if not isinstance(target, str) or ("path" in args and args["path"] != target):
        return True
    try:
        path = canonicalize_virtual_path(target)
    except UnsafeFilesystemPath:
        return True
    parts = path.casefold().split("/")
    return len(parts) == 4 and parts[1] == "agents" and parts[3] == "agents.md"
