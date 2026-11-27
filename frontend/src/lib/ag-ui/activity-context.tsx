'use client'

import { createContext, useContext, useMemo, type ReactNode } from 'react'
import { useAuiState } from '@assistant-ui/react'
import { indexMoldyMessageActivities, messageActivityPresentation } from './activity-projection'
import { useMoldyAgUiFileActivities } from './activity-file-hydration'
import type { AgUiActivitySnapshot, AgUiMessageLink } from './chat-history-types'

const NO_LINKS: readonly AgUiMessageLink[] = []

interface ActivityScope {
  readonly conversationId: string
  readonly activities: readonly AgUiActivitySnapshot[]
  readonly links: readonly AgUiMessageLink[]
  readonly index: ReturnType<typeof indexMoldyMessageActivities>
}

const ActivityContext = createContext<ActivityScope | null>(null)

export function AgUiActivityProvider({
  conversationId,
  activities,
  links = NO_LINKS,
  children,
}: {
  readonly conversationId: string
  readonly activities: readonly AgUiActivitySnapshot[]
  readonly links?: readonly AgUiMessageLink[]
  readonly children: ReactNode
}) {
  const fileActivities = useMoldyAgUiFileActivities(conversationId, activities)
  const value = useMemo(
    () => ({
      conversationId,
      activities,
      links,
      index: indexMoldyMessageActivities(fileActivities, links),
    }),
    [conversationId, activities, links, fileActivities],
  )
  return <ActivityContext.Provider value={value}>{children}</ActivityContext.Provider>
}

export function useMoldyAgUiActivities() {
  return useContext(ActivityContext)
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
