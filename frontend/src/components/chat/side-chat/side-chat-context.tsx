'use client'

import { createContext, useContext, type RefObject } from 'react'
import type { ResourceContextReference } from '@/lib/chat/context/resource-context'

export type MessageQuote = Extract<ResourceContextReference, { kind: 'conversation' }> & {
  readonly message_id: string
  readonly quote: string
}
export type QuoteDelivery = {
  readonly id: number
  readonly target: 'main' | 'side'
  readonly reference: MessageQuote
  readonly prompt?: string
}
export type SideChatContextValue = {
  readonly draft: RefObject<{
    text: string
    references: readonly ResourceContextReference[]
  } | null>
  readonly mainId: string | null
  readonly mainTitle: string
  readonly sideId: string | null
  readonly open: boolean
  readonly loading: boolean
  readonly error: boolean
  readonly delivery: QuoteDelivery | null
  readonly show: () => void
  readonly close: () => void
  readonly deliver: (request: Omit<QuoteDelivery, 'id'>) => void
  readonly acknowledge: (id: number) => void
}

export const SideChatContext = createContext<SideChatContextValue | null>(null)
export const useSideChat = () => useContext(SideChatContext)
