import { apiFetch } from './client'
import type { MiddlewareRegistryItem } from '@/lib/types'

export const middlewaresApi = {
  list: () => apiFetch<MiddlewareRegistryItem[]>('/api/middlewares'),
}
