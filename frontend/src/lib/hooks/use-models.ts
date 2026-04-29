'use client'

import { useQuery } from '@tanstack/react-query'
import { modelsApi } from '@/lib/api/models'

export function useModels() {
  return useQuery({ queryKey: ['models'], queryFn: modelsApi.list, staleTime: 60_000 })
}

export function useModel(id: string) {
  return useQuery({
    queryKey: ['models', id],
    queryFn: () => modelsApi.get(id),
    enabled: !!id,
  })
}
