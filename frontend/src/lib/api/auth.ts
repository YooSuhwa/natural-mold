import { apiFetch } from '@/lib/api/client'
import type { AuthResponse, MeResponse, RefreshResponse } from '@/lib/types/user'

export interface LoginPayload {
  email: string
  password: string
}

export interface RegisterPayload {
  email: string
  password: string
  name: string
}

export const authApi = {
  login: (payload: LoginPayload) =>
    apiFetch<AuthResponse>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  register: (payload: RegisterPayload) =>
    apiFetch<AuthResponse>('/api/auth/register', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  logout: () =>
    apiFetch<{ ok: boolean }>('/api/auth/logout', {
      method: 'POST',
    }),

  refresh: () =>
    apiFetch<RefreshResponse>('/api/auth/refresh', {
      method: 'POST',
    }),

  me: () => apiFetch<MeResponse>('/api/auth/me'),
}
