'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { modelsApi } from '@/lib/api/models'
import type { ModelCreateRequest, ModelUpdateRequest, ModelBulkCreateRequest } from '@/lib/types'

export function useModels() {
  return useQuery({ queryKey: ['models'], queryFn: modelsApi.list, staleTime: 60000 })
}

export function useCreateModel() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: ModelCreateRequest) => modelsApi.create(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['models'] }),
  })
}

export function useBulkCreateModels() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: ModelBulkCreateRequest) => modelsApi.bulkCreate(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['models'] }),
  })
}

export function useUpdateModel() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: ModelUpdateRequest }) =>
      modelsApi.update(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['models'] }),
  })
}

export function useDeleteModel() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => modelsApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['models'] }),
  })
}
