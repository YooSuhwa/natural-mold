'use client'

import { createContext, useContext, useMemo, type ReactNode } from 'react'
import { useAuiState } from '@assistant-ui/react'
import { useSetAtom } from 'jotai'
import type { DeepAgentFile } from '@/lib/chat/langgraph-runtime/deepagents-state'
import { chatRightRailAtom } from '@/lib/stores/chat-right-rail'
import { indexMoldyMessageActivities, messageActivityPresentation } from './activity-projection'
import { useMoldyAgUiFileActivities } from './activity-file-hydration'
import { selectAgUiStateFiles } from './activity-state-files'
import type { AgUiActivitySnapshot, AgUiMessageLink } from './chat-history-types'

const NO_LINKS: readonly AgUiMessageLink[] = []

interface ActivityScope {
  readonly conversationId: string
  readonly activities: readonly AgUiActivitySnapshot[]
  readonly links: readonly AgUiMessageLink[]
  readonly index: ReturnType<typeof indexMoldyMessageActivities>
  readonly stateFiles: {
    readonly files: readonly DeepAgentFile[]
    readonly openPreview: (file: DeepAgentFile) => void
  }
}

const ActivityContext = createContext<ActivityScope | null>(null)

export function AgUiActivityProvider({
  conversationId,
  activities,
  physicalRunId,
  links = NO_LINKS,
  children,
}: {
  readonly conversationId: string
  readonly activities: readonly AgUiActivitySnapshot[]
  readonly physicalRunId?: string | null
  readonly links?: readonly AgUiMessageLink[]
  readonly children: ReactNode
}) {
  const fileActivities = useMoldyAgUiFileActivities(conversationId, activities)
  const setRail = useSetAtom(chatRightRailAtom)
  const stateFiles = useMemo(() => {
    const files = selectAgUiStateFiles(fileActivities, conversationId, physicalRunId)
    return {
      files,
      openPreview(file: DeepAgentFile) {
        if (!file.previewUrl || !files.includes(file)) return
        setRail({
          mode: 'artifacts',
          artifacts: { conversationId, selectedArtifactId: file.id, view: 'preview' },
        })
      },
    }
  }, [conversationId, fileActivities, physicalRunId, setRail])
  const value = useMemo(
    () => ({
      conversationId,
      activities,
      links,
      index: indexMoldyMessageActivities(fileActivities, links),
      stateFiles,
    }),
    [conversationId, activities, links, fileActivities, stateFiles],
  )
  return <ActivityContext.Provider value={value}>{children}</ActivityContext.Provider>
}

export function useMoldyAgUiActivities() {
  return useContext(ActivityContext)
}

export function useAgUiStateFiles() {
  return useMoldyAgUiActivities()?.stateFiles
}

export function useAgUiMessageActivity() {
  const scope = useMoldyAgUiActivities()
  const message = useAuiState((s) => (scope ? s.message : null))
  return useMemo(
    () =>
      scope && message
        ? messageActivityPresentation(scope.index, message, scope.conversationId)
        : undefined,
    [scope, message],
  )
}
