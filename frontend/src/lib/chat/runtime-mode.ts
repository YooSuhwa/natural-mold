export type ChatRuntimeMode = 'legacy' | 'langgraph_v3'

export function getChatRuntimeMode(): ChatRuntimeMode {
  return process.env.NEXT_PUBLIC_CHAT_RUNTIME === 'legacy' ? 'legacy' : 'langgraph_v3'
}
