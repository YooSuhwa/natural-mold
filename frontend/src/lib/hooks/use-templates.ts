'use client'

import { useQuery } from '@tanstack/react-query'
import { templatesApi } from '@/lib/api/templates'

export function useTemplates(category?: string) {
  return useQuery({
    queryKey: ['templates', category],
    queryFn: () => templatesApi.list(category),
    staleTime: 300000,
  })
}
