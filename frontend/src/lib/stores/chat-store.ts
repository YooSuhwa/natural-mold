import { atom } from 'jotai'

export interface StreamingToolCall {
  name: string
  status: 'calling' | 'completed'
  params?: Record<string, unknown>
  result?: string
  startedAt?: number
  completedAt?: number
}

export interface TokenUsage {
  inputTokens: number
  outputTokens: number
  cost: number
}

export const streamingMessageAtom = atom<{ id: string; content: string } | null>(null)
export const streamingToolCallsAtom = atom<StreamingToolCall[]>([])
export const isStreamingAtom = atom(false)

export const sessionTokenUsageAtom = atom<TokenUsage>({
  inputTokens: 0,
  outputTokens: 0,
  cost: 0,
})
export const lastMessageTokensAtom = atom<TokenUsage | null>(null)
