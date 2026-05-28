'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { modelsApi } from '@/lib/api/models'
import type { ModelCreate, ModelTestPreviewRequest, ModelUpdate } from '@/lib/types/model'

const KEY_LIST = ['models'] as const

interface UseModelsOptions {
  /**
   * Super-user only. When true, the request also returns rows where
   * `is_visible=false`. Cached separately from the default (visible-only)
   * query so the agent-creation selector stays clean.
   */
  includeHidden?: boolean
}

export function useModels(options?: UseModelsOptions) {
  const includeHidden = options?.includeHidden ?? false
  return useQuery({
    queryKey: includeHidden ? [...KEY_LIST, 'all'] : KEY_LIST,
    queryFn: () => modelsApi.list({ include_hidden: includeHidden }),
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
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY_LIST })
      qc.invalidateQueries({ queryKey: [...KEY_LIST, 'all'] })
    },
  })
}

export function useUpdateModel() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: ModelUpdate }) => modelsApi.update(id, data),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: KEY_LIST })
      qc.invalidateQueries({ queryKey: [...KEY_LIST, 'all'] })
      qc.invalidateQueries({ queryKey: ['models', id] })
    },
  })
}

export function useDeleteModel() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => modelsApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY_LIST })
      qc.invalidateQueries({ queryKey: [...KEY_LIST, 'all'] })
    },
  })
}

/**
 * Discovery is a mutation rather than a query because it triggers a remote
 * network probe (provider list endpoint) and shouldn't be cached as a normal
 * read — the user explicitly clicks "Discover" each time.
 */
export function useDiscoverModels() {
  return useMutation({
    mutationFn: (credentialId: string) => modelsApi.discoverFromCredential(credentialId),
  })
}

/**
 * Test connection — supports both registered and preview modes through one
 * mutation surface. Caller picks shape; we route to the right backend route.
 */
export function useTestModel() {
  return useMutation({
    mutationFn: (
      input:
        | { mode: 'registered'; modelId: string; credentialId: string }
        | { mode: 'preview'; payload: ModelTestPreviewRequest },
    ) => {
      if (input.mode === 'registered') {
        return modelsApi.testRegistered(input.modelId, input.credentialId)
      }
      return modelsApi.testPreview(input.payload)
    },
  })
}
