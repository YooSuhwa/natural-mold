export type ChatRuntimeMode = 'legacy' | 'langgraph_v3' | 'ag_ui'

export function getChatRuntimeMode(): ChatRuntimeMode {
  const mode = process.env.NEXT_PUBLIC_CHAT_RUNTIME
  return mode === 'legacy' || mode === 'ag_ui' ? mode : 'langgraph_v3'
}
