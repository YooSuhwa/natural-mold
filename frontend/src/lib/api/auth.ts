import { apiFetch, apiUpload } from '@/lib/api/client'
import type { AuthResponse, RefreshResponse, User } from '@/lib/types/user'

export interface LoginPayload {
  email: string
  password: string
}

export interface RegisterPayload {
  email: string
  password: string
  display_name: string
}

export interface ProfileUpdatePayload {
  display_name: string | null
  avatar_mode: 'auto' | 'initials'
  avatar_initials: string | null
  avatar_color: NonNullable<User['avatar_color']>
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

  updateProfile: (payload: ProfileUpdatePayload) =>
    apiFetch<User>('/api/auth/me/profile', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  uploadAvatarImage: (file: File) => {
    const formData = new FormData()
    formData.append('file', file)
    return apiUpload<User>('/api/auth/me/avatar-image', formData)
  },

  deleteAvatarImage: () =>
    apiFetch<User>('/api/auth/me/avatar-image', {
      method: 'DELETE',
    }),

  logout: () =>
    apiFetch<{ ok: boolean }>('/api/auth/logout', {
      method: 'POST',
    }),

  refresh: () =>
    apiFetch<RefreshResponse>('/api/auth/refresh', {
      method: 'POST',
    }),

  me: () => apiFetch<User>('/api/auth/me'),
}
