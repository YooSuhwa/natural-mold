import { apiFetch } from './client'
import type { Agent, BuilderSession } from '@/lib/types'

export const builderApi = {
  start: (userRequest: string) =>
    apiFetch<BuilderSession>('/api/builder', {
      method: 'POST',
      body: JSON.stringify({ user_request: userRequest }),
    }),

  getSession: (sessionId: string) => apiFetch<BuilderSession>(`/api/builder/${sessionId}`),

  confirm: (sessionId: string) =>
    apiFetch<Agent>(`/api/builder/${sessionId}/confirm`, { method: 'POST' }),
}
