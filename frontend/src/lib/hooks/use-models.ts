'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { modelsApi } from '@/lib/api/models'
import type { ModelCreate, ModelUpdate } from '@/lib/types/model'

const KEY_LIST = ['models'] as const

export function useModels() {
  return useQuery({
    queryKey: KEY_LIST,
    queryFn: modelsApi.list,
    staleTime: 60_000,
  })
}

export function useModel(id: string) {
  return useQuery({
    queryKey: ['models', id],
    queryFn: () => modelsApi.get(id),
    enabled: !!id,
  })
}

export function useCreateModel() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: ModelCreate) => modelsApi.create(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY_LIST }),
  })
}

export function useUpdateModel() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: ModelUpdate }) =>
      modelsApi.update(id, data),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: KEY_LIST })
      qc.invalidateQueries({ queryKey: ['models', id] })
    },
  })
}

export function useDeleteModel() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => modelsApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY_LIST }),
  })
}

/**
 * Discovery is a mutation rather than a query because it triggers a remote
 * network probe (provider list endpoint) and shouldn't be cached as a normal
 * read — the user explicitly clicks "Discover" each time.
 */
export function useDiscoverModels() {
  return useMutation({
    mutationFn: (credentialId: string) =>
      modelsApi.discoverFromCredential(credentialId),
  })
}
