"""Recognize historical file calls against the agent's formerly writable shared memo."""

from collections.abc import Mapping

from app.agent_runtime.filesystem_permissions import UnsafeFilesystemPath, canonicalize_virtual_path
from app.agent_runtime.protocol_redaction import REDACTED_MEMORY_FIELD

_MEMO_FILE_TOOLS = frozenset({"read_file", "write_file", "edit_file", "grep"})


def _memo_path(path: str, *, search: bool = False) -> bool:
    parts = path.casefold().split("/")
    if len(parts) < 2 or parts[1] != "agents":
        return False
    # A search of the /agents mount or the agent's own directory intersects its memo.
    return (search and len(parts) in (2, 3)) or (len(parts) == 4 and parts[3] == "agents.md")


def redacted_file_args(name: str) -> dict[str, str]:
    return {"path" if name == "grep" else "file_path": REDACTED_MEMORY_FIELD}


def private_memo_file_call(name: str, args: object) -> bool:
    """Only disclose a file body when its actual target is known to be outside the memo."""
    if name not in _MEMO_FILE_TOOLS:
        return False
    if not isinstance(args, Mapping):
        return True
    key, other = ("path", "file_path") if name == "grep" else ("file_path", "path")
    target = args.get(key)
    if name == "grep" and target is None:
        target = "/"  # The native grep default. Filter file groups from its output below.
    if not isinstance(target, str) or (other in args and args[other] != target):
        return True
    try:
        path = canonicalize_virtual_path(target)
    except UnsafeFilesystemPath:
        return True
    return _memo_path(path, search=name == "grep")


def public_grep_result(content: object) -> str:
    """Filter native file, count and content output without reconstructing matched text."""
    if not isinstance(content, str):
        return REDACTED_MEMORY_FIELD
    retained: list[str] = []
    hiding = False
    removed = False
    # Deep Agents emits an absolute file heading and indents every matched/context
    # line. Split only on its actual separator, treating other Unicode characters
    # as data within a line.
    for line in content.split("\n"):
        if line.startswith("/"):
            path, separator, suffix = line.partition(":")
            try:
                path = canonicalize_virtual_path(path)
            except UnsafeFilesystemPath:
                # Its following rows have no trustworthy file attribution.
                hiding = removed = True
            else:
                if _memo_path(path):
                    hiding = removed = True
                elif (
                    not separator
                    or not suffix
                    or (suffix.startswith(" ") and suffix[1:].isdecimal())
                ):
                    hiding = False
        if not hiding:
            retained.append(line)
    return ("\n".join(retained) or REDACTED_MEMORY_FIELD) if removed else content
