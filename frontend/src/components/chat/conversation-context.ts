'use client'

import { createContext, useContext } from 'react'

export const ChatConversationContext = createContext<string | null>(null)

export function useChatConversationId(): string | null {
  return useContext(ChatConversationContext)
}
