'use client'

import { useQuery } from '@tanstack/react-query'
import { templatesApi } from '@/lib/api/templates'
import { templateQueryKeys } from '@/lib/query-keys/templates'

export function useTemplates(category?: string, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: templateQueryKeys.list(category),
    queryFn: () => templatesApi.list(category),
    staleTime: 300000,
    enabled: options?.enabled ?? true,
  })
}
