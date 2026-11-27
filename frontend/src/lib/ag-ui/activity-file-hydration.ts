import { useEffect, useMemo, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { artifactKeys, listConversationArtifacts } from '@/lib/api/artifacts'
import { fileActivityPointer, fileActivitySummary } from './activity-values'
import type { AgUiActivitySnapshot } from './chat-history-types'

/** The transcript carries references. Only the current viewer's authorized REST
 * read supplies the descriptor used by the file card and preview. */
export function useMoldyAgUiFileActivities(
  conversationId: string,
  activities: readonly AgUiActivitySnapshot[],
): readonly AgUiActivitySnapshot[] {
  const client = useQueryClient()
  const pointers = useMemo(
    () =>
      activities.map((activity) =>
        activity.activityType === 'moldy.file_event' && activity.physicalRunId
          ? fileActivityPointer(activity.content)
          : null,
      ),
    [activities],
  )
  const revision = JSON.stringify(
    pointers.flatMap((pointer) => (pointer ? [[pointer.id, pointer.version_id, pointer.op]] : [])),
  )
  const enabled = !!conversationId && pointers.some(Boolean)
  const files = useQuery({
    queryKey: artifactKeys.conversation(conversationId),
    queryFn: () => listConversationArtifacts(conversationId),
    enabled,
    refetchOnMount: 'always',
    retry: false,
  })
  const previous = useRef({ conversationId, revision })
  useEffect(() => {
    const old = previous.current
    previous.current = { conversationId, revision }
    if (
      enabled &&
      old.conversationId === conversationId &&
      old.revision !== revision &&
      old.revision !== '[]'
    ) {
      void client.invalidateQueries({
        queryKey: artifactKeys.conversation(conversationId),
        exact: true,
      })
    }
  }, [client, conversationId, enabled, revision])

  const authorized =
    files.isSuccess && files.isFetchedAfterMount && !files.isFetching ? files.data : undefined
  return useMemo(() => {
    const byId = new Map(
      (authorized ?? [])
        .filter((file) => file.conversation_id === conversationId)
        .map((file) => [file.id, file]),
    )
    return activities.map((activity, index) => {
      if (activity.activityType !== 'moldy.file_event') return activity
      const pointer = pointers[index]
      if (!pointer) return { ...activity, content: {} }
      const row = byId.get(pointer.id)
      if (
        !row ||
        row.version_id !== pointer.version_id ||
        row.run_id !== activity.physicalRunId ||
        row.status === 'deleted' ||
        pointer.op === 'deleted'
      ) {
        return { ...activity, content: { ...pointer } }
      }
      const file = fileActivitySummary({ ...row, op: pointer.op }, conversationId)
      return {
        ...activity,
        content: file ? { ...file, public_message_id: pointer.public_message_id } : { ...pointer },
      }
    })
  }, [activities, authorized, conversationId, pointers])
}
