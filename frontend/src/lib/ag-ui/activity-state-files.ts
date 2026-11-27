import type { DeepAgentFile } from '@/lib/chat/langgraph-runtime/deepagents-state'
import { fileActivityPointer, fileActivitySummary } from './activity-values'
import type { AgUiActivitySnapshot } from './chat-history-types'

export function selectAgUiStateFiles(
  hydrated: readonly AgUiActivitySnapshot[],
  conversationId: string,
  physicalRunId: string | null | undefined,
): readonly DeepAgentFile[] {
  const files = new Map<string, { versionId: string; file: DeepAgentFile }>()
  if (!physicalRunId) return []
  for (const activity of hydrated) {
    if (
      activity.activityType !== 'moldy.file_event' ||
      activity.subagentRunId ||
      activity.physicalRunId !== physicalRunId
    )
      continue
    const pointer = fileActivityPointer(activity.content)
    if (!pointer) continue
    const row = fileActivitySummary(activity.content, conversationId)
    if (!row || row.run_id !== physicalRunId || row.op === 'deleted' || row.status === 'deleted') {
      if (files.get(pointer.id)?.versionId === pointer.version_id) files.delete(pointer.id)
      continue
    }
    files.set(row.id, {
      versionId: row.version_id,
      file: {
        id: row.id,
        name: row.display_name,
        path: row.path,
        mimeType: row.mime_type,
        sizeBytes: row.size_bytes,
        artifactKind: row.artifact_kind,
        ...(row.status === 'ready'
          ? { previewUrl: row.preview_url, downloadUrl: row.download_url }
          : {}),
      },
    })
  }
  return Array.from(files.values(), (entry) => entry.file)
}
