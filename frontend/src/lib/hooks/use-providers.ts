'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { providersApi } from '@/lib/api/providers'
import type { ProviderCreateRequest, ProviderUpdateRequest } from '@/lib/types'

export function useProviders() {
  return useQuery({ queryKey: ['providers'], queryFn: providersApi.list, staleTime: 60000 })
}

export function useCreateProvider() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: ProviderCreateRequest) => providersApi.create(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['providers'] }),
  })
}

export function useUpdateProvider() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: ProviderUpdateRequest }) =>
      providersApi.update(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['providers'] }),
  })
}

export function useDeleteProvider() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => providersApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['providers'] }),
  })
}

export function useTestProvider() {
  return useMutation({
    mutationFn: (id: string) => providersApi.test(id),
  })
}

export function useDiscoverModels() {
  return useMutation({
    mutationFn: (id: string) => providersApi.discoverModels(id),
  })
}
