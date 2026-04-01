import { atom } from "jotai"

export interface StreamingToolCall {
  name: string
  status: "calling" | "completed"
  params?: Record<string, unknown>
  result?: string
}

export const streamingMessageAtom = atom<{ id: string; content: string } | null>(null)
export const streamingToolCallsAtom = atom<StreamingToolCall[]>([])
export const isStreamingAtom = atom(false)
