import { apiFetch } from './client'
import type { SystemStatusOut } from '@/lib/types/system-status'

// Platform operators only (super_user); read-only.
export const systemStatusApi = {
  get: () => apiFetch<SystemStatusOut>('/api/system/status'),
}
