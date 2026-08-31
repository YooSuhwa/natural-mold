"""Conversation-wide deletion for internal Deep Agents offloads."""

from pathlib import Path

from app.agent_runtime.offload_storage_cleanup import (
    conversation_deletion_lock,
    conversation_mutation_lock,
    preflight_scoped_tree,
    remove_preflighted_scoped_tree_with_status_locked,
    remove_scoped_tree_with_status_locked,
)
from app.agent_runtime.offload_storage_fd import (
    list_scoped_directories,
)
from app.agent_runtime.offload_storage_types import (
    PHYSICAL_ROOT,
    OffloadCleanupReceipt,
    OffloadGcReceipt,
    OffloadGcScope,
    OffloadMutationScope,
    token,
)


def delete_conversation_offloads(
    data_dir: Path,
    *,
    owner_id: str,
    conversation_id: str,
) -> OffloadCleanupReceipt:
    """Fence future mutations and delete the current conversation roots."""
    internal = data_dir.resolve() / PHYSICAL_ROOT / "offload"
    owner = token("owner", owner_id)
    conversation = token("conversation", conversation_id)
    mutation = OffloadMutationScope(internal, owner, conversation)
    history = ("history", owner, conversation)
    spill = ("spill", owner, conversation)
    with conversation_deletion_lock(mutation):
        _history_removed, history_files = remove_scoped_tree_with_status_locked(
            internal,
            history,
        )
        _spill_removed, spill_files = remove_scoped_tree_with_status_locked(
            internal,
            spill,
        )
        return OffloadCleanupReceipt(
            history_files=history_files,
            spill_files=spill_files,
        )


def gc_spill_run(scope: OffloadGcScope) -> OffloadGcReceipt:
    relative = ("spill", scope.owner, scope.conversation, scope.run)
    expected = preflight_scoped_tree(scope.internal_root, relative)
    with conversation_mutation_lock(scope.mutation_scope):
        removed_run, removed_files = remove_preflighted_scoped_tree_with_status_locked(
            scope.internal_root, relative, expected
        )
    return OffloadGcReceipt(removed_runs=int(removed_run), removed_files=removed_files)


def gc_spill_conversation(
    scope: OffloadGcScope, protected_run_ids: frozenset[str]
) -> OffloadGcReceipt:
    protected = {token("run", run_id) for run_id in protected_run_ids}
    with conversation_mutation_lock(scope.mutation_scope):
        run_names = list_scoped_directories(
            scope.internal_root, ("spill", scope.owner, scope.conversation)
        )
        removed_runs = 0
        removed_files = 0
        for run_name in run_names:
            if run_name in protected:
                continue
            removed_run, files = remove_scoped_tree_with_status_locked(
                scope.internal_root,
                ("spill", scope.owner, scope.conversation, run_name),
            )
            removed_files += files
            removed_runs += int(removed_run)
    return OffloadGcReceipt(removed_runs=removed_runs, removed_files=removed_files)


def gc_history_conversation(scope: OffloadGcScope) -> OffloadGcReceipt:
    relative = ("history", scope.owner, scope.conversation)
    expected = preflight_scoped_tree(scope.internal_root, relative)
    with conversation_mutation_lock(scope.mutation_scope):
        _removed_tree, removed = remove_preflighted_scoped_tree_with_status_locked(
            scope.internal_root, relative, expected
        )
    return OffloadGcReceipt(removed_files=removed)
