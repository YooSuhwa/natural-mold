'use client'

import { createContext, useContext } from 'react'

export const ChatConversationContext = createContext<string | null>(null)

// A restored right-rail task may outlive its originating room. Only this room may issue commands.
export const ChatWorkCommandConversationContext = createContext<string | null>(null)

export function useChatConversationId(): string | null {
  return useContext(ChatConversationContext)
}
