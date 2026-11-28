'use client'

import { useMemo } from 'react'
import { useAuiState } from '@assistant-ui/react'
import { resolveDataUI } from '@/lib/chat/data-ui-registry'
import { toolCardsForMessage } from './activity-card-owner'
import { useMoldyAgUiActivities } from './activity-context'

export function AgUiToolDataCards() {
  const scope = useMoldyAgUiActivities()
  const message = useAuiState((s) => (scope?.toolCards.size ? s.message : null))
  const cards = useMemo(() => {
    if (!scope || message?.role !== 'assistant') return []
    return toolCardsForMessage(message, scope.toolCards, scope.links).flatMap((activity) => {
      const data = activity.content
      const card = typeof data.type === 'string' ? resolveDataUI(data.type, data.props) : null
      return card
        ? [{ key: JSON.stringify([activity.physicalRunId, activity.messageId]), ...card }]
        : []
    })
  }, [message, scope])
  return cards.map(({ key, Component, props }) => <Component key={key} {...props} />)
}
