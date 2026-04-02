import { apiFetch } from './client'
import type { Template } from '@/lib/types'

export const templatesApi = {
  list: (category?: string) =>
    apiFetch<Template[]>(
      `/api/templates${category ? `?category=${encodeURIComponent(category)}` : ''}`,
    ),
  get: (id: string) => apiFetch<Template>(`/api/templates/${id}`),
}
