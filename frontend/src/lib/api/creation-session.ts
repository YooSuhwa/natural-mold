import { apiFetch } from './client'
import type { Agent, CreationSession, DraftConfig } from '@/lib/types'

export interface CreationMessageResult {
  role: string
  content: string
  current_phase: number
  phase_result: string | null
  question: string | null
  draft_config: DraftConfig | null
  suggested_replies: { options: string[]; multi_select: boolean } | null
  recommended_tools: { name: string; description: string }[]
  recommended_skills: { name: string; description: string }[]
}

export const creationSessionApi = {
  start: () => apiFetch<CreationSession>('/api/agents/create-session', { method: 'POST' }),
  get: (id: string) => apiFetch<CreationSession>(`/api/agents/create-session/${id}`),
  sendMessage: (id: string, content: string) =>
    apiFetch<CreationMessageResult>(`/api/agents/create-session/${id}/message`, {
      method: 'POST',
      body: JSON.stringify({ content }),
    }),
  confirm: (id: string) =>
    apiFetch<Agent>(`/api/agents/create-session/${id}/confirm`, { method: 'POST' }),
}
